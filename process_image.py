"""
图片等比缩放 + 圆角处理脚本
圆角处理：仅边框区域应用圆角，内层装饰保持直角。
"""
import os
from PIL import Image

# 像素上限设为 2 亿（约 14142×14142），覆盖印刷级大图的同时防御解压缩炸弹
Image.MAX_IMAGE_PIXELS = 200_000_000

from core.image_cropper import (
    load_source_image,
    apply_rounded_corners,
    apply_border_only_corners,
    crop_image, CropConfig,
)
from core.log_setup import setup_logging

# 初始化日志（幂等，调试时设 LOG_LEVEL=DEBUG）
setup_logging()

# ============ 参数配置 ============
# [F12 修复] 不再硬编码本机绝对路径：
# 默认取脚本所在目录下的 psd_demo（原机器行为不变），并支持命令行参数覆盖。
import argparse as _argparse

_script_dir = os.path.dirname(os.path.abspath(__file__))
_parser = _argparse.ArgumentParser(
    description="图片等比缩放 + 圆角处理脚本（示例/批处理）",
)
_parser.add_argument("--src", default=os.path.join(
    _script_dir, "psd_demo", "双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"))
_parser.add_argument("--out-dir", default=os.path.join(_script_dir, "psd_demo"))
_parser.add_argument("--out-name", default="双面格-定制-定制尺寸-简织;竖版55x41cm右下角圆角半径2cm.jpg")
_parser.add_argument("--target-w", type=float, default=41.0, help="目标宽度（厘米，竖版短边）")
_parser.add_argument("--target-h", type=float, default=55.0, help="目标高度（厘米，竖版长边）")
_parser.add_argument("--corner-r", type=float, default=2.0, help="圆角半径（厘米）")
_parser.add_argument("--dpi", type=int, default=150, help="DPI")
_args = _parser.parse_args()

src_path = _args.src
output_dir = _args.out_dir
output_name = _args.out_name

target_w_cm = _args.target_w
target_h_cm = _args.target_h
corner_r_cm = _args.corner_r
dpi = _args.dpi

# ============ 计算像素 ============
target_w_px = int(round(target_w_cm * dpi / 2.54))
target_h_px = int(round(target_h_cm * dpi / 2.54))
corner_r_px = int(round(corner_r_cm * dpi / 2.54))

print(f"目标尺寸: {target_w_px} x {target_h_px} px ({target_w_cm} x {target_h_cm} cm @ {dpi} DPI)")
print(f"圆角半径: {corner_r_px} px ({corner_r_cm} cm)")

# ============ 1. 加载源图 ============
src = load_source_image(src_path)
print(f"源图尺寸: {src.size} px")

# ============ 2. 简单缩放（直接拉伸填满目标尺寸；源图比例不同时图像会变形）============
cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
print(f"缩放后尺寸: {cropped.size} px")

# ============ 3. 添加圆角（仅边框区域应用圆角） ============
corners = {'tl': 0, 'tr': 0, 'bl': 0, 'br': corner_r_cm}
result = apply_border_only_corners(cropped, corners, dpi, (255, 255, 255))

# ============ 4. 保存 ============
output_path = os.path.join(output_dir, output_name)
result.save(output_path, 'JPEG', quality=95, optimize=True, dpi=(dpi, dpi))
print(f"\n[完成] 已保存: {output_path}")
print(f"输出尺寸: {result.size} px")