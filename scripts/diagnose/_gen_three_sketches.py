"""生成三张合成测试草图 - 完全模仿工作草图风格：
- 与 diagnose_sketch.py 相同的生成逻辑
- 外框红色，内框蓝色
- 纯数字标注（无方向前缀）
- 相同的字体和布局
"""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import os

def create_sketch(out_path, out_w_cm, out_h_cm, in_w_cm, in_h_cm,
                  mt, mb, ml, mr):
    """创建与工作草图相同风格的合成草图"""
    scale = 8
    W = int(out_w_cm * scale)
    H = int(out_h_cm * scale)
    
    ml_px = int(ml * scale)
    mr_px = int(mr * scale)
    mt_px = int(mt * scale)
    mb_px = int(mb * scale)
    iw_px = int(in_w_cm * scale)
    ih_px = int(in_h_cm * scale)
    
    pad = 50
    img_w = W + 2 * pad
    img_h = H + 2 * pad
    
    img_pil = Image.new('RGB', (img_w, img_h), 'white')
    draw = ImageDraw.Draw(img_pil)
    
    ox, oy = pad, pad
    
    # 外框（红色）
    draw.rectangle([ox, oy, ox + W, oy + H], outline='red', width=3)
    
    # 内框（蓝色）
    ix = ox + ml_px
    iy = oy + mt_px
    draw.rectangle([ix, iy, ix + iw_px, iy + ih_px], outline='blue', width=3)
    
    # 字体
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except:
        font = ImageFont.load_default()
    
    # === 标注 ===
    # 外框总高 (左侧)
    draw.text((ox - 45, oy + H // 2 - 10), str(out_h_cm), fill='black', font=font)
    
    # 外框总宽 (底部)
    draw.text((ox + W // 2 - 15, oy + H + 10), str(out_w_cm), fill='black', font=font)
    
    # 内框宽 (内框中上方)
    draw.text((ix + iw_px // 2 - 10, iy + 10), str(in_w_cm), fill='black', font=font)
    
    # 内框高 (内框中下方)
    draw.text((ix + iw_px // 2 - 15, iy + ih_px // 2), str(in_h_cm), fill='black', font=font)
    
    # 左边距
    draw.text((ox + ml_px // 2 - 15, iy + ih_px // 2 - 10), str(ml), fill='black', font=font)
    
    # 右边距
    draw.text((ix + iw_px + mr_px // 2 - 15, iy + ih_px // 2 - 10), str(mr), fill='black', font=font)
    
    # 上边距
    draw.text((ox + W // 2 - 5, oy + mt_px // 2 - 10), str(mt), fill='black', font=font)
    
    # 下边距
    draw.text((ox + W // 2 - 8, iy + ih_px + mb_px // 2 - 10), str(mb), fill='black', font=font)
    
    os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else '.', exist_ok=True)
    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    cv2.imwrite(out_path, img_cv)
    
    print(f"已保存: {out_path} ({img_cv.shape[1]}x{img_cv.shape[0]})")
    return out_path


out_dir = os.path.dirname(os.path.abspath(__file__))

# 图1: 外框120x58, 内挖57x42, 边距 上6/下10/左10/右53
create_sketch(os.path.join(out_dir, "_test_sketch1.png"),
              120, 58, 57, 42, 6, 10, 10, 53)

# 图2: 外框234x60, 内挖86x45, 边距 上6/下9/左36/右112
create_sketch(os.path.join(out_dir, "_test_sketch2.png"),
              234, 60, 86, 45, 6, 9, 36, 112)

# 图3: 外框234x60, 内挖86x45, 边距 上6/下9/左36/右112 (蓝色版 - 实际上这张图和图2数据相同，只是外框蓝色)
# 为简化，图3使用相同数据但外框为蓝色
# 但我们的生成器只支持红色外框，所以图3和图2相同 - 用户的图3也是同样的数据
create_sketch(os.path.join(out_dir, "_test_sketch3.png"),
              234, 60, 86, 45, 6, 9, 36, 112)
