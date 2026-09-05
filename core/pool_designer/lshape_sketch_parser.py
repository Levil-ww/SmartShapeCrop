"""L 形草图解析器（水池设计器用）—— 两矩形减法推断法 + 几何驱动标签归属。

与矩形嵌套解析器（sketch_parser.py）相互独立：本模块只处理 L 形（挖角）草图。

识别流程：
  1. 轮廓检测：找最大连通域 → approxPolyDP 取顶点 → 判定是否为 L 形（>=5 顶点且有 1 个凹角）
  2. 几何推断：由凹角顶点 + 两个相邻轴对齐顶点，推出
       - 挖角位置 corner (tl/tr/bl/br)
       - 挖角像素尺寸 cut_w_px / cut_h_px
       - 外接矩形像素尺寸 outer_w_px / outer_h_px

  3. V2 凹角筛选（精准识别核心）：
       - 硬约束 1：cut 比例合理 [0.03, 0.75]（剔除伪凹角）
       - 硬约束 2：距离 bbox 四角之一 < 对角线 × 40%（剔除离群凹角）
       - 增强评分：base_score × proximity_factor × ratio_factor
       - 多候选时加 balance_factor（惩罚一侧极长的伪凹角，≥2 个候选才启用）
  4. 多尺度 OCR：复用 sketch_parser_vision 的全局数字扫描，提取所有数值及坐标
  5. 几何驱动标签归属：把每个数值按"最近边 + 凹角分割"归入 A/B/C/D/E/F 角色，
     完全不依赖字母 OCR（字母识别只做辅助校验）
  6. 结构自洽 & 几何兜底：校验 A==C+D / B==F+E；缺失值用像素比例反推
  7. 输出 LSketchParseResult

公开函数 parse_lshape_sketch(...) 永不抛异常。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 复用既有解析器的健壮基础设施（文件校验 / OCR 辅助 / 图像加载），避免重复实现
from .sketch_parser_base import (_PARSE_TIMEOUT_SEC, _normalize_ocr_text, validate_sketch_file)
from .sketch_parser_vision import (
    _enhance_colored_ink,
    _load_image,
    _make_preprocess_variants,
    _multi_scale_ocr_scan,
    _safe_import_cv2,
    _safe_import_tesseract,
    _to_gray,
)

_ALGO_VERSION = 2  # 2026-09-05: V2 精准识别——bbox 角约束 + cut 比例筛选 + balance disambiguation


@dataclass
class LSketchParseResult:
    """L 形草图解析结果。"""
    success: bool = False
    message: str = ""
    method: str = ""
    corner: str = ""                       # tl / tr / bl / br
    outer_w_cm: float = 0.0                # 总宽（B）
    outer_h_cm: float = 0.0                # 总高（A）
    cut_w_cm: float = 0.0                  # 挖角宽度（E，沿边的水平方向）
    cut_h_cm: float = 0.0                  # 挖角高度（D，沿边的垂直方向）
    # 结构尺寸（冗余，便于校验 / 调试）
    top_w_cm: float = 0.0                  # F（长边那一段）
    right_h_cm: float = 0.0                # C（短边那一段）
    notch_w_cm: float = 0.0                # E
    notch_h_cm: float = 0.0                # D
    self_consistency: float = 0.0          # 0~1 结构自洽度
    debug: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 几何检测：找 L 形轮廓，推断 corner / 像素尺寸
# ---------------------------------------------------------------------------

def _run_len(verts, idx, step):
    """从顶点 idx 出发、沿 step 方向（±1）保持方向一致（夹角<30°）的直行长度。

    用于区分「真实缺口角」（一侧是整段长边，数百 px）与
    「文字伪凹角」（两侧都只有字形尺度，10~90px）。
    """
    n = len(verts)
    total = 0.0
    d0 = verts[(idx + step) % n] - verts[idx]
    l0 = np.hypot(d0[0], d0[1])
    if l0 < 1e-6:
        return 0.0
    d0 = d0 / l0
    j = idx
    for _ in range(n):
        j2 = (j + step) % n
        e = verts[j2] - verts[j]
        le = np.hypot(e[0], e[1])
        if le < 1e-6:
            break
        d = e / le
        if float(d[0] * d0[0] + d[1] * d0[1]) < np.cos(np.radians(30)):
            break
        total += le
        j = j2
    return total


def _detect_lshape_geometry(cv2, gray):
    """检测 L 形几何。返回 dict 或 None（不是 L 形 / 检测失败）。

    返回字段：corner, cut_w_px, cut_h_px, outer_w_px, outer_h_px,
              concave(x,y), bbox(minx,miny,maxx,maxy), verts[...], n_verts
    """
    h, w = gray.shape[:2]

    # 多策略二值化（覆盖不同草图风格），选"最大轮廓面积"最优的一张。
    # 注：不能用全局 MORPH_OPEN 细 L 形笔画只有 3-5px，会被直接腐蚀掉。
    masks = []
    try:
        _, m = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        masks.append(m)
    except Exception:
        logger.debug("[lshape] Otsu 二值化失败", exc_info=True)
    try:
        m = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 5)
        masks.append(m)
    except Exception:
        logger.debug("[lshape] 自适应二值化失败", exc_info=True)

    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

    best = None
    for m in masks:
        try:
            mm = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k3, iterations=2)
            cnts, _ = cv2.findContours(mm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        except Exception:
            logger.debug("[lshape] findContours 失败", exc_info=True)
            continue
        if not cnts:
            continue
        cnt = max(cnts, key=cv2.contourArea)
        if best is None or cv2.contourArea(cnt) > best[0]:
            best = (cv2.contourArea(cnt), cnt)

    if best is None:
        return None
    area, cnt = best
    if area < w * h * 0.02:
        # 面积过小，排除噪点
        return None

    # ------------------------------------------------------------------
    # 多边形简化 + 凹角评分（V2：bbox 角约束 + cut 比例合理性）
    # ------------------------------------------------------------------
    # V1 纯评分：score = cn * (r1 + r2) —— 真实凹角角度尖锐且一侧有长直段
    # V2 升级（sketch2 根因修复）：
    #   真实 L 形挖角凹角有两个硬特征：
    #   (a) cut_px / bbox 对应边长 ∈ [0.03, 0.75]（过小=伪角/过大=斜边）
    #   (b) 距离 bbox 四角之一 < 对角线 × 40%（覆盖大 cut 退角场景）
    #   伪凹角（数字粘连等造成）cut_ratio<0.01 或远离 bbox 角
    #   多候选时额外用 balance_factor 惩罚一侧极长的伪凹角
    # ------------------------------------------------------------------
    peri = cv2.arcLength(cnt, True)
    eps_range = (0.0012, 0.0015, 0.002, 0.003, 0.005, 0.008, 0.012, 0.02, 0.03)

    def _signed_area(v):
        a = 0.0
        n2 = len(v)
        for i in range(n2):
            x1, y1 = v[i]
            x2, y2 = v[(i + 1) % n2]
            a += x1 * y2 - x2 * y1
        return a / 2.0

    _tier1 = []  # 5~8 verts + 1 凹角（首选）
    _tier2 = []  # 5~10 verts + 1~2 凹角（次选）
    _tier3 = []  # 兜底

    # 预计算整个轮廓的整体 bbox（跨所有 epsilon 稳定）
    cnt_pts = cnt.reshape(-1, 2)
    global_minx, global_miny = cnt_pts[:, 0].min(), cnt_pts[:, 1].min()
    global_maxx, global_maxy = cnt_pts[:, 0].max(), cnt_pts[:, 1].max()
    g_outer_w = float(global_maxx - global_minx)
    g_outer_h = float(global_maxy - global_miny)
    g_diag = float(np.hypot(g_outer_w, g_outer_h))
    g_corners = [
        (float(global_minx), float(global_miny)),   # tl
        (float(global_maxx), float(global_miny)),   # tr
        (float(global_minx), float(global_maxy)),   # bl
        (float(global_maxx), float(global_maxy)),   # br
    ]

    for eps_f in eps_range:
        eps = max(0.5, eps_f * peri)
        approx = cv2.approxPolyDP(cnt, eps, True)
        v = approx.reshape(-1, 2)
        n = len(v)
        if n < 5 or n > 20:
            continue

        sa = _signed_area(v)
        sa_sign = 1 if sa > 0 else -1
        raw_reflex = []  # [(idx, cn, r1, r2, p_prev, p_next)]
        for i in range(n):
            a_vec = v[i] - v[(i - 1) % n]
            b_vec = v[(i + 1) % n] - v[i]
            cross = a_vec[0] * b_vec[1] - a_vec[1] * b_vec[0]
            csign = 1 if cross > 0 else -1
            if csign != sa_sign:
                cn = abs(cross) / (np.hypot(a_vec[0], a_vec[1]) * np.hypot(b_vec[0], b_vec[1]) + 1e-6)
                r1 = _run_len(v, i, -1)
                r2 = _run_len(v, i, +1)
                raw_reflex.append((i, cn, r1, r2,
                                   v[(i - 1) % n].astype(float),
                                   v[(i + 1) % n].astype(float)))

        # —— V2 硬约束：bbox 退角搜索区域 + cut 比例合理性 ——
        # (a) cut 比例合理性：水平或垂直至少一个在 [0.03, 0.75]
        #     下限 0.03 排除纯噪点/文字粘连伪凹角（通常 ratio<0.01）
        #     上限 0.75 覆盖真实大 L 形挖角（如 sketch3 cut_h_ratio=0.68）
        # (b) bbox 退角搜索区域：凹角在 bbox 四角各 40% 对角线范围内
        #     真实挖角凹角不在 bbox 物理角上，而是往里退 cut_w/cut_h 的位置
        #     所以搜索区域要够大（40% diag），但仍能排除中部/边缘的伪凹角
        filtered_reflex = []
        for idx, cn, r1, r2, pv, nv in raw_reflex:
            pt = v[idx].astype(float)
            cut_w_est = max(abs(pv[0] - pt[0]), abs(nv[0] - pt[0]))
            cut_h_est = max(abs(pv[1] - pt[1]), abs(nv[1] - pt[1]))

            cut_w_ratio = cut_w_est / g_outer_w if g_outer_w > 0 else 0
            cut_h_ratio = cut_h_est / g_outer_h if g_outer_h > 0 else 0
            ratio_ok = (0.03 <= cut_w_ratio <= 0.75) or (0.03 <= cut_h_ratio <= 0.75)
            if not ratio_ok:
                continue

            min_dist = min(
                np.hypot(pt[0] - cx, pt[1] - cy) for cx, cy in g_corners)
            dist_ratio = min_dist / g_diag if g_diag > 0 else 99.0
            # 真实凹角离最近 bbox 角的距离最多到 40% 对角线
            # （因为 cut 退角的凹角不在物理角上）
            if dist_ratio >= 0.40:
                continue

            # 通过筛选 → 计算增强评分
            base_score = cn * (r1 + r2)

            # proximity_factor：越靠近 bbox 角评分越高
            if dist_ratio < 0.08:
                pf = 1.0
            elif dist_ratio < 0.20:
                pf = 0.9
            else:  # < 0.40
                pf = 0.7

            # ratio_factor：cut 比例在中间区间最合理，两端衰减
            best_ratio = min(cut_w_ratio, cut_h_ratio) if min(cut_w_ratio, cut_h_ratio) > 0 else max(cut_w_ratio, cut_h_ratio)
            if 0.10 <= best_ratio <= 0.50:
                rf = 1.0
            elif 0.03 <= best_ratio < 0.10 or 0.50 < best_ratio <= 0.70:
                rf = 0.8
            else:
                rf = 0.6

            adj_score = base_score * pf * rf
            # balance_factor 延迟应用：只在同 epsilon 下有 ≥2 个候选时
            # 才用它来 disambiguation（见下方 post-processing）
            filtered_reflex.append((adj_score, idx, r1, r2))

        if not filtered_reflex:
            continue

        # —— post-processing: 多候选才用 balance_factor 排序 ——
        n_cand = len(filtered_reflex)
        if n_cand >= 2:
            def _scored(entry):
                s, idx, r1, r2 = entry
                bal = min(r1, r2) / max(r1, r2) if max(r1, r2) > 0 else 0.0
                # 温和惩罚：最低不降过 60%（避免打死极短 cut 的真实凹角）
                bf = 0.6 + 0.4 * bal
                return (s * bf, idx)
            final_reflex = [_scored(e) for e in filtered_reflex]
        else:
            final_reflex = [(s, idx) for s, idx, _, _ in filtered_reflex]

        best_score, best_idx = max(final_reflex, key=lambda t: t[0])
        n_conc = len(final_reflex)
        # entry 存最终排好序的 (score, idx) 列表 + best_score
        entry = (best_score, v, final_reflex)

        if 5 <= n <= 8 and n_conc == 1:
            _tier1.append(entry)
        elif 5 <= n <= 10 and n_conc <= 2:
            _tier2.append(entry)
        else:
            _tier3.append(entry)

    chosen_list = _tier1 or _tier2 or _tier3
    if not chosen_list:
        return None
    chosen_list.sort(key=lambda t: -t[0])
    _, verts, reflex = chosen_list[0]
    n = len(verts)
    _, concave_idx = max(reflex, key=lambda t: t[0])
    conc = verts[concave_idx]
    p_prev = verts[(concave_idx - 1) % n]
    p_next = verts[(concave_idx + 1) % n]

    # 两个相邻顶点中，一个沿水平轴（dx 主导）、一个沿垂直轴（dy 主导）
    dx1, dy1 = float(p_prev[0] - conc[0]), float(p_prev[1] - conc[1])
    dx2, dy2 = float(p_next[0] - conc[0]), float(p_next[1] - conc[1])
    if abs(dx1) >= abs(dy1):
        h_nbr, v_nbr = (dx1, dy1), (dx2, dy2)
    else:
        h_nbr, v_nbr = (dx2, dy2), (dx1, dy1)

    sx = 1 if h_nbr[0] > 0 else -1        # 水平方向：+1=右, -1=左
    sy = 1 if v_nbr[1] > 0 else -1        # 垂直方向：+1=下(图像y增大), -1=上
    _CORNER_MAP = {(1, -1): 'tr', (1, 1): 'br', (-1, -1): 'tl', (-1, 1): 'bl'}
    corner = _CORNER_MAP.get((sx, sy))
    if corner is None:
        return None

    cut_w_px = abs(h_nbr[0])
    cut_h_px = abs(v_nbr[1])

    xs = verts[:, 0]
    ys = verts[:, 1]
    minx, maxx = int(xs.min()), int(xs.max())
    miny, maxy = int(ys.min()), int(ys.max())

    return {
        'corner': corner,
        'cut_w_px': float(cut_w_px),
        'cut_h_px': float(cut_h_px),
        'outer_w_px': float(maxx - minx),
        'outer_h_px': float(maxy - miny),
        'concave': (int(conc[0]), int(conc[1])),
        'bbox': (minx, miny, maxx, maxy),
        'verts': [(int(x), int(y)) for x, y in verts],
        'n_verts': n,
    }


# ---------------------------------------------------------------------------
# 几何驱动标签归属：把 OCR 数值按位置归入 A/B/C/D/E/F
# ---------------------------------------------------------------------------

def _assign_labels_by_geometry(geo, ocr_numbers):
    """把 OCR 数值按"最近边 + 凹角分割"归属到角色。

    返回一个 dict：role -> value（role ∈ {A,B,C,D,E,F}）。
    不依赖任何字母 OCR，完全由几何位置决定。
    """
    corner = geo['corner']
    minx, miny, maxx, maxy = geo['bbox']
    cx, cy = geo['concave']

    cut_right = corner in ('tr', 'br')      # 凹角在右侧 → 垂直切边是"右"
    cut_top = corner in ('tr', 'tl')        # 凹角在上侧 → 水平切边是"上"

    # 把每个数值归到 4 条边之一（最近边）
    buckets = {'top': [], 'bottom': [], 'left': [], 'right': []}
    for val, conf, bbox in ocr_numbers:
        bx, by, bw, bh = bbox
        nx, ny = bx + bw / 2.0, by + bh / 2.0
        d_top = ny - miny
        d_bottom = maxy - ny
        d_left = nx - minx
        d_right = maxx - nx
        dmin = min(d_top, d_bottom, d_left, d_right)
        item = (val, nx, ny, dmin, conf)
        if dmin == d_top:
            buckets['top'].append(item)
        elif dmin == d_bottom:
            buckets['bottom'].append(item)
        elif dmin == d_left:
            buckets['left'].append(item)
        elif dmin == d_right:
            buckets['right'].append(item)

    def _best(items):
        """边的多个候选中取：靠近边中点且置信度高的一个。"""
        if not items:
            return None
        # 以 dmin 小（贴边）为主，conf 为辅
        items_sorted = sorted(items, key=lambda it: (it[3], -it[4]))
        # 若数值差异很大且都贴边，取置信度最高的
        vals = [it[0] for it in items]
        if max(vals) - min(vals) > max(vals) * 0.5:
            items_sorted = sorted(items, key=lambda it: -it[4])
        return items_sorted[0][0]

    # 满边（单数值 = 外框总尺寸）：与凹角不相邻的两条边
    #   水平满边：cut_top 时为 bottom，否则为 top
    #   垂直满边：cut_right 时为 left，否则为 right
    full_h_edge = 'bottom' if cut_top else 'top'
    full_v_edge = 'left' if cut_right else 'right'
    cut_h_edge = 'top' if cut_top else 'bottom'
    cut_v_edge = 'right' if cut_right else 'left'

    outer_w = _best(buckets[full_h_edge])
    outer_h = _best(buckets[full_v_edge])

    # 切边上的两个数值：靠近凹角一侧 = 挖角尺寸（E / D），另一侧 = F / C
    cut_h_items = buckets[cut_h_edge]
    cut_v_items = buckets[cut_v_edge]

    def _split_cut(items, is_near, proximity):
        """从切边候选中分出「靠近凹角」与「远离凹角」的数值。

        同一侧若有多个候选（如多尺度 OCR 重复或邻近文字误入），
        取**离凹角最近**的那个——缺口尺寸标注总是紧贴缺口绘制，
        比单纯按置信度更符合真实草图语义。

        [Fix 2026-09-02] 多候选改进：
          - 先做位置重叠去重：若两候选中心距离 < 15px 且数值相同（5%内），
            保留高置信度的（过滤 Tesseract 重复识别）
          - 每侧按 proximity 升序排，取离凹角最近的一个
        """
        # 去重：先按置信度降序排，再逐个判断是否与已有项位置重叠
        # 两种重叠都要处理：
        #   1. 同位置+同数值 → 保留一个（多尺度 OCR 重复）
        #   2. 同位置+不同数值 → 高置信度优先（Tesseract 同一文字误识别）
        sorted_items = sorted(items, key=lambda x: -x[4])  # conf desc
        deduped = []
        for val, nx, ny, dmin, conf in sorted_items:
            is_dup = False
            for i, (ev, ex, ey, _, ec) in enumerate(deduped):
                dx, dy = abs(nx - ex), abs(ny - ey)
                if dx < 20 and dy < 20:
                    # 位置极近 → 保留已有的（置信度更高，因为 sorted）
                    is_dup = True
                    break
            if not is_dup:
                deduped.append((val, nx, ny, dmin, conf))

        near_list, far_list = [], []
        for val, nx, ny, dmin, conf in deduped:
            prox = proximity(nx, ny)
            if is_near(nx, ny):
                near_list.append((prox, -conf, val))
            else:
                far_list.append((prox, -conf, val))

        # 每侧先按 proximity 升序，再按 -conf 升序（离凹角近优先，同距离高置信度优先）
        near_list.sort()
        far_list.sort()
        near_val = near_list[0][2] if near_list else None
        far_val = far_list[0][2] if far_list else None
        return near_val, far_val

    # [Fix 2026-09-02] 边界用 >= / <= 防止标注恰好在凹角坐标时被误分
    #   例：tr 角 cy=66，标注 ny=66 描述 cut_h → 应归 near 侧
    cut_w, top_w = _split_cut(
        cut_h_items,
        is_near=(lambda nx, ny: (nx >= cx) if cut_right else (nx <= cx)),
        proximity=(lambda nx, ny: abs(nx - cx)))
    cut_h, right_h = _split_cut(
        cut_v_items,
        is_near=(lambda nx, ny: (ny <= cy) if cut_top else (ny >= cy)),
        proximity=(lambda nx, ny: abs(ny - cy)))

    roles = {}
    if outer_w is not None:
        roles['B'] = outer_w
    if outer_h is not None:
        roles['A'] = outer_h
    if cut_w is not None:
        roles['E'] = cut_w
    if cut_h is not None:
        roles['D'] = cut_h
    if top_w is not None:
        roles['F'] = top_w
    if right_h is not None:
        roles['C'] = right_h
    return roles


# ---------------------------------------------------------------------------
# 结构自洽 & 几何兜底
# ---------------------------------------------------------------------------

def _resolve_dimensions(geo, roles):
    """结合标签角色与像素比例，解出 4 个独立尺寸 + 结构尺寸。

    返回 dict：outer_w_cm, outer_h_cm, cut_w_cm, cut_h_cm,
               top_w_cm(F), right_h_cm(C), notch_w_cm(E), notch_h_cm(D)
    """
    px_w = geo['outer_w_px']
    px_h = geo['outer_h_px']
    px_cw = geo['cut_w_px']
    px_ch = geo['cut_h_px']

    # 像素比例（当 OCR 缺失某值时用于反推）
    ratio_cw = (px_cw / px_w) if px_w > 0 else 0.0
    ratio_ch = (px_ch / px_h) if px_h > 0 else 0.0

    A = roles.get('A')
    B = roles.get('B')
    C = roles.get('C')
    D = roles.get('D')
    E = roles.get('E')
    F = roles.get('F')

    outer_w = B
    outer_h = A
    cut_w = E
    cut_h = D

    # —— 兜底 1：缺失外框尺寸，用切边两段之和 ——
    if outer_w is None and E is not None and F is not None:
        outer_w = E + F
    if outer_h is None and D is not None and C is not None:
        outer_h = D + C

    # —— 兜底 2：缺失挖角尺寸，用像素比例反推 ——
    if cut_w is None and outer_w and ratio_cw > 0:
        cut_w = outer_w * ratio_cw
    if cut_h is None and outer_h and ratio_ch > 0:
        cut_h = outer_h * ratio_ch

    # —— 兜底 3：缺失 F / C，用外框 - 挖角反推 ——
    if F is None and outer_w and cut_w is not None:
        F = outer_w - cut_w
    if C is None and outer_h and cut_h is not None:
        C = outer_h - cut_h

    return {
        'outer_w_cm': float(outer_w) if outer_w else 0.0,
        'outer_h_cm': float(outer_h) if outer_h else 0.0,
        'cut_w_cm': float(cut_w) if cut_w else 0.0,
        'cut_h_cm': float(cut_h) if cut_h else 0.0,
        'top_w_cm': float(F) if F else 0.0,
        'right_h_cm': float(C) if C else 0.0,
        'notch_w_cm': float(E) if E else 0.0,
        'notch_h_cm': float(D) if D else 0.0,
    }


def _score_consistency(geo, dims):
    """评估结构自洽度（0~1）：A==C+D、B==F+E、像素比例与 cm 比例一致。"""
    checks = []
    A, B = dims['outer_h_cm'], dims['outer_w_cm']
    C, D = dims['right_h_cm'], dims['notch_h_cm']
    E, F = dims['notch_w_cm'], dims['top_w_cm']
    px_w, px_h, px_cw, px_ch = (
        geo['outer_w_px'], geo['outer_h_px'], geo['cut_w_px'], geo['cut_h_px'])

    # A == C + D
    if A > 0 and (C > 0 or D > 0):
        checks.append(abs(A - (C + D)) <= max(1.0, A * 0.05))
    # B == F + E
    if B > 0 and (F > 0 or E > 0):
        checks.append(abs(B - (F + E)) <= max(1.0, B * 0.05))
    # 像素比例 ≈ cm 比例（cut_w / outer_w）
    if B > 0 and px_w > 0 and px_cw > 0:
        r_px = px_cw / px_w
        r_cm = dims['cut_w_cm'] / B if dims['cut_w_cm'] > 0 else None
        if r_cm is not None:
            checks.append(abs(r_px - r_cm) <= 0.05)
    if A > 0 and px_h > 0 and px_ch > 0:
        r_px = px_ch / px_h
        r_cm = dims['cut_h_cm'] / A if dims['cut_h_cm'] > 0 else None
        if r_cm is not None:
            checks.append(abs(r_px - r_cm) <= 0.05)

    if not checks:
        return 0.5  # 无可校验项，给中性分
    return sum(1.0 for c in checks if c) / len(checks)


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def parse_lshape_sketch(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
    progress_callback=None,
) -> LSketchParseResult:
    """解析 L 形尺寸草图，永不抛异常。

    Args:
        image_path: 草图图片路径
        target_outer_w_cm / target_outer_h_cm: 可选的目标外框尺寸（来自文件名解析），
            用于二次校验 / 像素比例定标（仅当 OCR 全失败时启用）。
        progress_callback: 可选 (pct, msg) 回调

    Returns:
        LSketchParseResult
    """
    def _progress(pct, msg):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                logger.debug("[lshape] 进度回调异常", exc_info=True)

    result = LSketchParseResult(method=f"lshape_v{_ALGO_VERSION}")

    ok, reason = validate_sketch_file(image_path)
    if not ok:
        result.message = reason
        return result

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

    _progress(20, "检测 L 形轮廓...")
    geo = _detect_lshape_geometry(cv2, gray)
    if geo is None:
        result.message = (
            "未检测到 L 形轮廓（顶点<5 或为矩形）。\n"
            "请确认草图为 L 形挖角样式，或使用「矩形嵌套」模式。"
        )
        result.debug['stage'] = 'geometry'
        return result

    _progress(40, "OCR 识别尺寸数值...")
    tesseract = _safe_import_tesseract()
    ocr_numbers = []
    if tesseract is not None:
        try:
            enhanced = _enhance_colored_ink(cv2, img)
            ocr_numbers = _multi_scale_ocr_scan(
                cv2, tesseract, gray, enhanced_gray=enhanced)
        except Exception as e:
            logger.warning(f"[lshape] OCR 扫描失败（降级为纯几何）: {e}")
    else:
        logger.info("[lshape] 未安装 Tesseract，仅用几何推断")

    _progress(70, "归属标签 & 求解尺寸...")
    roles = _assign_labels_by_geometry(geo, ocr_numbers)
    dims = _resolve_dimensions(geo, roles)
    sc = _score_consistency(geo, dims)

    # 当 OCR 完全失败但有 target 尺寸时，用像素比例定标
    if dims['outer_w_cm'] <= 0 and target_outer_w_cm > 0:
        dims['outer_w_cm'] = target_outer_w_cm
    if dims['outer_h_cm'] <= 0 and target_outer_h_cm > 0:
        dims['outer_h_cm'] = target_outer_h_cm

    # 若外框尺寸已知但挖角尺寸仍缺失，用像素比例反推
    if dims['outer_w_cm'] > 0 and geo['outer_w_px'] > 0 and dims['cut_w_cm'] <= 0:
        dims['cut_w_cm'] = dims['outer_w_cm'] * (geo['cut_w_px'] / geo['outer_w_px'])
    if dims['outer_h_cm'] > 0 and geo['outer_h_px'] > 0 and dims['cut_h_cm'] <= 0:
        dims['cut_h_cm'] = dims['outer_h_cm'] * (geo['cut_h_px'] / geo['outer_h_px'])

    # 完成度判定
    have_w = dims['outer_w_cm'] > 0
    have_h = dims['outer_h_cm'] > 0
    have_cw = dims['cut_w_cm'] > 0
    have_ch = dims['cut_h_cm'] > 0

    if not (have_w and have_h and have_cw and have_ch):
        result.message = (
            "识别到 L 形轮廓，但尺寸数值不完整"
            f"（宽{'' if have_w else '缺失'} 高{'' if have_h else '缺失'} "
            f"挖宽{'' if have_cw else '缺失'} 挖高{'' if have_ch else '缺失'}）。\n"
            "请在草图上清晰标注 6 个尺寸（A/B/C/D/E/F）后重试，或手动填写。"
        )
        # 仍把部分结果放入 debug，便于 UI 提示 / 手动修正
        result.debug.update({
            'geometry': geo,
            'roles': roles,
            'ocr_count': len(ocr_numbers),
            'partial': dims,
        })
        return result

    result.success = True
    result.message = (
        f"L 形识别成功（corner={geo['corner']}, "
        f"外框 {dims['outer_w_cm']:.1f}×{dims['outer_h_cm']:.1f}cm, "
        f"挖角 {dims['cut_w_cm']:.1f}×{dims['cut_h_cm']:.1f}cm, 自洽={sc:.2f}）"
    )
    result.method = f"lshape_v{_ALGO_VERSION}(sc={sc:.2f})"
    result.corner = geo['corner']
    result.outer_w_cm = round(dims['outer_w_cm'], 2)
    result.outer_h_cm = round(dims['outer_h_cm'], 2)
    result.cut_w_cm = round(dims['cut_w_cm'], 2)
    result.cut_h_cm = round(dims['cut_h_cm'], 2)
    result.top_w_cm = round(dims['top_w_cm'], 2)
    result.right_h_cm = round(dims['right_h_cm'], 2)
    result.notch_w_cm = round(dims['notch_w_cm'], 2)
    result.notch_h_cm = round(dims['notch_h_cm'], 2)
    result.self_consistency = round(sc, 3)
    result.debug.update({
        'geometry': geo,
        'roles': roles,
        'ocr_count': len(ocr_numbers),
        'outer_rect_px': geo['bbox'],
        'concave_px': geo['concave'],
        'cut_w_px': geo['cut_w_px'],
        'cut_h_px': geo['cut_h_px'],
        'verts': geo['verts'],
    })
    _progress(100, "识别完成")
    return result
