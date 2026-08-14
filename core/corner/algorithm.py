"""
core/corner/algorithm.py
统一的圆角 mask 算法模块。

消除 image_cropper.py 与 geometry.py 中 5 份重复的"挖正方形 + 填回 1/4 圆"逻辑。
所有圆角处理必须经过本模块，确保一致性（见 project_memory：三文件一致性警告）。

PIL 屏幕坐标系（y 向下）pieslice 角度映射：
  0° = 右, 90° = 下, 180° = 左, 270° = 上

单步扇形切割算法（取代早期两步法）：
  1. 先把角落 r×r 正方形设为 0（切掉尖角）
  2. 再用 pieslice 把"矩形内部的 1/4 圆"填回 255（保留圆弧）
  3. 后处理填充圆弧边界缺失像素（解决 C 形缺口）
  切掉的是 L 形（正方形减去 1/4 圆），即只切掉尖角，保留圆弧。
  圆心在正方形的"内角"顶点（即矩形内部那个角），bbox 以该圆心为中心。

向后兼容：原 core/rounded_corner.py 已改为薄重导出 shim，旧导入路径继续可用。
"""
from __future__ import annotations
import numpy as np
from PIL import Image, ImageDraw


# 各角的固定角度范围（PIL 屏幕坐标系，y 向下）
# tl: 180°→270° (左→上)，圆心在 (x+r, y+r)
# tr: 270°→360° (上→右)，圆心在 (x+w-r, y+r)
# bl: 90°→180° (下→左)，圆心在 (x+r, y+h-r)
# br: 0°→90° (右→下)，圆心在 (x+w-r, y+h-r)
CORNER_ANGLES: dict[str, tuple[int, int]] = {
    'tl': (180, 270),
    'tr': (270, 360),
    'bl': (90, 180),
    'br': (0, 90),
}


def get_corner_square(rect: tuple, corner_key: str, r: int) -> tuple[int, int, int, int]:
    """
    计算指定角落的 r×r 正方形区域（要被挖空的部分）。

    Args:
        rect: (x, y, w, h) 目标矩形（左上角 + 宽高）
        corner_key: 'tl' | 'tr' | 'bl' | 'br'
        r: 圆角半径（像素）

    Returns:
        (x1, y1, x2, y2) 像素坐标
    """
    x, y, w, h = rect
    if corner_key == 'tl':
        return (x, y, x + r, y + r)
    elif corner_key == 'tr':
        return (x + w - r, y, x + w, y + r)
    elif corner_key == 'bl':
        return (x, y + h - r, x + r, y + h)
    else:  # br
        return (x + w - r, y + h - r, x + w, y + h)


def get_corner_pieslice_bbox(rect: tuple, corner_key: str, r: int) -> tuple[int, int, int, int]:
    """
    计算 pieslice 的 bounding box（圆心在正方形内角顶点）。

    圆心位置：
      tl: (x+r, y+r)    tr: (x+w-r, y+r)
      bl: (x+r, y+h-r)  br: (x+w-r, y+h-r)
    bbox = [cx-r, cy-r, cx+r, cy+r]

    Args:
        rect: (x, y, w, h) 目标矩形
        corner_key: 'tl' | 'tr' | 'bl' | 'br'
        r: 圆角半径（像素）

    Returns:
        (x1, y1, x2, y2) pieslice bbox
    """
    x, y, w, h = rect
    if corner_key == 'tl':
        cx, cy = x + r, y + r
    elif corner_key == 'tr':
        cx, cy = x + w - r, y + r
    elif corner_key == 'bl':
        cx, cy = x + r, y + h - r
    else:  # br
        cx, cy = x + w - r, y + h - r
    return (cx - r, cy - r, cx + r, cy + r)


def carve_corner_on_mask(
    mask: Image.Image,
    rect: tuple,
    corners: dict,
    canvas_size: tuple[int, int] | None = None,
) -> None:
    """
    在已有的 L 模式 mask 上，对指定矩形的四个角刻出圆角（原地修改）。

    纯 numpy 距离场算法（v2，2026-08-14 重写）：
      对每个圆角 r、圆心 (cx, cy)：
        1. 将 r×r corner square 内的所有像素先设为 0（挖掉尖角）
        2. 用距离公式 dist(px,py) ≤ r 且 px/py 在对应象限 → 填回 255（保留 1/4 圆弧）
      几何上保证过渡点无 C 形缺口、无过绘、对 TR/BR 角不外溢到外框交界。
      不再依赖 PIL pieslice 的栅格化（其在 R 较大时会产生锯齿与过画）。

    本函数是项目中所有圆角处理的唯一入口，确保 image_cropper.py、
    geometry.py、process_image.py 三处的圆角逻辑完全一致。

    Args:
        mask: PIL 'L' 模式 mask（255=保留, 0=裁掉）
        rect: (x, y, w, h) 目标矩形坐标（左上角 + 宽高，整数对齐）
        corners: {corner_key: radius_px} 圆角半径（像素）；None 或 <= 0 的角跳过
        canvas_size: 可选，用于 clip 坐标到合法范围；None 则用 mask.size
    """
    W, H = canvas_size if canvas_size is not None else mask.size
    x, y, w, h = rect

    # 以 numpy 数组为唯一真源，最后再回写 PIL Image
    mask_arr = np.array(mask)
    if mask_arr.dtype != np.uint8:
        mask_arr = mask_arr.astype(np.uint8)

    for corner_key in ('tl', 'tr', 'bl', 'br'):
        r = corners.get(corner_key, 0)
        if r is None or r <= 0:
            continue
        r_px = max(1, int(round(r)))

        # 限制半径不超过矩形一半，避免越界
        max_r = max(1, min(w, h) // 2)
        r_px = min(r_px, max_r)
        if r_px <= 0:
            continue

        # 圆心位置（与 PIL 版本完全一致）
        if corner_key == 'tl':
            cx, cy = x + r_px, y + r_px
        elif corner_key == 'tr':
            cx, cy = x + w - r_px, y + r_px
        elif corner_key == 'bl':
            cx, cy = x + r_px, y + h - r_px
        else:  # br
            cx, cy = x + w - r_px, y + h - r_px

        # 1. 挖空 r×r corner square（切掉尖角）
        #    范围外扩 1px 以覆盖 PIL inclusive 语义的右/下边界列
        sq = get_corner_square(rect, corner_key, r_px)
        sq_x0 = max(0, int(sq[0]))
        sq_y0 = max(0, int(sq[1]))
        sq_x1 = min(W, int(sq[2]) + 1)  # +1 使 inclusive
        sq_y1 = min(H, int(sq[3]) + 1)

        if sq_x1 > sq_x0 and sq_y1 > sq_y0:
            mask_arr[sq_y0:sq_y1, sq_x0:sq_x1] = 0

        # 2. 用距离场公式把 1/4 圆弧像素填回 255（纯几何，无栅格化伪影）
        ys = np.arange(sq_y0, sq_y1)
        xs = np.arange(sq_x0, sq_x1)
        yy, xx = np.meshgrid(ys, xs, indexing='ij')

        # 计算到圆心的距离平方
        dist_sq = (xx - cx) ** 2 + (yy - cy) ** 2
        r_sq = float(r_px) * float(r_px)

        # 象限约束：每个角只保留对应象限内的圆弧
        if corner_key == 'tl':
            quadrant_mask = (xx <= cx) & (yy <= cy)
        elif corner_key == 'tr':
            quadrant_mask = (xx >= cx) & (yy <= cy)
        elif corner_key == 'bl':
            quadrant_mask = (xx <= cx) & (yy >= cy)
        else:  # br
            quadrant_mask = (xx >= cx) & (yy >= cy)

        # 圆内 AND 对应象限 = 真正应保留的 1/4 圆弧像素
        inside_circle = dist_sq <= r_sq
        fill_mask = inside_circle & quadrant_mask

        if fill_mask.any():
            mask_arr[sq_y0:sq_y1, sq_x0:sq_x1][fill_mask] = 255

    # 回写 PIL Image（L 模式）
    mask.paste(Image.fromarray(mask_arr, 'L'))


def _fill_corner_boundary_pixels(
    mask_arr: np.ndarray,
    cx: int,
    cy: int,
    r: int,
    corner_key: str,
    W: int,
    H: int,
    rect_bounds: tuple[int, int, int, int] | None = None,
) -> None:
    """
    [Fix C-shaped gap 0812] 填充圆角边界上的缺失像素。

    PIL pieslice 在绘制圆弧时，对于距离圆心略大于 r 的像素可能不会填充，
    导致圆角边界出现细微的 C 形缺口。本函数通过检查 r ± 1.5 像素范围内
    的所有像素，将应该在圆弧上但值为 0 的像素填充为 255。

    [Fix TR/BR 白色泄漏线 2026-08-14] 新增 rect_bounds 约束：
    rect_bounds = (rx0, ry0, rx1, ry1) 是与 fill_rect_mask 完全对齐的整数矩形
    （rx1/ry1 为 exclusive）。补全的像素必须落在 rect_bounds 内，否则：
    对于 TR / BR 角，刚好贴在外框交界的 x = rx1 列或 y = ry1 行会被错误地填回 255
    （这些像素其实属于外框区域，根本不在挖空 rect 内）→ 视觉上就是 1~2 条白竖/横线。

    Args:
        mask_arr: numpy 数组（原地修改）
        cx: 圆心 x 坐标
        cy: 圆心 y 坐标
        r: 圆角半径（像素）
        corner_key: 角落标识 ('tl' | 'tr' | 'bl' | 'br')
        W: 图像宽度
        H: 图像高度
        rect_bounds: 可选，(rx0, ry0, rx1, ry1) 像素对齐的合法范围（rx1/ry1 exclusive）
    """
    # 确定角度范围（与 CORNER_ANGLES 一致）
    angle_ranges = {
        'tl': (180, 270),
        'tr': (270, 360),
        'bl': (90, 180),
        'br': (0, 90),
    }
    start_deg, end_deg = angle_ranges[corner_key]

    rx0 = ry0 = -10_000
    rx1 = ry1 = 10_000_000
    if rect_bounds is not None:
        rx0, ry0, rx1, ry1 = rect_bounds

    # 检查 r ± 1.5 像素范围内的所有像素
    # 使用 1.5 像素容差覆盖边界舍入误差
    RADIUS_TOLERANCE = 1.5

    for angle in range(start_deg, end_deg + 1):
        rad = np.radians(angle)
        for offset in range(-1, 2):  # -1, 0, +1
            test_r = r + offset
            px = int(round(cx + test_r * np.cos(rad)))
            py = int(round(cy + test_r * np.sin(rad)))

            # 1) 图像范围
            if not (0 <= px < W and 0 <= py < H):
                continue
            # 2) [Fix TR/BR 泄漏] 必须落在 fill_rect_mask 的整数矩形内
            #    (rx1 / ry1 为 exclusive，与 PIL draw.rectangle 语义对齐)
            if not (rx0 <= px < rx1 and ry0 <= py < ry1):
                continue

            # 计算实际距离
            actual_dist = np.sqrt((px - cx) ** 2 + (py - cy) ** 2)

            # 如果实际距离在 r ± RADIUS_TOLERANCE 范围内，且值为 0，则填充为 255
            if abs(actual_dist - r) <= RADIUS_TOLERANCE and mask_arr[py, px] == 0:
                mask_arr[py, px] = 255
