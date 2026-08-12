"""
core/corner/sector_render.py
圆角弧线上的边框层重绘：多层同心圆弧设计。

从原 core/image_cropper.py 拆分而来，逻辑未变化。

核心设计（与 project_memory 一致）：
  - 所有边框层共享同一个圆心（外层圆角圆心），形成同心圆弧
  - cumulative_i = 所有外层边框的累计厚度
  - R_eff_i = max(0, R_total - cumulative_i)
  - 厚度由径向条件 d_outer <= d_p < d_inner 控制（与角度无关）
  - 角度范围与 carve_corner_on_mask 的 pieslice 角度完全一致（CORNER_ANGLES）

向后兼容：原 core/image_cropper.py 已改为薄重导出 shim，旧导入路径继续可用。
"""
from __future__ import annotations
import numpy as np
from PIL import Image

from .algorithm import CORNER_ANGLES


def _build_border_sector_mask(
    w: int, h: int, corner_key: str, cx: int, cy: int, R: int,
    d_outer: float, d_inner: float
) -> np.ndarray:
    """
    构建单个边框层在圆弧上的精确遮罩（固定角度扇区形状）。

    遮罩原理：对于每个像素 p 在角落区域：
      - r = 到该层圆心距离
      - d_p = R - r （该层自身坐标系下的深度，正值在弧内）
      - 保留条件：
        1) d_outer <= d_p < d_inner （径向范围属于该层）
        2) angle_p 在该角固定角度范围内
           （固定角度 = pieslice 角度，确保直线到圆弧的完整连接）

    注意：cx, cy 已经是该层独立的圆心位置（由调用方根据累计偏移调整），
    R 是该层的有效圆角半径（R_eff = R_total - cumulative_thickness）。
    返回 bool 数组 [H, W]，True 表示绘制区域。

    历史：早期版本使用 _angle_bottom/_angle_side 计算随深度收窄的角度，
    但这导致内层边框在圆弧上无法覆盖直线到圆弧的连接区域，
    产生白色扇形角和背景色漏出。修复为固定角度范围。
    """
    # 各角的固定角度范围（统一从 core.corner.algorithm.CORNER_ANGLES 取得，
    # 确保与 carve_corner_on_mask 的 pieslice 角度完全一致）
    ang_min, ang_max = CORNER_ANGLES[corner_key]

    # 只计算角落 ROI 以加速
    roi_x0 = max(0, cx - R)
    roi_y0 = max(0, cy - R)
    roi_x1 = min(w, cx + R + 1)
    roi_y1 = min(h, cy + R + 1)
    roi_w = roi_x1 - roi_x0
    roi_h = roi_y1 - roi_y0

    if roi_w <= 0 or roi_h <= 0:
        full_mask = np.zeros((h, w), dtype=bool)
        return full_mask

    yy, xx = np.mgrid[roi_y0:roi_y1, roi_x0:roi_x1].astype(np.float64)
    dx = xx - float(cx)
    dy = yy - float(cy)
    r = np.sqrt(dx * dx + dy * dy)
    d_p = float(R) - r  # 每个像素在该层坐标系下的深度

    # 条件 1: 径向属于该层（在该层的环形区域内）
    cond_r = (d_p >= d_outer) & (d_p < d_inner)

    # 条件 2: 角度在固定范围内（确保直线到圆弧的完整连接）
    # 用 atan2 计算角度 (dy, dx)，转为 0~360 度
    angle_p = np.degrees(np.arctan2(dy, dx))
    angle_p = np.mod(angle_p, 360.0)

    # 使用角度偏移法统一处理所有角（包括 tr 的 270°→360° 跨 0° 情况）
    # 将角度平移到以 ang_min 为起点的 [0°, 360°) 范围
    shifted_angle = np.mod(angle_p - ang_min, 360.0)
    angular_span = ang_max - ang_min
    # [Fix 白色接缝] 使用 <= 包含边界，确保圆弧与直边完全衔接
    cond_angle = shifted_angle <= angular_span

    roi_mask = cond_r & cond_angle

    full_mask = np.zeros((h, w), dtype=bool)
    full_mask[roi_y0:roi_y1, roi_x0:roi_x1] = roi_mask
    return full_mask


def _sample_border_color(
    src_img: Image.Image, corner_key: str,
    w: int, h: int, d_mid: float, d_thickness: float,
) -> tuple[int, int, int]:
    """
    从原图直线边框对应深度范围采样平均颜色，降低色差。

    [Fix B/S4 角落感知采样]：
      - 圆弧在某角只物理连接两条直线边（与角相邻的两条），
        因此只从这两条边采样才能得到"真正属于该边框层"的颜色。
        （若采样对面两条边，会采到图内部花纹/背景色，污染中位数，
         造成 TL/TR 偏灰、BL/BR 偏黑的色差现象。）
      - 从该两条边的厚度中心（d_mid）取 ±35% 厚度范围的像素行/列，
        避开抗锯齿过渡带（跳过最外层 2px）。
      - 对所有采样像素取中位数（median），对抗离群抗锯齿像素。
      - 采样位置：距角点 ≥ 1/4 边长的中段纯直线区域。
    """
    arr = np.array(src_img)
    thickness = max(1.0, float(d_thickness))
    samples: list[np.ndarray] = []

    # 采样深度：边框厚度中心 ±35% 的范围（避开最外层 2px 抗锯齿带）
    d_min = int(max(0, round(d_mid - thickness * 0.35)))
    d_max = int(min(max(d_min + 1, round(d_mid + thickness * 0.35)), max(w, h)))

    # 水平方向采样范围（顶/底边）：左右各留 25%，取中间 50%
    h_x0 = max(0, w * 1 // 4)
    h_x1 = min(w, w * 3 // 4)
    if h_x1 - h_x0 < 10:
        h_x0, h_x1 = max(0, w * 3 // 10), min(w, w * 7 // 10)

    # 垂直方向采样范围（左/右边）：上下各留 25%，取中间 50%
    v_y0 = max(0, h * 1 // 4)
    v_y1 = min(h, h * 3 // 4)
    if v_y1 - v_y0 < 10:
        v_y0, v_y1 = max(0, h * 3 // 10), min(h, h * 7 // 10)

    # 仅采样与该角相邻的两条直线边（物理上圆弧只与这两条边衔接）
    # TL: top + left    TR: top + right
    # BL: bottom + left  BR: bottom + right
    if corner_key == 'tl':
        sample_edges = ('top', 'left')
    elif corner_key == 'tr':
        sample_edges = ('top', 'right')
    elif corner_key == 'bl':
        sample_edges = ('bottom', 'left')
    else:  # br
        sample_edges = ('bottom', 'right')

    for edge in sample_edges:
        for d in range(d_min, d_max):
            if edge == 'top':
                # 距顶边 y=d 的行
                if 0 <= d < h and h_x1 > h_x0:
                    samples.append(arr[d, h_x0:h_x1, :])
            elif edge == 'bottom':
                # 距底边 y=h-1-d 的行
                y_pos = h - 1 - d
                if 0 <= y_pos < h and h_x1 > h_x0:
                    samples.append(arr[y_pos, h_x0:h_x1, :])
            elif edge == 'left':
                # 距左边 x=d 的列
                if 0 <= d < w and v_y1 > v_y0:
                    samples.append(arr[v_y0:v_y1, d, :])
            else:  # right
                # 距右边 x=w-1-d 的列
                x_pos = w - 1 - d
                if 0 <= x_pos < w and v_y1 > v_y0:
                    samples.append(arr[v_y0:v_y1, x_pos, :])

    if not samples:
        fallback_y = min(h - 1, max(0, int(round(d_mid))))
        fallback_x = min(w - 1, max(0, int(round(d_mid))))
        return tuple(arr[fallback_y, fallback_x, :].tolist())

    all_pixels = np.concatenate([s.reshape(-1, 3) for s in samples if s.size > 0], axis=0)
    if all_pixels.shape[0] == 0:
        fallback_y = min(h - 1, max(0, int(round(d_mid))))
        fallback_x = min(w - 1, max(0, int(round(d_mid))))
        return tuple(arr[fallback_y, fallback_x, :].tolist())

    # 取中位数（最鲁棒，对抗抗锯齿离群像素）
    median_color = np.median(all_pixels, axis=0)
    return tuple(int(round(v)) for v in median_color.tolist())


def _redraw_border_on_corner(
    result_img: Image.Image, corner_key: str,
    corner_radius_px: int,
    border_layers: list[tuple[tuple[int, int, int], int]],
    src_img: Image.Image | None = None,
    validity_mask: Image.Image | None = None,
    only_outermost: bool = False,
) -> None:
    """
    在圆角区域重新绘制边框颜色。

    修复设计（解决折叠感、边框不对齐、【装饰图案被纯色遮挡】）：
    1. 只在边框厚度范围内绘制边框颜色，超出边框层的深度保留原图内容
       （避免将内层图案/背景覆盖为边框颜色，消除"折叠"视觉）
    2. 使用 validity_mask 限制绘制区域，禁止向裁剪区（L形区域）绘制
       （确保裁剪区为背景色，边框只在圆弧可见区域内）
    3. [已修复] 角度边界包含 ang_max（TR角处理360°→0°环绕），消除圆弧与直边衔接处白色接缝
    4. [P0 Fix 遮挡] 智能保护装饰图案：只有当 src_img 对应像素颜色与目标
       边框色相近时才重绘为纯色边框色；若为花朵/点状线等装饰像素，
       保留原图内容不变（防止 6 万+ 像素被纯色覆盖造成遮挡）。

    Args:
        result_img: 结果图片（原地修改）
        corner_key: 角落标识 ('tl','tr','bl','br')
        corner_radius_px: 总圆角半径像素 (R_total)
        border_layers: 边框层 [(color_fallback, thickness), ...]
        src_img: 原图（用于采样颜色 + 判断是否为装饰像素）
        validity_mask: 可选，L模式。非零像素才允许修改
    """
    w, h = result_img.size
    if corner_radius_px <= 0 or not border_layers:
        return

    R_total = corner_radius_px
    # [Fix C1] 半径安全限制：与 carve_corner_on_mask / _build_multi_layer_corner_mask
    # 保持一致，防止 R > 半图导致圆心跑出图像外，进而把整图按"表层边框色"
    # 错误重绘（如 200x150 测试图中心被画成黑色）
    R_total = min(R_total, max(1, min(w, h) // 2))
    if R_total <= 0:
        return

    # 计算该角点的圆心位置
    if corner_key == 'tl':
        cx, cy = R_total, R_total
    elif corner_key == 'tr':
        cx, cy = w - R_total, R_total
    elif corner_key == 'bl':
        cx, cy = R_total, h - R_total
    else:  # br
        cx, cy = w - R_total, h - R_total

    result_arr = np.array(result_img)

    # 读取原图数组（用于判断像素是否与边框色一致）
    src_arr = None
    if src_img is not None:
        if src_img.size == result_img.size:
            src_arr = np.array(src_img)
        else:
            # 如果尺寸不一致，按结果图尺寸缩放原图以便坐标对齐
            src_resized = src_img.resize((w, h), Image.LANCZOS)
            src_arr = np.array(src_resized)

    # 构建 validity mask 数组（如果提供）
    validity_arr = None
    if validity_mask is not None:
        validity_arr = np.array(validity_mask, dtype=bool)

    # 构建各边框层的累积厚度
    cumulative_depths = [0]
    for _, thickness in border_layers:
        cumulative_depths.append(cumulative_depths[-1] + thickness)
    total_border_depth = cumulative_depths[-1]

    # 构建颜色映射表（仅映射到边框厚度范围）
    depth_mapping = {}
    for d in range(total_border_depth + 1):
        color_idx = 0
        for i in range(len(border_layers)):
            cum_i = cumulative_depths[i]
            cum_next = cumulative_depths[i + 1]
            if cum_i <= d < cum_next:
                color_idx = i
                break
            elif d >= cum_next and i == len(border_layers) - 1:
                color_idx = i
        depth_mapping[d] = color_idx

    # 预提取所有边框层颜色，用于装饰像素保护判断
    border_colors_arr = np.array(
        [np.array(c, dtype=np.float64) for c, _ in border_layers]
    )
    # 颜色距离阈值：与 BORDER_COLOR_DISTANCE_THRESHOLD 一致
    COLOR_DIST_THRESHOLD = 15.0
    # 层边界过渡带阈值（相邻层之间允许稍宽的匹配范围）
    TRANSITION_THRESHOLD = 25.0

    # [Fix D 升级：密集均匀采样 + 全局中值 提取内容参考色]
    # 为什么用密集采样？
    #   产品图内区可能有多张装饰卡/大花朵，少量分散点容易踩到装饰元素。
    # 策略：
    #   在「内容安全区」(15%~85% × 15%~85%) 内取 21×21=441 个均匀格点，
    #   每个格点取单像素，拼成 (N,3) 大矩阵后取 RGB 各通道中值。
    #   由于装饰卡片/花在整图中通常只占 <25% 面积，>75% 样本是真正的内容奶油色，
    #   中值不可能被少数"装饰色"样本拉偏 — 抗噪性远胜单点/五点采样。
    #   注：15%~85% 是为了避开靠近边框的外米色层/花漾之约的花朵延伸。
    def _sample_content_ref(arr: np.ndarray, ww: int, hh: int) -> np.ndarray:
        x_start = int(ww * 0.15)
        x_end = int(ww * 0.85)
        y_start = int(hh * 0.15)
        y_end = int(hh * 0.85)
        STEPS = 21  # 21x21 = 441 样本
        xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, ww - 1)
        ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, hh - 1)
        gx, gy = np.meshgrid(xs, ys)
        samples = arr[gy, gx, :].reshape(-1, 3).astype(np.float64)
        if samples.shape[0] == 0:
            return np.array([255.0, 255.0, 255.0])
        return np.median(samples, axis=0)

    content_ref_arr: np.ndarray | None = None
    if src_arr is not None:
        content_ref_arr = _sample_content_ref(src_arr, w, h)
    if content_ref_arr is None:
        content_ref_arr = _sample_content_ref(result_arr, w, h)

    # [Fix 墨上花开 5cm 圆角过厚 + Fix 安妮森林装饰文字被误清] 基于内容参考色检测间隙层
    # 间隙层：与内容参考色颜色距离 < 60 的层，且不是最外层边框
    # 同时新增规则：如果累计深度已超过最大边框深度(圆角半径的70% 或 5cm上限)，
    # 则后续层视为内容层 —— 但仍走 Smart Gap Check v2，有装饰的话会保留。
    GAP_COLOR_DIST = 60.0
    GAP_MAX_THICKNESS = 30.0
    # [Fix 安妮森林] 从 50% → 70% 放宽：避免窄图(如安妮森林 43cm高+5cm半径)
    #   的文字装饰带被过深地强制归为间隙；即使强制为间隙，v2也会区分装饰，
    #   但放宽能减少不必要的边界效应。同时加硬上限 ~3cm (150DPI下 ~177px)。
    MAX_BORDER_DEPTH_RATIO = 0.7
    MAX_BORDER_DEPTH_HARD_PX = 177  # 约 3cm @ 150DPI，绝对上限
    
    # 计算有效边框深度
    capped_by_ratio = MAX_BORDER_DEPTH_RATIO * R_total
    effective_border_depth = min(MAX_BORDER_DEPTH_HARD_PX, capped_by_ratio, total_border_depth)
    
    is_gap_layer = []
    for i, (c, t) in enumerate(border_layers):
        dist_to_content = float(np.sqrt(np.sum((np.array(c, dtype=np.float64) - content_ref_arr) ** 2)))
        # 条件：颜色距离 < 阈值 AND 厚度 < 阈值 AND 不是最外层
        # 新增：如果累计深度已超过有效边框深度，后面的层一律视为间隙
        cum_before = cumulative_depths[i]
        forced_gap = cum_before >= effective_border_depth
        
        is_gap = (
            dist_to_content < GAP_COLOR_DIST and
            t <= GAP_MAX_THICKNESS and
            i > 0  # 最外层（黑边框）不作为间隙层
        ) or forced_gap
        is_gap_layer.append(is_gap)
    
    gap_regions: list[tuple[int, int]] = []
    for i, is_gap in enumerate(is_gap_layer):
        if is_gap:
            gap_regions.append((cumulative_depths[i], cumulative_depths[i + 1]))

    # [Smart Gap Check] 构建不含间隙层的边框颜色数组，用于间隙区域装饰检测
    # 间隙层颜色与 content_ref 相同，若包含会导致间隙像素被误判为"接近边框色"
    solid_border_colors_arr = np.array(
        [np.array(c, dtype=np.float64) for (c, _), ig in zip(border_layers, is_gap_layer) if not ig]
    )

    # 各角的角度范围（与 carve_corner_on_mask / _build_multi_layer_corner_mask 完全一致）
    ang_min, ang_max = CORNER_ANGLES[corner_key]

    # 计算处理区域（圆心附近的 R×R 区域）
    x1 = max(0, cx - R_total)
    y1 = max(0, cy - R_total)
    x2 = min(w, cx + R_total + 1)
    y2 = min(h, cy + R_total + 1)

    if x2 <= x1 or y2 <= y1:
        return

    # 提取区域坐标网格
    yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)

    # 计算每个像素到圆心的距离和角度
    dx = xx - float(cx)
    dy = yy - float(cy)
    dist = np.sqrt(dx * dx + dy * dy)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)

    # 计算每个像素的深度（到弧线的距离，弧线上 depth=0）
    depth = float(R_total) - dist

    # 确定该角的有效绘制区域
    # 条件：角度在范围内 + 在圆弧内部、弧线上或弧外 2px 容差带（dist <= R_total + 2.0）
    # 注意：扩展到弧外 2px 是为了补偿像素离散化误差，确保弧线上的像素被正确着色
    #
    # [Fix C-shaped gap 0811] 扩展 2px 容差：
    #   当映射连续极坐标(角度+半径)到离散像素坐标时，实际距离可能略大于理想半径，
    #   导致弧线上的像素未被边框重绘覆盖，形成 C 形缺口。
    #   增加 2px 容差确保这些像素被包含在绘制区域内，同时 validity_mask
    #   会限制实际修改范围不超过应有区域。
    #
    # [Fix 白色直角线条] 包含两个角度边界：
    #   旧版 (angle >= ang_min) & (angle < ang_max) 排除了 ang_max 边界像素，
    #   导致圆弧与直边衔接处出现 1px 白色接缝。
    #   修复：使用 <= ang_max 包含边界；对 TR 角（ang_max=360°）处理 360°→0° 环绕。
    if ang_max == 360:
        # TR corner: 270°→360°，360° 在 np.mod 后变成 0°
        # 包含 ang_min (>=270) 和 ang_max (==0)
        valid_angle = (angle >= ang_min) | (angle < 1)
    else:
        # 其他角: [ang_min, ang_max] 包含两端边界
        valid_angle = (angle >= ang_min) & (angle <= ang_max)
    valid_region = valid_angle & (dist <= R_total + 2.0)

    # 如果有 validity_mask，进一步限制为 mask 中非零像素
    if validity_arr is not None:
        local_validity = validity_arr[y1:y2, x1:x2]
        valid_region = valid_region & local_validity

    # [Fix 白色扇形角遮挡问题 0812v2]
    # 根因分析：
    #   旧代码将间隙层和内层边框层清除为背景色(白色)，导致白色扇形角遮挡原图内容
    #   - 间隙层清除：将米色/白色间隙填充为白色 → 白色遮挡
    #   - inner_clear_mask：将最外层以外所有像素填充为白色 → 白色扇形角
    #
    # 修复策略：
    #   1. 不清除任何间隙层或内层边框为背景色
    #   2. 让原图内容(间隙、内层边框、图案)自然显示
    #   3. 仅通过 skip 逻辑跳过间隙层和内层边框的绘制
    #   4. 最外层黑色边框正常绘制为圆弧
    #
    # 效果：
    #   - 花漾之约：小圆点装饰保留
    #   - 奥斯汀：棕色边框颜色保留
    #   - 中古大花：花纹图案保留
    #   - 塞纳时光：内层米色边框保留
    #   - 中古花园：第二层黑色边框保留

    # === [Fix 只保留最外层边框] 智能重绘 ===
    for d in range(total_border_depth):
        d_region = valid_region & (depth >= d) & (depth < d + 1)
        if not np.any(d_region):
            continue

        color_idx = depth_mapping.get(d, 0)
        is_gap = is_gap_layer[color_idx] if color_idx < len(is_gap_layer) else False

        # [Fix 白色扇形角遮挡 0812v2]
        # 间隙层(is_gap=True) AND 非最外层边框层(color_idx!=0) 都跳过绘制
        # - 间隙层：跳过绘制，让原图间隙颜色自然显示（不产生独立弧线）
        # - 内层边框层：跳过绘制，让原图边框颜色自然显示
        # 结果：仅最外层黑色边框被绘制为圆弧，其余内容保持原图状态
        if is_gap:
            continue
        if only_outermost and color_idx != 0:
            continue

        # [Fix G/S7] 非间隙层：使用该层颜色作为目标绘制色
        target_color = border_layers[color_idx][0] if color_idx < len(border_layers) else (255, 255, 255)
        target_color_arr = np.array(target_color, dtype=np.float64)

        # 获取本层所有候选像素的坐标
        local_coords = np.where(d_region)
        if len(local_coords[0]) == 0:
            continue

        # [Fix G/S7+] 防止非间隙层覆盖间隙区域的像素
        # 例如：棕色边框不应覆盖黑色边框与棕色之间的米色间隙
        pixel_depths = float(R_total) - dist[local_coords]
        in_gap_region = np.zeros(len(local_coords[0]), dtype=bool)
        for gap_start, gap_end in gap_regions:
            in_gap_region |= (pixel_depths >= gap_start) & (pixel_depths < gap_end)

        if not is_gap and np.any(in_gap_region):
            # 非间隙层：过滤掉间隙区域的像素
            keep_mask = ~in_gap_region
            if not np.any(keep_mask):
                continue
            local_coords = (local_coords[0][keep_mask], local_coords[1][keep_mask])

        global_y = local_coords[0] + y1
        global_x = local_coords[1] + x1

        # 装饰像素保护逻辑
        if src_arr is not None:
            # 取出 src_img 上对应位置的所有像素颜色
            src_pixels = src_arr[global_y, global_x, :].astype(np.float64)  # [N, 3]

            # 计算与目标边框层颜色的距离
            dist_to_target = np.sqrt(
                np.sum((src_pixels - target_color_arr.reshape(1, 3)) ** 2, axis=1)
            )  # [N]

            # [Fix C2] 严格的相邻层匹配：只允许"当前层或其直接邻居"通过过滤。
            dists_nearby = [dist_to_target]
            if color_idx + 1 < len(border_colors_arr):
                next_color_arr = border_colors_arr[color_idx + 1]
                dist_to_next = np.sqrt(
                    np.sum((src_pixels - next_color_arr.reshape(1, 3)) ** 2, axis=1)
                )
                dists_nearby.append(dist_to_next)
            if color_idx - 1 >= 0:
                prev_color_arr = border_colors_arr[color_idx - 1]
                dist_to_prev = np.sqrt(
                    np.sum((src_pixels - prev_color_arr.reshape(1, 3)) ** 2, axis=1)
                )
                dists_nearby.append(dist_to_prev)

            min_dist_adjacent = np.min(np.stack(dists_nearby, axis=0), axis=0)
            threshold = TRANSITION_THRESHOLD if len(dists_nearby) > 1 else COLOR_DIST_THRESHOLD
            match_filter = min_dist_adjacent <= threshold

            # [Fix 0811 角度边界兜底]：距弧角边界 ±1.5° 内的像素（抗锯齿带）
            # 对于非间隙的边框层，强制匹配 —— 这些像素在弧线边缘，
            # 抗锯齿中间色可能不匹配边框颜色，导致白色直角边框线残留。
            if not is_gap and len(local_coords[0]) > 0:
                pixel_angles = angle[local_coords]
                ang_a = ang_min
                ang_b = ang_max
                if ang_max == 360:
                    d_to_max = np.minimum(np.abs(pixel_angles - ang_a), pixel_angles)
                else:
                    d_to_max = np.abs(pixel_angles - ang_b)
                d_to_min = np.abs(pixel_angles - ang_a)
                near_boundary = (d_to_min <= 1.5) | (d_to_max <= 1.5)
                match_filter = match_filter | near_boundary

            # === [Fix D 升级: 结构感知的混合策略] ===
            # 同时解决三大问题：
            #   A. 细双线对角内区 C 形缺口（塞纳时光/墨上花开 5cm 圆角）
            #   B. 点状线边框被强制绘成实线（花漾之约）
            #   C. 多层边框最外层颜色被内层覆盖（塞纳时光8cm黑边→棕）
            #
            # [Fix P0-9 对角内区覆盖兜底]
            # 核心修改：**对角内区必须与直边同深度区域颜色一致** —— 这是一个
            # 比 structure_allow 更强的不变量。
            #
            #   当 color_idx 是有色边框（非间隙）：
            #     - 先跑 structure_allow 的深度带 + fallback（尽量保护点线不被
            #       实心覆盖）；但 structure_allow 全 fail 时也不能让对角内
            #       区"露花纹色"——改为采用直边同深度的真实颜色填充。
            #   当 color_idx 是间隙层：
            #     - 旧版 diagonal_interior 上 base_force=False 且 match_filter
            #       也可能因为颜色色差（米色 gap ≈ RGB(245,235,215) vs content_ref
            #       = (245,245,245) Δ=31）而过滤失败 → 结果对角内区留着
            #       paste 过来的 src 花纹色（视觉上 = 外层黑框的 C 形缺口！
            #       因为黑框在对角线上本该是连续的，中间被花纹色戳了一段）。
            #     - 新版：间隙层在 diagonal_interior 上一律用 target_color（
            #       内容参考色）兜底覆盖。花纹区只属于「深度 > total_border_depth」
            #       的像素，不会被本循环处理。
            cum_before_i = cumulative_depths[color_idx]
            cum_after_i = cumulative_depths[color_idx + 1]

            if corner_key == 'tl':
                x_left, x_right = cum_before_i, cum_after_i
                y_top, y_bottom = cum_before_i, cum_after_i
                in_left_strip = (global_x >= x_left) & (global_x < x_right)
                in_top_strip = (global_y >= y_top) & (global_y < y_bottom)
                in_extension = in_left_strip | in_top_strip
            elif corner_key == 'tr':
                x_left, x_right = w - cum_after_i, w - cum_before_i
                y_top, y_bottom = cum_before_i, cum_after_i
                in_right_strip = (global_x >= x_left) & (global_x < x_right)
                in_top_strip = (global_y >= y_top) & (global_y < y_bottom)
                in_extension = in_right_strip | in_top_strip
            elif corner_key == 'bl':
                x_left, x_right = cum_before_i, cum_after_i
                y_top, y_bottom = h - cum_after_i, h - cum_before_i
                in_left_strip = (global_x >= x_left) & (global_x < x_right)
                in_bottom_strip = (global_y >= y_top) & (global_y < y_bottom)
                in_extension = in_left_strip | in_bottom_strip
            else:  # br
                x_left, x_right = w - cum_after_i, w - cum_before_i
                y_top, y_bottom = h - cum_after_i, h - cum_before_i
                in_right_strip = (global_x >= x_left) & (global_x < x_right)
                in_bottom_strip = (global_y >= y_top) & (global_y < y_bottom)
                in_extension = in_right_strip | in_bottom_strip

            diagonal_interior = ~in_extension

            # 判断当前层是否有色边框
            layer_to_content_dist = float(np.sqrt(
                np.sum((target_color_arr - content_ref_arr) ** 2)
            ))
            is_colored_border = layer_to_content_dist > COLOR_DIST_THRESHOLD

            # 基础 force_paint: 有色边框的对角内区（先假设全要画）
            base_force = diagonal_interior if is_colored_border \
                else np.zeros_like(diagonal_interior, dtype=bool)

            # =========== 结构感知修正: Straight-Edge Pattern Check ===========
            # [Fix 墨上花开半角弧线缺口] 升级为深度带采样：
            #   旧版仅检查 d_int_v 单深度，若刚好命中间隙层/抗锯齿过渡带会误判为间隙
            #   → 漏绘造成透明弧线缺口（半径 5cm 时对角内区面积大，命中概率高）
            # 新版：
            #   A) 对每个 force_paint 候选像素，采样 d_int_v ±2px 的深度带
            #      只要 ±2px 内任一深度 + 任一边判定为"实心/有色"，就 structure_allow=True
            #   B) Fallback 兜底：对角内区有色边框像素，若 A) 失败但 src 颜色与任一边框色
            #      距离 < 更宽阈值(35) 且与内容色距离 > 阈值，仍视为实心（抗锯齿中间色保护）
            structure_allow = np.ones_like(base_force, dtype=bool)
            n_force = int(np.sum(base_force))
            if n_force > 0 and src_arr is not None:
                force_idx = np.where(base_force)
                y_f = global_y[force_idx]
                x_f = global_x[force_idx]
                cx_map = {
                    'tl': R_total, 'tr': w - R_total,
                    'bl': R_total, 'br': w - R_total,
                }
                cy_map = {
                    'tl': R_total, 'tr': R_total,
                    'bl': h - R_total, 'br': h - R_total,
                }
                cxv = cx_map[corner_key]
                cyv = cy_map[corner_key]
                dxv = x_f.astype(np.float64) - float(cxv)
                dyv = y_f.astype(np.float64) - float(cyv)
                dist_v = np.sqrt(dxv * dxv + dyv * dyv)
                depth_v = float(R_total) - dist_v
                d_int_v = np.clip(np.round(depth_v).astype(np.int32), 0, total_border_depth)
                N = len(d_int_v)

                # ===== [A) 深度带 ±2px 多采样] =====
                DEPTH_BAND = 2
                STRUCT_THRESH = max(COLOR_DIST_THRESHOLD + 5.0, 20.0)
                allow_v = np.zeros(N, dtype=bool)

                for d_off in range(-DEPTH_BAND, DEPTH_BAND + 1):
                    d_band = np.clip(d_int_v + d_off, 0, total_border_depth)
                    for edge_k in range(2):  # 两条相邻边
                        if corner_key == 'tl':
                            if edge_k == 0:  # top
                                sy = np.clip(d_band, 0, h - 1)
                                sx = np.full(N, min(max(R_total + 5, w // 2), w - 1), dtype=np.int64)
                            else:  # left
                                sx = np.clip(d_band, 0, w - 1)
                                sy = np.full(N, min(max(R_total + 5, h // 2), h - 1), dtype=np.int64)
                        elif corner_key == 'tr':
                            if edge_k == 0:  # top
                                sy = np.clip(d_band, 0, h - 1)
                                sx = np.full(N, max(w - R_total - 6, w // 2), dtype=np.int64)
                            else:  # right
                                sx = np.clip(w - 1 - d_band, 0, w - 1)
                                sy = np.full(N, min(max(R_total + 5, h // 2), h - 1), dtype=np.int64)
                        elif corner_key == 'bl':
                            if edge_k == 0:  # left
                                sx = np.clip(d_band, 0, w - 1)
                                sy = np.full(N, max(h - R_total - 6, h // 2), dtype=np.int64)
                            else:  # bottom
                                sy = np.clip(h - 1 - d_band, 0, h - 1)
                                sx = np.full(N, min(max(R_total + 5, w // 2), w - 1), dtype=np.int64)
                        else:  # br
                            if edge_k == 0:  # bottom
                                sy = np.clip(h - 1 - d_band, 0, h - 1)
                                sx = np.full(N, max(w - R_total - 6, w // 2), dtype=np.int64)
                            else:  # right
                                sx = np.clip(w - 1 - d_band, 0, w - 1)
                                sy = np.full(N, max(h - R_total - 6, h // 2), dtype=np.int64)

                        edge_colors = src_arr[sy, sx, :].astype(np.float64)  # [N,3]
                        d2layer = np.sqrt(np.sum(
                            (edge_colors - target_color_arr.reshape(1, 3)) ** 2, axis=1
                        ))
                        min_adj = d2layer.copy()
                        if color_idx + 1 < len(border_colors_arr):
                            nc = border_colors_arr[color_idx + 1].reshape(1, 3)
                            d2n = np.sqrt(np.sum((edge_colors - nc) ** 2, axis=1))
                            min_adj = np.minimum(min_adj, d2n)
                        if color_idx - 1 >= 0:
                            pc = border_colors_arr[color_idx - 1].reshape(1, 3)
                            d2p = np.sqrt(np.sum((edge_colors - pc) ** 2, axis=1))
                            min_adj = np.minimum(min_adj, d2p)
                        d2cont = np.sqrt(np.sum(
                            (edge_colors - content_ref_arr.reshape(1, 3)) ** 2, axis=1
                        ))
                        is_solid = (min_adj <= STRUCT_THRESH) | (d2cont > STRUCT_THRESH + 5)
                        allow_v |= is_solid

                # ===== [B) Fallback 兜底：对角内区有色边框像素颜色检查] =====
                # 深度带采样仍失败（全是间隙判定）时，看 src 弧上像素本身的颜色：
                # 若该像素离任一边框层近 < WIDE_THRESH 且 离内容色远，说明是抗锯齿中间色
                # （真实边框像素被平滑处理过），仍然允许绘制，避免缺口
                WIDE_BORDER_THRESH = 35.0
                if not np.all(allow_v):
                    need_fb = ~allow_v
                    fb_y = y_f[need_fb]
                    fb_x = x_f[need_fb]
                    if len(fb_y) > 0:
                        fb_src = src_arr[fb_y, fb_x, :].astype(np.float64)
                        # 离所有边框层的最小距离
                        min_d_border = np.full(len(fb_y), 1e9, dtype=np.float64)
                        for bc_arr in border_colors_arr:
                            d_ = np.sqrt(np.sum(
                                (fb_src - bc_arr.reshape(1, 3)) ** 2, axis=1
                            ))
                            min_d_border = np.minimum(min_d_border, d_)
                        # 离内容色距离
                        d_cont_fb = np.sqrt(np.sum(
                            (fb_src - content_ref_arr.reshape(1, 3)) ** 2, axis=1
                        ))
                        fb_ok = (min_d_border <= WIDE_BORDER_THRESH) & \
                                (d_cont_fb > COLOR_DIST_THRESHOLD)
                        allow_v[need_fb] |= fb_ok

                structure_allow_idx = structure_allow[force_idx]
                structure_allow_idx &= allow_v
                structure_allow[force_idx] = structure_allow_idx

            # ===== [先计算 force_paint：双重否定逻辑必须依赖 force_paint 的最终值] =====
            # [Fix P0] 严重顺序 bug：旧代码在双重否定逻辑（上面）中先枚举 force_paint，
            # 再在下面赋值 → UnboundLocalError（花野案例崩溃），且即使不崩溃
            # 也是使用"上一轮循环 d-1 的 force_paint"，导致对角内区双重否定
            # 检测完全错误，出现 C 形缺口（墨上花开）。
            #
            # [Fix 0811 强力对角内区兜底]：对于有色边框层（非间隙），
            # 只要像素位于本层深度区间且在对角内区，无条件强制绘色，
            # 绕过 structure_allow 的脆弱性。这是解决 C 形缺口的核心修复。
            force_paint = base_force & structure_allow

            # [Fix 0811] 有色边框层：强制所有对角内区本层深度像素
            if is_colored_border and not is_gap and len(local_coords[0]) > 0:
                pixel_depths = float(R_total) - dist[local_coords]
                d_start_layer = cumulative_depths[color_idx]
                d_end_layer = cumulative_depths[color_idx + 1]
                in_this_layer_depth = (pixel_depths >= d_start_layer) & (pixel_depths < d_end_layer)
                force_paint = force_paint | (diagonal_interior & in_this_layer_depth)

            # [Fix P0-9 末尾] 对角内区的强制覆盖兜底
            # 规则：
            #  - 对于有色边框层 (is_colored_border)：diagonal_interior 上如果
            #    structure_allow 拒绝，但 match_filter 也没通过（= 该像素在 src
            #    中既不像边框色，也不像直边同深度的实心色 → 典型"花纹色泄漏"），
            #    这时用「直边同深度的真实像素颜色」覆盖，而不是 target_color
            #    （直边同深度可能是点状线，这样保留图案）。
            #  - 对于间隙层 (is_gap)：diagonal_interior 上直接强制
            #    用 target_color（间隙色 = content_ref）覆盖。match_filter 可能
            #    因色差(米色 245,235,215 vs 纯白 245,245,245)失败，若不兜底，
            #    对角内区会露出花纹色 → 视觉上是「外层黑框 C 形缺口」。
            diagonal_interior_idx = np.where(diagonal_interior)[0]
            if len(diagonal_interior_idx) > 0:
                if is_gap:
                    # [Fix 堇色素颜内层底色弧形缺口]
                    # 间隙层对角内区兜底填充必须限制在本层深度区间内！
                    # 旧代码：对所有 diagonal_interior 像素（可能覆盖整个扇形 99% 面积）
                    #   一律填间隙色。即使间隙层只有 0.3cm 厚，也会把装饰带区域
                    #   （depth 1~7.5cm，远超间隙层厚度）的花纹全填成米色（内容色），
                    #   形成用户看到的"内层底色弧形缺口"。
                    # 新代码：仅当像素 depth ∈ [cumulative_depths[color_idx],
                    #   cumulative_depths[color_idx+1])，即深度真正落在本间隙层
                    #   的厚度范围内时，才填间隙色进行兜底。
                    gap_pixel_depths_all = float(R_total) - dist[local_coords]
                    di_depths = gap_pixel_depths_all[diagonal_interior_idx]
                    d_start_layer = cumulative_depths[color_idx]
                    d_end_layer = cumulative_depths[color_idx + 1]
                    in_this_layer = (di_depths >= d_start_layer) & \
                                    (di_depths < d_end_layer)
                    fill_idx = diagonal_interior_idx[in_this_layer]

                    if len(fill_idx) > 0:
                        gap_fill_y = global_y[fill_idx]
                        gap_fill_x = global_x[fill_idx]
                        result_arr[gap_fill_y, gap_fill_x, :] = np.array(
                            target_color, dtype=result_arr.dtype
                        ).reshape(1, 3)
                        # 从后续 match_filter | force_paint 流程中移除这些像素
                        # (已经手动填好了)
                        di_mask_set = set(fill_idx.tolist())
                        keep_bool = np.array(
                            [i not in di_mask_set for i in range(len(global_y))],
                            dtype=bool
                        )
                        global_y = global_y[keep_bool]
                        global_x = global_x[keep_bool]
                        # 同步裁剪 force_paint 和 match_filter
                        if len(force_paint) == len(keep_bool):
                            force_paint = force_paint[keep_bool]
                        if len(match_filter) == len(keep_bool):
                            match_filter = match_filter[keep_bool]
                        if len(global_y) == 0:
                            continue
                elif is_colored_border:
                    # 有色边框层对角内区：对 (force_paint=False 且
                    # match_filter=False) 的"双重否定"像素用直边真实色覆盖
                    # （避免花纹色泄漏，也保留直边的图案性如点线）
                    d_straight = int(round(np.mean(depth[local_coords][diagonal_interior_idx])))
                    d_straight = np.clip(d_straight, 0, total_border_depth - 1)
                    cand_colors = []
                    if corner_key in ('tl', 'tr'):
                        ty = min(max(d_straight, 0), h - 1)
                        tx = min(max(w // 2, 0), w - 1)
                        cand_colors.append(src_arr[ty, tx, :].astype(np.float64))
                    if corner_key in ('bl', 'br'):
                        by = min(max(h - 1 - d_straight, 0), h - 1)
                        bx = min(max(w // 2, 0), w - 1)
                        cand_colors.append(src_arr[by, bx, :].astype(np.float64))
                    if corner_key in ('tl', 'bl'):
                        ly = min(max(h // 2, 0), h - 1)
                        lx = min(max(d_straight, 0), w - 1)
                        cand_colors.append(src_arr[ly, lx, :].astype(np.float64))
                    if corner_key in ('tr', 'br'):
                        ry = min(max(h // 2, 0), h - 1)
                        rx_v = min(max(w - 1 - d_straight, 0), w - 1)
                        cand_colors.append(src_arr[ry, rx_v, :].astype(np.float64))
                    if cand_colors:
                        straight_color = np.median(
                            np.stack(cand_colors, axis=0), axis=0
                        )
                        n_all = len(global_y)
                        diag_set = set(diagonal_interior_idx.tolist())
                        double_neg = np.zeros(n_all, dtype=bool)
                        if len(match_filter) == n_all and len(force_paint) == n_all:
                            for i in range(n_all):
                                if i in diag_set and not force_paint[i] and not match_filter[i]:
                                    double_neg[i] = True
                            if np.any(double_neg):
                                dn_y = global_y[double_neg]
                                dn_x = global_x[double_neg]
                                sc = np.array(
                                    [int(round(v)) for v in straight_color],
                                    dtype=result_arr.dtype
                                )
                                result_arr[dn_y, dn_x, :] = sc.reshape(1, 3)
            # 兼容：如果 global_y 长度 < len(match_filter)（前面间隙层裁剪过），
            # 则裁剪 match_filter 到相同长度
            if len(match_filter) > len(global_y):
                match_filter = match_filter[:len(global_y)]

            # [Fix 最外层边框弧线绘制 0812v3]
            # 问题：match_filter/force_paint 逻辑对最外层边框(color_idx=0)过于严格，
            #       因为圆角弧线上的原始像素通常是内容色而非边框色，导致黑色边框弧线
            #       无法绘制，圆角处露出原图内容色而非黑色边框。
            # 修复：最外层边框(color_idx=0)始终绘制，跳过 match_filter/force_paint 过滤。
            #       最外层边框定义了图像边界，必须在圆角弧线上完整绘制。
            if color_idx == 0 and not is_gap:
                # 最外层非间隙边框：绘制整个 d_region 区域
                final_mask = np.ones(len(global_y), dtype=bool)
            else:
                final_mask = match_filter | force_paint[:len(match_filter)] if len(force_paint) >= len(match_filter) else match_filter

            # 只对判定为"真正边框像素"的坐标应用纯色覆盖
            # 装饰像素（花朵/点线）不在此范围内 → 保留 result_img 中 paste 过来的原图内容
            if not np.any(final_mask):
                continue

            apply_y = global_y[final_mask]
            apply_x = global_x[final_mask]
            color_fill = np.array(target_color, dtype=result_arr.dtype)
            result_arr[apply_y, apply_x, :] = color_fill.reshape(1, 3)
        else:
            # Fallback：没有 src_img 时，仍按原逻辑覆盖（避免回归）
            color_fill = np.array(target_color, dtype=result_arr.dtype)
            result_arr[global_y, global_x, :] = color_fill.reshape(1, 3)

    # 回写结果
    new_img = Image.fromarray(result_arr.astype(np.uint8), mode='RGB')
    result_img.paste(new_img)
