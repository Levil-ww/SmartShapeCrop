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
  切掉的是 L 形（正方形减去 1/4 圆），即只切掉尖角，保留圆弧。
  圆心在正方形的"内角"顶点（即矩形内部那个角），bbox 以该圆心为中心。

向后兼容：原 core/rounded_corner.py 已改为薄重导出 shim，旧导入路径继续可用。
"""
from __future__ import annotations
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

    单步扇形切割算法：
      1. 挖空 r×r 正方形（fill=0）
      2. 填回矩形内部的 1/4 圆（fill=255）

    本函数是项目中所有圆角处理的唯一入口，确保 image_cropper.py、
    geometry.py、process_image.py 三处的圆角逻辑完全一致。

    Args:
        mask: PIL 'L' 模式 mask（255=保留, 0=裁掉）
        rect: (x, y, w, h) 目标矩形坐标（左上角 + 宽高）
        corners: {corner_key: radius_px} 圆角半径（像素）；
                None 或 <= 0 的角跳过
        canvas_size: 可选，用于 clip 坐标到合法范围；None 则用 mask.size
    """
    W, H = canvas_size if canvas_size is not None else mask.size
    x, y, w, h = rect
    draw = ImageDraw.Draw(mask)

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

        # 1. 挖空 r×r 正方形（切掉尖角）
        sq = get_corner_square(rect, corner_key, r_px)
        sq_safe = (max(0, sq[0]), max(0, sq[1]), min(W, sq[2]), min(H, sq[3]))
        if sq_safe[2] > sq_safe[0] and sq_safe[3] > sq_safe[1]:
            draw.rectangle(sq_safe, fill=0)

        # 2. 填回 1/4 圆（保留圆弧）
        bbox = get_corner_pieslice_bbox(rect, corner_key, r_px)
        start_deg, end_deg = CORNER_ANGLES[corner_key]
        safe_bbox = (max(0, bbox[0]), max(0, bbox[1]), min(W, bbox[2]), min(H, bbox[3]))
        if safe_bbox[2] > safe_bbox[0] and safe_bbox[3] > safe_bbox[1]:
            draw.pieslice(safe_bbox, start=start_deg, end=end_deg, fill=255)
