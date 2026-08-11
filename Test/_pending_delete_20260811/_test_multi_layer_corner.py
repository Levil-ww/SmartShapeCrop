"""
多层边框动态圆角裁剪测试脚本
验证新的圆角裁剪算法效果
"""
import os
import sys

# 添加项目路径
sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import (
    load_source_image,
    apply_border_only_corners,
    _get_border_layers_robust,
)
from core.config import DEFAULT_BG_COLOR

# 测试参数
src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_dir = r"D:\SmartShapeCrop\test_cropper_output"
dpi = 150
bg_color = DEFAULT_BG_COLOR

# 圆角测试配置
test_configs = [
    # (名称, 圆角半径cm, 裁剪尺寸cm)
    ("br_3cm", 3.0, 55, 41),      # 3cm 圆角 - 应只影响前两层
    ("br_5cm", 5.0, 55, 41),      # 5cm 圆角 - 应影响全部三层
    ("br_8cm", 8.0, 55, 41),      # 8cm 圆角 - 更深的圆角
]

def test_multi_layer_corner():
    """测试多层边框动态圆角裁剪"""
    print("=" * 60)
    print("多层边框动态圆角裁剪测试")
    print("=" * 60)
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 加载源图
    src = load_source_image(src_path)
    w, h = src.size
    print(f"\n[源图] {os.path.basename(src_path)}")
    print(f"  尺寸: {w} x {h} px")
    
    # 检测边框层
    border_layers = _get_border_layers_robust(src, bg_color)
    print(f"  检测到 {len(border_layers)} 层边框:")
    for i, (color, thickness) in enumerate(border_layers):
        cm = thickness * 2.54 / dpi
        print(f"    第{i+1}层: {thickness}px ({cm:.2f}cm)")
    
    print("\n" + "=" * 60)
    print("开始测试裁剪...")
    print("=" * 60)
    
    for name, r_cm, target_w_cm, target_h_cm in test_configs:
        print(f"\n[测试] {name}: 圆角半径={r_cm}cm")
        
        try:
            # 先缩放到目标尺寸
            target_w_px = int(round(target_w_cm * dpi / 2.54))
            target_h_px = int(round(target_h_cm * dpi / 2.54))
            
            # 使用 simple_resize 模式
            cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
            print(f"  缩放后: {target_w_px} x {target_h_px} px")
            
            # 应用圆角（仅右下角）
            corners = {'tl': 0, 'tr': 0, 'bl': 0, 'br': r_cm}
            result = apply_border_only_corners(cropped, corners, dpi, bg_color)
            
            # 保存结果
            output_name = f"test_multi_layer_{name}.jpg"
            output_path = os.path.join(output_dir, output_name)
            result.save(output_path, 'JPEG', quality=95, dpi=(dpi, dpi))
            
            print(f"  [成功] 已保存: {output_path}")
            print(f"  输出尺寸: {result.size[0]} x {result.size[1]} px")
            
        except Exception as e:
            print(f"  [错误] {str(e)}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("测试完成！请查看输出目录:")
    print(f"  {output_dir}")
    print("=" * 60)

if __name__ == '__main__':
    test_multi_layer_corner()
