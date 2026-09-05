"""7 个回归的诊断脚本：跑测试 + 输出诊断图/错误像素分布/逐字段对比。

产出位置：F:\\SmartShapeCrop\\.workbuddy\\diag\\
"""
from __future__ import annotations
import io, json, math, sys, traceback
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont as PILImageFont

OUT_DIR = Path(r"F:\SmartShapeCrop\.workbuddy\diag")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 让脚本既可作为 pytest 用例导入跑，也可独立 python 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# 复用 image_cropper 测试文件里的输入构造
def _build_input_complex_pattern(w=800, h=1000):
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    arr[0:50, :] = (0, 0, 0); arr[-50:, :] = (0, 0, 0)
    arr[:, 0:50] = (0, 0, 0); arr[:, -50:] = (0, 0, 0)
    arr[50:70, 50:750] = (200, 200, 200)
    arr[-70:-50, 50:750] = (200, 200, 200)
    arr[50:950, 50:70] = (200, 200, 200)
    arr[50:950, -70:-50] = (200, 200, 200)
    arr[70:100, 70:730] = (200, 0, 0); arr[-100:-70, 70:730] = (200, 0, 0)
    arr[70:930, 70:100] = (200, 0, 0); arr[70:930, -100:-70] = (200, 0, 0)
    for y in range(200, 400):
        for x in range(200, 400):
            if (x - 300) ** 2 + (y - 300) ** 2 <= 100 ** 2:
                arr[y, x] = (0, 100, 200)
    arr[500:700, 200:400] = (0, 200, 100)
    arr[350:450, 500:600] = (255, 200, 0)
    return Image.fromarray(arr, 'RGB')


def _build_input_multilayer(w=600, h=800):
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    arr[0:40, :] = (0, 50, 200); arr[-40:, :] = (0, 50, 200)
    arr[:, 0:40] = (0, 50, 200); arr[:, -40:] = (0, 50, 200)
    arr[55:80, 55:545] = (0, 150, 80); arr[-80:-55, 55:545] = (0, 150, 80)
    arr[55:725, 55:80] = (0, 150, 80); arr[55:725, -80:-55] = (0, 150, 80)
    for y in range(150, 650, 20):
        for x in range(150, 550, 20):
            if ((x // 20) + (y // 20)) % 2 == 0:
                arr[y:y + 10, x:x + 10] = (255, 100, 100)
            else:
                arr[y:y + 10, x:x + 10] = (100, 100, 255)
    return Image.fromarray(arr, 'RGB')


def _build_input_extreme(w=500, h=500):
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    arr[0:30, :] = (200, 200, 200); arr[-30:, :] = (200, 200, 200)
    arr[:, 0:30] = (200, 200, 200); arr[:, -30:] = (200, 200, 200)
    arr[60:440, 60:440] = (100, 100, 100)
    for y in range(100, 400):
        for x in range(100, 400):
            intensity = int(100 + 50 * math.sin(x / 20.0) * math.cos(y / 20.0))
            arr[y, x] = (intensity, intensity, intensity + 10)
    for y in range(30, 35):
        for x in range(30, w - 30):
            arr[y, x] = (220, 220, 220)
    for y in range(45, 50):
        for x in range(45, w - 45):
            arr[y, x] = (180, 180, 180)
    for y in range(30, h - 30):
        for x in range(30, 35):
            arr[y, x] = (220, 220, 220)
    for y in range(45, h - 45):
        for x in range(45, 50):
            arr[y, x] = (180, 180, 180)
    return Image.fromarray(arr, 'RGB')


def _build_input_border_smoothness(w=800, h=1000):
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    arr[0:50, :] = (0, 0, 0); arr[-50:, :] = (0, 0, 0)
    arr[:, 0:50] = (0, 0, 0); arr[:, -50:] = (0, 0, 0)
    arr[100:150, 100:700] = (200, 0, 0)
    arr[150:850, 100:150] = (200, 0, 0)
    return Image.fromarray(arr, 'RGB')


def _build_input_gap_no_fill(w=800, h=1000):
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    arr[0:50, :] = (0, 0, 0); arr[-50:, :] = (0, 0, 0)
    arr[:, 0:50] = (0, 0, 0); arr[:, -50:] = (0, 0, 0)
    arr[50:70, 50:750] = (200, 200, 200)
    arr[-70:-50, 50:750] = (200, 200, 200)
    arr[50:950, 50:70] = (200, 200, 200)
    arr[50:950, -70:-50] = (200, 200, 200)
    arr[70:100, 70:730] = (200, 0, 0); arr[-100:-70, 70:730] = (200, 0, 0)
    arr[70:930, 70:100] = (200, 0, 0); arr[70:930, -100:-70] = (200, 0, 0)
    return Image.fromarray(arr, 'RGB')


def _save_compare(name: str, src: Image.Image, dst: Image.Image,
                  overlays: list[dict], summary: dict):
    """保存三联图：原图 | 标记错误的输出图 | 错误像素高亮图。

    overlays: [{'color': (r,g,b), 'points': [(x,y), ...]}] 像素级标注
    summary:  错误摘要文字
    """
    w, h = src.size
    gap = 12
    out_w = w * 3 + gap * 2
    out_h = h + 56
    canvas = Image.new('RGB', (out_w, out_h), (245, 245, 248))
    canvas.paste(src, (0, 56))
    canvas.paste(dst, (w + gap, 56))
    # 第三格：错误像素高亮
    overlay_img = dst.copy()
    overlay_draw = ImageDraw.Draw(overlay_img)
    for o in overlays:
        c = o['color']
        for x, y in o['points']:
            if 0 <= x < w and 0 <= y < h:
                overlay_draw.point((x, y), fill=c)
    canvas.paste(overlay_img, ((w + gap) * 2, 56))

    # 顶部标题
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 8), "原图", fill=(40, 40, 40))
    draw.text((w + gap + 10, 8), "apply_rounded_corners 输出", fill=(40, 40, 40))
    draw.text(((w + gap) * 2 + 10, 8), "错误像素标注", fill=(40, 40, 40))
    # 底部摘要
    draw.text((10, 56 + h + 4), summary.get('line1', '')[:200], fill=(180, 40, 40))
    if summary.get('line2'):
        draw.text((10, 56 + h + 18), summary['line2'][:200], fill=(80, 80, 80))

    out_path = OUT_DIR / f"{name}.png"
    canvas.save(out_path)
    return out_path


# === 诊断 1: test_complex_pattern_with_gaps ===
def diag_1_complex_pattern():
    from core.image_cropper import apply_rounded_corners
    img = _build_input_complex_pattern()
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
    result = apply_rounded_corners(img, corners, dpi=dpi, bg_color=(255, 255, 255))
    result_arr = np.array(result)

    cx, cy = 50, 950
    r_px = int(3.0 / 2.54 * dpi)
    # 检查间隙区域 arr[950:1000, 50:70] 是否保持为灰色 (200,200,200)
    gap_region = result_arr[950:1000, 50:70]
    non_gray = np.where(
        (gap_region[:, :, 0] > 220) | (gap_region[:, :, 1] > 220) | (gap_region[:, :, 2] > 220)
    )
    err_points = [(50 + int(x), 950 + int(y)) for y, x in zip(*non_gray)]

    summary = {
        'line1': f"间隙区非灰色像素: {len(err_points)} (期望0) | 圆角像素={r_px}",
        'line2': f"采样颜色示例: {gap_region[non_gray[0][0] if len(non_gray[0]) else 0, non_gray[1][0] if len(non_gray[1]) else 0] if len(err_points) else '无'}",
    }
    p = _save_compare('1_complex_pattern_with_gaps', img, result,
                      [{'color': (255, 0, 0), 'points': err_points}], summary)
    return {'name': '1_complex_pattern_with_gaps', 'errors': len(err_points),
            'radius_px': r_px, 'img': str(p)}


# === 诊断 2: test_multilayer_border_with_mixed_colors ===
def diag_2_multilayer():
    from core.image_cropper import apply_border_only_corners
    img = _build_input_multilayer()
    dpi = 150
    corners = {'tl': 2.0, 'tr': 2.0, 'bl': 2.0, 'br': 2.0}
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=(255, 255, 255))
    result_arr = np.array(result)

    # 检查四个角的间隙 (40-55 行/列) 是否保持白色
    w, h = img.size
    r_px = int(2.0 / 2.54 * dpi)
    failed_corners = {}
    err_points = []
    for corner, slices in [
        ('tl', (40, 55, 0, 15)),
        ('tr', (40, 55, 545, 560)),
        ('bl', (745, 760, 0, 15)),
        ('br', (745, 760, 545, 560)),
    ]:
        y1, y2, x1, x2 = slices
        region = result_arr[y1:y2, x1:x2]
        all_white = (region > 250).all()
        if not all_white:
            non_white = np.where(region[:, :, 0] < 250)
            for dy, dx in zip(*non_white):
                err_points.append((x1 + int(dx), y1 + int(dy)))
            failed_corners[corner] = len(non_white[0])

    summary = {
        'line1': f"失败的角: {failed_corners} | 总错误像素: {len(err_points)}",
        'line2': f"圆角半径 {r_px}px | 4 个角均为 2.0cm",
    }
    p = _save_compare('2_multilayer_border', img, result,
                      [{'color': (255, 0, 0), 'points': err_points}], summary)
    return {'name': '2_multilayer_border', 'failed_corners': failed_corners,
            'total_errors': len(err_points), 'img': str(p)}


# === 诊断 3: test_extreme_colors_and_anti_aliasing ===
def diag_3_extreme():
    from core.image_cropper import apply_border_only_corners
    img = _build_input_extreme()
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 0, 'br': 2.5}
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=(255, 255, 255))
    result_arr = np.array(result)

    # 右下角间隙 arr[440:460, 440:460] 应保持白色
    gap_region = result_arr[440:460, 440:460]
    all_white = (gap_region > 245).all()
    non_white = np.where(gap_region[:, :, 0] < 245)
    err_points_gap = [(440 + int(x), 440 + int(y)) for y, x in zip(*non_white)]

    # 圆弧上颜色偏差 > 100 的像素
    cx, cy = 469, 469
    r_px = int(2.5 / 2.54 * dpi)
    color_err = []
    for angle_deg in range(0, 91, 5):
        angle_rad = math.radians(angle_deg)
        for dist_offset in range(-28, 29):
            if abs(dist_offset) > 3:
                continue
            dist_px = r_px + dist_offset
            x = int(cx - dist_px * math.cos(angle_rad))
            y = int(cy - dist_px * math.sin(angle_rad))
            if 0 <= x < img.size[0] and 0 <= y < img.size[1]:
                p = result_arr[y, x]
                dev = abs(int(p[0]) - 200) + abs(int(p[1]) - 200) + abs(int(p[2]) - 200)
                if dev > 100:
                    color_err.append((x, y))

    summary = {
        'line1': f"右下角间隙非白像素: {len(err_points_gap)} (期望0) | 圆弧异常: {len(color_err)} (期望<10)",
        'line2': f"圆角半径 {r_px}px | 边框色(200,200,200) | 间隙色(255,255,255)",
    }
    p = _save_compare('3_extreme_colors', img, result,
                      [{'color': (255, 0, 0), 'points': err_points_gap},
                       {'color': (0, 0, 255), 'points': color_err}], summary)
    return {'name': '3_extreme_colors', 'gap_errors': len(err_points_gap),
            'arc_errors': len(color_err), 'img': str(p)}


# === 诊断 4: test_corner_smoothness_no_gap_fill ===
def diag_4_corner_smoothness():
    from core.image_cropper import apply_rounded_corners
    img = _build_input_complex_pattern()
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
    result = apply_rounded_corners(img, corners, dpi=dpi, bg_color=(255, 255, 255))
    result_arr = np.array(result)

    cx, cy = 50, 950
    r_px = int(3.0 / 2.54 * dpi)
    # 间隙区 (距中心50-71) 应保持白色
    black_pts = []
    white_pts = []
    for angle_deg in range(0, 91, 5):
        angle_rad = math.radians(angle_deg)
        for dist_px in range(50, 71):
            x = int(cx + dist_px * math.cos(angle_rad))
            y = int(cy - dist_px * math.sin(angle_rad))
            if 0 <= x < img.size[0] and 0 <= y < img.size[1]:
                p = tuple(result_arr[y, x])
                if p == (0, 0, 0):
                    black_pts.append((x, y))
                elif p == (255, 255, 255):
                    white_pts.append((x, y))

    summary = {
        'line1': f"间隙区黑色像素: {len(black_pts)} (期望<5) | 白色像素: {len(white_pts)} (期望较多)",
        'line2': f"圆角半径 {r_px}px | 检查左下角 (中心50,950)",
    }
    p = _save_compare('4_corner_smoothness_no_gap_fill', img, result,
                      [{'color': (255, 0, 0), 'points': black_pts},
                       {'color': (0, 200, 0), 'points': white_pts}], summary)
    return {'name': '4_corner_smoothness_no_gap_fill', 'black_in_gap': len(black_pts),
            'white_in_gap': len(white_pts), 'img': str(p)}


# === 诊断 5: test_border_smoothness ===
def diag_5_border_smoothness():
    from core.image_cropper import apply_rounded_corners
    img = _build_input_border_smoothness()
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
    result = apply_rounded_corners(img, corners, dpi=dpi, bg_color=(255, 255, 255))
    result_arr = np.array(result)

    cx, cy = 50, 950
    r_px = int(3.0 / 2.54 * dpi)
    gap_pts = []
    total = 0
    for angle_deg in range(2, 91, 3):
        angle_rad = math.radians(angle_deg)
        x = int(cx + r_px * math.cos(angle_rad))
        y = int(cy - r_px * math.sin(angle_rad))
        if 0 <= x < img.size[0] and 0 <= y < img.size[1]:
            total += 1
            p = tuple(result_arr[y, x])
            if p == (255, 255, 255):
                gap_pts.append((x, y))

    ratio = len(gap_pts) / total * 100 if total else 0
    summary = {
        'line1': f"圆弧白色像素: {len(gap_pts)}/{total} = {ratio:.1f}% (期望<5%)",
        'line2': f"圆角半径 {r_px}px | 步长3° | 红色=缺口像素",
    }
    p = _save_compare('5_border_smoothness', img, result,
                      [{'color': (255, 0, 0), 'points': gap_pts}], summary)
    return {'name': '5_border_smoothness', 'gap_count': len(gap_pts),
            'total_checks': total, 'gap_ratio': ratio, 'img': str(p)}


# === 诊断 6: test_no_wrong_gap_fill ===
def diag_6_no_wrong_gap_fill():
    from core.image_cropper import apply_rounded_corners
    img = _build_input_gap_no_fill()
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
    result = apply_rounded_corners(img, corners, dpi=dpi, bg_color=(255, 255, 255))
    result_arr = np.array(result)

    cx, cy = 50, 950
    r_px = int(3.0 / 2.54 * dpi)
    black_pts = []
    total = 0
    for angle_deg in range(5, 91, 5):
        angle_rad = math.radians(angle_deg)
        for dist_px in range(52, 69):  # 排除边界
            x = int(cx + dist_px * math.cos(angle_rad))
            y = int(cy - dist_px * math.sin(angle_rad))
            if 0 <= x < img.size[0] and 0 <= y < img.size[1]:
                total += 1
                p = result_arr[y, x]
                if p[0] < 30 and p[1] < 30 and p[2] < 30:
                    black_pts.append((x, y))

    ratio = len(black_pts) / total * 100 if total else 0
    summary = {
        'line1': f"核心间隙黑色像素: {len(black_pts)}/{total} = {ratio:.1f}% (期望<5%)",
        'line2': f"距圆心 52-68px 的核心区 | 红色=错误填充",
    }
    p = _save_compare('6_no_wrong_gap_fill', img, result,
                      [{'color': (255, 0, 0), 'points': black_pts}], summary)
    return {'name': '6_no_wrong_gap_fill', 'black_count': len(black_pts),
            'total': total, 'black_ratio': ratio, 'img': str(p)}


# === 诊断 7: test_name_parser ===
def diag_7_name_parser():
    from core.parser.name_parser import parse_filename
    # —— 2026-09-05 校准：与 test_fix_validation.py 的期望保持一致 ——
    # 产品语义：长边=宽、短边=高，水池模式与通用模式统一
    cases = [
        ('model_60.5x133CM水池', 133.0, 60.5, '横版'),
        ('model_133x60.5CM水池', 133.0, 60.5, '横版'),
        ('model_45x45CM水池',    45.0,  45.0,  '横版'),
        ('model_100x50CM水池',   100.0, 50.0,  '横版'),
        ('model_50x100CM水池',   100.0, 50.0,  '横版'),
        ('model_100x200CM裁剪有图', 200.0, 100.0, '横版'),
    ]
    rows = []
    for fname, exp_w, exp_h, exp_layout in cases:
        r = parse_filename(fname)
        w = round(r.width_cm, 1) if r.width_cm else 0
        h = round(r.height_cm, 1) if r.height_cm else 0
        layout = r.layout
        ok = (w == exp_w and h == exp_h and layout == exp_layout)
        rows.append({
            'fname': fname,
            'expected': f'{exp_w}x{exp_h} ({exp_layout})',
            'got': f'{w}x{h} ({layout})',
            'pool_mode': r.pool_mode,
            'is_pool': r.is_pool_mode(),
            'ok': ok,
        })

    # 输出解析状态对比图（文本而非位图）
    img = Image.new('RGB', (900, 360), (245, 245, 248))
    draw = ImageDraw.Draw(img)
    draw.text((20, 10), "test_name_parser — 逐字段对比", fill=(40, 40, 40))
    draw.text((20, 40), "文件名", fill=(80, 80, 80))
    draw.text((280, 40), "期望", fill=(80, 80, 80))
    draw.text((430, 40), "实际", fill=(80, 80, 80))
    draw.text((580, 40), "pool_mode/is_pool", fill=(80, 80, 80))
    draw.text((760, 40), "结果", fill=(80, 80, 80))
    for i, row in enumerate(rows):
        y = 70 + i * 42
        color = (40, 120, 40) if row['ok'] else (200, 40, 40)
        draw.text((20, y), row['fname'][:24], fill=(40, 40, 40))
        draw.text((280, y), row['expected'], fill=(80, 80, 80))
        draw.text((430, y), row['got'], fill=color)
        draw.text((580, y), f"{row['pool_mode']}/{row['is_pool']}", fill=(80, 80, 80))
        draw.text((760, y), "✅" if row['ok'] else "❌", fill=color)
    out_path = OUT_DIR / "7_name_parser.png"
    img.save(out_path)

    return {'name': '7_name_parser', 'rows': rows, 'img': str(out_path)}


def main():
    results = []
    funcs = [diag_1_complex_pattern, diag_2_multilayer, diag_3_extreme,
             diag_4_corner_smoothness, diag_5_border_smoothness,
             diag_6_no_wrong_gap_fill, diag_7_name_parser]
    for f in funcs:
        try:
            r = f()
            results.append(r)
            print(f"[OK] {r['name']}")
        except Exception as e:
            traceback.print_exc()
            print(f"[ERR] {f.__name__}: {e}")
            results.append({'name': f.__name__, 'error': str(e)})

    out_json = OUT_DIR / 'diag_results.json'
    with open(out_json, 'w', encoding='utf-8') as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\n=== 汇总已写入 {out_json} ===")
    print(f"=== 对比图保存于 {OUT_DIR} ===")


if __name__ == '__main__':
    main()