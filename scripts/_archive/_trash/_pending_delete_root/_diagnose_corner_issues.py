"""诊断脚本：分析圆角裁剪的色差、扇形向内折叠、边框错位问题
不依赖实际源图，构造合成测试图精确分析每个环节。
"""
import os
import numpy as np
from PIL import Image, ImageDraw

import sys
sys.path.insert(0, os.path.dirname(__file__))

from core.image_cropper import (
    apply_border_only_corners,
    _get_border_layers_robust,
    detect_nested_rect_layers,
)
from core.corner.sector_render import (
    _redraw_border_on_corner,
    _sample_border_color,
    _build_border_sector_mask,
)
from core.config import DEFAULT_BORDER_WIDTH_CM


def make_synthetic_test_image(w=800, h=600):
    """构造合成测试图：多层黑框+内层花纹+米黄背景"""
    img = Image.new('RGB', (w, h), (250, 248, 235))  # 米黄背景
    draw = ImageDraw.Draw(img)

    # 最外层黑框（5px）
    draw.rectangle([0, 0, w-1, h-1], outline=(0, 0, 0), width=5)
    # 第二层内框（距边缘 40px，3px 红框）
    draw.rectangle([40, 40, w-41, h-41], outline=(180, 30, 30), width=3)
    # 第三层花纹框（距边缘 80px，2px 绿框模拟花纹边缘）
    draw.rectangle([80, 80, w-81, h-81], outline=(30, 120, 30), width=2)
    # 内层中心黑框（距边缘 200px，3px）
    draw.rectangle([200, 200, w-201, h-201], outline=(0, 0, 0), width=3)

    # 中心花纹（简单线条模拟）
    for i in range(100, w-100, 40):
        draw.line([(i, 250), (i+20, 280)], fill=(80, 80, 80), width=1)
        draw.line([(i+20, 250), (i, 280)], fill=(80, 80, 80), width=1)

    return img


def test_1_border_only_mask_merge():
    """测试1: apply_border_only_corners mask 合并正确性（关闭重绘）"""
    print("="*60)
    print("测试1: apply_border_only_corners 小半径 mask 正确性")
    print("="*60)
    img = make_synthetic_test_image(800, 600)
    w, h = img.size

    # 小半径：1cm = ~59px @ 150dpi，但这里为了看清楚用更大的
    corners = {'tl': 1.5, 'tr': 1.5, 'bl': 1.5, 'br': 1.5}  # 1.5cm
    dpi = 150
    bg = (250, 248, 235)

    # --- 手动复现 apply_border_only_corners 但跳过 _redraw 步骤 ---
    border_w_cm = DEFAULT_BORDER_WIDTH_CM
    border_w_px = max(1, int(round(border_w_cm * dpi / 2.54)))
    max_r_px = max(1, int(round(1.5 * dpi / 2.54)))
    if border_w_px < max_r_px:
        border_w_px = max_r_px
    print(f"  border_w_px = {border_w_px}, max_r_px = {max_r_px}")

    # 检测边框层
    border_layers = _get_border_layers_robust(img, bg)
    print(f"  检测到 border_layers: {border_layers}")

    # 构造 full_mask
    from core.corner.algorithm import carve_corner_on_mask
    full_mask = Image.new('L', (w, h), 255)
    corners_px = {k: max(1, int(round(v * dpi / 2.54))) for k, v in corners.items()}
    carve_corner_on_mask(full_mask, (0, 0, w, h), corners_px, canvas_size=(w, h))

    # inner_mask
    inner_mask = Image.new('L', (w, h), 0)
    inner_draw = ImageDraw.Draw(inner_mask)
    inner_rect = [border_w_px, border_w_px, w - border_w_px, h - border_w_px]
    inner_draw.rectangle(inner_rect, fill=255)

    # 合并 mask（原逻辑）
    zero_img = Image.new('L', (w, h), 0)
    border_region_mask = Image.composite(zero_img, full_mask, inner_mask)
    final_mask = Image.composite(inner_mask, border_region_mask, inner_mask)

    # 验证：内部区域必须全 255，边框区域必须等于 full_mask
    fm_arr = np.array(final_mask)
    fl_arr = np.array(full_mask)
    im_arr = np.array(inner_mask)

    region_inner = im_arr == 255
    region_border = ~region_inner

    inner_ok = np.all(fm_arr[region_inner] == 255)
    border_ok = np.array_equal(fm_arr[region_border], fl_arr[region_border])
    print(f"  内部区域全保留? {inner_ok}  (错误数量: {np.sum(fm_arr[region_inner] != 255)})")
    print(f"  边框区域 mask 等于 full_mask? {border_ok}  (错误数量: {np.sum(fm_arr[region_border] != fl_arr[region_border])})")

    # 应用 mask 不做重绘
    result_no_redraw = Image.new('RGB', (w, h), bg)
    result_no_redraw.paste(img, mask=final_mask)

    # 保存对比
    out_dir = r"D:\SmartShapeCrop\test_cropper_output"
    os.makedirs(out_dir, exist_ok=True)
    result_no_redraw.save(os.path.join(out_dir, "diag_test1_no_redraw.jpg"), quality=95)

    # --- 开启重绘 ---
    result_with_redraw = result_no_redraw.copy()
    if border_layers:
        for ck, r_px in corners_px.items():
            _redraw_border_on_corner(result_with_redraw, ck, r_px, border_layers, src_img=img)
    result_with_redraw.save(os.path.join(out_dir, "diag_test1_with_redraw.jpg"), quality=95)

    # 对比：取左上角圆角区域的像素差异
    diff = np.abs(np.array(result_with_redraw).astype(np.int16) - np.array(result_no_redraw).astype(np.int16))
    roi = diff[:border_w_px+max_r_px+10, :border_w_px+max_r_px+10, :]
    changed_pixels = np.sum(np.any(roi > 5, axis=2))
    print(f"  重绘后左上角区域像素变化数(色差>5): {changed_pixels} / {roi.shape[0]*roi.shape[1]}")
    if changed_pixels > 0:
        # 采样变化的像素颜色
        idx = np.where(np.any(roi > 5, axis=2))
        for i in range(min(5, len(idx[0]))):
            y, x = idx[0][i], idx[1][i]
            old = tuple(np.array(result_no_redraw)[y, x])
            new = tuple(np.array(result_with_redraw)[y, x])
            print(f"    ({x},{y}): {old} → {new}  Δ={tuple(int(b-a) for a,b in zip(old,new))}")
    print("  输出: diag_test1_no_redraw.jpg, diag_test1_with_redraw.jpg")


def test_2_color_sampling():
    """测试2: 采样边框颜色与实际边框线的匹配度"""
    print("\n" + "="*60)
    print("测试2: 边框颜色采样精度（_sample_border_color）")
    print("="*60)
    img = make_synthetic_test_image(800, 600)
    w, h = img.size
    arr = np.array(img)

    # 真实边框颜色（从构造函数已知）
    true_outer_color = (0, 0, 0)  # 5px 黑框

    for ck in ['tl', 'tr', 'bl', 'br']:
        # 对不同厚度采样
        for thickness_px in [3, 5, 10]:
            d_mid = thickness_px / 2.0
            sampled = _sample_border_color(img, ck, w, h, d_mid, float(thickness_px))
            dist = np.sqrt(sum((a-b)**2 for a, b in zip(sampled, true_outer_color)))
            print(f"  角 {ck}, 厚={thickness_px}px: 采样={sampled}, 真实={true_outer_color}, 色差={dist:.1f}")


def test_3_redraw_area():
    """测试3: _redraw_border_on_corner 实际绘制区域是否超出边框线范围"""
    print("\n" + "="*60)
    print("测试3: _redraw_border_on_corner 绘制区域检查")
    print("="*60)
    W, H = 500, 400
    R = 80  # px
    ck = 'br'
    # 单层边框，厚度6px
    layers = [((0, 0, 0), 6)]

    # 纯白画布上画
    img = Image.new('RGB', (W, H), (255, 255, 255))
    # 先在右下角画一个实际的黑色L形边框
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W-1, H-1], outline=(0, 0, 0), width=6)
    # 参考原图（用于采样）
    src = img.copy()

    # 圆心
    cx, cy = W - R, H - R
    print(f"  R={R}, 圆心(cx,cy)=({cx},{cy})")

    # 构建重绘遮罩用于可视化
    cumulative = 0
    for color, thickness in layers:
        d_out = float(cumulative)
        d_in = float(cumulative + thickness)
        if d_in > R: d_in = float(R)
        mask_bool = _build_border_sector_mask(W, H, ck, cx, cy, R, d_out, d_in)
        print(f"  层: d_out={d_out}, d_in={d_in}, 绘制像素数={np.sum(mask_bool)}")

        # 可视化：用红色标出绘制区域
        vis_arr = np.array(img)
        vis_arr[mask_bool] = [255, 0, 0]
        vis_img = Image.fromarray(vis_arr)
        out_dir = r"D:\SmartShapeCrop\test_cropper_output"
        os.makedirs(out_dir, exist_ok=True)
        vis_img.save(os.path.join(out_dir, f"diag_test3_redraw_area_layer.png"))

        # 检查绘制区域与真实边框的重合度
        src_arr = np.array(src)
        true_border = np.all(src_arr == [0, 0, 0], axis=2)  # 真实黑框
        redraw_area = mask_bool
        overlap = np.sum(redraw_area & true_border)
        extra = np.sum(redraw_area & ~true_border)
        missing = np.sum(~redraw_area & true_border & (mask_bool | True))
        # 简化：只看角部ROI内的真实边框
        roi = np.zeros_like(true_border)
        roi[H-R-10:H, W-R-10:W] = True
        border_in_roi = true_border & roi
        missing_in_roi = np.sum(border_in_roi & ~redraw_area)
        print(f"    重合(重绘∩真边框): {overlap}")
        print(f"    超出(重绘∩非边框): {extra}  ← 应=0，否则有问题")
        print(f"    遗漏(ROI内真边框∩未重绘): {missing_in_roi}  ← 越小越好")
        cumulative += thickness

    # 现在做完整重绘并检查颜色
    result = Image.new('RGB', (W, H), (255, 255, 255))
    result.paste(img, mask=Image.new('L', (W, H), 255))
    _redraw_border_on_corner(result, ck, R, layers, src_img=src)

    # 取几个关键像素点
    res_arr = np.array(result)
    # 圆弧上应该是黑色边框的点
    check_pts = [
        (W-1, H-R+1),    # 右侧竖线末端附近
        (W-R+1, H-1),    # 底部横线末端附近
        (W-R+int(R*0.7), H-R+int(R*0.7)),  # 圆弧中部
    ]
    print(f"\n  关键点颜色（应为黑色 (0,0,0) 或接近）:")
    for pt in check_pts:
        x, y = pt
        if 0 <= x < W and 0 <= y < H:
            c = tuple(res_arr[y, x])
            print(f"    ({x},{y}): {c}")
    result.save(os.path.join(out_dir, "diag_test3_full_redraw.png"))


def test_4_large_radius():
    """测试4: 大半径（8cm）时 apply_border_only_corners 边框区域扩展对花纹的影响"""
    print("\n" + "="*60)
    print("测试4: 大半径(8cm)对边框区域宽度扩展的影响")
    print("="*60)
    img = make_synthetic_test_image(2400, 1800)  # ~模拟大图
    w, h = img.size
    dpi = 150
    corners = {'tl': 8.0, 'tr': 8.0, 'bl': 8.0, 'br': 8.0}
    bg = (250, 248, 235)

    # 模拟 apply_border_only_corners 内部的 border_w_px 计算
    border_w_cm = DEFAULT_BORDER_WIDTH_CM
    border_w_px = max(1, int(round(border_w_cm * dpi / 2.54)))
    max_r_px = max(1, int(round(8.0 * dpi / 2.54)))
    print(f"  初始 border_w_px = {border_w_px} ({border_w_cm}cm)")
    print(f"  max_r_px = {max_r_px} (8cm)")
    if border_w_px < max_r_px:
        border_w_px = max_r_px
        print(f"  → 自动扩展到 border_w_px = {border_w_px}")

    border_layers = _get_border_layers_robust(img, bg)
    total_t = sum(t for _, t in border_layers) if border_layers else 0
    print(f"  检测到 border_layers: {border_layers}, 总厚度={total_t}px")
    if border_w_px < total_t + max_r_px:
        print(f"  → 进一步扩展到 {total_t + max_r_px}px (total_thickness({total_t}) + max_r({max_r_px}))")
        border_w_px = total_t + max_r_px
    print(f"  最终 border_w_px = {border_w_px}")

    inner_rect = [border_w_px, border_w_px, w - border_w_px, h - border_w_px]
    print(f"  inner_rect = {inner_rect}")
    print(f"  内部尺寸 = {inner_rect[2]-inner_rect[0]} x {inner_rect[3]-inner_rect[1]} px")

    # 检查 inner_rect 是否侵入了内层花纹（在合成图中花纹从80px开始）
    if border_w_px > 80:
        print(f"  ⚠️  警告: border_w_px({border_w_px}) > 花纹起始位置(80px)，内层花纹的边缘直角将被保留而非被裁剪！")
    if border_w_px > 200:
        print(f"  ⚠️  警告: border_w_px({border_w_px}) > 中心黑框位置(200px)，中心黑框可能受影响！")

    # 检测嵌套矩形层
    layers = detect_nested_rect_layers(img)
    print(f"\n  detect_nested_rect_layers 检测到 {len(layers)} 层:")
    for i, l in enumerate(layers):
        print(f"    层{i}: {l}, 尺寸={l[2]-l[0]}x{l[3]-l[1]}")


if __name__ == '__main__':
    test_1_border_only_mask_merge()
    test_2_color_sampling()
    test_3_redraw_area()
    test_4_large_radius()
    print("\n所有诊断完成！请查看 test_cropper_output 目录下的 diag_*.jpg/png 文件。")
