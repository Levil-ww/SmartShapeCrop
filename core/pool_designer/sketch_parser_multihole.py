"""多洞矩形嵌套草图解析器（水池设计器用）—— 空间位置判定 + 箭头方向提示。

与单洞 sketch_parser.py 完全独立：本模块只处理 "1 外框 + N 内框" 的多洞布局。

核心设计原则：
  1. 零侵入：不修改单洞 7 步法的任何函数体，仅通过入口分流启用
  2. 复用基础设施：继续使用 _find_all_rectangles / _multi_scale_ocr_scan /
     _enhance_colored_ink 等已有 vision 辅助函数
  3. 空间判定优先：数值按 (cx, cy) 坐标直接归入区域，不依赖方向汉字
  4. 箭头增强：识别 ← → ↑ ↓ 符号作为方向辅助绑定（可选增强）

识别流程（9 步串行）：
  Step 1. 矩形检测：_find_all_rectangles 找所有候选矩形
  Step 2. 布局分类：从候选中选 1 个外框 + N 个内框，判定横向/纵向排列
  Step 3. 区域划分：基于外框和 N 个内框，划分语义区域（共享区/洞专属区/gap区）
  Step 4. 全局 OCR：复用 _multi_scale_ocr_scan 提取所有数值
  Step 5. 方向标签：方向汉字 + 箭头符号（←→↑↓）双重识别，锁定已知字段
  Step 6. 空间归属：其余数值按 (cx, cy) 坐标归入多洞语义区域
  Step 7. 外框选优：枚举两两组合，选自洽分最高的外框宽高
  Step 8. 几何校验：多洞版本 outer=Σ 自洽校验，缺失值反推
  Step 9. 冲突解决：方向/箭头锁定 > 洞邻近性 > 置信度 > 位数
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    Image = None  # type: ignore

logger = logging.getLogger(__name__)

# ===== 复用现有基础设施 =====
from .sketch_parser_base import (
    _PARSE_TIMEOUT_SEC,
    _normalize_ocr_text,
)
from .sketch_parser_vision import (
    _build_binary_masks,
    _enhance_colored_ink,
    _find_all_rectangles,
    _load_image,
    _make_preprocess_variants,
    _multi_scale_ocr_scan,
    _safe_import_cv2,
    _safe_import_tesseract,
    _to_gray,
)

# ===== 公共常量 =====

# 箭头字符 → 标准边距字段映射
# (同时覆盖 Unicode 箭头和常见 OCR 误读替代字符)
_ARROW_CHAR_MAP = {
    # 左向箭头
    '←': 'margin_left', '←': 'margin_left', '↺': 'margin_left',
    '<': 'margin_left', '«': 'margin_left',
    # 右向箭头
    '→': 'margin_right', '↑': 'margin_right', '↻': 'margin_right',
    '>': 'margin_right', '»': 'margin_right',
    # 上向箭头
    '↑': 'margin_top', '↖': 'margin_top', '↗': 'margin_top',
    '^': 'margin_top', '∧': 'margin_top',
    # 下向箭头
    '↓': 'margin_bottom', '↙': 'margin_bottom', '↘': 'margin_bottom',
    '∨': 'margin_bottom',
}

# 方向汉字映射（保持与 _DIR_CHAR_MAP 一致）
_DIR_CHAR_MAP_MH = {'上': 'margin_top', '下': 'margin_bottom',
                    '左': 'margin_left', '右': 'margin_right'}

# 联合映射：方向汉字 + 箭头符号 共用
_UNIFIED_DIR_MAP = {}
_UNIFIED_DIR_MAP.update(_DIR_CHAR_MAP_MH)
_UNIFIED_DIR_MAP.update(_ARROW_CHAR_MAP)


# ===== 数据结构 =====

@dataclass
class HoleInfo:
    """单个内洞的几何信息 + 专属边距。"""
    index: int = 0                       # 洞编号（从左到右 / 从上到下）
    # 像素坐标（基于草图原图）
    rect_px: tuple = (0, 0, 0, 0)        # (x, y, w, h)
    # 厘米尺寸（解析后赋值）
    w_cm: float = 0.0
    h_cm: float = 0.0
    # 该洞的专属边距（横向为洞左右的外距；纵向为洞上下的外距）
    margin_left_cm: float = 0.0
    margin_right_cm: float = 0.0
    margin_top_cm: float = 0.0       # 共享上边距时，所有洞取同一值
    margin_bottom_cm: float = 0.0    # 共享下边距时，所有洞取同一值


@dataclass
class MultiHoleParseResult:
    """多洞解析的内部中间结果（对外统一转为 SketchParseResult）。"""
    success: bool = False
    message: str = ""
    outer_w_cm: float = 0.0
    outer_h_cm: float = 0.0
    # 共享边距（适用于所有洞等高/等宽的横排/竖排场景）
    margin_top_cm: float = 0.0
    margin_bottom_cm: float = 0.0
    margin_left_cm: float = 0.0     # 最左洞的左侧外边距
    margin_right_cm: float = 0.0    # 最右洞的右侧外边距
    # 洞列表
    holes: list = field(default_factory=list)   # list[HoleInfo]
    # 洞间 gaps（横向洞间距）
    gaps: list = field(default_factory=list)    # list[float]，长度 = len(holes)-1
    # 布局类型
    layout: str = "horizontal"   # "horizontal" | "vertical" | "mixed"
    # 外框像素坐标
    outer_rect_px: tuple = (0, 0, 0, 0)
    # 调试信息
    debug: dict = field(default_factory=dict)


# ======================================================================
# Step 1 + 2: 矩形检测 + 多洞布局分类
# ======================================================================


def _classify_hole_layout(all_rects):
    """从候选矩形中分类出 1 外框 + N 内框，返回 (outer, inners, layout)。

    三阶段算法（抗「联合包围盒/双洞 hull」误识别）：
      Phase A. 选外框：面积最大的矩形
      Phase B. 收集全部满足「严格嵌套 + 面积比例合理」的候选为 pool
      Phase C. 从 pool 中剔除「包含 ≥2 个其他 pool 成员」的 hull（联合包围盒）
      Phase D. 从去 hull 的 pool 中贪心地选一组「互不重叠」的真实洞，
               优先选面积接近的（真实洞间面积差通常 ≤50%）

    Args:
        all_rects: _find_all_rectangles 返回的列表，每项 (x,y,w,h,score,area)

    Returns:
        (outer_rect or None, list[inner_rect], layout_str)
        其中 outer_rect / inner_rect 均为 (x,y,w,h) 四元组
        当无法满足多洞（≥2 个内框）时，返回 (None, [], '')，上层走单洞路径
    """
    if len(all_rects) < 3:
        # 至少需要 1 外框 + 2 内框；不够则标记为单洞，交给现有 7 步法
        logger.debug(f"[MH Step1-2] quick_fail: all_rects={len(all_rects)} < 3")
        return None, [], ''

    # ========== Phase A: 选外框 ==========
    # all_rects 已按面积降序排序 → 面积最大者作为首选外框
    best_outer = all_rects[0]
    best_outer_idx = 0
    ox, oy, ow, oh = best_outer[:4]
    area_outer = ow * oh

    # ========== Phase B: 收集 pool ==========
    # 所有严格嵌套 + 面积比例合理的候选，不管是否互相重叠（先攒入池）
    # 保留原始 idx 以便诊断日志
    pool = []            # list[(ix, iy, iw, ih, area, ratio, src_idx)]
    n_show = min(len(all_rects), 10)
    for idx in range(len(all_rects)):
        if idx == best_outer_idx:
            continue
        r = all_rects[idx]
        ix, iy, iw, ih = r[:4]
        area_inner = iw * ih
        ratio = area_inner / max(1, area_outer)
        nested_ok = (ox < ix and oy < iy and ix + iw < ox + ow and iy + ih < oy + oh)
        ratio_ok = (0.01 <= ratio <= 0.85)  # 放宽：1%~85%（真实洞 ~10%，hull ~30% 均在此区间）
        if idx < n_show:
            logger.debug(f"[MH Step1-2] 候选[{idx}] xywh={(ix,iy,iw,ih)} "
                         f"area={area_inner} ratio={ratio:.3f} "
                         f"nested={nested_ok} ratio_ok={ratio_ok}")
        if not nested_ok:
            continue
        if not ratio_ok:
            continue
        pool.append((ix, iy, iw, ih, area_inner, ratio, idx))

    logger.debug(f"[MH Step1-2] PhaseB pool 规模: {len(pool)} / 总候选 {len(all_rects)}")

    if len(pool) < 2:
        logger.info(f"[MH Step1-2] 非多洞：pool 仅 {len(pool)} 项（<2）→ 回退单洞")
        return None, [], ''

    # ========== Phase C: 剔除 hull ==========
    # 定义：若矩形 A **严格包含** 矩形 B 且 A≠B，则称 A「含」B。
    # 若某候选 pool[i] 含 ≥2 个其他 pool 成员 → 判定为双洞(或多洞)的联合包围盒 → 剔除。
    # 「严格包含」判定：A.x ≤ B.x ∧ A.y ≤ B.y ∧ A.x+A.w ≥ B.x+B.w ∧ A.y+A.h ≥ B.y+B.h
    #   且 4 条边至少有 1 条是严格不同（防止自包含/全等）。
    def _strictly_contains(a, b):
        ax, ay, aw, ah = a[:4]
        bx, by, bw, bh = b[:4]
        edges = (ax <= bx, ay <= by, ax + aw >= bx + bw, ay + ah >= by + bh)
        if not all(edges):
            return False
        # 四条边全相等 → 是同一个/重合矩形，不算包含
        if ax == bx and ay == by and aw == bw and ah == bh:
            return False
        return True

    hull_mask = [False] * len(pool)
    for i in range(len(pool)):
        a = pool[i]
        contains_count = 0
        for j in range(len(pool)):
            if i == j:
                continue
            if _strictly_contains(a, pool[j]):
                contains_count += 1
        if contains_count >= 2:
            hull_mask[i] = True
            logger.info(f"[MH Step1-2] 剔除 hull 候选[{pool[i][6]}] xywh={pool[i][:4]} "
                        f"ratio={pool[i][5]:.3f} 含{contains_count}其他成员")

    pool_filtered = [pool[i] for i in range(len(pool)) if not hull_mask[i]]
    logger.debug(f"[MH Step1-2] PhaseC 去 hull 后 pool: {len(pool_filtered)} 项")

    # 若去 hull 后成员不足 2，回退一次：容忍 1 个包含（两洞之一+少许标注box重合情形）
    if len(pool_filtered) < 2:
        pool_filtered = pool[:]
        logger.debug(f"[MH Step1-2] PhaseC 回退：保留 hull 并改用面积相似度优先选洞")

    # ========== Phase D: 贪心选互不重叠的真实洞 ==========
    # 排序策略：优先 area_ratio 在 [0.05, 0.50] 的真实洞区间（联合 hull 一般在 ~30%
    #   但单洞在 ~10%）→ 若都在此区间，则按「面积最接近平均值」排序，让真正的多洞
    #   （等大）优先被选；再依次做 overlap 剔除。
    area_avg = sum(p[4] for p in pool_filtered) / max(1, len(pool_filtered))

    def _pick_key(p):
        ix, iy, iw, ih, area, ratio, src = p
        sweet_spot = 1.0 if 0.05 <= ratio <= 0.50 else 0.0
        sim_to_avg = -abs(area - area_avg) / max(1.0, area_avg)   # 越接近 avg 越大
        return (sweet_spot, sim_to_avg, -area)   # sweet_spot 是最高优先级

    ordered = sorted(pool_filtered, key=_pick_key, reverse=True)

    inners4 = []
    used_keys = set()
    for p in ordered:
        ix, iy, iw, ih = p[:4]
        overlap = False
        for ek in used_keys:
            ex, ey, ew, eh = ek
            x_overlap = max(0, min(ix + iw, ex + ew) - max(ix, ex))
            y_overlap = max(0, min(iy + ih, ey + eh) - max(iy, ey))
            # 允许 25px² 的浮点误差；真实洞应该完全不相交
            if x_overlap * y_overlap > 25:
                overlap = True
                break
        if overlap:
            continue
        inners4.append((ix, iy, iw, ih))
        used_keys.add((ix, iy, iw, ih))
        if len(inners4) >= 6:
            break

    if len(inners4) < 2:
        logger.info(f"[MH Step1-2] 非多洞：最终选出内框 {len(inners4)} 个（<2）→ 回退单洞")
        return None, [], ''

    # ========== Phase D.5: 单洞否决验证层 [CRITICAL FIX 2026-08-29] ==========
    # 问题：OpenCV 矩形检测有时会把**一个内框**错误分割成两部分（如边框断裂、
    # 内部有装饰线条打断），导致两个几乎贴在一起的候选矩形被 Phase D 选出，
    # 被 Phase E 分类为多洞。这是**严重的功能错误**——单洞草图被识别为多洞。
    #
    # 本阶段在 Phase E（布局分类）之前拦截这类假多洞。检查逻辑：
    #
    # VETO-1 间距不足否决:
    #   所有候选洞两两之间的 gap（在中心距较大的轴上）若 < min(洞在该轴的尺寸) × 0.10
    #   AND < 外框短边 × 0.015 → 判定为"一个洞被分割" → 否决
    #
    # VETO-2 极端面积差否决:
    #   最大洞面积 / 最小洞面积 > 5.0 → 大概率是一个真实洞 + 若干噪点
    #
    # VETO-3 严格包含否决:
    #   候选洞 A 严格包含候选洞 B → 是嵌套结构，不是并排多洞 → 否决
    #
    # VETO-4 轴对齐+同尺寸否决:
    #   两候选共享同一 x 起点或同一 y 起点，且在共享轴上尺寸差 < 15%
    #   AND 间距 < min(洞尺寸) × 0.15 → 极大概率是同洞被水平/垂直分割
    #
    # 所有 VETO 均返回 (None, [], '') → 上层自动回退单洞 7 步法。
    _outer_short_side = min(ow, oh)

    # 预计算两两几何关系（供所有 VETO 共享）
    def _pairwise_gaps(inners_list):
        """返回 list of (gap_axis_name, gap_value, axis_hole_size_min, a_idx, b_idx)。"""
        results = []
        n = len(inners_list)
        for i in range(n):
            xi, yi, wi, hi = inners_list[i]
            cx_i, cy_i = xi + wi / 2, yi + hi / 2
            for j in range(i + 1, n):
                xj, yj, wj, hj = inners_list[j]
                cx_j, cy_j = xj + wj / 2, yj + hj / 2
                dx = abs(cx_j - cx_i)
                dy = abs(cy_j - cy_i)
                # 水平距离（两洞在 x 轴方向的 gap）
                gap_x = max(0, max(xj, xi) - min(xi + wi, xj + wj))
                # 垂直距离（两洞在 y 轴方向的 gap）
                gap_y = max(0, max(yj, yi) - min(yi + hi, yj + hj))
                # 中心距较大的轴为主轴
                if dx >= dy:
                    results.append(('x', gap_x, min(wi, wj), i, j))
                else:
                    results.append(('y', gap_y, min(hi, hj), i, j))
        return results

    _pgaps = _pairwise_gaps(inners4)

    # --- VETO-1: 间距不足否决 ---
    _veto1_triggered = False
    for axis_name, gap_val, axis_hole_min, _ai, _bi in _pgaps:
        # 间距 < 该轴洞最小尺寸的 10% AND < 外框短边的 1.5%
        if gap_val < axis_hole_min * 0.10 and gap_val < _outer_short_side * 0.015:
            _veto1_triggered = True
            logger.warning(
                f"[MH Step1-2 VETO-1] 间距不足否决: "
                f"gap_{axis_name}={gap_val:.1f}px < "
                f"min_hole_{axis_name}={axis_hole_min:.1f}×10%={axis_hole_min*0.10:.1f} "
                f"AND < outer_short={_outer_short_side:.1f}×1.5%={_outer_short_side*0.015:.1f}"
            )
            break

    if _veto1_triggered:
        logger.info("[MH Step1-2 VETO-1 ⇒ 回退单洞] 候选洞间距过小，判定为单洞被分割")
        return None, [], ''

    # --- VETO-2: 极端面积差否决 ---
    _areas = [iw * ih for _ix, _iy, iw, ih in inners4]
    _max_area = max(_areas)
    _min_area = min(_areas)
    if _min_area > 0 and _max_area / _min_area > 5.0:
        logger.warning(
            f"[MH Step1-2 VETO-2] 极端面积差否决: "
            f"max_area={_max_area} / min_area={_min_area} = "
            f"{_max_area / _min_area:.1f}x > 5.0x → 大概率单洞+噪点"
        )
        logger.info("[MH Step1-2 VETO-2 ⇒ 回退单洞] 候选洞面积差异过大")
        return None, [], ''

    # --- VETO-3: 严格包含否决 ---
    def _strictly_contains_veto(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        edges = (ax <= bx, ay <= by, ax + aw >= bx + bw, ay + ah >= by + bh)
        if not all(edges):
            return False
        if ax == bx and ay == by and aw == bw and ah == bh:
            return False
        return True

    _veto3_triggered = False
    for _i in range(len(inners4)):
        for _j in range(len(inners4)):
            if _i == _j:
                continue
            if _strictly_contains_veto(inners4[_i], inners4[_j]):
                _veto3_triggered = True
                logger.warning(
                    f"[MH Step1-2 VETO-3] 严格包含否决: "
                    f"inners4[{_i}]={inners4[_i]} 包含 inner[{_j}]={inners4[_j]} → 嵌套非并排"
                )
                break
        if _veto3_triggered:
            break

    if _veto3_triggered:
        logger.info("[MH Step1-2 VETO-3 ⇒ 回退单洞] 候选洞存在严格包含关系")
        return None, [], ''

    # --- VETO-4: 轴对齐+同尺寸+小间距否决 ---
    _veto4_triggered = False
    for axis_name, gap_val, axis_hole_min, ai, bi in _pgaps:
        ra = inners4[ai]
        rb = inners4[bi]
        ax, ay, aw, ah = ra
        bx, by, bw, bh = rb
        # 横排假洞检查：共享同一 y 起点 OR 共享同一 y 中心（水平对齐）
        # 且 width 差 < 15%，且间距 < min(w)×0.15
        if axis_name == 'x':
            # y 轴对齐检查（共享 y 起点或 y 中心接近）
            y_aligned = (abs(ay - by) < max(ah, bh) * 0.10) or \
                        (abs((ay + ah / 2) - (by + bh / 2)) < max(ah, bh) * 0.10)
            w_similar = abs(aw - bw) / max(aw, bw, 1) < 0.15
            if y_aligned and w_similar and gap_val < min(aw, bw) * 0.15:
                _veto4_triggered = True
                logger.warning(
                    f"[MH Step1-2 VETO-4] 水平轴对齐+同尺寸否决: "
                    f"y_aligned={y_aligned} w_diff={abs(aw-bw)/max(aw,bw,1)*100:.0f}% "
                    f"gap={gap_val:.1f}px < {min(aw,bw)*0.15:.1f}px"
                )
                break
        else:  # axis_name == 'y'
            x_aligned = (abs(ax - bx) < max(aw, bw) * 0.10) or \
                        (abs((ax + aw / 2) - (bx + bw / 2)) < max(aw, bw) * 0.10)
            h_similar = abs(ah - bh) / max(ah, bh, 1) < 0.15
            if x_aligned and h_similar and gap_val < min(ah, bh) * 0.15:
                _veto4_triggered = True
                logger.warning(
                    f"[MH Step1-2 VETO-4] 垂直轴对齐+同尺寸否决: "
                    f"x_aligned={x_aligned} h_diff={abs(ah-bh)/max(ah,bh,1)*100:.0f}% "
                    f"gap={gap_val:.1f}px < {min(ah,bh)*0.15:.1f}px"
                )
                break

    if _veto4_triggered:
        logger.info("[MH Step1-2 VETO-4 ⇒ 回退单洞] 候选洞轴对齐+同尺寸+小间距")
        return None, [], ''

    logger.info(f"[MH Step1-2 PhaseD.5 通过] 4 项 VETO 均未触发 → 确认真多洞")

    # ========== Phase E: 布局分类 + 排序 ==========
    cxs = [ix + iw / 2 for ix, iy, iw, ih in inners4]
    cys = [iy + ih / 2 for ix, iy, iw, ih in inners4]
    cx_range = max(cxs) - min(cxs) if len(cxs) >= 2 else 0
    cy_range = max(cys) - min(cys) if len(cys) >= 2 else 0

    if cx_range > cy_range * 1.5:
        layout = 'horizontal'
        inners4.sort(key=lambda r: r[0] + r[2] / 2)
    elif cy_range > cx_range * 1.5:
        layout = 'vertical'
        inners4.sort(key=lambda r: r[1] + r[3] / 2)
    else:
        layout = 'mixed'
        inners4.sort(key=lambda r: r[0] + r[2] / 2)

    logger.info(f"[MH Step1-2] 多洞布局: {layout} 外框=({ox},{oy},{ow},{oh}) 内框数={len(inners4)}")
    for i, inner in enumerate(inners4):
        logger.info(f"    Hole {i}: rect={inner} area={inner[2]*inner[3]}")

    return (ox, oy, ow, oh), inners4, layout


# ======================================================================
# Step 3: 多洞语义区域划分
# ======================================================================


def _divide_multi_hole_zones(outer, inners, layout, img_w, img_h):
    """基于 1 外框 + N 内框，构建 zone_of(cx, cy) → field_name 判定函数。

    区域定义（以横排 horizontal 为例）：

      ┌──────────────────── outer ────────────────────┐
      │  margin_top (共享，覆盖所有洞上方)             │
      │ ┌──────┐    gap_0_1    ┌──────┐  margin_right │
      │ │Hole 0│◁────────────▷│Hole 1│               │
      │ └──────┘               └──────┘               │
      │  margin_bottom (共享，覆盖所有洞下方)          │
      └───────────────────────────────────────────────┘
        ↑
     margin_left (最左洞左侧外边距)

    field_name 命名约定：
      共享:  margin_top / margin_bottom / outer_w / outer_h
      洞 0:  inner_w_0 / inner_h_0 / margin_left_0 (=最左洞外边距)
      洞 i:  inner_w_i / inner_h_i
      洞 N-1: inner_w_{N-1} / inner_h_{N-1} / margin_right_{N-1} (=最右洞外边距)
      gap:   gap_0_1, gap_1_2, ...  (两洞之间的间距)

    Args:
        outer: (ox, oy, ow, oh) 外框像素矩形
        inners: list of (ix, iy, iw, ih) 已排序的内框列表
        layout: 'horizontal' | 'vertical' | 'mixed'
        img_w, img_h: 原图尺寸（用于外框外部标注区）

    Returns:
        zone_of(cx, cy) → field_name 或 None 的闭包函数
    """
    ox, oy, ow, oh = outer
    n = len(inners)

    # 共享上/下的 y 范围：外框顶 → 第一个洞的顶；最后一个洞的底 → 外框底
    min_top_iy = min(r[1] for r in inners)
    max_bot_iy = max(r[1] + r[3] for r in inners)
    min_left_ix = min(r[0] for r in inners)
    max_right_ix = max(r[0] + r[2] for r in inners)

    # 洞列表索引便捷函数
    def hole_idx_at_point(cx, cy):
        """返回包含该点的洞索引，否则 -1。"""
        for idx, (hx, hy, hw, hh) in enumerate(inners):
            if hx <= cx <= hx + hw and hy <= cy <= hy + hh:
                return idx
        return -1

    def gap_idx_between_holes(cx, cy):
        """若点在相邻两洞之间的 gap 区，返回 gap 索引；否则 -1。"""
        if layout == 'horizontal':
            for idx in range(n - 1):
                h0_right = inners[idx][0] + inners[idx][2]
                h1_left = inners[idx + 1][0]
                # gap 的 y 范围 = 两个洞 y 范围的交集（即最小的共同 y 段）
                gap_top = max(inners[idx][1], inners[idx + 1][1])
                gap_bot = min(inners[idx][1] + inners[idx][3],
                              inners[idx + 1][1] + inners[idx + 1][3])
                if h0_right <= cx <= h1_left and gap_top <= cy <= gap_bot:
                    return idx
            return -1
        elif layout == 'vertical':
            for idx in range(n - 1):
                h0_bot = inners[idx][1] + inners[idx][3]
                h1_top = inners[idx + 1][1]
                gap_left = max(inners[idx][0], inners[idx + 1][0])
                gap_right = min(inners[idx][0] + inners[idx][2],
                                inners[idx + 1][0] + inners[idx + 1][2])
                if h0_bot <= cy <= h1_top and gap_left <= cx <= gap_right:
                    return idx
            return -1
        else:  # mixed
            for idx in range(n - 1):
                # 简化版 mixed：矩形包围盒间隙
                h0 = inners[idx]
                h1 = inners[idx + 1]
                mid_xl = min(h0[0] + h0[2], h1[0] + h1[2])
                mid_xr = max(h0[0], h1[0])
                mid_yt = min(h0[1] + h0[3], h1[1] + h1[3])
                mid_yb = max(h0[1], h1[1])
                if mid_xl <= cx <= mid_xr and mid_yt <= cy <= mid_yb:
                    return idx
            return -1

    def zone_of(cx, cy):
        # --- 外框外部的 outer_w / outer_h 标注区 ---
        if cy > oy + oh and ox <= cx <= ox + ow:
            return 'outer_w'
        if cx < ox and oy <= cy <= oy + oh:
            return 'outer_h'

        # --- 外框内部 ---
        if not (ox <= cx <= ox + ow and oy <= cy <= oy + oh):
            return None

        # 1) 点在某个洞内部 → 取该洞的 inner_w / inner_h
        hidx = hole_idx_at_point(cx, cy)
        if hidx >= 0:
            hx, hy, hw, hh = inners[hidx]
            icx = hx + hw / 2
            icy = hy + hh / 2
            if layout == 'horizontal':
                # 横排：洞的上半部 → inner_h；下半部 → inner_w
                # （与单洞 zone 划分逻辑一致）
                if cy > icy:
                    return f'inner_w_{hidx}'
                elif cx < icx:
                    return f'inner_h_{hidx}'
                else:
                    return f'inner_w_{hidx}'
            elif layout == 'vertical':
                # 竖排：洞的左半部 → inner_w；右半部 → inner_h
                if cx < icx:
                    return f'inner_w_{hidx}'
                elif cy < icy:
                    return f'inner_h_{hidx}'
                else:
                    return f'inner_w_{hidx}'
            else:
                # mixed：按 y-x 类似横排规则
                if cy > icy:
                    return f'inner_w_{hidx}'
                else:
                    return f'inner_h_{hidx}'

        # 2) 点在相邻洞之间的 gap 区
        gidx = gap_idx_between_holes(cx, cy)
        if gidx >= 0:
            return f'gap_{gidx}_{gidx + 1}'

        # ===== [MULTI-HOLE PER-HOLE Add-On 2026-08-29] per-hole mt_i/mb_i zone 归属 =====
        # 当异尺寸异边距的多洞草图上，每个洞的上下边距是独立标注的
        # （如 Case A 洞1 mt=20.5 / 洞2 mt=21.7）。共享 margin_top 桶只取一个 top 值，
        # 会把两个标注混在一起丢信息。
        # 解决方案：在共享 margin_top/bottom 桶逻辑（下面 3/4/5 段）之前，
        # 先尝试把该点归属到「某洞正上方 / 正下方」的专属区 → 返回 per-hole 桶名。
        # 早 return；没命中再 fall through 到原共享逻辑 → 同尺寸同边距的旧场景零影响。
        # 保留的原则：(1) per-hole 桶名与全局桶名不同 → 不会互相污染；
        #             (2) fallback 策略：所有 per-hole 桶全空 → 自然回退全局桶。
        if layout in ('horizontal', 'mixed'):
            for i, (hx, hy, hw, hh) in enumerate(inners):
                # 正上方：点在该洞的 x 范围内 AND cy 在 0..洞顶之间
                if hx <= cx <= hx + hw and oy <= cy < hy:
                    return f'margin_top_{i}'
                # 正下方：点在该洞的 x 范围内 AND cy 在 洞底..外框底之间
                if hx <= cx <= hx + hw and hy + hh < cy <= oy + oh:
                    return f'margin_bottom_{i}'
        if layout == 'vertical':
            for i, (hx, hy, hw, hh) in enumerate(inners):
                # 竖排：每洞独立 left / right 归属（同理横向场景的 mt/mb）
                if ox <= cx < hx and hy <= cy <= hy + hh:
                    return f'margin_left_{i}'
                if hx + hw < cx <= ox + ow and hy <= cy <= hy + hh:
                    return f'margin_right_{i}'

        # 3) 共享的 top / bottom 区域（横排和 mixed）
        if layout in ('horizontal', 'mixed'):
            if oy <= cy < min_top_iy and min_left_ix <= cx <= max_right_ix:
                return 'margin_top'
            if max_bot_iy < cy <= oy + oh and min_left_ix <= cx <= max_right_ix:
                return 'margin_bottom'
        # 竖排的共享 left / right
        if layout == 'vertical':
            if ox <= cx < min_left_ix and min_top_iy <= cy <= max_bot_iy:
                return 'margin_left'
            if max_right_ix < cx <= ox + ow and min_top_iy <= cy <= max_bot_iy:
                return 'margin_right'

        # 4) 某个洞的专属外边距区（左侧 margin_left_0 / 右侧 margin_right_{n-1}）
        #    横排时：margin_left_0 = 外框左到 0 号洞左
        #           margin_right_{n-1} = n-1 号洞右 到 外框右
        #    纵排时：margin_top_0 = 外框顶 到 0 号洞顶（等价 margin_top 共享）
        #           margin_bottom_{n-1} = n-1 号洞底 到 外框底（等价 margin_bottom）
        if layout in ('horizontal', 'mixed'):
            # 最左洞的左侧外边距
            h0 = inners[0]
            if ox <= cx <= h0[0] and h0[1] <= cy <= h0[1] + h0[3]:
                return 'margin_left_0'
            # 最右洞的右侧外边距
            hN = inners[-1]
            if hN[0] + hN[2] <= cx <= ox + ow and hN[1] <= cy <= hN[1] + hN[3]:
                return f'margin_right_{n - 1}'
        if layout == 'vertical':
            h0 = inners[0]
            if ox <= cx <= ox + ow and oy <= cy <= h0[1]:
                # 顶上外距（共享 margin_top）
                return 'margin_top'
            hN = inners[-1]
            if ox <= cx <= ox + ow and hN[1] + hN[3] <= cy <= oy + oh:
                return 'margin_bottom'

        # 5) 角落区域（未明确分配）：按最近的洞外边距归属
        #    计算到每个洞 4 条边的距离，取最近的字段
        if layout in ('horizontal', 'mixed'):
            # 角落：按最近的边距
            d_top = abs(cy - min_top_iy)
            d_bot = abs(cy - max_bot_iy)
            d_left_h0 = abs(cx - inners[0][0])
            d_right_hN = abs(cx - (inners[-1][0] + inners[-1][2]))
            min_d = min(d_top, d_bot, d_left_h0, d_right_hN)
            if min_d == d_top:
                return 'margin_top'
            elif min_d == d_bot:
                return 'margin_bottom'
            elif min_d == d_left_h0:
                return 'margin_left_0'
            else:
                return f'margin_right_{n - 1}'
        else:  # vertical
            d_left = abs(cx - min_left_ix)
            d_right = abs(cx - max_right_ix)
            d_top_h0 = abs(cy - inners[0][1])
            d_bot_hN = abs(cy - (inners[-1][1] + inners[-1][3]))
            min_d = min(d_left, d_right, d_top_h0, d_bot_hN)
            if min_d == d_left:
                return 'margin_left'
            elif min_d == d_right:
                return 'margin_right'
            elif min_d == d_top_h0:
                return 'margin_top'
            else:
                return 'margin_bottom'

    return zone_of


# ======================================================================
# Step 5 (part): 方向汉字 + 箭头符号双重识别
# ======================================================================


def _parse_arrow_or_dir_token(text):
    """Phase MH-5 扩展版：方向+数值 或 箭头+数值 的双向匹配。

    支持的组合：
      方向在前：  上6 / 左:22 / →46 / ← 21.5
      数值在前：  8下 / 16.8右 / 11.5↑ / 35.5 →
      箭头夹数值：←21.5→   (箭头在数字两侧，表示"这个数字指的是距离")
      数字在箭头尖：192.5→  (数字在箭头尾端，表示"距离为X指向箭头方向")

    Returns:
        (field_name_or_None, value_or_None)
    """
    if not text:
        return None, None

    # 规范化：去除多余空白
    t = text.strip()
    if not t:
        return None, None

    # ===== 规则 1：单 token 内的 "方向字/箭头 + 数值" 正向匹配 =====
    # 方向字在前
    m1 = re.search(r'([上下左右←→↑↓←↑↖↗↙↘↺↻<>«»^∧∨])\s*[:：=\-]*\s*(\d+\.?\d*|\.\d+)', t)
    if m1:
        try:
            char = m1.group(1)
            val = float(m1.group(2))
            field = _UNIFIED_DIR_MAP.get(char)
            if field:
                return field, val
        except (ValueError, IndexError):
            pass

    # ===== 规则 2：单 token 内的 "数值 + 方向字/箭头" 反向匹配 =====
    m2 = re.search(r'(\d+\.?\d*|\.\d+)\s*[:：=\-]*\s*([上下左右←→↑↓←↑↖↗↙↘↺↻<>«»^∧∨])', t)
    if m2:
        try:
            val = float(m2.group(1))
            char = m2.group(2)
            field = _UNIFIED_DIR_MAP.get(char)
            if field:
                return field, val
        except (ValueError, IndexError):
            pass

    # ===== 规则 3：仅方向字/箭头（独立token），用于 Phase3 的双token关联 =====
    # 此场景返回 (field, None) 表示仅识别到方向符，需要邻近数值配合
    if len(t) == 1 and t in _UNIFIED_DIR_MAP:
        return _UNIFIED_DIR_MAP[t], None

    return None, None


def _extract_arrow_direction_numbers(cv2, tesseract, gray_img,
                                     enhanced_gray=None,
                                     target_outer_w_cm=0.0,
                                     target_outer_h_cm=0.0):
    """方向+箭头增强版的数值字段锁定（返回 dict: field → (val, conf, bbox)）。

    复用单洞 _extract_direction_label_numbers 的整体框架，但：
      1. 方向字符表扩展包含箭头
      2. 允许更多字符组合
      3. 合理性 cap 按 target 轴向
    """
    from PIL import Image as PILImage
    result = {}

    if tesseract is None:
        return result

    # ===== 合理性 cap（与单洞 Step4 一致）=====
    _target_authoritative = (target_outer_w_cm > 0 and target_outer_h_cm > 0)
    if _target_authoritative:
        _cap_h = target_outer_w_cm * 0.90
        _cap_v = target_outer_h_cm * 0.90
        _margin_cap = max(_cap_h, _cap_v)
    else:
        _ref = max(target_outer_w_cm, target_outer_h_cm, 0.0)
        _margin_cap = min(_ref * 0.30, 30.0) if _ref > 0 else 30.0

    def _is_reasonable_for_field(v, fld_hint):
        if v is None or v <= 0 or v < 0.3:
            return False
        if fld_hint in ('margin_left', 'margin_right'):
            return v <= (_cap_h if _target_authoritative else _margin_cap)
        if fld_hint in ('margin_top', 'margin_bottom'):
            return v <= (_cap_v if _target_authoritative else _margin_cap)
        return v <= _margin_cap if _margin_cap > 0 else v <= 500

    # ===== 扫描列表 =====
    scan_list = [(gray_img, 1.0, 'gray')]
    try:
        scan_list.append((cv2.resize(gray_img, None, fx=2.5, fy=2.5,
                                     interpolation=cv2.INTER_CUBIC), 2.5, 'gray'))
        scan_list.append((cv2.resize(gray_img, None, fx=4.0, fy=4.0,
                                     interpolation=cv2.INTER_CUBIC), 4.0, 'gray'))
    except Exception:
        pass
    if enhanced_gray is not None:
        try:
            scan_list.append((enhanced_gray, 1.0, 'enh'))
            scan_list.append((cv2.resize(enhanced_gray, None, fx=2.5, fy=2.5,
                                         interpolation=cv2.INTER_CUBIC), 2.5, 'enh'))
        except Exception:
            pass

    def _tokens_adjacent_line(l1, t1, w1, h1, l2, t2, w2, h2):
        y_overlap = max(0, min(t1 + h1, t2 + h2) - max(t1, t2))
        min_h = max(1, min(h1, h2))
        if y_overlap < 0.45 * min_h:
            return False
        box1_right = l1 + w1
        box2_right = l2 + w2
        if box1_right <= l2:
            gap_x = l2 - box1_right
        elif box2_right <= l1:
            gap_x = l1 - box2_right
        else:
            gap_x = 0
        if gap_x > 3 * max(1, min(w1, w2)):
            return False
        if gap_x > 350:
            return False
        return True

    def _try_bind_value(field, val, conf, bx, by, bw, bh, tag):
        if val is None or not (0.3 <= val <= 500):
            return
        # 外框值排除
        if _target_authoritative:
            for xv in (target_outer_w_cm, target_outer_h_cm,
                       target_outer_w_cm - 0.5, target_outer_w_cm + 0.5,
                       target_outer_h_cm - 0.5, target_outer_h_cm + 0.5):
                if xv > 0 and abs(val - xv) < 0.15:
                    logger.info(f"[MH Step5] 外框值拒绝: {field}={val}")
                    return
        if not _is_reasonable_for_field(val, field):
            # 尝试小数点恢复
            if abs(val - round(val)) <= 0.01 and 10 <= val <= 99:
                s = str(int(val))
                if len(s) == 2 and s[1] != '0':
                    dv = float(f"{s[0]}.{s[1]}")
                    if _is_reasonable_for_field(dv, field):
                        val = dv
                        conf *= 0.85
                        logger.info(f"[MH Step5] 小数恢复: {int(round(val*100)/100)}原→{dv}")
                    else:
                        return
                else:
                    return
            else:
                return
        # 同值去重 + 覆盖保护
        if field in result:
            old_val, old_conf, _ = result[field]
            if abs(old_val - val) < 0.01:
                return  # 同值，保留先到的
            if conf <= old_conf + 5:
                return  # 置信度没有显著提升，保留先到的
        result[field] = (val, conf, (bx, by, bw, bh))
        logger.info(f"[MH Step5] {tag}: {field}={val} conf={conf}")

    lang_options = ['chi_sim+eng', 'eng']
    psm_list = [6, 4, 11, 12]

    for img, scale, src_tag in scan_list:
        try:
            pil = PILImage.fromarray(img)
        except Exception:
            continue
        for lang in lang_options:
            for psm in psm_list:
                try:
                    data = tesseract.image_to_data(
                        pil, lang=lang,
                        config=f'--oem 3 --psm {psm}',
                        output_type=tesseract.Output.DICT,
                        timeout=_PARSE_TIMEOUT_SEC)
                except Exception:
                    continue
                if not data or 'text' not in data:
                    continue
                texts = data.get('text', [])
                n = len(texts)
                confs = data.get('conf', ['0'] * n)
                lefts = data.get('left', [0] * n)
                tops = data.get('top', [0] * n)
                widths = data.get('width', [0] * n)
                heights = data.get('height', [0] * n)
                for i in range(n):
                    raw = _normalize_ocr_text(str(texts[i]))
                    if not raw:
                        continue
                    try:
                        ci = max(0, int(str(confs[i])))
                    except Exception:
                        ci = 0
                    if ci < 8:
                        continue

                    bx = int(lefts[i]) / scale
                    by = int(tops[i]) / scale
                    bw = int(widths[i]) / scale
                    bh = int(heights[i]) / scale

                    # ---- A: 单 token 双向匹配（方向字/箭头 + 数值）----
                    field, val = _parse_arrow_or_dir_token(raw)
                    if field is not None and val is not None:
                        _try_bind_value(field, val, ci, bx, by, bw, bh,
                                        f"A单token({src_tag} {lang} psm{psm})")
                        continue
                    # ---- B: 双 token 方向字/箭头在前 + 数值在后 ----
                    if field is not None and val is None:
                        # 仅方向/箭头字符，尝试结合下一个数值 token
                        if i + 1 < n:
                            ntxt = _normalize_ocr_text(str(texts[i + 1]))
                            nm = re.match(r'^(\d+\.?\d*|\.\d+)', ntxt)
                            if nm:
                                try:
                                    vv = float(nm.group(1))
                                    li, ti, wi, hi = int(lefts[i]), int(tops[i]), int(widths[i]), int(heights[i])
                                    lj, tj, wj, hj = int(lefts[i+1]), int(tops[i+1]), int(widths[i+1]), int(heights[i+1])
                                    if _tokens_adjacent_line(li, ti, wi, hi, lj, tj, wj, hj):
                                        nbw = (lj + wj - li) / scale
                                        nbh = max(hi, hj) / scale
                                        _try_bind_value(field, vv, ci, bx, by, nbw, nbh,
                                                        f"B双token(符→值 {src_tag} {lang} psm{psm})")
                                        continue
                                except ValueError:
                                    pass
                    # ---- C: 双 token 数值在前 + 方向/箭头在后 ----
                    m_num = re.match(r'^(\d+\.?\d*|\.\d+)$', raw)
                    if m_num and i + 1 < n:
                        nxt = _normalize_ocr_text(str(texts[i + 1]))
                        nxt_field, _ = _parse_arrow_or_dir_token(nxt)
                        if nxt_field is not None:
                            try:
                                vv = float(m_num.group(1))
                                li, ti, wi, hi = int(lefts[i]), int(tops[i]), int(widths[i]), int(heights[i])
                                lj, tj, wj, hj = int(lefts[i+1]), int(tops[i+1]), int(widths[i+1]), int(heights[i+1])
                                if _tokens_adjacent_line(li, ti, wi, hi, lj, tj, wj, hj):
                                    nbw = (lj + wj - li) / scale
                                    nbh = max(hi, hj) / scale
                                    _try_bind_value(nxt_field, vv, ci, li/scale, ti/scale, nbw, nbh,
                                                    f"C双token(值→符 {src_tag} {lang} psm{psm})")
                                    continue
                            except ValueError:
                                pass

    # ===== 小数字补漏（6x + PSM 10/7）=====
    missing_count = 4 - len(result)
    if missing_count > 0 and enhanced_gray is not None and cv2 is not None:
        logger.info(f"[MH Step5] 小数字补漏触发: 缺失{missing_count}")
        try:
            s6 = cv2.resize(enhanced_gray, None, fx=6.0, fy=6.0,
                            interpolation=cv2.INTER_CUBIC)
            pil_s6 = PILImage.fromarray(s6)
        except Exception:
            pil_s6 = None
        if pil_s6 is not None:
            for psm_s in [10, 7]:
                try:
                    d6 = tesseract.image_to_data(
                        pil_s6, lang='chi_sim+eng',
                        config=f'--oem 3 --psm {psm_s}',
                        output_type=tesseract.Output.DICT,
                        timeout=_PARSE_TIMEOUT_SEC)
                except Exception:
                    continue
                if not d6 or 'text' not in d6:
                    continue
                t6s = d6.get('text', [])
                ns = len(t6s)
                c6s = d6.get('conf', ['0'] * ns)
                l6s = d6.get('left', [0] * ns)
                tp6s = d6.get('top', [0] * ns)
                w6s = d6.get('width', [0] * ns)
                h6s = d6.get('height', [0] * ns)
                for i in range(ns):
                    raw_s = _normalize_ocr_text(str(t6s[i]))
                    if not raw_s:
                        continue
                    try:
                        ci_s = max(0, int(str(c6s[i])))
                    except Exception:
                        ci_s = 0
                    if ci_s < 8:
                        continue
                    fld_s, val_s = _parse_arrow_or_dir_token(raw_s)
                    if fld_s is not None and val_s is not None:
                        bx6 = int(l6s[i]) / 6.0
                        by6 = int(tp6s[i]) / 6.0
                        bw6 = int(w6s[i]) / 6.0
                        bh6 = int(h6s[i]) / 6.0
                        _try_bind_value(fld_s, val_s, ci_s, bx6, by6, bw6, bh6,
                                        f"小数字单token(6x psm{psm_s})")

    logger.info(f"[MH Step5] 方向/箭头锁定 {len(result)} 个字段: {list(result.keys())}")
    return result


# ======================================================================
# Step 6: 多洞数值空间归属
# ======================================================================


def _multi_hole_spatial_bind(ocr_results, zone_func, excluded_fields, excluded_values,
                              n_holes, layout):
    """将 OCR 数值按 (cx, cy) 坐标归入多洞语义区域。

    新增两步反拆读策略（对付 Tesseract 将「11.5」同时读成整段「11.5」+ 尾位「5」的双读）：
      Post A. 桶内 bbox 几何包含去重：若候选 A 的 bbox 完全包含候选 B 的 bbox
              (x/y 100% 重叠) 且数值不同 → 丢弃被包含者 B（必然是拆读残片）。
      Post B. 桶内按加权众数排序，不再按单条 conf 排序（避免 5.0@96 胜过
              11.5@81+11.5@86=167）。

    Args:
        ocr_results: [(val, conf, bbox)]
        zone_func: _divide_multi_hole_zones 返回的 zone_of 函数
        excluded_fields: set[str]，已被方向/箭头锁定的字段
        excluded_values: list[float]，已被使用的数值
        n_holes: 洞的数量
        layout: 布局类型

    Returns:
        dict: {field_name: list of (val, conf, bbox)}  — 按加权众数排序
    """
    buckets = {}
    used_v_set = set()
    for v in (excluded_values or []):
        used_v_set.add(round(v, 1))

    for val, conf, bbox in ocr_results:
        bx, by, bw, bh = bbox
        cx, cy = bx + bw / 2, by + bh / 2
        field = zone_func(cx, cy)
        if field is None:
            continue
        if field in excluded_fields:
            continue
        vr = round(val, 1)
        if any(abs(vr - uv) <= 0.1 for uv in used_v_set):
            continue
        buckets.setdefault(field, []).append((val, conf, bbox))

    # ===== Post A. 桶内几何包含去重（拆读剔除） =====
    # 规则：若 bbox A 在 x、y 两个轴上都完全/100% 包含 bbox B，且两者数值不同
    #       → B 是 A 的拆读（如「11.5」整段 vs 「5」尾位），丢弃 B。
    for f, cands in buckets.items():
        if len(cands) < 2:
            continue
        drop_idx = set()
        n_c = len(cands)
        for i in range(n_c):
            if i in drop_idx:
                continue
            _, _, (axi, ayi, awi, ahi) = cands[i]
            ax1, ax2 = axi, axi + awi
            ay1, ay2 = ayi, ayi + ahi
            for j in range(n_c):
                if j == i or j in drop_idx:
                    continue
                vj, cj, (bxj, byj, bwj, bhj) = cands[j]
                bx1, bx2 = bxj, bxj + bwj
                by1, by2 = byj, byj + bhj
                # j 是否被 i 完全包含？（允许 ±1px 浮点误差）
                x_contain = (bx1 >= ax1 - 1) and (bx2 <= ax2 + 1)
                y_contain = (by1 >= ay1 - 1) and (by2 <= ay2 + 1)
                if x_contain and y_contain:
                    # 但值相同的不丢（两个独立标签，如 margin_bottom: 12×2）
                    if abs(cands[i][0] - vj) > 0.01:
                        drop_idx.add(j)
        if drop_idx:
            buckets[f] = [cands[k] for k in range(n_c) if k not in drop_idx]
            logger.debug(f"[MH Step6-PostA] {f}: 剔除拆读残片 {len(drop_idx)} 项")

    # ===== Post B. 加权众数排序（sum-of-confidence-vote） =====
    # 按 round(v,1) 分组累加 conf，按累加 conf 降序；同组内部再按 conf 降序。
    # 好处：11.5@81 + 11.5@86 (=sum 167) 排在 5.0@96 前面。
    for f, cands in buckets.items():
        if len(cands) < 2:
            continue
        # 1) 分组：key=round(v,1), value=sum_conf
        grp_sum = {}
        for (v, c, bb) in cands:
            k = round(v, 1)
            grp_sum[k] = grp_sum.get(k, 0.0) + c
        # 2) 排序：主=grp_sum[v] desc, 副=c desc
        cands_sorted = sorted(
            cands,
            key=lambda r: (-grp_sum.get(round(r[0], 1), 0.0), -r[1])
        )
        buckets[f] = cands_sorted

    logger.info(f"[MH Step6] 空间归属桶: {len(buckets)} 个字段")
    for f, cands in buckets.items():
        values_rounded = [round(v, 1) for v, _, _ in cands[:3]]
        # 显示 grp_sum 信息用于调参（仅取 top 值的和）
        if cands:
            topv = round(cands[0][0], 1)
            sumconf = sum(c for (v, c, _) in cands if round(v, 1) == topv)
            ext = f"(众数 top={topv} sum_conf={sumconf:.0f})"
        else:
            ext = ""
        logger.info(f"    {f}: {values_rounded}...(共{len(cands)}) {ext}")
    return buckets


# ======================================================================
# Step 7 + 8: 外框选优 + 多洞几何自洽校验
# ======================================================================


def _round_pref_bonus_multi(v):
    """外框圆整加分（与单洞逻辑一致）。"""
    if abs(v - round(v)) > 0.01:
        return 0.0
    iv = int(v)
    if iv % 100 == 0:
        return 0.05
    if iv % 50 == 0:
        return 0.04
    if iv % 10 == 0:
        return 0.03
    if iv % 5 == 0:
        return 0.02
    return 0.0


def _score_multi_hole_consistency(tw, th, holes, gaps, layout,
                                   shared_mt, shared_mb, shared_ml, shared_mr):
    """多洞赋值自洽评分（0~1）。

    评分维度：
      1. 横向/纵向几何守恒（权重各 0.3）
      2. 洞尺寸一致性（横排洞同高/竖排洞同宽，权重 0.15）
      3. 完整性（各字段非零比例，权重 0.15）
      4. 比例匹配（像素比例 vs cm 比例，权重 0.1）
    """
    if tw <= 0 or th <= 0:
        return 0.0

    sc = 0.0

    # --- 横向守恒 ---
    if layout in ('horizontal', 'mixed'):
        h_sum = shared_ml + shared_mr
        for h in holes:
            h_sum += h.get('w', 0.0)
        for g in gaps:
            h_sum += g
        if h_sum > 0:
            ratio = min(tw, h_sum) / max(tw, h_sum, 0.01)
            sc += 0.3 * ratio
    else:  # vertical
        h_sum = shared_ml + shared_mr
        for h in holes:
            h_sum += h.get('w', 0.0)
        if len(holes) > 0 and h_sum > 0:
            ratio = min(tw, h_sum) / max(tw, h_sum, 0.01)
            sc += 0.3 * ratio

    # --- 纵向守恒 ---
    if layout == 'vertical':
        v_sum = shared_mt + shared_mb
        for h in holes:
            v_sum += h.get('h', 0.0)
        for g in gaps:
            v_sum += g
        if v_sum > 0:
            ratio = min(th, v_sum) / max(th, v_sum, 0.01)
            sc += 0.3 * ratio
    else:  # horizontal / mixed
        v_sum = shared_mt + shared_mb
        max_hole_h = 0.0
        for h in holes:
            hh = h.get('h', 0.0)
            if hh > max_hole_h:
                max_hole_h = hh
        v_sum += max_hole_h
        if v_sum > 0:
            ratio = min(th, v_sum) / max(th, v_sum, 0.01)
            sc += 0.3 * ratio

    # --- 洞尺寸一致性（横排洞等高/竖排洞同宽）---
    if len(holes) >= 2:
        if layout in ('horizontal', 'mixed'):
            hs = [h.get('h', 0.0) for h in holes if h.get('h', 0.0) > 0]
            if len(hs) >= 2:
                avg_h = sum(hs) / len(hs)
                diffs = [abs(h - avg_h) / max(avg_h, 0.01) for h in hs]
                avg_diff = sum(diffs) / len(diffs)
                sc += 0.15 * max(0.0, 1.0 - avg_diff * 2)
        else:
            ws = [h.get('w', 0.0) for h in holes if h.get('w', 0.0) > 0]
            if len(ws) >= 2:
                avg_w = sum(ws) / len(ws)
                diffs = [abs(w - avg_w) / max(avg_w, 0.01) for w in ws]
                avg_diff = sum(diffs) / len(diffs)
                sc += 0.15 * max(0.0, 1.0 - avg_diff * 2)
    else:
        sc += 0.15  # 只有单洞没有一致性问题

    # --- 完整性 ---
    valid = 0
    total = 4  # mt, mb, ml, mr
    if shared_mt > 0:
        valid += 1
    if shared_mb > 0:
        valid += 1
    if shared_ml > 0:
        valid += 1
    if shared_mr > 0:
        valid += 1
    for h in holes:
        total += 2
        if h.get('w', 0.0) > 0:
            valid += 1
        if h.get('h', 0.0) > 0:
            valid += 1
    for g in gaps:
        total += 1
        if g > 0:
            valid += 1
    sc += 0.15 * (valid / max(total, 1))

    return min(1.0, max(0.0, sc))


def _build_multi_hole_assignment(dir_locked, buckets, outer_w_cand, outer_h_cand,
                                  n_holes, layout):
    """从方向锁定 + 空间桶 + 外框候选值 构建多洞赋值 dict。

    返回的赋值 dict 使用以下键：
      total_w, total_h,
      margin_top, margin_bottom, margin_left, margin_right,
      inner_w_0, inner_h_0, ... inner_w_{n-1}, inner_h_{n-1},
      gap_0_1, gap_1_2, ...
    """
    assignment = {}

    def _top_from_bucket(key, default_val, default_conf=0.5):
        if key in buckets and buckets[key]:
            v, c, bb = buckets[key][0]
            return v, max(c / 100.0, default_conf), bb
        return default_val, default_conf, None

    # --- 外框 ---
    assignment['total_w'] = (outer_w_cand, 0.7)
    assignment['total_h'] = (outer_h_cand, 0.7)

    # --- 共享边距：方向锁定优先 ---
    # [Bug fix] alt_key 必须指向 per-hole 桶（margin_top_0 等），不能和 key 相同！
    # 桶结构是 margin_top_0/margin_top_1... 没有全局 margin_top 桶。
    # 之前 alt_key=key 导致 fallback 永远不触发，全局 mt/mb 恒为 0。
    shared_fields = {
        'margin_top': ('margin_top_0', 0.0),      # fallback 到第一个洞的 mt
        'margin_bottom': (f'margin_bottom_{n_holes - 1}', 0.0),  # fallback 到最后一个洞的 mb
        'margin_left': ('margin_left_0', 0.0),
        'margin_right': (f'margin_right_{n_holes - 1}', 0.0),
    }
    for key, (alt_key, _) in shared_fields.items():
        if key in dir_locked:
            assignment[key] = dir_locked[key][:2]
        else:
            # 尝试两个桶键
            vv, cc, _ = _top_from_bucket(key, 0.0)
            if vv <= 0.0 and alt_key != key:
                vv, cc, _ = _top_from_bucket(alt_key, 0.0)
            if vv > 0:
                assignment[key] = (vv, cc)
            else:
                assignment[key] = (0.0, 0.3)

    # ===== [MULTI-HOLE PER-HOLE Add-On 2026-08-29] per-hole mt_i / mb_i =====
    # 横向场景每洞可能有独立的 mt_i / mb_i；纵向场景同理 ml_i / mr_i。
    # 读取策略：先查 per-hole 桶 → 无则 fallback 全局桶。
    # 这样 Case A（异边距）每个洞取自己的值；Case B（同边距）per-hole 桶为空 →
    # 自然回退全局桶 → 行为等价共享模式，零回归。
    for idx in range(n_holes):
        for axis, (global_key, per_key) in (
                ('top', ('margin_top', f'margin_top_{idx}')),
                ('bottom', ('margin_bottom', f'margin_bottom_{idx}')),
                ('left', ('margin_left', f'margin_left_{idx}')),
                ('right', ('margin_right', f'margin_right_{idx}')),
        ):
            # 方向锁定优先（per-hole 暂不支持方向锁定）
            # 注意：assignment 存的是 2-tuple (value, conf)，不能按 3-tuple 解包
            gv, gc = assignment.get(global_key, (0.0, 0.3))
            pv, pc, _ = _top_from_bucket(per_key, 0.0)
            final_v = pv if pv > 0 else gv
            final_c = max(pc, gc)
            assignment[per_key] = (max(final_v, 0.0), final_c)

    # --- 各洞 inner_w / inner_h ---
    for idx in range(n_holes):
        w_key = f'inner_w_{idx}'
        h_key = f'inner_h_{idx}'
        w_val, w_conf, _ = _top_from_bucket(w_key, 0.0)
        h_val, h_conf, _ = _top_from_bucket(h_key, 0.0)
        if w_val <= 0:
            # 从通用 inner_w 桶借用（若存在）
            w_val, w_conf, _ = _top_from_bucket('inner_w', 0.0)
        if h_val <= 0:
            h_val, h_conf, _ = _top_from_bucket('inner_h', 0.0)
        assignment[w_key] = (max(w_val, 0.0), w_conf)
        assignment[h_key] = (max(h_val, 0.0), h_conf)

    # --- gaps ---
    for idx in range(n_holes - 1):
        g_key = f'gap_{idx}_{idx + 1}'
        g_val, g_conf, _ = _top_from_bucket(g_key, 0.0)
        assignment[g_key] = (max(g_val, 0.0), g_conf)

    return assignment


def _validate_multi_hole_geometry(assignment, n_holes, layout,
                                    target_outer_w=0.0, target_outer_h=0.0):
    """多洞几何约束修正：
      - 外框优先使用 target（权威）
      - 横向守恒: outer_w = ml + Σ inner_w_i + Σ gap + mr
      - 纵向守恒: outer_h = mt + max(inner_h_i) + mb  (横排洞等高)
                  或 outer_h = mt + Σ inner_h_i + Σ gap + mb  (竖排洞)
      - 缺失值反推
      - 异常裁剪（边距不得超过外框 90%）
    """
    def get(key, d=0.0):
        return assignment.get(key, (d, 0.5))[0]

    def put(key, v, c=0.4):
        assignment[key] = (v, c)

    # 权威外框
    if target_outer_w > 0 and target_outer_h > 0:
        put('total_w', target_outer_w, 0.99)
        put('total_h', target_outer_h, 0.99)

    tw = get('total_w')
    th = get('total_h')

    # ---- 边距极端值裁剪 ----
    def _cap_margin(v, axis_max):
        if v <= 0:
            return 0.0
        return min(v, axis_max * 0.90)

    if tw > 0:
        put('margin_left', _cap_margin(get('margin_left'), tw))
        put('margin_right', _cap_margin(get('margin_right'), tw))
    if th > 0:
        put('margin_top', _cap_margin(get('margin_top'), th))
        put('margin_bottom', _cap_margin(get('margin_bottom'), th))

    # ---- 横向修正（所有布局类型都需要）----
    if tw > 0:
        ml = get('margin_left')
        mr = get('margin_right')
        iws = [get(f'inner_w_{i}') for i in range(n_holes)]
        gaps = [get(f'gap_{i}_{i+1}') for i in range(n_holes - 1)]

        known_h = [ml, mr] + iws + gaps
        known_h_pos = [v for v in known_h if v > 0]
        missing_count = known_h.count(0.0)

        if missing_count == 1 and sum(known_h_pos) < tw:
            # 恰好缺失 1 个 → 反推
            derived = tw - sum(known_h_pos)
            if derived > 0:
                if ml <= 0:
                    put('margin_left', derived, 0.5)
                    logger.info(f"[MH Step8] 横向反推 ml={derived:.1f}")
                elif mr <= 0:
                    put('margin_right', derived, 0.5)
                    logger.info(f"[MH Step8] 横向反推 mr={derived:.1f}")
                else:
                    for i in range(n_holes):
                        if iws[i] <= 0:
                            put(f'inner_w_{i}', derived, 0.5)
                            logger.info(f"[MH Step8] 横向反推 inner_w_{i}={derived:.1f}")
                            break
                    else:
                        for i in range(n_holes - 1):
                            if gaps[i] <= 0:
                                put(f'gap_{i}_{i+1}', derived, 0.5)
                                logger.info(f"[MH Step8] 横向反推 gap_{i}_{i+1}={derived:.1f}")
                                break

    # ---- 纵向修正 ----
    if th > 0:
        mt = get('margin_top')
        mb = get('margin_bottom')
        ihs = [get(f'inner_h_{i}') for i in range(n_holes)]
        gaps_v = [get(f'gap_{i}_{i+1}') for i in range(n_holes - 1)]

        if layout in ('horizontal', 'mixed'):
            # 横排：洞近似等高，纵向守恒用 max(inner_h) 或平均
            valid_ih = [v for v in ihs if v > 0]
            if valid_ih:
                avg_ih = sum(valid_ih) / len(valid_ih)
                # 缺失的 inner_h 用平均填充
                for i in range(n_holes):
                    if ihs[i] <= 0:
                        put(f'inner_h_{i}', avg_ih, 0.4)
                        logger.info(f"[MH Step8] 纵洞高填充 inner_h_{i}={avg_ih:.1f}")
                ih_max = max([get(f'inner_h_{i}') for i in range(n_holes)])
            else:
                ih_max = 0.0

            known_v = [mt, mb, ih_max]
            known_v_pos = [v for v in known_v if v > 0]
            missing = known_v.count(0.0)
            if missing == 1 and sum(known_v_pos) < th:
                derived = th - sum(known_v_pos)
                if derived > 0:
                    if mt <= 0:
                        put('margin_top', derived, 0.5)
                    elif mb <= 0:
                        put('margin_bottom', derived, 0.5)

        else:  # vertical
            known_v = [mt, mb] + ihs + gaps_v
            known_v_pos = [v for v in known_v if v > 0]
            missing = known_v.count(0.0)
            if missing == 1 and sum(known_v_pos) < th:
                derived = th - sum(known_v_pos)
                if derived > 0:
                    if mt <= 0:
                        put('margin_top', derived, 0.5)
                    elif mb <= 0:
                        put('margin_bottom', derived, 0.5)
                    else:
                        for i in range(n_holes):
                            if ihs[i] <= 0:
                                put(f'inner_h_{i}', derived, 0.5)
                                break
                        else:
                            for i in range(n_holes - 1):
                                if gaps_v[i] <= 0:
                                    put(f'gap_{i}_{i+1}', derived, 0.5)
                                    break

    # ---- 极端值兜底：所有负值/零值不通过，后续可能被再次反推 ----
    for k in list(assignment.keys()):
        v, c = assignment[k]
        if v < 0:
            put(k, 0.0, c)

    return assignment


# ======================================================================
# 主入口：多洞 9 步法解析
# ======================================================================


def _9step_multi_hole_parse(cv2, gray_img, color_img, tesseract,
                             target_outer_w_cm=0.0, target_outer_h_cm=0.0,
                             enhanced_gray=None, deadline=None):
    """多洞模式 9 步串行解析。成功返回 dict，失败返回 {'success': False, message: '...'}。"""

    def _check_deadline(phase):
        if deadline is not None and time.monotonic() > deadline:
            return {'success': False,
                    'message': f'多洞解析超时（{phase}阶段超过 {_PARSE_TIMEOUT_SEC} 秒）'}
        return None

    h_img, w_img = gray_img.shape[:2]

    # ==== Step 1: 矩形检测 ====
    all_rects = _find_all_rectangles(cv2, gray_img, color_img)
    if len(all_rects) < 3:
        return {'success': False, 'message': f'矩形候选不足({len(all_rects)}<3)，非多洞布局'}

    # ==== Step 2: 布局分类 ====
    outer, inners, layout = _classify_hole_layout(all_rects)
    if outer is None or len(inners) < 2:
        return {'success': False, 'message': '无法分类出多洞布局（内框<2个）'}
    n_holes = len(inners)
    ox, oy, ow, oh = outer

    # ==== Step 3: 多洞区域划分 ====
    zone_of = _divide_multi_hole_zones(outer, inners, layout, w_img, h_img)

    # ==== Step 4: 全局 OCR ====
    if (early := _check_deadline('OCR扫描')) is not None:
        return early
    ocr_raw = _multi_scale_ocr_scan(cv2, tesseract, gray_img,
                                    target_w_cm=target_outer_w_cm,
                                    target_h_cm=target_outer_h_cm,
                                    enhanced_gray=enhanced_gray)
    if not ocr_raw:
        return {'success': False, 'message': '多洞OCR未识别到任何数值'}

    # 小数合并（复用单洞已有的 _merge_split_decimals）
    try:
        from .sketch_parser_numbers import _merge_split_decimals
        ocr_raw = _merge_split_decimals(ocr_raw)
    except Exception:
        pass

    # ==== Step 5: 方向标签 + 箭头符号 ====
    if (early := _check_deadline('方向/箭头识别')) is not None:
        return early
    dir_locked = _extract_arrow_direction_numbers(
        cv2, tesseract, gray_img,
        enhanced_gray=enhanced_gray,
        target_outer_w_cm=target_outer_w_cm,
        target_outer_h_cm=target_outer_h_cm)
    excluded_fields = set(dir_locked.keys())
    excluded_values = [v[0] for v in dir_locked.values()]

    # 外框值排除
    tw_t = round(float(target_outer_w_cm or 0), 1)
    th_t = round(float(target_outer_h_cm or 0), 1)
    if tw_t > 0:
        excluded_values.extend([tw_t + d for d in (0, -0.5, 0.5, -1.0, 1.0) if tw_t + d > 0])
    if th_t > 0:
        excluded_values.extend([th_t + d for d in (0, -0.5, 0.5, -1.0, 1.0) if th_t + d > 0])

    # ==== Step 6: 空间归属 ====
    buckets = _multi_hole_spatial_bind(
        ocr_raw, zone_of, excluded_fields, excluded_values,
        n_holes, layout)

    # ===== [NUMERIC SANITY Add-On 2026-08-29] per-hole 桶 outlier 修正 =====
    # OCR 偶发把「21.7」读成「217」（丢小数点），Step6 Post A 错删了几何包含
    # 的正确值，留下错误的整数。这里在进入 Step7 之前做合理性 cap + 小数点
    # 恢复：217→21.7，215→21.5，413→41.3 等。 Staff Engineer Mode 铁律：
    # 纯 ADD-ON，旧 buckets 构建代码一字未改。
    _cap_h = max(target_outer_w_cm * 0.9, 1.0) if target_outer_w_cm > 0 else 999.0
    _cap_v = max(target_outer_h_cm * 0.9, 1.0) if target_outer_h_cm > 0 else 999.0
    for f in list(buckets.keys()):
        # 只处理 margin_* 和 inner_* 桶，gap/outer 等不动
        is_margin = f.startswith('margin_')
        is_inner_dim = f.startswith('inner_')
        is_gap = f.startswith('gap_')
        is_outer = f in ('outer_w', 'outer_h')
        if not (is_margin or is_inner_dim):
            continue
        # 判定轴向上限（margin_top/bottom 用 _cap_v；margin_left/right 用 _cap_h）
        if is_margin:
            axis_limit = _cap_v if ('top' in f or 'bottom' in f) else _cap_h
        else:  # inner_w / inner_h（桶名是 inner_w_0/inner_w_1... 以 _N 结尾，不能用 endswith）
            axis_limit = _cap_h if f.startswith('inner_w') else _cap_v
        # 对桶内每个候选做 outlier 修正
        _repaired = []
        for (v, c, bb) in buckets[f]:
            if v <= axis_limit:
                _repaired.append((v, c, bb))
                continue
            # 值超 cap → 尝试小数点恢复（OCR 丢了小数点）
            rec = None
            if abs(v - round(v)) <= 0.01:  # 整数 → 小数丢失
                iv = int(round(v))
                s = str(iv)
                # 3 位整数 → 倒数 2 位加小数点：217→21.7, 413→41.3
                if len(s) == 3:
                    rec = float(f"{s[:2]}.{s[2:]}")
                # 2 位整数 → 只有当恢复后更合理才尝试（如 99→9.9 但 margin 一般>10，跳过）
            if rec is not None and 0.3 <= rec <= axis_limit:
                _repaired.append((rec, c * 0.85, bb))
                logger.info(f"[MH Step6-Sanity] {f}: {v}→{rec} (小数恢复)")
            # else: 恢复失败 → 丢弃这个 outlier
        if _repaired:
            buckets[f] = _repaired
        else:
            del buckets[f]  # 所有候选都是 outlier → 删桶（后续 fallback）

    # ==== Step 7: 外框候选枚举选优 ====
    def _try_assignment(tw_cand, th_cand):
        asg = _build_multi_hole_assignment(
            dir_locked, buckets, tw_cand, th_cand, n_holes, layout)
        asg = _validate_multi_hole_geometry(
            asg, n_holes, layout,
            target_outer_w=target_outer_w_cm, target_outer_h=target_outer_h_cm)
        # 计算 sc
        holes_list = []
        for idx in range(n_holes):
            holes_list.append({
                'w': asg.get(f'inner_w_{idx}', (0, 0))[0],
                'h': asg.get(f'inner_h_{idx}', (0, 0))[0],
            })
        gaps_list = [asg.get(f'gap_{i}_{i+1}', (0, 0))[0] for i in range(n_holes - 1)]
        sc = _score_multi_hole_consistency(
            tw_cand, th_cand, holes_list, gaps_list, layout,
            asg.get('margin_top', (0, 0))[0],
            asg.get('margin_bottom', (0, 0))[0],
            asg.get('margin_left', (0, 0))[0],
            asg.get('margin_right', (0, 0))[0])
        # 像素比例匹配
        px_r = ow / max(oh, 1)
        cm_r = tw_cand / max(th_cand, 1)
        ratio_match = 1.0 - min(abs(px_r - cm_r) / max(px_r, cm_r, 0.1), 1.0)
        round_bonus = _round_pref_bonus_multi(tw_cand) + _round_pref_bonus_multi(th_cand)
        total = sc * 0.65 + ratio_match * 0.15 + round_bonus * 0.20
        return total, sc, asg

    if target_outer_w_cm > 0 and target_outer_h_cm > 0:
        # target 权威模式：直接构建
        assignment = _build_multi_hole_assignment(
            dir_locked, buckets, target_outer_w_cm, target_outer_h_cm, n_holes, layout)
        assignment = _validate_multi_hole_geometry(
            assignment, n_holes, layout,
            target_outer_w=target_outer_w_cm, target_outer_h=target_outer_h_cm)
        sc_after = 0.0
    else:
        # 收集大值候选池
        all_big = []
        seen = set()
        for bname, cands in buckets.items():
            for v, c, b in cands:
                if 20.0 <= v <= 600:
                    key = round(v, 1)
                    if key not in seen:
                        seen.add(key)
                        all_big.append((v, c, b))
        for v, c, b in ocr_raw:
            if 20.0 <= v <= 600:
                key = round(v, 1)
                if key not in seen:
                    seen.add(key)
                    all_big.append((v, c, b))
        def _sortk(r):
            v = r[0]
            dig = len(f"{int(v)}") if v >= 1 else 1
            return (dig, r[1], v)
        all_big.sort(key=_sortk, reverse=True)
        top_cands = all_big[:8]
        logger.info(f"[MH Step7] 外框候选池: {[round(v,1) for v,_,_ in top_cands]}")

        best_total = -1.0
        best_sc = -1.0
        best_asg = None
        for i in range(len(top_cands)):
            for j in range(len(top_cands)):
                if i == j and len(top_cands) > 1:
                    continue
                tw_i = top_cands[i][0]
                th_j = top_cands[j][0]
                if tw_i + th_j < 60:
                    continue
                try:
                    score, sc, asg = _try_assignment(tw_i, th_j)
                except Exception:
                    continue
                if score > best_total:
                    best_total = score
                    best_sc = sc
                    best_asg = asg
                    logger.info(f"[MH Step7] 候选外框({tw_i:.1f}x{th_j:.1f}) "
                                f"total={score:.3f} sc={sc:.3f}")
        if best_asg is None:
            assignment = _build_multi_hole_assignment(
                dir_locked, buckets, 0.0, 0.0, n_holes, layout)
            assignment = _validate_multi_hole_geometry(assignment, n_holes, layout)
            best_sc = _score_multi_hole_consistency(
                assignment.get('total_w', (0, 0))[0],
                assignment.get('total_h', (0, 0))[0],
                [{'w': assignment.get(f'inner_w_{i}', (0,0))[0],
                  'h': assignment.get(f'inner_h_{i}', (0,0))[0]}
                 for i in range(n_holes)],
                [assignment.get(f'gap_{i}_{i+1}', (0,0))[0] for i in range(n_holes - 1)],
                layout,
                assignment.get('margin_top', (0,0))[0],
                assignment.get('margin_bottom', (0,0))[0],
                assignment.get('margin_left', (0,0))[0],
                assignment.get('margin_right', (0,0))[0])
            sc_after = best_sc
        else:
            assignment = best_asg
            sc_after = best_sc

    # ==== Step 6.5: 像素比例 vs cm 比例 swap（与单洞类似）====
    tw_val = assignment.get('total_w', (0, 0))[0]
    th_val = assignment.get('total_h', (0, 0))[0]
    if tw_val > 0 and th_val > 0 and target_outer_w_cm <= 0 and target_outer_h_cm <= 0:
        px_r = ow / max(oh, 1)
        cm_r = tw_val / max(th_val, 1)
        need_swap = (px_r > 1.2 and cm_r < 0.83) or (px_r < 0.83 and cm_r > 1.2)
        if need_swap:
            logger.info(f"[MH Step6.5] 比例不符: px={px_r:.2f} vs cm={cm_r:.2f}，交换宽高")
            # 交换 total
            old_tw = assignment.pop('total_w', (0, 0.5))
            old_th = assignment.pop('total_h', (0, 0.5))
            assignment['total_w'] = old_th
            assignment['total_h'] = old_tw
            # 交换 margin
            old_ml = assignment.pop('margin_left', (0, 0.5))
            old_mr = assignment.pop('margin_right', (0, 0.5))
            old_mt = assignment.pop('margin_top', (0, 0.5))
            old_mb = assignment.pop('margin_bottom', (0, 0.5))
            assignment['margin_top'] = old_ml
            assignment['margin_bottom'] = old_mr
            assignment['margin_left'] = old_mt
            assignment['margin_right'] = old_mb
            # 交换每个洞的 w/h
            for idx in range(n_holes):
                ow_k = f'inner_w_{idx}'
                oh_k = f'inner_h_{idx}'
                old_iw = assignment.pop(ow_k, (0, 0.5))
                old_ih = assignment.pop(oh_k, (0, 0.5))
                assignment[ow_k] = old_ih
                assignment[oh_k] = old_iw
            # 交换后重新验证
            assignment = _validate_multi_hole_geometry(
                assignment, n_holes, layout,
                target_outer_w=0, target_outer_h=0)

    # ==== 构建结果 ====
    tw = assignment.get('total_w', (0, 0))[0]
    th = assignment.get('total_h', (0, 0))[0]
    mt = assignment.get('margin_top', (0, 0))[0]
    mb = assignment.get('margin_bottom', (0, 0))[0]
    ml = assignment.get('margin_left', (0, 0))[0]
    mr = assignment.get('margin_right', (0, 0))[0]

    holes = []
    for idx in range(n_holes):
        # ===== [MULTI-HOLE PER-HOLE Add-On 2026-08-29] per-hole mt/mb/ml/mr =====
        # Bug fix (2026-08-29): 全局方向锁定值（如 margin_left=36.0）是权威来源。
        # 优先使用全局值，per-hole 桶**仅当存在且与全局值方向一致**（即不是移位/duplicate 噪声）时才覆盖。
        # 根因：之前 assignment.get(f'margin_left_{idx}', (ml, 0))[0] 取到错误的 3.6（decimal 移位），
        # 而 fallback 才是正确的 36.0——逻辑完全反了！
        def _per_hole_margin(field_idx, global_val, global_val_is_dir_locked):
            """Per-hole margin：全局优先，per-hole 仅当合理时覆盖。"""
            key = f'{field_idx}_{idx}'
            # 先尝试方向锁定桶（Step5 把方向值写进了 direction_labels，
            # 但 assignment 里全局 margin_{side} 已经是方向锁定值）
            ph_val, ph_conf = assignment.get(key, (0.0, 0))
            if ph_val <= 0:
                return global_val  # per-hole 桶不存在 → 全局兜底
            # 全局是方向锁定值且 per-hole 值相差很大 → 丢弃 per-hole（它是移位/重复识别噪声）
            if global_val_is_dir_locked and abs(ph_val - global_val) > 5.0:
                return global_val
            # 其他情况：per-hole 值更精细（如同一边距不同洞有微调场景），保留
            return ph_val

        # 方向锁定字段判断：该 side 是否在 dir_locked 里有值
        def _is_dir_locked(field_side):
            return field_side in dir_locked

        mt_i = _per_hole_margin('margin_top', mt, _is_dir_locked('margin_top'))
        mb_i = _per_hole_margin('margin_bottom', mb, _is_dir_locked('margin_bottom'))
        ml_i = _per_hole_margin('margin_left', ml, _is_dir_locked('margin_left'))
        mr_i = _per_hole_margin('margin_right', mr, _is_dir_locked('margin_right'))
        hi = HoleInfo(
            index=idx,
            rect_px=inners[idx],
            w_cm=assignment.get(f'inner_w_{idx}', (0, 0))[0],
            h_cm=assignment.get(f'inner_h_{idx}', (0, 0))[0],
            margin_left_cm=ml_i,
            margin_right_cm=mr_i,
            margin_top_cm=mt_i,
            margin_bottom_cm=mb_i,
        )
        holes.append(hi)

    gaps = [assignment.get(f'gap_{i}_{i+1}', (0, 0))[0] for i in range(n_holes - 1)]

    # 兼容单洞输出：inner_w/h = 第一个洞的尺寸（对外保持零破坏）
    compat_inner_w = holes[0].w_cm if holes else 0.0
    compat_inner_h = holes[0].h_cm if holes else 0.0

    logger.info(f"[MH Step9] 多洞识别完成: outer={tw:.1f}x{th:.1f} 布局={layout} "
                f"洞数={n_holes} sc={sc_after:.3f}")
    for idx, h in enumerate(holes):
        logger.info(f"    Hole {idx}: {h.w_cm:.1f}x{h.h_cm:.1f}cm")
    logger.info(f"    gaps: {[round(g,1) for g in gaps]}")
    logger.info(f"    margins: t={mt:.1f} b={mb:.1f} l={ml:.1f} r={mr:.1f}")

    return {
        'success': True,
        'message': f'多洞9步法识别成功（layout={layout}, sc={sc_after:.2f}）',
        'outer_w': tw, 'outer_h': th,
        'inner_w': compat_inner_w, 'inner_h': compat_inner_h,
        'margin_top': mt, 'margin_bottom': mb,
        'margin_left': ml, 'margin_right': mr,
        'holes': holes,
        'gaps': gaps,
        'layout': layout,
        'outer_rect_px': outer,
        'inner_rects_px': inners,
        'direction_labels': {k: (v[0], v[1]) for k, v in dir_locked.items()},
        'ocr_values': ocr_raw,
        'method': f'multihole_v1(layout={layout}, sc={sc_after:.2f})',
        'debug_assignment': assignment,
        'self_consistency': sc_after,
        'is_multi_hole': True,
    }


# ======================================================================
# 公开入口：多洞识别尝试
# ======================================================================


def try_parse_multi_hole(image_path, target_outer_w_cm=0.0, target_outer_h_cm=0.0,
                          progress_callback=None):
    """尝试以多洞模式解析草图。成功返回 dict(success=True,...)，失败返回 dict(success=False,...)。

    说明：此函数从不抛异常；当草图不是多洞布局时，返回 success=False 且 message
    说明原因，调用方（sketch_parser.py 的入口）应自动回退到单洞 7 步法。
    """
    import time as _time

    def _progress(pct, msg):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass

    # 基础依赖
    cv2 = _safe_import_cv2()
    if cv2 is None:
        return {'success': False, 'message': 'cv2 未安装'}

    img, err = _load_image(image_path)
    if err:
        return {'success': False, 'message': err}
    gray = _to_gray(img)
    tesseract = _safe_import_tesseract()

    enhanced = _enhance_colored_ink(cv2, img)
    deadline = _time.monotonic() + _PARSE_TIMEOUT_SEC

    try:
        _progress(20, "检测多洞布局...")
        # 先做一次轻量级矩形检测，快速判断是否为多洞布局
        # 若 all_rects<3 或 classify 返回 (None,[],'') 则直接告诉上层回退单洞
        all_rects = _find_all_rectangles(cv2, gray, img)
        logger.info(f"[MH quick_check] 矩形候选数={len(all_rects)}")
        if len(all_rects) < 3:
            logger.info("[MH quick_check] 候选<3 → 回退单洞 7 步法")
            return {'success': False, 'message': 'quick_check: 矩形候选<3',
                    '_fallback_to_single_hole': True}

        outer, inners, layout = _classify_hole_layout(all_rects)
        if outer is None or len(inners) < 2:
            logger.info("[MH quick_check] 未识别出≥2个内框 → 回退单洞 7 步法 "
                        f"(inners_count={len(inners)})")
            return {'success': False, 'message': 'quick_check: 非多洞布局（内框<2个）',
                    '_fallback_to_single_hole': True}

        _progress(30, f"识别为多洞({layout}, {len(inners)}洞)，9步法解析中...")
        geo = _9step_multi_hole_parse(
            cv2, gray, img, tesseract,
            target_outer_w_cm=target_outer_w_cm,
            target_outer_h_cm=target_outer_h_cm,
            enhanced_gray=enhanced, deadline=deadline)
        return geo
    except Exception as e:
        logger.exception(f"[multi_hole] 异常: {e}")
        return {'success': False, 'message': f'多洞解析异常: {e}',
                '_fallback_to_single_hole': True}
