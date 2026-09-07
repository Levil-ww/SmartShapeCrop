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

_ALGO_VERSION = 3  # 2026-09-05: V3 cut 尺寸改用 bbox 边界距离（抗数字粘连）


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
    # 不做 MORPH_CLOSE：闭运算会把数字标注与 L 形轮廓粘连，
    # 导致伪凹角和缺口检测失效。数字标注是独立轮廓，取最大轮廓即可分离。
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

    best = None
    for m in masks:
        try:
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
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
    # 滑动窗口凹角检测（V3 新增，最高优先级）
    # ------------------------------------------------------------------
    # 直接在原始轮廓上找凹角，不依赖 approxPolyDP 的多边形简化。
    # approxPolyDP 对浅挖角（如 D=2cm）会平滑掉真实凹角，
    # 滑动窗口通过局部叉积计算能保留浅凹角的弯曲信号。
    # ------------------------------------------------------------------
    def _detect_concave_sliding_window(pts, outer_w, outer_h, diag, corners,
                                       minx, miny, maxx, maxy):
        n = len(pts)
        if n < 10:
            return None
        # 整体方向
        sa = 0.0
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            sa += x1 * y2 - x2 * y1
        sa_sign = 1 if sa > 0 else -1

        # bbox 面积 - 轮廓面积 = 缺失面积 ≈ cut_w * cut_h
        bbox_area = outer_w * outer_h
        contour_area = abs(sa) / 2.0
        missing_area = bbox_area - contour_area

        # 多窗口凹度
        k_values = [max(3, int(n * 0.01)), max(5, int(n * 0.02)),
                    max(8, int(n * 0.03))]
        concavity = np.zeros(n)
        for k in k_values:
            k = min(k, n // 4)
            if k < 2:
                continue
            for i in range(n):
                p_prev = pts[(i - k) % n]
                p_cur = pts[i]
                p_next = pts[(i + k) % n]
                a = p_cur - p_prev
                b = p_next - p_cur
                cross = a[0] * b[1] - a[1] * b[0]
                csign = 1 if cross > 0 else -1
                if csign != sa_sign:
                    cn = abs(cross) / (np.hypot(a[0], a[1]) * np.hypot(b[0], b[1]) + 1e-6)
                    concavity[i] += cn

        if concavity.max() < 0.1:
            return None

        # —— 几何约束筛选后选 concavity 最大 ——
        # 不直接选 concavity 最大的点（数字粘连伪凹角 concavity 更高），
        # 而是先要求 cut 比例合理（< 0.50，因为 F=B-cut_w > cut_w），
        # 再在合理候选中选 concavity 最大的。
        # 方向判定窗口：用较大的 k，让真实凹角（长直行段）的邻接 cut 接近 bbox cut
        # 伪凹角（数字笔画短）跨越拐点后邻接 cut 与 bbox cut 不一致 → cf 降低
        k_dir = max(8, int(min(outer_w, outer_h) * 0.05))
        k_dir = min(k_dir, n // 6)
        best_score = -1.0
        best_pt = None
        best_corner = None

        for i in range(n):
            if concavity[i] < 0.1:
                continue
            pt = pts[i].astype(float)

            # 位置约束：在 bbox 四角 45% 对角线内
            min_dist = min(np.hypot(pt[0] - cx, pt[1] - cy) for cx, cy in corners)
            dist_ratio = min_dist / diag if diag > 0 else 99.0
            if dist_ratio >= 0.45:
                continue

            # 用小窗口判定方向（避免跨越拐点导致方向错误）
            p_prev = pts[(i - k_dir) % n].astype(float)
            p_next = pts[(i + k_dir) % n].astype(float)
            dx1, dy1 = float(p_prev[0] - pt[0]), float(p_prev[1] - pt[1])
            dx2, dy2 = float(p_next[0] - pt[0]), float(p_next[1] - pt[1])
            if abs(dx1) >= abs(dy1):
                h_nbr, v_nbr = (dx1, dy1), (dx2, dy2)
            else:
                h_nbr, v_nbr = (dx2, dy2), (dx1, dy1)
            sx = 1 if h_nbr[0] > 0 else -1
            sy = 1 if v_nbr[1] > 0 else -1
            corner = {(1, -1): 'tr', (1, 1): 'br',
                      (-1, -1): 'tl', (-1, 1): 'bl'}.get((sx, sy))
            if corner is None:
                continue

            # 邻接 cut 尺寸（小窗口）
            adj_cut_w = max(abs(dx1), abs(dx2))
            adj_cut_h = max(abs(dy1), abs(dy2))

            # bbox 边界距离 cut 尺寸
            if corner == 'tr':
                cut_w = maxx - pt[0]
                cut_h = pt[1] - miny
            elif corner == 'tl':
                cut_w = pt[0] - minx
                cut_h = pt[1] - miny
            elif corner == 'br':
                cut_w = maxx - pt[0]
                cut_h = maxy - pt[1]
            else:
                cut_w = pt[0] - minx
                cut_h = maxy - pt[1]

            # 硬约束：cut 比例合理 + cut < 外框 75%（深挖角场景 cut_h 可达 0.68）
            cw_ratio = cut_w / outer_w if outer_w > 0 else 0
            ch_ratio = cut_h / outer_h if outer_h > 0 else 0
            if not (0.02 <= cw_ratio <= 0.75 and 0.02 <= ch_ratio <= 0.75):
                continue

            # 一致性因子：邻接 cut 与 bbox cut 应接近
            def _agr(a, b):
                m = max(a, b)
                return min(a, b) / m if m > 0 else 0.0
            cf = min(_agr(adj_cut_w, cut_w), _agr(adj_cut_h, cut_h))

            score = concavity[i] * cf
            if score > best_score:
                best_score = score
                best_pt = pt
                best_corner = corner

        if best_pt is None:
            return None

        pt = best_pt
        corner = best_corner

        # 构造 6 顶点多边形，凹角在 index 0
        if corner == 'tr':
            verts = np.array([
                [pt[0], pt[1]], [maxx, pt[1]], [maxx, maxy],
                [minx, maxy], [minx, miny], [pt[0], miny],
            ])
        elif corner == 'tl':
            verts = np.array([
                [pt[0], pt[1]], [minx, pt[1]], [minx, maxy],
                [maxx, maxy], [maxx, miny], [pt[0], miny],
            ])
        elif corner == 'br':
            verts = np.array([
                [pt[0], pt[1]], [pt[0], maxy], [minx, maxy],
                [minx, miny], [maxx, miny], [maxx, pt[1]],
            ])
        else:
            verts = np.array([
                [pt[0], pt[1]], [pt[0], maxy], [maxx, maxy],
                [maxx, miny], [minx, miny], [minx, pt[1]],
            ])

        return {
            'score': best_score * 10000.0,  # 绝对最高优先级
            'verts': verts,
            'concave_pt': pt,
            'corner': corner,
        }

    # —— V3 凸包差异法（检测缺口角和 cut 尺寸）——
    # L 形 = 矩形 - 角上小矩形。轮廓的凸包就是 bbox 矩形，
    # 凸包填充减去轮廓填充 = 缺口区域。缺口区域的 bbox 就是 cut 尺寸。
    # 对数字粘连鲁棒，因为数字被包含在轮廓内部，不影响凸包差异。
    def _detect_by_convex_hull():
        minx, miny = float(global_minx), float(global_miny)
        maxx, maxy = float(global_maxx), float(global_maxy)
        W = maxx - minx
        H = maxy - miny
        if W <= 0 or H <= 0:
            return None

        # 创建掩码
        h = int(H) + 4
        w = int(W) + 4
        shifted = (cnt_pts - np.array([minx, miny])).astype(np.int32)

        # 轮廓填充
        mask_cnt = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(mask_cnt, [shifted], 1)

        # 凸包填充
        hull = cv2.convexHull(shifted)
        mask_hull = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(mask_hull, [hull], 1)

        # 缺口 = 凸包 - 轮廓
        gap = mask_hull - mask_cnt
        if gap.sum() == 0:
            return None  # 没有缺口（矩形）

        # 缺口区域的 bbox
        ys, xs = np.where(gap > 0)
        gx0, gx1 = xs.min(), xs.max()
        gy0, gy1 = ys.min(), ys.max()
        cut_w = float(gx1 - gx0 + 1)
        cut_h = float(gy1 - gy0 + 1)

        # 比例校验（深挖角场景 cut_h 可达 0.68，上限放宽到 0.75）
        cwr = cut_w / W
        chr_ = cut_h / H
        if not (0.02 <= cwr <= 0.75 and 0.02 <= chr_ <= 0.75):
            return None

        # 判断缺口在哪个角
        cx_gap = (gx0 + gx1) / 2.0
        cy_gap = (gy0 + gy1) / 2.0
        cx_bbox = w / 2.0
        cy_bbox = h / 2.0
        if cx_gap < cx_bbox and cy_gap < cy_bbox:
            corner = 'tl'
        elif cx_gap >= cx_bbox and cy_gap < cy_bbox:
            corner = 'tr'
        elif cx_gap < cx_bbox and cy_gap >= cy_bbox:
            corner = 'bl'
        else:
            corner = 'br'

        # 凹角位置
        if corner == 'tr':
            cx, cy = maxx - cut_w, miny + cut_h
        elif corner == 'tl':
            cx, cy = minx + cut_w, miny + cut_h
        elif corner == 'br':
            cx, cy = maxx - cut_w, maxy - cut_h
        else:
            cx, cy = minx + cut_w, maxy - cut_h

        if corner == 'tr':
            verts = np.array([
                [cx, cy], [maxx, cy], [maxx, maxy],
                [minx, maxy], [minx, miny], [cx, miny],
            ])
        elif corner == 'tl':
            verts = np.array([
                [cx, cy], [minx, cy], [minx, maxy],
                [maxx, maxy], [maxx, miny], [cx, miny],
            ])
        elif corner == 'br':
            verts = np.array([
                [cx, cy], [cx, maxy], [minx, maxy],
                [minx, miny], [maxx, miny], [maxx, cy],
            ])
        else:
            verts = np.array([
                [cx, cy], [cx, maxy], [maxx, maxy],
                [maxx, miny], [minx, miny], [minx, cy],
            ])

        return {
            'score': 200000.0,
            'verts': verts,
            'concave_pt': np.array([cx, cy]),
            'corner': corner,
        }

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

    # —— V3 滑动窗口凹角检测（最高优先级）——
    # 直接在原始轮廓上找凹角，不依赖 approxPolyDP。
    # approxPolyDP 会把浅挖角（如 D=2cm）平滑掉，而滑动窗口能捕捉到。
    # 方法：对轮廓每个点 i，取前后 k 步的点，计算叉积判断凹凸性。
    # 用多个窗口大小 k 取平均凹度，提高鲁棒性。
    _sliding_result = _detect_concave_sliding_window(
        cnt_pts, g_outer_w, g_outer_h, g_diag, g_corners,
        global_minx, global_miny, global_maxx, global_maxy)
    if _sliding_result is not None:
        _tier1.append((_sliding_result['score'],
                       _sliding_result['verts'],
                       [(1.0, 0)]))
    # 凸包差异法（最高优先级）
    _hull_result = _detect_by_convex_hull()
    if _hull_result is not None:
        _tier1.append((_hull_result['score'],
                       _hull_result['verts'],
                       [(1.0, 0)]))

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

        # —— V3 硬约束 + 评分：抗数字粘连 ——
        # [V3 改进 2026-09-05]
        # 问题：数字标注与轮廓粘连 → 伪凹角的邻接直行段被拉长（r1+r2 虚高），
        #       base_score = cn*(r1+r2) 可能超过真实凹角，导致选错。
        # 方案：
        #   1. 同时计算邻接 cut（adj_cut）和 bbox 边界距离 cut（bbox_cut）
        #   2. ratio_ok 用两者的 OR（任一合理即可）—— 防止浅挖角真实凹角被误杀
        #   3. 一致性因子 cf = adj_cut 与 bbox_cut 的接近程度
        #      真实凹角：邻接直行段 = 到 bbox 边界距离 → cf ≈ 1.0
        #      伪凹角（数字粘连）：邻接直行段 >> 到 bbox 边界距离 → cf 很低
        #   4. 最终 cut 尺寸用 bbox_cut（稳定、不受数字干扰）
        filtered_reflex = []
        for idx, cn, r1, r2, pv, nv in raw_reflex:
            pt = v[idx].astype(float)

            # 邻接顶点 cut 尺寸（受数字粘连影响）
            adj_cut_w = max(abs(pv[0] - pt[0]), abs(nv[0] - pt[0]))
            adj_cut_h = max(abs(pv[1] - pt[1]), abs(nv[1] - pt[1]))

            # 由邻接顶点方向判定 corner
            dx1, dy1 = float(pv[0] - pt[0]), float(pv[1] - pt[1])
            dx2, dy2 = float(nv[0] - pt[0]), float(nv[1] - pt[1])
            if abs(dx1) >= abs(dy1):
                h_nbr, v_nbr = (dx1, dy1), (dx2, dy2)
            else:
                h_nbr, v_nbr = (dx2, dy2), (dx1, dy1)
            sx = 1 if h_nbr[0] > 0 else -1
            sy = 1 if v_nbr[1] > 0 else -1
            corner_guess = {(1, -1): 'tr', (1, 1): 'br',
                            (-1, -1): 'tl', (-1, 1): 'bl'}.get((sx, sy))
            if corner_guess is None:
                continue

            # bbox 边界距离 cut 尺寸（稳定、不受数字干扰）
            if corner_guess == 'tr':
                bbox_cut_w = global_maxx - pt[0]
                bbox_cut_h = pt[1] - global_miny
            elif corner_guess == 'tl':
                bbox_cut_w = pt[0] - global_minx
                bbox_cut_h = pt[1] - global_miny
            elif corner_guess == 'br':
                bbox_cut_w = global_maxx - pt[0]
                bbox_cut_h = global_maxy - pt[1]
            else:  # bl
                bbox_cut_w = pt[0] - global_minx
                bbox_cut_h = global_maxy - pt[1]

            # ratio_ok：邻接或 bbox 任一合理即可（保护浅挖角真实凹角）
            adj_cw_ratio = adj_cut_w / g_outer_w if g_outer_w > 0 else 0
            adj_ch_ratio = adj_cut_h / g_outer_h if g_outer_h > 0 else 0
            bbox_cw_ratio = bbox_cut_w / g_outer_w if g_outer_w > 0 else 0
            bbox_ch_ratio = bbox_cut_h / g_outer_h if g_outer_h > 0 else 0
            adj_ok = (0.03 <= adj_cw_ratio <= 0.75) or (0.03 <= adj_ch_ratio <= 0.75)
            bbox_ok = (0.03 <= bbox_cw_ratio <= 0.75) or (0.03 <= bbox_ch_ratio <= 0.75)
            if not (adj_ok or bbox_ok):
                continue

            # 位置约束：凹角在 bbox 四角 40% 对角线范围内
            min_dist = min(
                np.hypot(pt[0] - cx, pt[1] - cy) for cx, cy in g_corners)
            dist_ratio = min_dist / g_diag if g_diag > 0 else 99.0
            if dist_ratio >= 0.40:
                continue

            # —— 一致性因子 cf ——
            # 真实凹角的邻接直行长度 ≈ 到 bbox 边界距离 → cf ≈ 1.0
            # 伪凹角（数字粘连）的邻接直行长度 >> 边界距离 → cf 很低
            def _agreement(a, b):
                mx = max(a, b)
                return min(a, b) / mx if mx > 0 else 0.0
            cf = min(_agreement(adj_cut_w, bbox_cut_w),
                     _agreement(adj_cut_h, bbox_cut_h))

            # 评分：base × proximity × ratio × consistency
            base_score = cn * (r1 + r2)

            if dist_ratio < 0.08:
                pf = 1.0
            elif dist_ratio < 0.20:
                pf = 0.9
            else:
                pf = 0.7

            # 用 bbox cut 比例计算 ratio_factor（更稳定）
            best_ratio = min(bbox_cw_ratio, bbox_ch_ratio) if min(bbox_cw_ratio, bbox_ch_ratio) > 0 else max(bbox_cw_ratio, bbox_ch_ratio)
            if 0.10 <= best_ratio <= 0.50:
                rf = 1.0
            elif 0.03 <= best_ratio < 0.10 or 0.50 < best_ratio <= 0.70:
                rf = 0.8
            else:
                rf = 0.6

            adj_score = base_score * pf * rf * cf
            filtered_reflex.append((adj_score, idx, r1, r2, corner_guess))

        if not filtered_reflex:
            continue

        # —— post-processing: 多候选才用 balance_factor 排序 ——
        n_cand = len(filtered_reflex)
        if n_cand >= 2:
            def _scored(entry):
                s, idx, r1, r2, _c = entry
                bal = min(r1, r2) / max(r1, r2) if max(r1, r2) > 0 else 0.0
                bf = 0.6 + 0.4 * bal
                return (s * bf, idx)
            final_reflex = [_scored(e) for e in filtered_reflex]
        else:
            final_reflex = [(s, idx) for s, idx, _, _, _ in filtered_reflex]

        best_score, best_idx = max(final_reflex, key=lambda t: t[0])
        n_conc = len(final_reflex)
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

    # —— V3: corner + cut 尺寸统一用 bbox 边界距离 ——
    # 先用邻接顶点方向判定 corner，再用凹角到 bbox 两条相邻边的距离作为 cut 尺寸。
    # 这样 cut 尺寸不受 approxPolyDP 顶点偏差 / 数字标注粘连的影响。
    dx1, dy1 = float(p_prev[0] - conc[0]), float(p_prev[1] - conc[1])
    dx2, dy2 = float(p_next[0] - conc[0]), float(p_next[1] - conc[1])
    if abs(dx1) >= abs(dy1):
        h_nbr, v_nbr = (dx1, dy1), (dx2, dy2)
    else:
        h_nbr, v_nbr = (dx2, dy2), (dx1, dy1)

    sx = 1 if h_nbr[0] > 0 else -1
    sy = 1 if v_nbr[1] > 0 else -1
    _CORNER_MAP = {(1, -1): 'tr', (1, 1): 'br', (-1, -1): 'tl', (-1, 1): 'bl'}
    corner = _CORNER_MAP.get((sx, sy))
    if corner is None:
        return None

    xs = verts[:, 0]
    ys = verts[:, 1]
    minx, maxx = int(xs.min()), int(xs.max())
    miny, maxy = int(ys.min()), int(ys.max())

    cx, cy = float(conc[0]), float(conc[1])
    if corner == 'tr':
        cut_w_px = maxx - cx          # 凹角到右边 = E
        cut_h_px = cy - miny          # 凹角到上边 = D
    elif corner == 'tl':
        cut_w_px = cx - minx          # 凹角到左边 = E
        cut_h_px = cy - miny          # 凹角到上边 = D
    elif corner == 'br':
        cut_w_px = maxx - cx          # 凹角到右边 = E
        cut_h_px = maxy - cy          # 凹角到下边 = D
    else:  # bl
        cut_w_px = cx - minx          # 凹角到左边 = E
        cut_h_px = maxy - cy          # 凹角到下边 = D

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
    W_bbox = maxx - minx
    H_bbox = maxy - miny
    bbox_area = W_bbox * H_bbox

    cut_right = corner in ('tr', 'br')      # 凹角在右侧 → 垂直切边是"右"
    cut_top = corner in ('tr', 'tl')        # 凹角在上侧 → 水平切边是"上"

    # 把每个数值归到 4 条边之一（最近边）
    buckets = {'top': [], 'bottom': [], 'left': [], 'right': []}
    for val, conf, bbox in ocr_numbers:
        bx, by, bw, bh = bbox
        # 过滤超大框噪点（多尺度 OCR 整页识别产生的 bbox 覆盖全图）
        if bw * bh > bbox_area * 0.15:
            continue
        # 过滤过小或过大的数值（异常值）
        if val <= 0 or val > 5000:
            continue
        nx, ny = bx + bw / 2.0, by + bh / 2.0
        d_top = ny - miny
        d_bottom = maxy - ny
        d_left = nx - minx
        d_right = maxx - nx
        dmin = min(d_top, d_bottom, d_left, d_right)
        item = (val, nx, ny, dmin, conf, bw * bh)
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
        for it in sorted_items:
            val, nx, ny, dmin, conf = it[0], it[1], it[2], it[3], it[4]
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

    # —— 直接从凹角附近 OCR 提取挖角尺寸（最高优先级）——
    # 挖角尺寸(E/D)总是标注在凹角紧邻的两条切边上，直接读取比边归属更可靠。
    # 不依赖 cut_w_px/cut_h_px（几何 cut 检测可能因数字粘连而出错），
    # 也不依赖边桶归属（数字可能因位置偏差落入错误的边桶）。
    diag = np.hypot(W_bbox, H_bbox)
    near_radius = diag * 0.40  # 凹角附近 40% 对角线范围内
    # 切边容差：标注中心到切边的距离
    edge_tol = max(15.0, min(W_bbox, H_bbox) * 0.08)

    def _dedup_near(items):
        """位置去重：同位置(<25px)保留置信度高的"""
        s = sorted(items, key=lambda x: -x[4])
        out = []
        for it in s:
            v, nx, ny, _, conf = it[0], it[1], it[2], it[3], it[4]
            dup = any(abs(nx - ex) < 25 and abs(ny - ey) < 25 for _, ex, ey, _, _ in out)
            if not dup:
                out.append(it)
        return out

    e_candidates = []  # 水平切边上、靠近凹角 x 的数值 → E
    d_candidates = []  # 垂直切边上、靠近凹角 y 的数值 → D

    for it in ocr_numbers:
        val, conf, bbox = it
        bx, by, bw, bh = bbox
        if bw * bh > bbox_area * 0.15:
            continue
        if val <= 0 or val > 5000:
            continue
        nx, ny = bx + bw / 2.0, by + bh / 2.0
        dist_to_concave = np.hypot(nx - cx, ny - cy)
        if dist_to_concave > near_radius:
            continue

        # 判断是否在水平切边上
        h_cut_y = miny if cut_top else maxy
        on_h_cut = abs(ny - h_cut_y) < edge_tol
        # 判断是否在垂直切边上
        v_cut_x = maxx if cut_right else minx
        on_v_cut = abs(nx - v_cut_x) < edge_tol

        if on_h_cut:
            # 水平切边：仅凹角「切边侧」的数值 = E。
            # cut_right 时切边侧为 x>=cx；否则 x<=cx。
            # 必须按侧过滤，否则对侧的 F（长段）若离凹角更近会被误判为 E。
            e_near_side = (nx >= cx) if cut_right else (nx <= cx)
            if e_near_side:
                prox_x = abs(nx - cx)
                e_candidates.append((prox_x, -conf, val, nx, ny))
        if on_v_cut:
            # 垂直切边：仅凹角「切边侧」的数值 = D。
            # cut_top 时切边侧为 y<=cy；否则 y>=cy。
            # 必须按侧过滤，否则对侧的 C（剩余段）若离凹角更近会被误判为 D。
            d_near_side = (ny <= cy) if cut_top else (ny >= cy)
            if d_near_side:
                prox_y = abs(ny - cy)
                d_candidates.append((prox_y, -conf, val, nx, ny))

    e_candidates.sort()
    d_candidates.sort()

    # 去重并取最近的
    e_direct = None
    d_direct = None
    if e_candidates:
        e_direct = e_candidates[0][2]
    if d_candidates:
        d_direct = d_candidates[0][2]

    # 直接提取的 E/D 优先于边归属结果
    if e_direct is not None:
        cut_w = e_direct
    if d_direct is not None:
        cut_h = d_direct

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

    # —— 数量级校验：A/B 应与 px_h/px_w 同数量级 ——
    # OCR 可能丢失小数点（如 47.5 → 475），导致 A/B 与像素比例差 10 倍。
    if A is not None and A > 0 and B is not None and B > 0 and px_w > 0 and px_h > 0:
        r_cm = A / B
        r_px = px_h / px_w
        ratio = r_cm / r_px if r_px > 0 else 1.0
        # 若 cm 比例是像素比例的约 10 倍或 1/10，修正数量级
        if 7.0 < ratio < 13.0:
            A = A / 10.0
        elif 0.07 < ratio < 0.13:
            A = A * 10.0

    # —— 几何一致性校验：仅当 OCR 数值明显异常时才用几何反推 ——
    # E/D 现在直接从凹角附近 OCR 提取，比几何像素比例更可靠。
    # 只有当 OCR 值与像素比例差异极大（>40%）时才认为是 OCR 误识别，
    # 避免几何 cut 检测出错时把正确的 OCR 值覆盖掉。
    if B is not None and B > 0 and E is not None and E > 0 and ratio_cw > 0:
        r_ocr = E / B
        if abs(r_ocr - ratio_cw) > 0.40:
            E = B * ratio_cw
    if A is not None and A > 0 and D is not None and D > 0 and ratio_ch > 0:
        r_ocr = D / A
        if abs(r_ocr - ratio_ch) > 0.40:
            D = A * ratio_ch
    # F = B - E，C = A - D 的一致性校验
    if B is not None and B > 0 and F is not None and F > 0 and E is not None and E > 0:
        if abs(B - (E + F)) > max(2.0, B * 0.05):
            F = B - E
    if A is not None and A > 0 and C is not None and C > 0 and D is not None and D > 0:
        if abs(A - (C + D)) > max(2.0, A * 0.05):
            C = A - D

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
