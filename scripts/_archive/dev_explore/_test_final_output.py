"""
生成最终圆角裁剪结果图
目标: 35.5x256cm, 左下角和右下角圆角半径3.5cm
"""
import sys
sys.path.insert(0, r"D:\SmartShapeCrop")

from core.image_cropper import crop_image, CropConfig
from core.config import DEFAULT_BG_COLOR

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_path = r"D:\SmartShapeCrop\Test\output\双面格-定制-定制尺寸-蔓生花;35.5x256cm.jpg"

print("=" * 60)
print("生成最终圆角裁剪结果图")
print(f"源文件: {src_path}")
print(f"目标尺寸: 35.5 x 256 cm")
print(f"圆角: 左下角 3.5cm, 右下角 3.5cm")
print(f"输出: {output_path}")
print("=" * 60)

config = CropConfig(
    src_path=src_path,
    target_w_cm=35.5,
    target_h_cm=256,
    dpi=150,
    mode='cover',
    bg_color=DEFAULT_BG_COLOR,
    corners={'bl': 3.5, 'br': 3.5},
    output_path=output_path,
)

result = crop_image(config)
w, h = result.size
print(f"\n完成! 结果尺寸: {w} x {h} px")
print(f"输出文件: {output_path}")
