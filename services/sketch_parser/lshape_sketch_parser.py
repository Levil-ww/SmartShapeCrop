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
    notches_detected: int = 0              # G1: 检测到的凹角数
    notches_consumed: int = 0             # G1: 实际应用的凹角数
    cuts_cm: list[dict] = field(default_factory=list)  # 全部挖角建议值
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




def _extract_largest_contour(cv2, gray):
    """多策略二值化后取最大轮廓（数字标注为独立轮廓，取最大即可分离）。

    返回 (area, cnt) 或 None。面积过滤由调用方完成。
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
    return best


# ------------------------------------------------------------------
# 滑动窗口凹角检测（V3，最高优先级）
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
    # [V1.1 多角改造] 解除收敛点 ③：不再只保留 best_score，
    # 改为按 bbox 四角分桶，每桶留最优 1 个，最多 4 个。
    # [V2.2 B1 阶梯支持] 解除桶合并：每桶保留全部候选，返回前按凹点绝对坐标去重
    # （欧氏距离 < 5% 对角线合并），每桶最多 4 个（防误判爆炸）。
    from collections import defaultdict
    detected = defaultdict(list)  # corner -> [(score, pt)]

    for i in range(n):
        if concavity[i] < 0.1:
            continue
        pt = pts[i].astype(float)

        # 位置约束：在 bbox 四角 45% 对角线内
        min_dist = min(np.hypot(pt[0] - cx, pt[1] - cy) for cx, cy in corners)
        dist_ratio = min_dist / diag if diag > 0 else 99.0
        if dist_ratio >= 0.45:
            continue

        # 用凹点相对于 bbox 中心的位置判定 corner（比邻居方向更鲁棒）
        # 凹点靠近哪个角，缺口就在那个角
        cx_bbox = (minx + maxx) / 2.0
        cy_bbox = (miny + maxy) / 2.0
        if pt[0] < cx_bbox and pt[1] < cy_bbox:
            corner = 'tl'
        elif pt[0] >= cx_bbox and pt[1] < cy_bbox:
            corner = 'tr'
        elif pt[0] < cx_bbox and pt[1] >= cy_bbox:
            corner = 'bl'
        else:
            corner = 'br'

        # 计算邻接向量（用于 adj_cut 尺寸）
        p_prev = pts[(i - k_dir) % n].astype(float)
        p_next = pts[(i + k_dir) % n].astype(float)
        dx1, dy1 = float(p_prev[0] - pt[0]), float(p_prev[1] - pt[1])
        dx2, dy2 = float(p_next[0] - pt[0]), float(p_next[1] - pt[1])

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
        detected[corner].append((score, pt))

    if not detected:
        return []

    # [V2.2 B1] 按凹点绝对坐标去重（欧氏距离 < 5% 对角线），每桶最多 4 个
    dedup_threshold = 0.05 * diag if diag > 0 else 10.0
    results = []
    for corner, candidates in detected.items():
        # 按 score 降序排序
        candidates.sort(key=lambda t: -t[0])
        deduped = []
        for score, pt in candidates:
            # 检查是否与已保留的点距离过近
            if any(np.hypot(pt[0] - kept_pt[0], pt[1] - kept_pt[1]) < dedup_threshold
                   for _, kept_pt in deduped):
                continue
            deduped.append((score, pt))
            if len(deduped) >= 4:
                break

        for score, pt in deduped:
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
            results.append({
                'score': score * 10000.0,  # 绝对最高优先级
                'verts': verts,
                'concave_pt': pt,
                'corner': corner,
            })
    return results


# ------------------------------------------------------------------
# 凸包差异法（V3：缺口角与 cut 尺寸检测，对数字粘连鲁棒）
# ------------------------------------------------------------------
def _detect_by_convex_hull(cv2, cnt_pts, global_minx, global_miny, global_maxx, global_maxy):
    """凸包差异法检测挖角区域。

    [V1.1 多角改造] 解除收敛点 ①：不再只取 argmax(areas) 最大连通域，
    改为用 §5.3 判据（面积 ≥0.5% bbox 且触碰 ≥2 条 bbox 边）过滤全部连通域，
    返回所有合格挖角的列表。单角时列表长度为 1，等价于旧行为。
    """
    minx, miny = float(global_minx), float(global_miny)
    maxx, maxy = float(global_maxx), float(global_maxy)
    W = maxx - minx
    H = maxy - miny
    if W <= 0 or H <= 0:
        return []

    h = int(H) + 4
    w = int(W) + 4
    shifted = (cnt_pts - np.array([minx, miny])).astype(np.int32)

    mask_cnt = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask_cnt, [shifted], 1)

    hull = cv2.convexHull(shifted)
    mask_hull = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask_hull, [hull], 1)

    gap = mask_hull - mask_cnt
    if gap.sum() == 0:
        return []

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        gap.astype(np.uint8), connectivity=8)
    if num_labels <= 1:
        return []

    bbox_area = W * H
    results = []
    for label_idx in range(1, num_labels):
        comp_area = int(stats[label_idx, cv2.CC_STAT_AREA])
        # §5.3 判据 1：面积 ≥ bbox 的 0.5%（排除文字噪点）
        if comp_area < bbox_area * 0.005:
            continue
        gx0 = int(stats[label_idx, cv2.CC_STAT_LEFT])
        gy0 = int(stats[label_idx, cv2.CC_STAT_TOP])
        gw = int(stats[label_idx, cv2.CC_STAT_WIDTH])
        gh = int(stats[label_idx, cv2.CC_STAT_HEIGHT])
        # §5.3 判据 2：触碰 ≥ 2 条 bbox 边（拓扑性质，排除贴边文字）
        # 注意：mask 尺寸为 (H+4, W+4)，但 gap 连通域只延伸到轮廓 bbox (W, H)
        # 容差 5px：草图描边宽度（stroke=6）导致 gap 不到达 bbox 真正边缘
        touches = 0
        if gx0 <= 5:
            touches += 1
        if gy0 <= 5:
            touches += 1
        if gx0 + gw >= int(W) - 5:
            touches += 1
        if gy0 + gh >= int(H) - 5:
            touches += 1
        if touches < 2:
            continue

        gx1 = gx0 + gw - 1
        gy1 = gy0 + gh - 1
        cut_w = float(gw)
        cut_h = float(gh)

        cwr = cut_w / W
        chr_ = cut_h / H
        if not (0.02 <= cwr <= 0.75 and 0.02 <= chr_ <= 0.75):
            continue

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

        results.append({
            'score': 200000.0,
            'verts': verts,
            'concave_pt': np.array([cx, cy]),
            'corner': corner,
        })
    return results


def _collect_approx_candidates(cv2, cnt, cnt_pts, global_minx, global_miny,
                               global_maxx, global_maxy,
                               g_outer_w, g_outer_h, g_diag, g_corners,
                               sliding_results, hull_results):
    """approxPolyDP 多 epsilon 简化 + 凹角评分（V2/V3），生成 tier1/2/3。

    [V1.1 多角改造] sliding_results / hull_results 改为列表（每角一个 dict）。
    单角时列表长度为 1，等价于旧行为。
    返回 (tier1, tier2, tier3)。
    """
    _tier1 = []  # 5~8 verts + 1 凹角（首选）
    _tier2 = []  # 5~10 verts + 1~2 凹角（次选）
    _tier3 = []  # 兜底

    # 滑动窗口 + 凸包结果优先并入 tier1
    if sliding_results:
        for sr in sliding_results:
            _tier1.append((sr['score'],
                           sr['verts'],
                           [(1.0, 0)]))
    if hull_results:
        for hr in hull_results:
            _tier1.append((hr['score'],
                           hr['verts'],
                           [(1.0, 0)]))

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


    # —— V3 滑动窗口凹角检测（最高优先级）——
    # 直接在原始轮廓上找凹角，不依赖 approxPolyDP。
    # approxPolyDP 会把浅挖角（如 D=2cm）平滑掉，而滑动窗口能捕捉到。
    # 方法：对轮廓每个点 i，取前后 k 步的点，计算叉积判断凹凸性。
    # 用多个窗口大小 k 取平均凹度，提高鲁棒性。
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

            # 用凹点相对于 bbox 中心的位置判定 corner（比邻居方向更鲁棒）
            cx_bbox = (global_minx + global_maxx) / 2.0
            cy_bbox = (global_miny + global_maxy) / 2.0
            if pt[0] < cx_bbox and pt[1] < cy_bbox:
                corner_guess = 'tl'
            elif pt[0] >= cx_bbox and pt[1] < cy_bbox:
                corner_guess = 'tr'
            elif pt[0] < cx_bbox and pt[1] >= cy_bbox:
                corner_guess = 'bl'
            else:
                corner_guess = 'br'

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

    return _tier1, _tier2, _tier3


def _finalize_lshape_geometry(chosen_list):
    """从候选列表中选优，计算 L 形参数与顶点。

    [V1.1 多角改造] 不再只取 1 个 reflex，而是对 entry 内全部 reflex
    逐个计算 corner + cut，返回 all_corners 列表。
    单角时 all_corners 长度为 1，主字段等价于旧行为。
    """
    # [V1.1] 解除收敛点 ②：不再按 score 排序取第一个，
    # 改为按 reflex 数量降序（多角优先）、再按 score 降序
    chosen_list.sort(key=lambda t: (-len(t[2]), -t[0]))
    _, verts, reflex = chosen_list[0]
    n = len(verts)

    xs = verts[:, 0]
    ys = verts[:, 1]
    minx, maxx = int(xs.min()), int(xs.max())
    miny, maxy = int(ys.min()), int(ys.max())

    _CORNER_MAP = {(1, -1): 'tr', (1, 1): 'br', (-1, -1): 'tl', (-1, 1): 'bl'}

    # 对全部 reflex 逐个计算 corner + cut
    sorted_reflexes = sorted(reflex, key=lambda t: -t[0])
    all_corners = []
    for score, concave_idx in sorted_reflexes:
        conc = verts[concave_idx]
        p_prev = verts[(concave_idx - 1) % n]
        p_next = verts[(concave_idx + 1) % n]

        # 用凹点相对于 bbox 中心的位置判定 corner（比邻居方向更鲁棒）
        cx_bbox = (minx + maxx) / 2.0
        cy_bbox = (miny + maxy) / 2.0
        cx, cy = float(conc[0]), float(conc[1])
        if cx < cx_bbox and cy < cy_bbox:
            corner = 'tl'
        elif cx >= cx_bbox and cy < cy_bbox:
            corner = 'tr'
        elif cx < cx_bbox and cy >= cy_bbox:
            corner = 'bl'
        else:
            corner = 'br'

        if corner == 'tr':
            cut_w_px = maxx - cx
            cut_h_px = cy - miny
        elif corner == 'tl':
            cut_w_px = cx - minx
            cut_h_px = cy - miny
        elif corner == 'br':
            cut_w_px = maxx - cx
            cut_h_px = maxy - cy
        else:  # bl
            cut_w_px = cx - minx
            cut_h_px = maxy - cy

        all_corners.append({
            'corner': corner,
            'cut_w_px': float(cut_w_px),
            'cut_h_px': float(cut_h_px),
            'concave': (int(conc[0]), int(conc[1])),
            'score': float(score),
        })

    if not all_corners:
        return None

    primary = all_corners[0]
    return {
        'corner': primary['corner'],
        'cut_w_px': primary['cut_w_px'],
        'cut_h_px': primary['cut_h_px'],
        'outer_w_px': float(maxx - minx),
        'outer_h_px': float(maxy - miny),
        'concave': primary['concave'],
        'bbox': (minx, miny, maxx, maxy),
        'verts': [(int(x), int(y)) for x, y in verts],
        'n_verts': n,
        'all_corners': all_corners,
        'n_detected': len(all_corners),
    }


def _classify_pattern(all_corners):
    """分类 L 形挖角模式：单边阶梯 vs 多边 L 形。

    [V2.2 B3] 按 corner 分桶 → 桶内 ≥2 个凹点 → 检查是否在同竖边（|Δx| < ε）
    或同横边（|Δy| < ε）→ 标记 single_edge_stepped；否则 multi_edge。

    Args:
        all_corners: _finalize_lshape_geometry 返回的 all_corners 列表

    Returns:
        'single_edge_stepped' 或 'multi_edge'
    """
    if len(all_corners) < 2:
        return 'multi_edge'

    # 按 corner 分桶
    from collections import defaultdict
    by_corner = defaultdict(list)
    for c in all_corners:
        by_corner[c['corner']].append(c)

    # 检查是否有桶内 ≥2 个凹点在同边
    eps = 5.0  # 像素容差
    for corner, candidates in by_corner.items():
        if len(candidates) < 2:
            continue
        # 检查是否在同竖边（x 坐标接近）
        xs = [c['concave'][0] for c in candidates]
        if max(xs) - min(xs) < eps:
            return 'single_edge_stepped'
        # 检查是否在同横边（y 坐标接近）
        ys = [c['concave'][1] for c in candidates]
        if max(ys) - min(ys) < eps:
            return 'single_edge_stepped'

    return 'multi_edge'


def _compute_staircase_iou(cuts_cm, outer_w_cm, outer_h_cm, contour_pts, pxcm):
    """计算阶梯 CutRect 反拼轮廓与识别轮廓的 IoU。

    [V2.2 B4] 用 cuts 列表反向裁剪外框 bbox，构建理论阶梯多边形，
    与识别轮廓做 IoU 比对。< 0.92 则降级为多边 L 形。

    Args:
        cuts_cm: CutRect 列表（阶梯格式）
        outer_w_cm, outer_h_cm: 外框尺寸（厘米）
        contour_pts: 识别轮廓点（像素）
        pxcm: 像素/厘米比例

    Returns:
        IoU 值（0.0 ~ 1.0）
    """
    import cv2
    if not cuts_cm or contour_pts is None or len(contour_pts) < 3:
        return 0.0

    W = int(round(outer_w_cm * pxcm))
    H = int(round(outer_h_cm * pxcm))
    if W <= 0 or H <= 0:
        return 0.0

    # 构建理论阶梯多边形
    # 从外框 bbox 开始，逐级裁剪
    mask_theory = np.ones((H, W), dtype=np.uint8) * 255
    for cut in cuts_cm:
        anchor = cut.get('anchor', 'tr')
        ox = int(round(cut.get('offset_x_cm', 0.0) * pxcm))
        oy = int(round(cut.get('offset_y_cm', 0.0) * pxcm))
        cw = int(round(cut.get('w_cm', 0.0) * pxcm))
        ch = int(round(cut.get('h_cm', 0.0) * pxcm))
        if cw <= 0 or ch <= 0:
            continue
        # 计算 cut rect 的位置
        if anchor == 'tr':
            x0, y0 = W - ox - cw, oy
        elif anchor == 'tl':
            x0, y0 = ox, oy
        elif anchor == 'br':
            x0, y0 = W - ox - cw, H - oy - ch
        else:  # bl
            x0, y0 = ox, H - oy - ch
        # 裁剪（挖空）
        x0 = max(0, min(x0, W))
        y0 = max(0, min(y0, H))
        x1 = max(0, min(x0 + cw, W))
        y1 = max(0, min(y0 + ch, H))
        mask_theory[y0:y1, x0:x1] = 0

    # 构建识别轮廓 mask
    mask_contour = np.zeros((H, W), dtype=np.uint8)
    cnt_int = contour_pts.astype(np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(mask_contour, [cnt_int], 255)

    # 计算 IoU
    intersection = np.sum((mask_theory > 0) & (mask_contour > 0))
    union = np.sum((mask_theory > 0) | (mask_contour > 0))
    if union == 0:
        return 0.0
    return float(intersection) / float(union)


def _detect_lshape_geometry(cv2, gray):
    """检测 L 形几何。返回 dict 或 None（不是 L 形 / 检测失败）。

    返回字段：corner, cut_w_px, cut_h_px, outer_w_px, outer_h_px,
              concave(x,y), bbox(minx,miny,maxx,maxy), verts[...], n_verts
    """
    h, w = gray.shape[:2]

    # 轮廓分析：多策略二值化 + 取最大轮廓
    best = _extract_largest_contour(cv2, gray)
    if best is None:
        return None
    area, cnt = best
    if area < w * h * 0.02:
        # 面积过小，排除噪点
        return None

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

    # —— 几何自洽：滑动窗口凹角检测（最高优先级）——
    _sliding_results = _detect_concave_sliding_window(
        cnt_pts, g_outer_w, g_outer_h, g_diag, g_corners,
        global_minx, global_miny, global_maxx, global_maxy)
    # —— 凸包差异法（最高优先级）——
    _hull_results = _detect_by_convex_hull(
        cv2, cnt_pts, global_minx, global_miny, global_maxx, global_maxy)

    # 多边形简化 + 凹角评分（V2/V3）：组织 tier 候选
    _tier1, _tier2, _tier3 = _collect_approx_candidates(
        cv2, cnt, cnt_pts, global_minx, global_miny, global_maxx, global_maxy,
        g_outer_w, g_outer_h, g_diag, g_corners,
        _sliding_results, _hull_results)

    # [V1.1 多角改造] 解除收敛点 ②：不再用 _tier1 or _tier2 or _tier3 短路，
    # 改为合并全部 tier，按 reflex 数量降序（多角优先）+ score 降序择优。
    # 单角场景：所有 entry 的 reflex 数 = 1，退化为按 score 取最高，等价于旧行为。
    all_entries = _tier1 + _tier2 + _tier3
    if not all_entries:
        return None

    # L 形参数计算
    geo = _finalize_lshape_geometry(all_entries)
    if geo is None:
        return None

    # G1 闸口数据：记录各通道检测到的角数
    geo['n_detected_hull'] = len(_hull_results) if _hull_results else 0
    geo['n_detected_sliding'] = len(_sliding_results) if _sliding_results else 0
    return geo


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

    # 切边上的数值用「几何比例匹配」分辨挖角尺寸(E/D)与剩余段(F/C)，
    # 不再依赖凹角 cx/cy 做 near/far 分割——凹角来自 approxPolyDP，
    # 顶点位置有抖动，会导致 E↔F 互换、E=B−F 算出错值（如把 E 标误当 F）。
    # 几何比例 cut_w_px/outer_w_px 稳定，能唯一确定哪个值是挖角尺寸。
    # L 形的 E/D 标注通常贴在凹口的两条内边，而不是外接矩形边：
    # 例如左下挖角时，E 在 y=cy 的内水平边，D 在 x=cx 的内垂直边。
    # 仅按外接矩形四边分桶会把它们误归到左/上外边，随后 B-F / A-C
    # 推导出错误的挖角尺寸。把对应内边上的 OCR 候选补入切边桶，
    # 后续仍由像素比例区分挖角段与剩余段。
    edge_tol = max(15.0, min(W_bbox, H_bbox) * 0.08)
    inner_segments = {
        'h': (cy, cx, maxx) if cut_right else (cy, minx, cx),
        'v': (cx, cy, maxy) if not cut_top else (cx, miny, cy),
    }

    def _inner_edge_items(axis):
        line, seg_start, seg_end = inner_segments[axis]
        matched = []
        for val, conf, bbox in ocr_numbers:
            bx, by, bw, bh = bbox
            if bw * bh > bbox_area * 0.15 or val <= 0 or val > 5000:
                continue
            nx, ny = bx + bw / 2.0, by + bh / 2.0
            line_dist = abs(ny - line) if axis == 'h' else abs(nx - line)
            along = nx if axis == 'h' else ny
            if line_dist > edge_tol or not (seg_start - edge_tol <= along <= seg_end + edge_tol):
                continue
            if not any(abs(nx - old[1]) < 20 and abs(ny - old[2]) < 20
                       for old in matched):
                matched.append((val, nx, ny, line_dist, conf, bw * bh))
        return matched

    inner_h_items = _inner_edge_items('h')
    inner_v_items = _inner_edge_items('v')
    inner_items = inner_h_items + inner_v_items

    def _same_position(left, right):
        return abs(left[1] - right[1]) < 20 and abs(left[2] - right[2]) < 20

    # 内边优先：同一个 OCR 项若也因距离更近落入外框桶，先移除，
    # 否则 E/D 会互相竞争并被错误地解释为同一条外边上的两个分段。
    for bucket in buckets.values():
        bucket[:] = [item for item in bucket
                     if not any(_same_position(item, inner) for inner in inner_items)]
    cut_h_items = buckets[cut_h_edge] + inner_h_items
    cut_v_items = buckets[cut_v_edge] + inner_v_items

    # 几何挖角比例（用于在切边多个数值中识别 E/D）
    px_outer_w = geo.get('outer_w_px', W_bbox)
    px_outer_h = geo.get('outer_h_px', H_bbox)
    px_cut_w = geo.get('cut_w_px', 0.0)
    px_cut_h = geo.get('cut_h_px', 0.0)
    geo_ratio_w = (px_cut_w / px_outer_w) if px_outer_w > 0 else 0.0
    geo_ratio_h = (px_cut_h / px_outer_h) if px_outer_h > 0 else 0.0

    def _dedup_edge(items):
        """切边候选去重：同位置(<20px)保留置信度高的"""
        sorted_items = sorted(items, key=lambda x: -x[4])
        deduped = []
        for it in sorted_items:
            val, nx, ny, dmin, conf = it[0], it[1], it[2], it[3], it[4]
            is_dup = any(abs(nx - ex) < 20 and abs(ny - ey) < 20
                         for _, ex, ey, _, _ in deduped)
            if not is_dup:
                deduped.append((val, nx, ny, dmin, conf))
        return deduped

    def _resolve_cut_pair(items, outer, geo_ratio):
        """从切边数值中分辨 (挖角尺寸, 剩余段)。

        [Bug Fix 2026-09-15] 修复 geo_ratio 不准时 cut↔remaining 互换的根因：
        原逻辑纯依赖 geo_ratio 选最优 cut，互补性检查阈值仅 outer*0.05（93+26=119
        vs 112 差 7 时刚好卡在阈值外，导致两个值互相竞争被错配）。新逻辑：
        1. **互补性优先**：先找互补对 v1+v2 ≈ outer（阈值放宽到 15%，容忍 OCR 误差
           和草图几何不精确）。有互补对时，结合 geo_ratio + 大小启发式分辨
           哪个是 cut。
        2. **单值**：保持原 geo_ratio 判断不变。
        3. **无互补对多值**：退回纯 geo_ratio，但 complement 阈值也放宽到 15%。

        返回 (cut_val, remaining_val)。
        """
        if not items or outer is None or outer <= 0:
            return None, None
        COMPLEMENT_TOL = outer * 0.15   # 放宽到 15%：OCR 误差 + 草图不精确
        geo_cut = outer * geo_ratio
        vals = [it[0] for it in items]
        if len(vals) == 1:
            v = vals[0]
            err_as_cut = abs(v - geo_cut)
            err_as_rem = abs((outer - v) - geo_cut)
            if err_as_cut <= err_as_rem:
                return v, outer - v
            else:
                return outer - v, v
        # —— Step 1: 找互补对（cut + remaining ≈ outer）——
        # 枚举所有不重复对，挑加和最接近 outer 的那个
        best_pair = None
        best_pair_delta = float('inf')
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                pair_sum = vals[i] + vals[j]
                delta = abs(pair_sum - outer)
                if delta < best_pair_delta:
                    best_pair_delta = delta
                    best_pair = (vals[i], vals[j])
        # —— Step 2: 有互补对（在 15% 容差内）→ geo_ratio + 大小启发式 ——
        if best_pair is not None and best_pair_delta <= COMPLEMENT_TOL:
            v1, v2 = best_pair
            err1 = abs(v1 - geo_cut)
            err2 = abs(v2 - geo_cut)
            # 启发式：挖角通常比外框小（cut < outer*0.6）
            # 结合 geo_ratio 误差和大小约束：geo_ratio 误差差距不大时选小的
            if abs(err1 - err2) < geo_cut * 0.3:  # geo_ratio 信号不明显
                # 大小启发式：较小的值更可能是挖角（除非挖角特别大）
                if min(v1, v2) < outer * 0.6:
                    return (v1, v2) if v1 < v2 else (v2, v1)
            # geo_ratio 信号明确或大小启发式不适用时，用 geo_ratio
            if err1 <= err2:
                return v1, v2
            else:
                return v2, v1
        # —— Step 3: 无合适互补对 → 退回 geo_ratio 逐个评分，complement 阈值也放宽 ——
        best_cut, best_rem, best_score = None, None, float('inf')
        for v in vals:
            score = abs(v - geo_cut)
            has_complement = any(abs((outer - v) - v2) < COMPLEMENT_TOL
                                 for v2 in vals if v2 != v)
            if has_complement:
                score *= 0.3
            if score < best_score:
                best_score = score
                best_cut = v
                best_rem = outer - v
        return best_cut, best_rem

    h_dedup = _dedup_edge(cut_h_items)
    v_dedup = _dedup_edge(cut_v_items)
    cut_w, top_w = _resolve_cut_pair(h_dedup, outer_w, geo_ratio_w)
    cut_h, right_h = _resolve_cut_pair(v_dedup, outer_h, geo_ratio_h)

    # —— 主策略：利用几何不变量推导挖角尺寸 ——
    # 安全不变量：B = E + F（外宽 = 挖角宽 + 剩余顶段）
    #           A = D + C（外高 = 挖角高 + 剩余侧段）
    # F/C（长段）标注在外框切边上，识别最可靠；E/D 用 B-F / A-C 推导，
    # 比直接读取挖角内部的 E/D 标签更稳健——后者常因标签贴凹角、落在两条
    # 切边的 edge_tol 重叠区而被互换（E↔D），或因标注在内侧切边(y=cy/x=cx)
    # 上而被外切边检测漏掉。
    cut_w = None  # E
    cut_h = None  # D
    if outer_w is not None and top_w is not None:
        derived_e = outer_w - top_w
        if 0 < derived_e <= outer_w:
            cut_w = derived_e
    if outer_h is not None and right_h is not None:
        derived_d = outer_h - right_h
        if 0 < derived_d <= outer_h:
            cut_h = derived_d

    # —— 宽高比交叉校验：防止 E↔D 互换 ——
    # 挖角标签贴凹角时，E(宽)可能落入垂直切边桶、D(高)可能落入水平切边桶，
    # 导致 _resolve_cut_pair 把两者互换。各自内部虽满足 E+F=B、D+C=A，
    # 但 E/D 会偏离几何像素比 cut_w_px/cut_h_px。此时交换 E、D 即可纠正。
    if cut_w is not None and cut_h is not None and cut_w > 0 and cut_h > 0:
        px_cw = geo.get('cut_w_px', 0.0)
        px_ch = geo.get('cut_h_px', 0.0)
        if px_cw > 0 and px_ch > 0:
            geo_ar = px_cw / px_ch          # 几何宽高比
            cur_ar = cut_w / cut_h          # 当前宽高比
            swap_ar = cut_h / cut_w         # 交换后的宽高比
            cur_err = abs(cur_ar - geo_ar)
            swap_err = abs(swap_ar - geo_ar)
            # 仅当交换后：① 误差显著更小（<一半）且 ② 交换后比值确实接近几何比（<50%），才交换
            if swap_err < cur_err * 0.5 and swap_err < geo_ar * 0.5:
                cut_w, cut_h = cut_h, cut_w
                top_w = (outer_w - cut_w) if outer_w is not None else None
                right_h = (outer_h - cut_h) if outer_h is not None else None

    # —— 兜底：直接从凹角附近/挖角区域 OCR 提取 E/D（仅当不变量推导失败时）——
    e_direct = None
    d_direct = None
    if cut_w is None or cut_h is None:
        diag = np.hypot(W_bbox, H_bbox)
        near_radius = diag * 0.40
        edge_tol = max(15.0, min(W_bbox, H_bbox) * 0.08)

        def _dedup_near(items):
            s = sorted(items, key=lambda x: -x[4])
            out = []
            for it in s:
                v, nx, ny, _, conf = it[0], it[1], it[2], it[3], it[4]
                dup = any(abs(nx - ex) < 25 and abs(ny - ey) < 25 for _, ex, ey, _, _ in out)
                if not dup:
                    out.append(it)
            return out

        e_candidates = []
        d_candidates = []
        for it in ocr_numbers:
            val, conf, bbox = it
            bx, by, bw, bh = bbox
            if bw * bh > bbox_area * 0.15 or val <= 0 or val > 5000:
                continue
            nx, ny = bx + bw / 2.0, by + bh / 2.0
            if np.hypot(nx - cx, ny - cy) > near_radius:
                continue
            h_cut_y = miny if cut_top else maxy
            v_cut_x = maxx if cut_right else minx
            on_h_cut = abs(ny - h_cut_y) < edge_tol
            on_v_cut = abs(nx - v_cut_x) < edge_tol
            if on_h_cut:
                e_near_side = (nx >= cx) if cut_right else (nx <= cx)
                if e_near_side:
                    e_candidates.append((abs(nx - cx), -conf, val, nx, ny))
            if on_v_cut:
                d_near_side = (ny <= cy) if cut_top else (ny >= cy)
                if d_near_side:
                    d_candidates.append((abs(ny - cy), -conf, val, nx, ny))
        e_candidates.sort()
        d_candidates.sort()
        if cut_w is None and e_candidates:
            e_direct = e_candidates[0][2]
        if cut_h is None and d_candidates:
            d_direct = d_candidates[0][2]

        # 最后兜底：挖角区域内部标注
        if (cut_w is None and e_direct is None) or (cut_h is None and d_direct is None):
            if cut_top and cut_right:
                in_cutout = lambda nx, ny: (cx <= nx <= maxx) and (miny <= ny <= cy)
            elif cut_top and not cut_right:
                in_cutout = lambda nx, ny: (minx <= nx <= cx) and (miny <= ny <= cy)
            elif (not cut_top) and cut_right:
                in_cutout = lambda nx, ny: (cx <= nx <= maxx) and (cy <= ny <= maxy)
            else:
                in_cutout = lambda nx, ny: (minx <= nx <= cx) and (cy <= ny <= maxy)
            cutout_items = []
            for it in ocr_numbers:
                val, conf, bbox = it
                bx, by, bw, bh = bbox
                if bw * bh > bbox_area * 0.15 or val <= 0 or val > 5000:
                    continue
                nx, ny = bx + bw / 2.0, by + bh / 2.0
                if in_cutout(nx, ny):
                    cutout_items.append((val, nx, ny, None, conf))
            cutout_items = _dedup_near(cutout_items)
            if cut_w is None and e_direct is None:
                e_cands = [(abs(nx - cx), -conf, val) for val, nx, ny, _, conf in cutout_items]
                e_cands.sort()
                if e_cands:
                    e_direct = e_cands[0][2]
            if cut_h is None and d_direct is None:
                d_cands = [(abs(ny - cy), -conf, val) for val, nx, ny, _, conf in cutout_items]
                d_cands.sort()
                if d_cands:
                    d_direct = d_cands[0][2]

    # 仅在不变量推导失败时，用直接提取结果填充
    if cut_w is None and e_direct is not None:
        cut_w = e_direct
    if cut_h is None and d_direct is not None:
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
    # [Fix 2026-09-12 P1-02] 旧代码恒修正 A，但当 B 是误读的一方时修正 A 是错的。
    #   新策略：比较 A/px_h 和 B/px_w 的比例（cm/px，即 DPI 量级），谁的偏离大就修谁。
    if A is not None and A > 0 and B is not None and B > 0 and px_w > 0 and px_h > 0:
        r_cm = A / B
        r_px = px_h / px_w
        ratio = r_cm / r_px if r_px > 0 else 1.0
        # 若 cm 比例是像素比例的约 10 倍或 1/10，修正数量级
        if 7.0 < ratio < 13.0:
            # A/B 比 px_h/px_w 大 ~10x：判断是 A 过大还是 B 过小
            ratio_a = A / px_h
            ratio_b = B / px_w
            if ratio_a > 3 * ratio_b:
                A = A / 10.0
            else:
                B = B * 10.0
        elif 0.07 < ratio < 0.13:
            # A/B 比 px_h/px_w 小 ~10x：判断是 A 过小还是 B 过大
            ratio_a = A / px_h
            ratio_b = B / px_w
            if ratio_b > 3 * ratio_a:
                B = B / 10.0
            else:
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
# 多角 OCR 逐角归属（三期）
# ---------------------------------------------------------------------------

def _resolve_cut_pair_per_corner(items, outer, geo_ratio):
    """从切边数值中分辨 (挖角尺寸, 剩余段)。

    [Bug Fix 2026-09-15] 与 _assign_labels_by_geometry 内的 _resolve_cut_pair 同步修复：
    互补性优先找 pair（阈值 15%）+ geo_ratio + 大小启发式联合分辨。
    返回 (cut_val, remaining_val)。
    """
    if not items or outer is None or outer <= 0:
        return None, None
    COMPLEMENT_TOL = outer * 0.15
    geo_cut = outer * geo_ratio
    vals = [it[0] for it in items]
    if len(vals) == 1:
        v = vals[0]
        err_as_cut = abs(v - geo_cut)
        err_as_rem = abs((outer - v) - geo_cut)
        if err_as_cut <= err_as_rem:
            return v, outer - v
        else:
            return outer - v, v
    # —— Step 1: 找互补对 ——
    best_pair = None
    best_pair_delta = float('inf')
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            pair_sum = vals[i] + vals[j]
            delta = abs(pair_sum - outer)
            if delta < best_pair_delta:
                best_pair_delta = delta
                best_pair = (vals[i], vals[j])
    # —— Step 2: 有互补对 → geo_ratio + 大小启发式 ——
    if best_pair is not None and best_pair_delta <= COMPLEMENT_TOL:
        v1, v2 = best_pair
        err1 = abs(v1 - geo_cut)
        err2 = abs(v2 - geo_cut)
        # geo_ratio 信号不明显（误差差 < geo_cut*0.3）时，用大小启发式
        if abs(err1 - err2) < geo_cut * 0.3 if geo_cut > 0 else True:
            if min(v1, v2) < outer * 0.6:
                return (v1, v2) if v1 < v2 else (v2, v1)
        if err1 <= err2:
            return v1, v2
        else:
            return v2, v1
    # —— Step 3: 无合适互补对 → 退回 geo_ratio 逐个评分 ——
    best_cut, best_rem, best_score = None, None, float('inf')
    for v in vals:
        score = abs(v - geo_cut)
        has_complement = any(abs((outer - v) - v2) < COMPLEMENT_TOL
                             for v2 in vals if v2 != v)
        if has_complement:
            score *= 0.3
        if score < best_score:
            best_score = score
            best_cut = v
            best_rem = outer - v
    return best_cut, best_rem


def _attribute_cut_ocr_per_corner(all_corners, ocr_numbers, geo,
                                   outer_w_cm, outer_h_cm):
    """对每个角，从 OCR 数值中归属该角的挖角尺寸 (E, D)。

    策略：
    1. 对每个角，确定其切边方向（水平切边 + 垂直切边）
    2. 同时收集**外边**和**内边**的 OCR 数值
       - 外边：L 形外框的对应边（top/bottom/left/right），用于识别 F/C 剩余段
       - 内边：凹角点位置的内切边（y=cy 的水平内边、x=cx 的垂直内边），用于识别 E/D 挖角尺寸
       - 相邻角共享外边时，按各角凹角点位置分段，每个角只认自己那一侧
    3. 用几何比例匹配分辨挖角尺寸(E/D)与剩余段(F/C)
    4. OCR 归属成功用 OCR 值，否则回退到像素比例反推

    [Bug Fix 2026-09-16] 修复相邻角挖角时只有一个方位识别准确的问题：
    根因是本函数只收集外边标注，漏掉了内边标注。
    当 E（挖角宽）/ D（挖角高）标注在内侧切边上时，完全收集不到 →
    全部回退像素比例 → 像素比例不准导致数值漂移。

    对比同文件 _assign_labels_by_geometry (L843-867) 已正确实现内边收集，
    本函数作为多角独立归属版同步移植了内边逻辑。

    返回 list[dict]: 每项 {corner, cut_w_cm, cut_h_cm, source, ocr_cut_w, ocr_cut_h}
    """
    minx, miny, maxx, maxy = geo['bbox']
    W_bbox = maxx - minx
    H_bbox = maxy - miny
    bbox_area = W_bbox * H_bbox
    px_outer_w = geo.get('outer_w_px', W_bbox)
    px_outer_h = geo.get('outer_h_px', H_bbox)

    edge_tol = max(15.0, min(W_bbox, H_bbox) * 0.08)

    def _is_valid(item):
        """OCR 项有效性过滤（与 _assign_labels_by_geometry 保持一致）。"""
        val, conf, bbox = item
        bx, by, bw, bh = bbox
        if bw * bh > bbox_area * 0.15:
            return False
        if val <= 0 or val > 5000:
            return False
        return True

    results = []
    for cand in all_corners:
        corner = cand['corner']
        cut_w_px = float(cand.get('cut_w_px', 0.0) or 0.0)
        cut_h_px = float(cand.get('cut_h_px', 0.0) or 0.0)
        cx, cy = cand.get('concave', (0, 0))

        cut_right = corner in ('tr', 'br')
        cut_top = corner in ('tr', 'tl')

        geo_ratio_w = (cut_w_px / px_outer_w) if px_outer_w > 0 else 0.0
        geo_ratio_h = (cut_h_px / px_outer_h) if px_outer_h > 0 else 0.0

        # ========== 外边切边（原有逻辑） ==========
        cut_h_edge_y = miny if cut_top else maxy
        cut_v_edge_x = maxx if cut_right else minx

        # ========== 内边切边（新增修复） ==========
        # 内水平边：y=cy，从外框对应边延伸到凹角点
        #   cut_right=False (左角): x ∈ [minx, cx]
        #   cut_right=True  (右角): x ∈ [cx, maxx]
        if not cut_right:
            inner_h_y, inner_h_x1, inner_h_x2 = cy, minx, cx
        else:
            inner_h_y, inner_h_x1, inner_h_x2 = cy, cx, maxx

        # 内垂直边：x=cx，从外框对应边延伸到凹角点
        #   cut_top=True  (上角): y ∈ [miny, cy]
        #   cut_top=False (下角): y ∈ [cy, maxy]
        if cut_top:
            inner_v_x, inner_v_y1, inner_v_y2 = cx, miny, cy
        else:
            inner_v_x, inner_v_y1, inner_v_y2 = cx, cy, maxy

        cut_h_items = []  # 水平方向候选（含 E 挖角宽 + 可能的 F 剩余段）
        cut_v_items = []  # 垂直方向候选（含 D 挖角高 + 可能的 C 剩余段）
        for val, conf, bbox in ocr_numbers:
            if not _is_valid((val, conf, bbox)):
                continue
            bx, by, bw, bh = bbox
            nx, ny = bx + bw / 2.0, by + bh / 2.0

            # ---- 水平方向收集 ----
            h_match = False
            # 外边：y ≈ cut_h_edge_y 且在正确的水平侧（相邻角共享外边时按 cx 分段）
            on_outer_h = abs(ny - cut_h_edge_y) < edge_tol
            if on_outer_h:
                if cut_right and nx >= cx:
                    h_match = True
                elif not cut_right and nx <= cx:
                    h_match = True
            # 内边：y ≈ inner_h_y 且 x 落在本角的内水平段范围内
            # 内边上的数值（E 挖角宽）肯定属于本角，无需再分段
            on_inner_h = (abs(ny - inner_h_y) < edge_tol
                          and inner_h_x1 - edge_tol <= nx <= inner_h_x2 + edge_tol)
            if on_inner_h:
                h_match = True

            if h_match:
                cut_h_items.append((val, nx, ny, conf))

            # ---- 垂直方向收集 ----
            v_match = False
            # 外边：x ≈ cut_v_edge_x 且在正确的垂直侧
            on_outer_v = abs(nx - cut_v_edge_x) < edge_tol
            if on_outer_v:
                if cut_top and ny <= cy:
                    v_match = True
                elif not cut_top and ny >= cy:
                    v_match = True
            # 内边：x ≈ inner_v_x 且 y 落在本角的内垂直段范围内
            # 内边上的数值（D 挖角高）肯定属于本角
            on_inner_v = (abs(nx - inner_v_x) < edge_tol
                          and inner_v_y1 - edge_tol <= ny <= inner_v_y2 + edge_tol)
            if on_inner_v:
                v_match = True

            if v_match:
                cut_v_items.append((val, nx, ny, conf))

        # 去重：同一个 OCR 项可能同时匹配外边和内边（edge_tol 重叠）
        def _dedup(items):
            seen = set()
            out = []
            for it in items:
                key = (round(it[1], 1), round(it[2], 1))  # 按 (nx, ny) 去重
                if key not in seen:
                    seen.add(key)
                    out.append(it)
            return out

        cut_h_items = _dedup(cut_h_items)
        cut_v_items = _dedup(cut_v_items)

        # 方向冲突解决：同一个 OCR 可能同时被水平内边和垂直外边/内边抓到
        # （eg. 一个标在角顶点附近的数值，ny 接近 cy 同时 nx 接近 maxx），
        # 按"到对应方向切边的最近距离"决定归属。
        # 水平方向的度量是到 cut_h_edge_y 或 inner_h_y 的垂直距离；
        # 垂直方向的度量是到 cut_v_edge_x 或 inner_v_x 的水平距离。
        h_centers = {(round(it[1], 1), round(it[2], 1)) for it in cut_h_items}
        v_centers = {(round(it[1], 1), round(it[2], 1)) for it in cut_v_items}
        conflict = h_centers & v_centers  # 两个列表都有的 OCR

        if conflict:
            new_h, new_v = [], []
            for it in cut_h_items:
                key = (round(it[1], 1), round(it[2], 1))
                if key not in conflict:
                    new_h.append(it)
                    continue
                nx, ny = it[1], it[2]
                # 水平方向：到水平切边（外边或内边）的垂直距离
                d_h = min(abs(ny - cut_h_edge_y), abs(ny - inner_h_y))
                # 垂直方向：到垂直切边（外边或内边）的水平距离
                d_v = min(abs(nx - cut_v_edge_x), abs(nx - inner_v_x))
                if d_h <= d_v:
                    new_h.append(it)  # 更近水平，保留在水平
                # 否则留给垂直方向
            for it in cut_v_items:
                key = (round(it[1], 1), round(it[2], 1))
                if key not in conflict:
                    new_v.append(it)
                    continue
                nx, ny = it[1], it[2]
                d_h = min(abs(ny - cut_h_edge_y), abs(ny - inner_h_y))
                d_v = min(abs(nx - cut_v_edge_x), abs(nx - inner_v_x))
                if d_v < d_h:  # 严格小于：冲突项只能在一个列表中
                    new_v.append(it)
                # 否则已在水平方向
            cut_h_items, cut_v_items = new_h, new_v

        ocr_cut_w, _ = _resolve_cut_pair_per_corner(
            cut_h_items, outer_w_cm, geo_ratio_w)
        ocr_cut_h, _ = _resolve_cut_pair_per_corner(
            cut_v_items, outer_h_cm, geo_ratio_h)

        # 几何一致性校验：OCR 值与像素比例偏差>40% 时不信任 OCR
        if ocr_cut_w is not None and outer_w_cm > 0 and geo_ratio_w > 0:
            if abs(ocr_cut_w / outer_w_cm - geo_ratio_w) > 0.40:
                ocr_cut_w = None
        if ocr_cut_h is not None and outer_h_cm > 0 and geo_ratio_h > 0:
            if abs(ocr_cut_h / outer_h_cm - geo_ratio_h) > 0.40:
                ocr_cut_h = None

        cut_w_cm = ocr_cut_w
        cut_h_cm = ocr_cut_h
        source = 'ocr' if (ocr_cut_w is not None or ocr_cut_h is not None) else 'pixel_ratio'

        if cut_w_cm is None and outer_w_cm > 0 and geo_ratio_w > 0:
            cut_w_cm = round(outer_w_cm * geo_ratio_w, 2)
        if cut_h_cm is None and outer_h_cm > 0 and geo_ratio_h > 0:
            cut_h_cm = round(outer_h_cm * geo_ratio_h, 2)

        results.append({
            'corner': corner,
            'cut_w_cm': round(float(cut_w_cm), 2) if cut_w_cm else 0.0,
            'cut_h_cm': round(float(cut_h_cm), 2) if cut_h_cm else 0.0,
            'source': source,
            'ocr_cut_w': round(float(ocr_cut_w), 2) if ocr_cut_w else None,
            'ocr_cut_h': round(float(ocr_cut_h), 2) if ocr_cut_h else None,
        })

    return results


# ---------------------------------------------------------------------------
# G1 结构一致性不变量（永久常驻，不随识别能力提升而移除）
#
# 不变量定义：parse_lshape_sketch 的所有退出路径必须经过 G1 检查，
# 确保 notches_detected == notches_consumed 或在 message 中明确告警。
# 即使未来识别层能完美检测全部凹角，G1 仍须运行——它是结构正确性的
# 最终守卫，不是临时补丁。
# ---------------------------------------------------------------------------

def _apply_g1_invariant(result, n_detected, n_consumed, all_corners_geo,
                        cuts_cm, *, base_msg=''):
    """在结果对象上执行 G1 不变量检查。

    所有 parse_lshape_sketch 退出路径必须调用此函数，确保：
    1. notches_detected / notches_consumed 被正确设置
    2. 检测到的凹角数 != 消费数时在 message 中告警
    3. success 字段反映 G1 状态

    Args:
        result: LSketchParseResult 对象
        n_detected: 几何检测到的凹角数
        n_consumed: 实际转换为 cuts_cm 的角数
        all_corners_geo: geo['all_corners'] 列表
        cuts_cm: 已消费的挖角参数列表
        base_msg: 基础消息（已有的识别摘要）
    """
    result.notches_detected = n_detected
    result.notches_consumed = n_consumed
    g1_blocked = n_detected != n_consumed

    msg = base_msg
    if g1_blocked:
        consumed_corners = {c['corner'] for c in cuts_cm}
        unused = [c['corner'] for c in all_corners_geo
                  if c['corner'] not in consumed_corners]
        warning = (
            f" ⚠️ G1 闸口：检测到 {n_detected} 个凹角，当前仅应用 {n_consumed} 个"
            f"（{[c['corner'] for c in cuts_cm]}），其余角位 {unused} 需手动补充"
        )
        msg += warning

    result.message = msg
    result.debug['g1_blocked'] = g1_blocked
    result.debug['g1_invariant_applied'] = True
    return not g1_blocked


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def parse_lshape_sketch(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
    progress_callback=None,
    external_cancel_check=None,
) -> LSketchParseResult:
    """解析 L 形尺寸草图，永不抛异常。

    Args:
        image_path: 草图图片路径
        target_outer_w_cm / target_outer_h_cm: 可选的目标外框尺寸（来自文件名解析），
            用于二次校验 / 像素比例定标（仅当 OCR 全失败时启用）。
        progress_callback: 可选 (pct, msg) 回调
        external_cancel_check: 可选的无参回调，返回 True 时中断 OCR 循环

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

    # G1 审计字段先于 OCR/尺寸求解写入，确保部分结果也能说明检测到的角数。
    n_detected = geo.get('n_detected', 1)
    result.notches_detected = n_detected
    result.notches_consumed = 0

    _progress(40, "OCR 识别尺寸数值...")
    tesseract = _safe_import_tesseract()
    ocr_numbers = []
    if tesseract is not None:
        try:
            enhanced = _enhance_colored_ink(cv2, img)
            # [Fix N-P0-01] L 形解析也传入 deadline，防止 OCR 循环无限制运行
            from .sketch_parser_base import _PARSE_TIMEOUT_SEC
            import time as _sp_time
            _lshape_deadline = _sp_time.monotonic() + _PARSE_TIMEOUT_SEC
            def _lshape_cancel():
                if _sp_time.monotonic() > _lshape_deadline:
                    return True
                if external_cancel_check is not None:
                    return external_cancel_check()
                return False
            ocr_numbers = _multi_scale_ocr_scan(
                cv2, tesseract, gray, enhanced_gray=enhanced,
                check_cancel=_lshape_cancel)
        except Exception as e:
            logger.warning(f"[lshape] OCR 扫描失败（降级为纯几何）: {e}")
    else:
        logger.info("[lshape] 未安装 Tesseract，仅用几何推断")

    _progress(70, "归属标签 & 求解尺寸...")
    roles = _assign_labels_by_geometry(geo, ocr_numbers)
    dims = _resolve_dimensions(geo, roles)
    sc = _score_consistency(geo, dims)

    # —— [2026-09-11 FIX] 外框真值始终信任 target 文件名 ——
    # 产品不变量：草图的外框尺寸一定与目标文件名尺寸一致。
    # OCR 可能因模糊/位置偏差识别出完全错误的外框值（如 9x21cm 应为 85x38cm），
    # 导致方向颠倒（9<21 变竖版）、挖角 cm 值被像素比例定标污染。
    # 当 target 值可用时，始终用 target 值覆盖 OCR；OCR 值仅作 debug 记录。
    if target_outer_w_cm > 0:
        if dims['outer_w_cm'] > 0 and abs(dims['outer_w_cm'] - target_outer_w_cm) / target_outer_w_cm > 0.5:
            logger.warning(
                f"[lshape] OCR 外框宽 {dims['outer_w_cm']:.1f}cm 与 "
                f"target {target_outer_w_cm:.1f}cm 差距 >50%，使用 target 值"
            )
        dims['outer_w_cm'] = target_outer_w_cm
    elif dims['outer_w_cm'] <= 0 and target_outer_w_cm > 0:
        dims['outer_w_cm'] = target_outer_w_cm

    if target_outer_h_cm > 0:
        if dims['outer_h_cm'] > 0 and abs(dims['outer_h_cm'] - target_outer_h_cm) / target_outer_h_cm > 0.5:
            logger.warning(
                f"[lshape] OCR 外框高 {dims['outer_h_cm']:.1f}cm 与 "
                f"target {target_outer_h_cm:.1f}cm 差距 >50%，使用 target 值"
            )
        dims['outer_h_cm'] = target_outer_h_cm
    elif dims['outer_h_cm'] <= 0 and target_outer_h_cm > 0:
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
        partial_msg = (
            "识别到 L 形轮廓，但尺寸数值不完整"
            f"（宽{'' if have_w else '缺失'} 高{'' if have_h else '缺失'} "
            f"挖宽{'' if have_cw else '缺失'} 挖高{'' if have_ch else '缺失'}）。\n"
            "请在草图上清晰标注 6 个尺寸（A/B/C/D/E/F）后重试，或手动填写。"
        )
        result.debug.update({
            'geometry': geo,
            'roles': roles,
            'ocr_count': len(ocr_numbers),
            'partial': dims,
        })
        _apply_g1_invariant(
            result, n_detected, 0, geo.get('all_corners', []),
            [], base_msg=partial_msg)
        return result

    # —— 多角参数：OCR 逐角归属 + 像素比例兜底 ——
    all_corners_geo = geo.get('all_corners', [])
    if all_corners_geo and ocr_numbers:
        cuts_cm = _attribute_cut_ocr_per_corner(
            all_corners_geo, ocr_numbers, geo,
            dims['outer_w_cm'], dims['outer_h_cm'])
    else:
        cuts_cm = []
        for candidate in all_corners_geo:
            cut_w_px = float(candidate.get('cut_w_px', 0.0) or 0.0)
            cut_h_px = float(candidate.get('cut_h_px', 0.0) or 0.0)
            if cut_w_px <= 0 or cut_h_px <= 0:
                continue
            cuts_cm.append({
                'corner': candidate['corner'],
                'cut_w_cm': round(dims['outer_w_cm'] * cut_w_px / geo['outer_w_px'], 2),
                'cut_h_cm': round(dims['outer_h_cm'] * cut_h_px / geo['outer_h_px'], 2),
                'source': 'pixel_ratio',
            })

    # [V2.2 B2] 阶梯场景：转换为 CutRect 格式
    pattern = _classify_pattern(all_corners_geo) if all_corners_geo else 'multi_edge'
    if pattern == 'single_edge_stepped' and len(cuts_cm) >= 2:
        # 按 concave 坐标排序（y 升序或 x 升序，取决于角位）
        corner = cuts_cm[0]['corner']
        # 构建 CutRect 列表：每级的 offset = 前一级的尺寸累加
        cut_rects = []
        sorted_corners = sorted(all_corners_geo, key=lambda c: (
            c['concave'][1] if corner in ('tl', 'tr') else -c['concave'][1],
            c['concave'][0] if corner in ('tl', 'bl') else -c['concave'][0],
        ))
        offset_x, offset_y = 0.0, 0.0
        for cand in sorted_corners:
            cut_w_px = float(cand.get('cut_w_px', 0.0) or 0.0)
            cut_h_px = float(cand.get('cut_h_px', 0.0) or 0.0)
            if cut_w_px <= 0 or cut_h_px <= 0:
                continue
            w_cm = round(dims['outer_w_cm'] * cut_w_px / geo['outer_w_px'], 2)
            h_cm = round(dims['outer_h_cm'] * cut_h_px / geo['outer_h_px'], 2)
            cut_rects.append({
                'anchor': corner,
                'offset_x_cm': round(offset_x, 2),
                'offset_y_cm': round(offset_y, 2),
                'w_cm': w_cm,
                'h_cm': h_cm,
            })
            # 累加 offset（内缩阶梯：每级向内收缩）
            offset_x += w_cm
            offset_y += h_cm
        # 替换 cuts_cm 为 cut_rects
        cuts_cm = cut_rects

        # [V2.2 B4] 反拼轮廓 IoU 校验
        if geo.get('verts') and len(geo['verts']) >= 3:
            contour_pts = np.array(geo['verts'], dtype=float)
            pxcm = geo['outer_w_px'] / dims['outer_w_cm'] if dims['outer_w_cm'] > 0 else 0
            iou = _compute_staircase_iou(
                cuts_cm, dims['outer_w_cm'], dims['outer_h_cm'],
                contour_pts, pxcm)
            if iou < 0.92:
                # 降级为 multi_edge，转换回旧格式
                pattern = 'multi_edge'
                cuts_cm = [
                    {
                        'corner': cut['anchor'],
                        'cut_w_cm': cut['w_cm'],
                        'cut_h_cm': cut['h_cm'],
                        'source': 'pixel_ratio',
                    }
                    for cut in cut_rects
                ]

    # —— G1 闸口：检测到的凹角数 vs 实际消费数（不变量常驻） ——
    n_consumed = len(cuts_cm)
    msg = (
        f"L 形识别{'成功' if n_detected == n_consumed else '部分完成'}"
        f"（corner={geo['corner']}, "
        f"外框 {dims['outer_w_cm']:.1f}×{dims['outer_h_cm']:.1f}cm, "
        f"挖角 {dims['cut_w_cm']:.1f}×{dims['cut_h_cm']:.1f}cm, 自洽={sc:.2f}）"
    )
    g1_passed = _apply_g1_invariant(
        result, n_detected, n_consumed, geo.get('all_corners', []),
        cuts_cm, base_msg=msg)
    result.success = g1_passed
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
    result.cuts_cm = cuts_cm
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
        'all_corners': geo.get('all_corners', []),
        'cuts_cm': cuts_cm,
        'n_detected': n_detected,
        'n_consumed': n_consumed,
        'n_detected_hull': geo.get('n_detected_hull', 0),
        'n_detected_sliding': geo.get('n_detected_sliding', 0),
        'pattern': pattern,
    })
    _progress(100, "识别完成")
    return result