"""
验证厚边框圆角修复效果：模拟克罗印花结构
- 外层：2cm 厚棕色边框
- 内层：0.1cm 白色间隙 + 0.1cm 黑色细线 + 米白底 + 花纹
- 目标尺寸：58x149CM，DPI=150 → 3425x8799px（缩小到 1/5 便于测试）
- 右下角：2cm 半径圆角
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PIL import Image, ImageDraw, ImageFont
import numpy as np
from core.config import cm_to_px, DEFAULT_DPI
from core.corner.detection import _get_border_layers_robust
from core.image_cropper import apply_border_only_corners


DPI = 150
SCALE = 0.2  # 缩放到 1/5 便于快速测试


def make_keluo_like_image(scale=SCALE):
    """构造模拟克罗印花的厚边框图"""
    # 目标尺寸 58x149cm @ 150DPI，缩小 scale 倍
    W = int(cm_to_px(58, DPI) * scale)
    H = int(cm_to_px(149, DPI) * scale)
    print(f"画布尺寸: {W}x{H}px (scale={scale})")

    # 边框厚度
    brown_thick = int(cm_to_px(2.0, DPI) * scale)  # 2cm棕色厚边框
    white_gap = int(cm_to_px(0.1, DPI) * scale)
    black_line = int(max(1, cm_to_px(0.1, DPI) * scale))
    print(f"边框参数: 棕色={brown_thick}px ({brown_thick/DPI*2.54:.2f}cm), "
          f"白隙={white_gap}px, 黑线={black_line}px")

    arr = np.zeros((H, W, 3), dtype=np.uint8)

    # 1. 米色内容底 (240, 232, 215)
    arr[:, :] = (240, 232, 215)

    # 2. 花纹：随机黑色小圆点 + 棕色十字花纹（模拟克罗心花纹）
    rng = np.random.RandomState(42)
    inner_pad = brown_thick + white_gap + black_line
    color_choices = [(0, 0, 0), (139, 90, 43), (20, 20, 20)]
    for _ in range(120):
        cx = rng.randint(inner_pad + 10, W - inner_pad - 10)
        cy = rng.randint(inner_pad + 10, H - inner_pad - 10)
        size = rng.randint(3, 10)
        color_idx = rng.randint(0, len(color_choices))
        color = color_choices[color_idx]
        # 画十字
        arr[cy-size:cy+size+1, cx-1:cx+2] = color
        arr[cy-1:cy+2, cx-size:cx+size+1] = color

    # 3. 内层黑色细线
    arr[inner_pad-black_line:H-inner_pad+black_line+1,
        inner_pad-black_line:inner_pad+1] = (0, 0, 0)  # 左
    arr[inner_pad-black_line:H-inner_pad+black_line+1,
        W-inner_pad:W-inner_pad+black_line+1] = (0, 0, 0)  # 右
    arr[inner_pad-black_line:inner_pad+1,
        inner_pad-black_line:W-inner_pad+black_line+1] = (0, 0, 0)  # 上
    arr[H-inner_pad:H-inner_pad+black_line+1,
        inner_pad-black_line:W-inner_pad+black_line+1] = (0, 0, 0)  # 下

    # 4. 白色间隙层
    inner = brown_thick
    arr[inner:H-inner, inner:inner+white_gap+1] = (255, 255, 255)  # 左
    arr[inner:H-inner, W-inner-white_gap:W-inner+1] = (255, 255, 255)  # 右
    arr[inner:inner+white_gap+1, inner:W-inner] = (255, 255, 255)  # 上
    arr[H-inner-white_gap:H-inner+1, inner:W-inner] = (255, 255, 255)  # 下

    # 5. 外层棕色厚边框 (139, 90, 43)
    arr[:brown_thick, :] = (139, 90, 43)
    arr[-brown_thick:, :] = (139, 90, 43)
    arr[:, :brown_thick] = (139, 90, 43)
    arr[:, -brown_thick:] = (139, 90, 43)

    img = Image.fromarray(arr, 'RGB')
    return img, brown_thick


def measure_straight_border_thickness(img, side='bottom'):
    """测量直边棕色边框厚度"""
    arr = np.array(img.convert('RGB'))
    H, W = arr.shape[:2]
    brown = np.array([139, 90, 43], dtype=np.float64)

    if side == 'bottom':
        # 从底部向上扫描
        scan_y = H // 2
        for y in range(H - 1, -1, -1):
            px = arr[y, scan_x := W // 2].astype(np.float64)
            dist = np.sqrt(np.sum((px - brown) ** 2))
            if dist > 50:
                return H - 1 - y
    return H


def measure_arc_thickness(img, corner='br', radius_px=None):
    """测量圆角弧线棕色厚度 - 径向扫描多个角度取平均值"""
    arr = np.array(img.convert('RGB'))
    H, W = arr.shape[:2]
    r = radius_px
    if r is None:
        r = min(H, W) // 10

    if corner == 'br':
        cx, cy = W - 1, H - 1

    brown = np.array([139, 90, 43], dtype=np.float64)
    angles = np.linspace(190, 260, 25)  # 覆盖90度圆角
    thicknesses = []

    for ang_deg in angles:
        ang = np.radians(ang_deg)
        dx0, dy0 = np.cos(ang), np.sin(ang)

        # 从圆弧 (dist=r) 向圆心 (dist=0) 扫描
        found_start = False
        start_d = None
        end_d = None
        for d in range(r + 10, -1, -1):  # 从外向内
            x = int(cx + dx0 * d)
            y = int(cy + dy0 * d)
            if 0 <= x < W and 0 <= y < H:
                px = arr[y, x].astype(np.float64)
                dist = np.sqrt(np.sum((px - brown) ** 2))
                if dist < 50:
                    if not found_start:
                        found_start = True
                        start_d = d
                else:
                    if found_start:
                        end_d = d
                        break
        if found_start and end_d is not None:
            thicknesses.append(start_d - end_d)
        elif found_start:
            thicknesses.append(start_d)

    avg = np.mean(thicknesses) if thicknesses else 0
    med = np.median(thicknesses) if thicknesses else 0
    print(f"  弧线厚度扫描: angles={len(angles)}, valid={len(thicknesses)}, "
          f"avg={avg:.1f}px, median={med:.1f}px, min={min(thicknesses):.0f}, max={max(thicknesses):.0f}")
    return med


def main():
    img, expected_thick = make_keluo_like_image()
    W, H = img.size
    corner_r_cm = 2.0
    corner_r_px = int(cm_to_px(corner_r_cm, DPI) * SCALE)
    print(f"\n圆角半径: {corner_r_px}px ({corner_r_cm}cm)")
    print(f"预期边框厚度: {expected_thick}px (2.0cm)")

    # 直边厚度
    straight_t = measure_straight_border_thickness(img, 'bottom')
    print(f"\n[处理前] 直边棕色边框厚度: {straight_t}px")

    # 检测边框层
    layers = _get_border_layers_robust(img)
    print(f"检测到的边框层 ({len(layers)} 层):")
    total = 0
    for i, (c, t) in enumerate(layers):
        total += t
        # 因为 scale=0.2 缩小了，但检测层默认走 DEFAULT_DPI(150)，换算用实际像素
        cm_val = t * 2.54 / (DPI * SCALE) if (DPI * SCALE) > 0 else t * 2.54 / 30
        print(f"  L{i}: color={c}, thickness={t}px ({cm_val:.3f}cm), cum={total}px")

    # 应用圆角（右下角）
    # 注意：apply_border_only_corners 的 corners 值单位是 cm，不是 px
    # 我们的画布是缩放过的，所以 dpi 也要按比例缩小，保证 cm→px 换算正确
    canvas_dpi = int(DPI * SCALE)
    print(f"使用 canvas_dpi={canvas_dpi} 进行 cm→px 换算")
    corners_cm = {'tl': 0, 'tr': 0, 'bl': 0, 'br': corner_r_cm}
    result = img.copy()
    result = apply_border_only_corners(
        result, corners_cm, dpi=canvas_dpi,
        pre_detected_layers=layers,
    )

    # 保存调试图
    out_dir = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.join(out_dir, 'debug_keluo_src.png')
    dst_path = os.path.join(out_dir, 'debug_keluo_rounded.png')
    img.save(src_path)
    result.save(dst_path)
    print(f"\n已保存: {src_path}")
    print(f"已保存: {dst_path}")

    # 测量直边厚度
    straight_after = measure_straight_border_thickness(result, 'bottom')
    print(f"\n[处理后] 底部直边棕色厚度: {straight_after}px")

    # 测量右下角弧线厚度
    print(f"[处理后] 右下角圆角弧线厚度测量:")
    arc_t = measure_arc_thickness(result, 'br', corner_r_px)

    # 对比
    ratio = arc_t / straight_after if straight_after > 0 else 0
    print(f"\n===== 修复验证结果 =====")
    print(f"直边厚度: {straight_after}px")
    print(f"弧线厚度: {arc_t:.1f}px (median)")
    print(f"弧线/直边比例: {ratio*100:.1f}%")
    if ratio > 0.9:
        print("✅ PASS: 弧线厚度与直边匹配 (>=90%)")
    elif ratio > 0.7:
        print(f"⚠️  WARN: 弧线略细于直边 ({ratio*100:.0f}%)")
    else:
        print(f"❌ FAIL: 弧线明显细于直边 (仅 {ratio*100:.0f}%)")

    # 放大局部显示圆角
    zoom = 3
    crop = result.crop((W - corner_r_px * 3, H - corner_r_px * 3, W, H))
    crop = crop.resize((crop.width * zoom, crop.height * zoom), Image.Resampling.NEAREST)
    zoom_path = os.path.join(out_dir, 'debug_keluo_corner_zoom.png')
    crop.save(zoom_path)
    print(f"圆角放大图: {zoom_path}")


if __name__ == '__main__':
    main()
