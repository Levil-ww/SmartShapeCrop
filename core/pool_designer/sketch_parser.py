"""尺寸草图解析器（水池设计器用）—— 严格7步法实现。

7步法识别流程：
  Step 1. 矩形检测：定位外框+内框两个嵌套矩形
  Step 2. 区域划分：基于两个矩形划分8个语义区域
  Step 3. 全局OCR：全图多尺度扫描提取所有数值（值+坐标+置信度）
  Step 4. 方向标签：带"上/下/左/右"前缀的值优先锁定对应字段
  Step 5. 位置映射：其余数值按中心点坐标归入8个区域
  Step 6. 几何校验：用 outer=inner+margin_sum 两个等式做纠错与反推
  Step 7. 冲突解决：方向标签 > 几何自洽 > 置信度 > 位数多

所有公开函数都不会抛异常；失败时返回带有 success=False 的结果。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# [F3 修复] 解炸弹二级防御：尽量为 PIL 设置像素上限，防止恶意/超大图在
# 全量解码时 OOM。主闸门是 parse_sketch 入口的 validate_sketch_file（40MP
# 头信息校验，见 _SKETCH_MAX_PIXELS），此处作为任何 PIL 全量加载路径的兜底网；
# 上限与 core/image_ops.py 保持一致（2 亿像素 ≈ 14142×14142）。
# 用 try 包裹：本模块主解码走 cv2，PIL 不可用时跳过也不影响导入。
try:  # pragma: no cover - 依赖环境差异
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    logging.getLogger(__name__).debug("[module] PIL 导入失败，已降级", exc_info=True)
    Image = None  # type: ignore

logger = logging.getLogger(__name__)

_PARSE_TIMEOUT_SEC = 20
_ALGO_VERSION = 7  # 2026-08-20: 严格7步法重构版

# ---------------------------------------------------------------------------
# 字符规范化：全角→半角（OCR 在 chi_sim 模式下常输出全角数字 ０-９ 句号．）
# ---------------------------------------------------------------------------

from .sketch_parser_base import (_FW_HW_TRANSLATION, _PARSE_TIMEOUT_SEC, _SKETCH_ACCEPT_EXT, _SKETCH_MAX_FILE_MB, _SKETCH_MAX_PIXELS, _normalize_ocr_text, validate_sketch_file)
from .sketch_parser_cache import (_ALGO_VERSION, _SKETCH_CACHE, _SKETCH_CACHE_LOCK, _SKETCH_CACHE_MAX, _SKETCH_CONSISTENT_CACHE, _SKETCH_CONSISTENT_CACHE_LOCK, _SKETCH_CONSISTENT_CACHE_MAX, _get_cache_key, _get_cached_result, _get_consistent_cache_key, _get_consistent_cached_result, _store_cached_result, _store_consistent_cached_result)
from .sketch_parser_vision import (_TESSERACT_STATUS, _build_binary_masks, _compute_gaps, _divide_8_zones, _enhance_colored_ink, _find_all_rectangles, _load_image, _make_preprocess_variants, _multi_scale_ocr_scan, _safe_import_cv2, _safe_import_tesseract, _select_best_nested_pair, _spatial_map_values, _to_gray, get_tesseract_status)
from .sketch_parser_numbers import (_DIR_CHAR_MAP, _extract_direction_label_numbers, _merge_split_decimals, _parse_dir_num_token)
from .sketch_parser_margins import (_brute_force_margin_permute, _build_assignment, _score_assignment_consistency, _validate_and_fix_margins, _validate_geometric_constraints)
@dataclass
class SketchParseResult:
    success: bool = False
    message: str = ""
    method: str = ""
    outer_w_cm: float = 0.0
    outer_h_cm: float = 0.0
    inner_w_cm: float = 0.0
    inner_h_cm: float = 0.0
    margin_top_cm: float = 0.0
    margin_bottom_cm: float = 0.0
    margin_left_cm: float = 0.0
    margin_right_cm: float = 0.0
    debug: dict = field(default_factory=dict)
    # ===== 多洞扩展字段（2026-08-29 新增，均有默认值 → 向后兼容）=====
    # 布局类型："single"（单洞，默认） / "horizontal"（横排多洞） /
    #           "vertical"（竖排多洞） / "mixed"（混合）
    layout_type: str = "single"
    # 是否为多洞识别结果
    is_multi_hole: bool = False
    # 洞列表（仅当 is_multi_hole=True 时有值；单洞时空列表）
    # 每项是 sketch_parser_multihole.HoleInfo dataclass
    holes: list = field(default_factory=list)
    # 洞间距列表（仅多洞横/竖排时有意义，长度 = len(holes)-1）
    hole_gaps_cm: list = field(default_factory=list)
    # 多洞模式下内框像素矩形列表（单洞时只有 debug['inner_rect_px']）
    inner_rects_px: list = field(default_factory=list)


def _7step_parse(cv2, gray_img, color_img, tesseract,
                 target_outer_w_cm=0.0, target_outer_h_cm=0.0,
                 enhanced_gray=None, deadline=None):
    """严格7步法草图解析。

    [F6 修复] deadline: 可选 float 时间戳（time.monotonic），用于在 OCR 等
    耗时步骤之间检查总耗时，超时立即返回失败，避免“解析中”状态永久挂起。
    （单次 OCR 调用的硬超时由 image_to_data(timeout=_PARSE_TIMEOUT_SEC) 保证。）
    """
    import time as _time
    def _check_deadline(phase: str):
        if deadline is not None and _time.monotonic() > deadline:
            return {'success': False,
                    'message': f'解析超时（{phase}阶段超过 {_PARSE_TIMEOUT_SEC} 秒）'}
        return None

    h_img, w_img = gray_img.shape[:2]

    # Step 1: 矩形检测
    all_rects = _find_all_rectangles(cv2, gray_img, color_img)
    if len(all_rects) < 2:
        return {'success': False, 'message': f'只检测到{len(all_rects)}个矩形，无法确定内外框'}
    outer, inner = _select_best_nested_pair(all_rects)
    if outer is None or inner is None:
        return {'success': False, 'message': '无法找到嵌套的内外框对'}
    ox, oy, ow, oh = outer[:4]
    ix, iy, iw, ih = inner[:4]

    # Step 2: 8区域划分
    gaps = _compute_gaps(ox, oy, ow, oh, ix, iy, iw, ih)
    zone_of = _divide_8_zones(outer, inner, w_img, h_img)
    logger.info(f"[Step2] 间隙区域: {list(gaps.keys())}")

    # Step 3: 全局OCR扫描（传入颜色增强灰度图作为附加变体）
    if (early := _check_deadline('OCR扫描')) is not None:
        return early
    ocr_raw = _multi_scale_ocr_scan(cv2, tesseract, gray_img,
                                    target_w_cm=target_outer_w_cm,
                                    target_h_cm=target_outer_h_cm,
                                    enhanced_gray=enhanced_gray)
    if not ocr_raw:
        return {'success': False, 'message': '全局OCR未识别到任何数值'}
    ocr_raw = _merge_split_decimals(ocr_raw)

    # Step 4: 方向标签优先锁定（传入颜色增强灰度图 + target 用于边距合理性校验）
    if (early := _check_deadline('方向标签识别')) is not None:
        return early
    dir_locked = _extract_direction_label_numbers(cv2, tesseract, gray_img,
                                                   enhanced_gray=enhanced_gray,
                                                   target_outer_w_cm=target_outer_w_cm,
                                                   target_outer_h_cm=target_outer_h_cm)
    excluded_fields = set(dir_locked.keys())
    excluded_values = [v[0] for v in dir_locked.values()]
    # [Fix Bug2] 将权威外框尺寸和目标尺寸加入 value 排除，防止 total_w/total_h 被误分配给 inner_* 或 margin_* 桶
    # 例如：172（外框宽）不应出现在 inner_h、margin_left 等候选中；60（外框高）同理
    target_w = round(float(target_outer_w_cm or 0), 1)
    target_h = round(float(target_outer_h_cm or 0), 1)
    if target_w > 0:
        excluded_values.append(target_w)
        # 同时添加近似整数值（如 172.0 被识别为 172、或 171.5）
        for dv in [target_w + 0.5, target_w - 0.5, target_w + 1.0, target_w - 1.0]:
            if dv > 0:
                excluded_values.append(dv)
    if target_h > 0:
        excluded_values.append(target_h)
        for dv in [target_h + 0.5, target_h - 0.5, target_h + 1.0, target_h - 1.0]:
            if dv > 0:
                excluded_values.append(dv)
    logger.info(f"[Step4] 方向标签锁定 {len(dir_locked)} 个字段: {list(dir_locked.keys())}")

    # Step 5: 空间位置映射
    buckets = _spatial_map_values(ocr_raw, zone_of, excluded_fields, excluded_values)
    for field, cands in buckets.items():
        logger.info(f"[Step5] 区域[{field}] 候选数={len(cands)} top1={cands[0][0] if cands else None}")

    # ---- Step 5.5：外框候选组合枚举选优（核心改进）----
    # 外框尺寸通常是所有数值中最大的2个。从所有桶+OCR候选收集大值，枚举两两组合并评分。
    def _round_pref_bonus(v):
        """圆整数偏好加分：外框尺寸通常是5或10的倍数。"""
        if abs(v - round(v)) > 0.01:
            return 0.0
        iv = int(v)
        if iv % 100 == 0:
            return 0.05  # 整百 +5%
        if iv % 50 == 0:
            return 0.04  # 整五十 +4%
        if iv % 10 == 0:
            return 0.03  # 整十 +3%
        if iv % 5 == 0:
            return 0.02  # 整五 +2%
        return 0.0

    def _try_assignment(tw_cand, th_cand):
        asg = _build_assignment(dir_locked, buckets, tw_cand, th_cand)
        asg = _validate_and_fix_margins(asg, tw_cand, th_cand,
                                         dir_locked_fields=set(dir_locked.keys()))
        sc = _score_assignment_consistency(asg)
        # 像素比例匹配分
        px_r = ow / max(oh, 1)
        cm_r = asg.get('total_w', (0, 0))[0] / max(asg.get('total_h', (0, 0))[0], 1)
        ratio_match = 1.0 - min(abs(px_r - cm_r) / max(px_r, cm_r, 0.1), 1.0)
        # 圆整数偏好：外框宽高为5/10/50/100倍数的加分
        round_bonus = _round_pref_bonus(tw_cand) + _round_pref_bonus(th_cand)
        # 外框桶匹配奖励：若候选值来自 outer_w/outer_h 桶，给予额外加分
        outer_w_set = set(round(v, 1) for v, _, _ in buckets.get('outer_w', []))
        outer_h_set = set(round(v, 1) for v, _, _ in buckets.get('outer_h', []))
        bucket_bonus = 0.0
        if round(tw_cand, 1) in outer_w_set:
            bucket_bonus += 0.10
        if round(th_cand, 1) in outer_h_set:
            bucket_bonus += 0.10
        # 尺寸合理性：若外框两边都很小（<40cm），给予惩罚
        size_penalty = 1.0
        if tw_cand < 40 and th_cand < 40:
            size_penalty = 0.5
        # 综合得分：自洽65% + 比例匹配10% + 圆整偏好10% + 桶匹配15%
        return (sc * 0.65 + ratio_match * 0.10 + round_bonus * 0.10 + bucket_bonus) * size_penalty, sc, asg

    if target_outer_w_cm <= 0 and target_outer_h_cm <= 0:
        # 收集所有可能的大值候选（20~600）
        all_big_vals = []
        seen_v = set()
        for bucket_name, cands in buckets.items():
            for v, c, b in cands:
                if 20.0 <= v <= 600:
                    key = round(v, 1)
                    if key not in seen_v:
                        seen_v.add(key)
                        all_big_vals.append((v, c, b))
        # 补充：merge 所有全局OCR原始值里的大值（避免遗漏）
        for v, c, b in ocr_raw:
            if 20.0 <= v <= 600:
                key = round(v, 1)
                if key not in seen_v:
                    seen_v.add(key)
                    all_big_vals.append((v, c, b))
        # 按 (位数, 置信度, 值大小) 排序取top8（越大越像外框）
        def _sort_key(r):
            v, c, _ = r
            dig = len(f"{int(v)}") if v >= 1 else 1
            return (dig, c, v)
        all_big_vals.sort(key=_sort_key, reverse=True)
        top_cands = all_big_vals[:8]
        logger.info(f"[Step5.5] 外框候选池: {[round(v,1) for v,_,_ in top_cands]}")

        # 枚举所有两两组合（含自身swap），选综合得分最高的
        best_total = -1.0
        best_sc = -1.0
        best_asg = None
        for i in range(len(top_cands)):
            for j in range(len(top_cands)):
                if i == j and len(top_cands) > 1:
                    continue
                tw_i = top_cands[i][0]
                th_j = top_cands[j][0]
                # 避免太小组合：两值之和至少60
                if tw_i + th_j < 60:
                    continue
                try:
                    score, sc, asg = _try_assignment(tw_i, th_j)
                except Exception:
                    logger.debug("[_7step_parse] 忽略异常", exc_info=True)
                    continue
                if score > best_total:
                    best_total = score
                    best_sc = sc
                    best_asg = asg
                    logger.info(f"[Step5.5] 候选外框组合({tw_i:.1f}x{th_j:.1f}) "
                                f"综合分={score:.3f} 自洽sc={sc:.3f} → 暂时领先")
        # 兜底：同时尝试只取单个最大外框值，另一侧用对称比例估算
        if best_asg is None:
            best_asg = _build_assignment(dir_locked, buckets, 0.0, 0.0)
            best_asg = _validate_and_fix_margins(best_asg, 0.0, 0.0,
                                                  dir_locked_fields=set(dir_locked.keys()))
            best_sc = _score_assignment_consistency(best_asg)
        assignment = best_asg
        sc_after = best_sc
        logger.info(f"[Step5.5选出] 综合分={best_total:.3f} 自洽sc={sc_after:.3f} "
                    f"外框={assignment.get('total_w',(0,0))[0]:.1f}x{assignment.get('total_h',(0,0))[0]:.1f}")
    else:
        # target模式：直接用用户指定外框尺寸
        assignment = _build_assignment(dir_locked, buckets, target_outer_w_cm, target_outer_h_cm)
        assignment = _validate_and_fix_margins(assignment, target_outer_w_cm, target_outer_h_cm,
                                                dir_locked_fields=set(dir_locked.keys()))
        sc_after = _score_assignment_consistency(assignment)

    # ---- Step 6.5：像素比例 vs cm比例 对齐校验（方向搞反则swap）----
    px_ratio = ow / max(oh, 1)  # 像素外框宽/高
    tw_val = assignment.get('total_w', (0, 0))[0]
    th_val = assignment.get('total_h', (0, 0))[0]
    if tw_val > 0 and th_val > 0 and target_outer_w_cm <= 0 and target_outer_h_cm <= 0:
        cm_ratio = tw_val / max(th_val, 1)
        need_swap = (px_ratio > 1.2 and cm_ratio < 0.83) or (px_ratio < 0.83 and cm_ratio > 1.2)
        if need_swap:
            logger.info(f"[Step6.5] 像素/厘米比例不符，交换宽高！px比例={px_ratio:.2f} cm比例={cm_ratio:.2f}")
            pairs = [('total_w', 'total_h'), ('inner_w', 'inner_h')]
            for a, b in pairs:
                av = assignment.pop(a, (0, 0.5))
                bv = assignment.pop(b, (0, 0.5))
                assignment[a] = bv
                assignment[b] = av
            ml = assignment.pop('margin_left', (0, 0.5))
            mr = assignment.pop('margin_right', (0, 0.5))
            mt = assignment.pop('margin_top', (0, 0.5))
            mb = assignment.pop('margin_bottom', (0, 0.5))
            assignment['margin_top'] = ml
            assignment['margin_bottom'] = mr
            assignment['margin_left'] = mt
            assignment['margin_right'] = mb
            assignment = _validate_and_fix_margins(assignment, 0.0, 0.0,
                                                    dir_locked_fields=set(dir_locked.keys()))
            sc_after = _score_assignment_consistency(assignment)
            logger.info(f"[Step6.5后] 新值: "
                        f"total={assignment.get('total_w',(0,0))[0]:.1f}x{assignment.get('total_h',(0,0))[0]:.1f} "
                        f"inner={assignment.get('inner_w',(0,0))[0]:.1f}x{assignment.get('inner_h',(0,0))[0]:.1f} "
                        f"上{assignment.get('margin_top',(0,0))[0]:.1f}下{assignment.get('margin_bottom',(0,0))[0]:.1f}"
                        f"左{assignment.get('margin_left',(0,0))[0]:.1f}右{assignment.get('margin_right',(0,0))[0]:.1f} "
                        f"sc={sc_after:.3f}")

    # ---- Step 6.6：外框尺寸圆整校验（尝试5/10倍数圆整，提升自洽度）----
    if target_outer_w_cm <= 0 and target_outer_h_cm <= 0:
        cur_tw = assignment.get('total_w', (0, 0))[0]
        cur_th = assignment.get('total_h', (0, 0))[0]
        if cur_tw > 20 and cur_th > 20:
            best_sc = _score_assignment_consistency(assignment)
            best_assg = dict(assignment)

            def _try_round(orig_val, step, label):
                """尝试将原值圆整到最近的step倍数，返回候选列表。"""
                near = round(orig_val / step) * step
                results = []
                for delta in [-step, 0, step]:
                    nv = near + delta
                    if 20 <= nv <= 600 and abs(nv - orig_val) <= orig_val * 0.15:
                        results.append(nv)
                return list(set(results))

            tw_candidates = _try_round(cur_tw, 5, 'w')
            th_candidates = _try_round(cur_th, 5, 'h')
            tw_candidates += _try_round(cur_tw, 10, 'w')
            th_candidates += _try_round(cur_th, 10, 'h')
            tw_candidates = sorted(set([v for v in tw_candidates if v != cur_tw]))
            th_candidates = sorted(set([v for v in th_candidates if v != cur_th]))

            # 仅保留"很圆"的候选（100/50/25的倍数），且原始值在10%范围内
            def _is_very_round(v):
                for base in [100, 50, 25]:
                    if abs(v - round(v / base) * base) < 0.01:
                        return True
                return False

            tw_very_round = [v for v in tw_candidates if _is_very_round(v) and abs(cur_tw - v) / max(cur_tw, 1) < 0.10]
            th_very_round = [v for v in th_candidates if _is_very_round(v) and abs(cur_th - v) / max(cur_th, 1) < 0.10]

            if tw_very_round or th_very_round:
                logger.info(f"[Step6.6] 尝试外框尺寸圆整(很圆): tw={tw_very_round} th={th_very_round}")
                # 遍历所有tw和th组合（含原始值），选自洽分+圆整分最高的
                all_tw = list(set(tw_very_round + [cur_tw]))
                all_th = list(set(th_very_round + [cur_th]))
                best_total = best_sc * 0.6 + (_round_pref_bonus(cur_tw) + _round_pref_bonus(cur_th)) * 0.4
                best_final_assg = dict(assignment)
                best_final_sc = best_sc
                for tw_c in all_tw:
                    for th_c in all_th:
                        if tw_c == cur_tw and th_c == cur_th:
                            continue
                        alt_asg = dict(assignment)
                        alt_asg['total_w'] = (tw_c, 0.9)
                        alt_asg['total_h'] = (th_c, 0.9)
                        alt_asg = _validate_and_fix_margins(alt_asg, tw_c, th_c,
                                                                dir_locked_fields=set(dir_locked.keys()))
                        alt_sc = _score_assignment_consistency(alt_asg)
                        alt_round = _round_pref_bonus(tw_c) + _round_pref_bonus(th_c)
                        alt_total = alt_sc * 0.6 + alt_round * 0.4
                        logger.info(f"[Step6.6] 候选 {tw_c}x{th_c} sc={alt_sc:.3f} round={alt_round:.3f} total={alt_total:.3f}")
                        if alt_total > best_total + 0.003:
                            best_total = alt_total
                            best_final_sc = alt_sc
                            best_final_assg = alt_asg
                            logger.info(f"[Step6.6] ⬆ 采用 {tw_c}x{th_c} total={alt_total:.3f}")

                if best_final_assg != assignment:
                    assignment = best_final_assg
                    sc_after = best_final_sc
                    logger.info(f"[Step6.6采用] 圆整后: "
                                f"total={assignment.get('total_w',(0,0))[0]:.1f}x{assignment.get('total_h',(0,0))[0]:.1f} "
                                f"sc={sc_after:.3f}")

    # ---- Step 6.7：几何自洽穷举校验（Phase2 改进5）----
    # 性能保护（条件触发）：仅当 sc<0.9 或 边距有0 或 锁定<2项 时才运行
    need_brute = (
        sc_after < 0.9
        or assignment.get('margin_top', (0,0))[0] <= 0
        or assignment.get('margin_bottom', (0,0))[0] <= 0
        or assignment.get('margin_left', (0,0))[0] <= 0
        or assignment.get('margin_right', (0,0))[0] <= 0
        or len(dir_locked) < 2
    )
    if need_brute:
        locked_fields_set = set(dir_locked.keys())
        new_asg, new_sc, info = _brute_force_margin_permute(
            assignment, locked_fields_set, buckets=buckets)
        if new_asg is not None and new_sc > sc_after:
            assignment = new_asg
            sc_after = new_sc
            # 穷举后再做一次修正，保证边距裁剪规则有效
            assignment = _validate_and_fix_margins(
                assignment,
                assignment.get('total_w', (0,0))[0],
                assignment.get('total_h', (0,0))[0],
                dir_locked_fields=locked_fields_set)
            sc_after = _score_assignment_consistency(assignment)
            logger.info(f"[Step6.7穷举] ✅ 采用改进方案: {info}")
            logger.info(f"[Step6.7穷举后] 边距: "
                        f"上{assignment.get('margin_top',(0,0))[0]:.1f}"
                        f"下{assignment.get('margin_bottom',(0,0))[0]:.1f}"
                        f"左{assignment.get('margin_left',(0,0))[0]:.1f}"
                        f"右{assignment.get('margin_right',(0,0))[0]:.1f} sc={sc_after:.3f}")
        else:
            logger.info(f"[Step6.7穷举] 无改进 ({info})")
    else:
        logger.info(f"[Step6.7穷举] 跳过(sc={sc_after:.3f}≥0.9且边距完整，锁定{len(dir_locked)}项→主路径0额外开销)")

    logger.info(f"[Step7终态] 赋值: "
                f"total={assignment.get('total_w',(0,0))[0]:.1f}x{assignment.get('total_h',(0,0))[0]:.1f} "
                f"inner={assignment.get('inner_w',(0,0))[0]:.1f}x{assignment.get('inner_h',(0,0))[0]:.1f} "
                f"边距上{assignment.get('margin_top',(0,0))[0]:.1f}下{assignment.get('margin_bottom',(0,0))[0]:.1f}"
                f"左{assignment.get('margin_left',(0,0))[0]:.1f}右{assignment.get('margin_right',(0,0))[0]:.1f}")

    return {
        'success': True,
        'message': f'7步法识别成功（自洽sc={sc_after:.2f}）',
        'outer_w': assignment.get('total_w', (0, 0))[0],
        'outer_h': assignment.get('total_h', (0, 0))[0],
        'inner_w': assignment.get('inner_w', (0, 0))[0],
        'inner_h': assignment.get('inner_h', (0, 0))[0],
        'margin_top': assignment.get('margin_top', (0, 0))[0],
        'margin_bottom': assignment.get('margin_bottom', (0, 0))[0],
        'margin_left': assignment.get('margin_left', (0, 0))[0],
        'margin_right': assignment.get('margin_right', (0, 0))[0],
        'outer_rect_px': (ox, oy, ow, oh),
        'inner_rect_px': (ix, iy, iw, ih),
        'direction_labels': {k: (v[0], v[1]) for k, v in dir_locked.items()},
        'ocr_values': ocr_raw,
        'method': f'7step_v7(sc={sc_after:.2f})',
        'debug_assignment': assignment,
        'self_consistency': sc_after,
    }


def parse_sketch(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
    progress_callback=None,
) -> SketchParseResult:
    """解析尺寸草图（严格7步法），永不抛异常。"""
    def _progress(pct, msg):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                logger.debug("[_progress] 忽略异常", exc_info=True)
                pass

    result = SketchParseResult(method="7step_v7")

    # [F3 修复] 校验提前：在调用 cv2 全量解码之前，先做头信息校验
    # （存在性/格式/文件大小/像素上限）。超大或坏图在解码前即被拦截，
    # 避免 cv2.imread 一次性把整张图读进内存造成 OOM/卡死。
    _progress(5, "校验文件...")
    ok, reason = validate_sketch_file(image_path)
    if not ok:
        result.message = reason
        return result

    # ===== [多洞扩展 2026-08-29] 入口分流 =====
    # 原则：先快速尝试多洞路径（_classify_hole_layout <5ms），
    # 若草图判定为多洞 → 走 9 步多洞解析；
    # 若草图为单洞或多洞失败（_fallback_to_single_hole=True）→ 继续执行
    #   原有的 7 步单洞解析，零修改、零干扰。
    # 这段代码是 PURE ADD-ON，不触碰任何后续原有逻辑体。
    try:
        from .sketch_parser_multihole import try_parse_multi_hole
        _progress(7, "检测多洞布局...")
        mhr = try_parse_multi_hole(
            image_path,
            target_outer_w_cm=target_outer_w_cm,
            target_outer_h_cm=target_outer_h_cm,
            progress_callback=progress_callback,
        )
        if mhr.get('success'):
            # 多洞识别成功 → 填充扩展结果立即返回
            result.success = True
            result.message = mhr.get('message', '多洞识别成功')
            result.method = mhr.get('method', 'multihole_v1')
            result.outer_w_cm = mhr.get('outer_w', 0.0)
            result.outer_h_cm = mhr.get('outer_h', 0.0)
            # 兼容字段：第一个洞的尺寸（下游代码不变即可读到"内框尺寸"）
            result.inner_w_cm = mhr.get('inner_w', 0.0)
            result.inner_h_cm = mhr.get('inner_h', 0.0)
            result.margin_top_cm = mhr.get('margin_top', 0.0)
            result.margin_bottom_cm = mhr.get('margin_bottom', 0.0)
            result.margin_left_cm = mhr.get('margin_left', 0.0)
            result.margin_right_cm = mhr.get('margin_right', 0.0)
            # 多洞扩展字段
            result.is_multi_hole = True
            result.layout_type = mhr.get('layout', 'horizontal')
            result.holes = mhr.get('holes', [])
            result.hole_gaps_cm = mhr.get('gaps', [])
            result.inner_rects_px = mhr.get('inner_rects_px', [])
            # 调试信息（兼容旧键名 + 新增多洞键）
            result.debug['outer_rect_px'] = mhr.get('outer_rect_px')
            result.debug['inner_rects_px'] = mhr.get('inner_rects_px', [])
            result.debug['direction_margins'] = mhr.get('direction_labels', {})
            result.debug['self_consistency'] = mhr.get('self_consistency', 0)
            result.debug['is_multi_hole'] = True
            result.debug['layout'] = mhr.get('layout', '')
            result.debug['hole_gaps'] = mhr.get('gaps', [])
            result.debug['step9_assignment'] = {
                k: (round(v[0], 2), round(v[1], 3))
                for k, v in mhr.get('debug_assignment', {}).items()
            }
            logger.info(f"[parse_sketch] 多洞路径成功: layout={result.layout_type} "
                        f"outer={result.outer_w_cm:.1f}x{result.outer_h_cm:.1f} "
                        f"holes={len(result.holes)}")
            return result
        else:
            # 非多洞布局 → 静默回退到单洞 7 步法（不打日志打扰用户）
            if not mhr.get('_fallback_to_single_hole', True):
                # 罕见：多洞路径识别为多洞但解析失败 → 失败结果直接返回
                result.message = mhr.get('message', '多洞识别失败')
                result.debug['fail_reason'] = mhr.get('message', '')
                return result
            # 否则（quick_check 判定非多洞，或异常 → fallback=True）→ 继续单洞
    except Exception as e:
        # 多洞扩展层异常 → 绝不影响原有单洞路径
        logger.warning(f"[多洞扩展] 入口分流异常（继续单洞路径）: {e}")

    _progress(10, "加载图片...")
    cv2 = _safe_import_cv2()
    if cv2 is None:
        result.message = "未安装 OpenCV"
        return result
    img, err = _load_image(image_path)
    if err:
        result.message = err
        return result
    gray = _to_gray(img)

    cached = _get_cached_result(image_path, target_outer_w_cm, target_outer_h_cm)
    if cached is not None:
        return cached
    # 只有在没有目标尺寸时才使用一致缓存（目标尺寸不同时需要重新解析）
    if target_outer_w_cm <= 0 and target_outer_h_cm <= 0:
        consistent = _get_consistent_cached_result(image_path)
        if consistent is not None:
            _store_cached_result(image_path, target_outer_w_cm, target_outer_h_cm, consistent)
            return consistent

    # 注：文件合法性已在函数开头（解码前）通过 validate_sketch_file 校验，
    # 此处不再重复校验，避免对超大/坏图做无意义的解码。
    _progress(15, "7步法识别中...")
    tesseract = _safe_import_tesseract()

    # Phase1改进：颜色通道增强（仅作为OCR附加变体，不替换原图）
    # 耗时 < 30ms，仅在彩色图有红笔时有效；无红字直接返回gray=无额外开销
    enhanced_gray = _enhance_colored_ink(cv2, img)

    # [F6 修复] 真实超时：单次 OCR 调用由 image_to_data(timeout=...) 兜底，
    # 此处给整个 7 步法设置总 deadline，OCR 等耗时步骤之间提前退出。
    import time as _time
    deadline = _time.monotonic() + _PARSE_TIMEOUT_SEC

    try:
        geo = _7step_parse(cv2, gray, img, tesseract,
                           target_outer_w_cm=target_outer_w_cm,
                           target_outer_h_cm=target_outer_h_cm,
                           enhanced_gray=enhanced_gray,
                           deadline=deadline)
    except Exception as e:
        logger.exception(f"[sketch_parser] 7步法异常: {e}")
        result.message = f"识别异常: {e}"
        return result

    if not geo.get('success'):
        result.message = geo.get('message', '识别失败')
        result.debug['fail_reason'] = geo.get('message', '')
        return result

    result.success = True
    result.message = geo.get('message', '识别成功')
    result.method = geo.get('method', '7step_v7')
    result.outer_w_cm = geo.get('outer_w', 0)
    result.outer_h_cm = geo.get('outer_h', 0)
    result.inner_w_cm = geo.get('inner_w', 0)
    result.inner_h_cm = geo.get('inner_h', 0)
    result.margin_top_cm = geo.get('margin_top', 0)
    result.margin_bottom_cm = geo.get('margin_bottom', 0)
    result.margin_left_cm = geo.get('margin_left', 0)
    result.margin_right_cm = geo.get('margin_right', 0)
    result.debug['outer_rect_px'] = geo.get('outer_rect_px')
    result.debug['inner_rect_px'] = geo.get('inner_rect_px')
    result.debug['direction_margins'] = geo.get('direction_labels', {})
    result.debug['self_consistency'] = geo.get('self_consistency', 0)
    result.debug['step7_assignment'] = {k: (round(v[0], 2), round(v[1], 3))
                                         for k, v in geo.get('debug_assignment', {}).items()}

    # 完全自洽 → 存自洽缓存
    sc = geo.get('self_consistency', 0)
    if sc >= 0.98 and all([
        result.outer_w_cm > 0, result.outer_h_cm > 0,
        result.inner_w_cm > 0, result.inner_h_cm > 0,
        result.margin_top_cm > 0, result.margin_bottom_cm > 0,
        result.margin_left_cm > 0, result.margin_right_cm > 0,
    ]):
        _store_consistent_cached_result(image_path, result)
    _store_cached_result(image_path, target_outer_w_cm, target_outer_h_cm, result)

    _progress(100, "识别完成")
    return result


def _assess_complexity(gray_img):
    """兼容占位（7步法已内置鲁棒矩形检测，不需要复杂度跳过）。"""
    return False, ""


def _find_two_nested_rectangles(cv2, gray, img=None):
    """兼容占位：转调 find_all + select_best。"""
    rects = _find_all_rectangles(cv2, gray, img)
    o, i = _select_best_nested_pair(rects)
    result = []
    if o:
        result.append(o)
    if i:
        result.append(i)
    return result


def _estimate_inner_from_outer(*a, **kw):
    return None


def _detect_direction_labels_by_template(*a, **kw):
    return []


def _detect_direction_labels_by_ocr(*a, **kw):
    return []


def _detect_margins_by_geometry_ocr(*a, **kw):
    return {}


def _assign_margins_by_spatial_reasoning(*a, **kw):
    return {}


def _focused_ocr_for_direction_label(*a, **kw):
    return None


def _is_label_position_strict(*a, **kw):
    return True


def _find_and_read_numbers(*a, **kw):
    return {}


def _scan_gap_for_value(*a, **kw):
    return 0, 0, ''


def _detect_dir_labels_separate_pass(*a, **kw):
    return {}


