"""
验证修复后的颜色映射逻辑
"""
import numpy as np
from PIL import Image

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
dpi = 150

print("=" * 60)
print("验证新的颜色映射逻辑")
print("=" * 60)

# 加载源图
src = Image.open(src_path)
w, h = src.size
src_arr = np.array(src)

# 边框层
border_layers = [
    ((8, 8, 8), 157),      # 第1层: 0-157px, 厚度157px
    ((186, 165, 131), 30), # 第2层: 157-187px, 厚度30px
    ((221, 206, 185), 108) # 第3层: 187-295px, 厚度108px
]

R_total = 207  # 3.5cm

# 构建累积厚度
cumulative_depths = [0]
for _, thickness in border_layers:
    cumulative_depths.append(cumulative_depths[-1] + thickness)
total_border_depth = cumulative_depths[-1]

# 计算每层被裁剪的厚度
layer_clipped_thickness = []
for i, (_, thickness) in enumerate(border_layers):
    cum_i = cumulative_depths[i]
    R_remaining = max(0, R_total - cum_i)
    clipped = min(thickness, R_remaining)
    layer_clipped_thickness.append(clipped)
    print(f"第{i+1}层: 累积[{cum_i}, {cum_i+thickness}), 裁剪厚度={clipped}px")

# 构建颜色采样映射表
depth_mapping = {}
for d in range(min(R_total + 1, total_border_depth + 100)):
    src_depth = d
    for i in range(len(border_layers)):
        cum_i = cumulative_depths[i]
        cum_next = cumulative_depths[i + 1]
        clipped = layer_clipped_thickness[i]
        
        if cum_i <= d < cum_next:
            if clipped >= (cum_next - cum_i):
                src_depth = min(cum_next, total_border_depth)
            elif d < cum_i + clipped:
                src_depth = min(cum_next, total_border_depth)
            else:
                src_depth = min(d, total_border_depth)
            break
    
    depth_mapping[d] = src_depth

# 验证映射结果
print(f"\n[颜色映射验证]")
print(f"  结果图深度 -> 原图采样深度 -> 采样颜色所属层")
print(f"  ----------------------------------------")

# 采样右下角的颜色
def sample_pixel(depth):
    x = w - 1 - depth
    y = h - 1 - depth
    if 0 <= x < w and 0 <= y < h:
        return tuple(src_arr[y, x].tolist())
    return None

for d in [0, 50, 100, 150, 156, 157, 165, 170, 180, 187, 195, 200, 207]:
    if d >= len(depth_mapping):
        continue
    
    src_d = depth_mapping[d]
    
    # 确定 src_d 所属的层
    layer_info = "背景"
    for i, (_, thickness) in enumerate(border_layers):
        cum = cumulative_depths[i]
        if cum <= src_d < cum + thickness:
            layer_info = f"第{i+1}层"
            break
    
    pixel = sample_pixel(src_d)
    if pixel:
        print(f"  d={d:3d}px -> src_d={src_d:3d}px -> {layer_info}, 颜色={pixel}")
    else:
        print(f"  d={d:3d}px -> src_d={src_d:3d}px -> {layer_info}, 位置无效")

# 关键验证
print(f"\n[关键验证]")
print(f"  结果图中深度 0-157px 的区域应该显示第2层的颜色(米色)")
print(f"  结果图中深度 157-187px 的区域应该显示第3层的颜色(浅米色)")
print(f"  结果图中深度 187-207px 的区域应该显示第3层的颜色(浅米色)")

# 检查几个关键点
print(f"\n  检查 d=50 (应该显示第2层):")
src_d = depth_mapping[50]
pixel = sample_pixel(src_d)
print(f"    src_d={src_d}, 颜色={pixel}")

print(f"\n  检查 d=170 (应该显示第3层):")
src_d = depth_mapping[170]
pixel = sample_pixel(src_d)
print(f"    src_d={src_d}, 颜色={pixel}")

print(f"\n  检查 d=195 (应该显示第3层):")
src_d = depth_mapping[195]
pixel = sample_pixel(src_d)
print(f"    src_d={src_d}, 颜色={pixel}")

print("\n" + "=" * 60)
