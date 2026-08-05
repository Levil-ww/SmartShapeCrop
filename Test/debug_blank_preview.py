"""诊断设计预览全白问题"""
import sys
sys.path.insert(0, '.')
import numpy as np
from core.geometry import CropDesign, compute_border_bands
from core.image_ops import render_design, _get_inner_pixel_mask

# 创建一个简单的测试设计
design = CropDesign(
    canvas_w_cm=41.2,
    canvas_h_cm=54.0,
    dpi=150,
    mode='rect_hole',
    outer_margin_cm=1.0,
    inner_margin_top_cm=1.5,
    inner_margin_bottom_cm=1.5,
    inner_margin_left_cm=1.5,
    inner_margin_right_cm=1.5,
    corner_tl_cm=9.0,
    corner_tr_cm=9.0,
    corner_bl_cm=9.0,
    corner_br_cm=9.0,
    hole_bg_color=(250, 245, 230),
    outer_bg_color=(0, 0, 0),
)

print("=== 测试设计参数 ===")
print(f"画布尺寸: {design.canvas_w_px} x {design.canvas_h_px} px")
print(f"外矩形: {design.outer_rect_px()}")
print(f"内矩形: {design.inner_rect_px()}")
print(f"圆角半径(px): {design.corners_px}")

# 测试 compute_border_bands
print("\n=== 测试 compute_border_bands ===")
try:
    bands = compute_border_bands(design)
    print(f"边框带数量: {len(bands)}")
    for i, (band_mask, layer) in enumerate(bands):
        print(f"  Band {i}: shape={band_mask.shape}, True像素数={band_mask.sum()}, fill_type={layer.fill_type}, color={layer.color}")
except Exception as e:
    print(f"compute_border_bands 出错: {e}")
    import traceback
    traceback.print_exc()

# 测试 _get_inner_pixel_mask
print("\n=== 测试 _get_inner_pixel_mask ===")
try:
    inner_mask = _get_inner_pixel_mask(design)
    print(f"内部mask shape: {inner_mask.shape}, True像素数={inner_mask.sum()}")
except Exception as e:
    print(f"_get_inner_pixel_mask 出错: {e}")
    import traceback
    traceback.print_exc()

# 测试 render_design
print("\n=== 测试 render_design ===")
try:
    result = render_design(design)
    print(f"渲染结果尺寸: {result.size}")
    result_arr = np.array(result)
    print(f"结果数组统计: min={result_arr.min()}, max={result_arr.max()}, mean={result_arr.mean():.1f}")
    
    # 检查是否全白
    if result_arr.min() == 255 and result_arr.max() == 255:
        print("❌ 结果全白！")
    elif result_arr.min() == 0 and result_arr.max() == 0:
        print("❌ 结果全黑！")
    else:
        print("✅ 结果有内容")
    
    result.save('test_cropper_output/debug_blank_preview.jpg')
    print("已保存到 test_cropper_output/debug_blank_preview.jpg")
except Exception as e:
    print(f"render_design 出错: {e}")
    import traceback
    traceback.print_exc()

print("\n=== 诊断完成 ===")
