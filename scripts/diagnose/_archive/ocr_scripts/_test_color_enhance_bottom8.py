"""Phase1修复验证：合成模拟用户场景草图（红笔标注+下8小字）
并运行 parse_sketch 捕获 Step4 日志，确认颜色增强/小数字补漏效果。
"""
import sys, os, logging, io, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ========== 1. 合成用户场景草图 ==========
W, H = 900, 650          # 画布像素
img = np.ones((H, W, 3), dtype=np.uint8) * 255  # 白底

black = (0, 0, 0)
red_bgr = (0, 0, 220)   # 红色标注（BGR，不是最饱和的→模拟浅红笔低对比度）
faded_red = (60, 60, 200)  # 更淡的红：模拟小字"8"对比度不足（易误识为0）

# 外框：对应 121 x 58 cm → 像素比例：宽:高 = 121:58 ≈ 2.086:1
pad_x, pad_y = 80, 100
ox, oy = pad_x, pad_y
ow = W - 2*pad_x              # 740 px
oh = int(ow * 58 / 121)       # 740 * 58/121 ≈ 355 px

# 内框：68 x 45 cm，对应边距：左2/右51，上5/下8
# 横向比例：2 : 68 : 51 → 总宽 121 → 左=2/121, 内w=68/121, 右=51/121
ix = int(ox + ow * 2 / 121)
iw = int(ow * 68 / 121)
# 纵向比例：5 : 45 : 8 → 总高 58
iy = int(oy + oh * 5 / 58)
ih = int(oh * 45 / 58)

# 画外框和内框（黑色粗线）
cv2.rectangle(img, (ox, oy), (ox+ow, oy+oh), black, 3)
cv2.rectangle(img, (ix, iy), (ix+iw, iy+ih), black, 3)

# 转换到 PIL 画文字和箭头
pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
draw = ImageDraw.Draw(pil_img)

# 加载字体（优先支持中文）
def get_chinese_font(size):
    font_candidates = [
        r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",    # 黑体
        r"C:\Windows\Fonts\simsun.ttc",    # 宋体
        "arial.ttf",
    ]
    for fp in font_candidates:
        try:
            return ImageFont.truetype(fp, size)
        except:
            continue
    return ImageFont.load_default()

font_big    = get_chinese_font(30)   # 外框总尺寸
font_mid    = get_chinese_font(24)   # 方向标签+较大数值
font_small  = get_chinese_font(20)   # 内框尺寸
font_tiny   = get_chinese_font(16)   # "下 8" 用最小字体→模拟小字识别难点

red_rgb = (200, 0, 0)       # 正常红标注
faded_red_rgb = (170, 70, 70)  # 淡红：对比度低，8 最容易被误识为 0

def draw_arrow(draw, p1, p2, color, width=3, head=12):
    """画带箭头的直线"""
    draw.line([p1, p2], fill=color, width=width)
    import math
    angle = math.atan2(p2[1]-p1[1], p2[0]-p1[0])
    hp1 = (p2[0] - head*math.cos(angle-math.pi/6), p2[1] - head*math.sin(angle-math.pi/6))
    hp2 = (p2[0] - head*math.cos(angle+math.pi/6), p2[1] - head*math.sin(angle+math.pi/6))
    draw.line([p2, hp1], fill=color, width=width)
    draw.line([p2, hp2], fill=color, width=width)

# ---- 外框总尺寸标注 ----
draw.text((ox + ow//2 - 25, oy - 45), "121", fill=red_rgb, font=font_big)      # 上边界：121
draw.text((ox - 55, oy + oh//2 - 15), "58", fill=red_rgb, font=font_big)       # 左边界：58
# 外框总高箭头（左侧）
draw_arrow(draw, (ox-25, oy), (ox-25, oy+oh), red_rgb)
draw_arrow(draw, (ox-25, oy+oh), (ox-25, oy), red_rgb)

# ---- 边距：上 5（上方箭头+文字）----
top_cx = ox + ow//2
draw_arrow(draw, (top_cx, oy - 8), (top_cx, iy + 8), red_rgb)
draw.text((top_cx + 15, oy + 15), "上 5", fill=red_rgb, font=font_mid)

# ---- 边距：左 2（左方箭头+文字）----
left_cy = oy + oh//2
draw_arrow(draw, (ox - 8, left_cy), (ix + 8, left_cy), red_rgb)
draw.text((ox - 80, left_cy - 55), "左 2", fill=red_rgb, font=font_mid)

# ---- 边距：右 51（右方箭头+文字）----
draw_arrow(draw, (ox+ow + 8, left_cy), (ix+iw - 8, left_cy), red_rgb)
draw.text((ox + ow + 15, left_cy - 15), "右 51", fill=red_rgb, font=font_mid)

# ---- 边距：下 8（下方箭头+文字）关键：小字+淡红→最容易误识为0 ----
bottom_cx = ix + iw//2
draw_arrow(draw, (bottom_cx, iy+ih - 8), (bottom_cx, oy+oh + 8), faded_red_rgb)
# "下 8" 放在蓝框所示位置（外框下方偏右），字体小+淡红
draw.text((ix + 30, oy + oh + 12), "下 8", fill=faded_red_rgb, font=font_tiny)

# ---- 内框尺寸 ----
draw.text((ix + iw//2 - 15, iy - 32), "68", fill=black, font=font_mid)        # 内框宽 68
draw.text((ix - 40, iy + ih//2 - 10), "45", fill=black, font=font_small)      # 内框高 45（左）
draw.text((ix + iw + 10, iy + 20), "51", fill=black, font=font_small)         # 内框高 51（右，用户截图有）

# 保存
out_sketch = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_synth_bottom8_red.png")
img_final = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
cv2.imwrite(out_sketch, img_final)
print(f"[1/3] 合成草图已保存: {out_sketch}")
print(f"      外框: {ow}x{oh}px ≈ 121x58cm  内框: {iw}x{ih}px ≈ 68x45cm")
print(f"      标注(红笔): 上5 / 左2 / 右51 / 下8(小字+淡红)")

# ========== 2. 捕获 parse_sketch 日志 ==========
print(f"\n[2/3] 运行 parse_sketch (含颜色增强+小数字补漏) ...")

# 设置 logger 捕获 Step4 的 DEBUG/INFO 输出
log_stream = io.StringIO()
stream_handler = logging.StreamHandler(log_stream)
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))

from core.pool_designer import sketch_parser as sp_mod
# sketch_parser.py: logger = logging.getLogger(__name__) → name = 'core.pool_designer.sketch_parser'
logger_name = sp_mod.logger.name
sp_logger = logging.getLogger(logger_name)
old_handlers = sp_logger.handlers[:]
old_level = sp_logger.level
old_propagate = sp_logger.propagate
sp_logger.handlers = []
sp_logger.addHandler(stream_handler)
sp_logger.setLevel(logging.DEBUG)
sp_logger.propagate = False  # 避免双重输出

t0 = time.perf_counter()
result = sp_mod.parse_sketch(out_sketch, target_outer_w_cm=121.0, target_outer_h_cm=58.0)
elapsed = time.perf_counter() - t0

# 恢复 logger
sp_logger.handlers = old_handlers
sp_logger.level = old_level
sp_logger.propagate = old_propagate

# ========== 3. 打印结果和关键日志 ==========
full_log = log_stream.getvalue()

print(f"\n[3/3] 解析结果 (耗时 {elapsed:.2f}s):")
print(f"  成功: {result.success}")
print(f"  消息: {result.message}")
print(f"  方法: {result.method}")
if result.success:
    print(f"  外框: {result.outer_w_cm:.1f} x {result.outer_h_cm:.1f} cm")
    print(f"  内框: {result.inner_w_cm:.1f} x {result.inner_h_cm:.1f} cm")
    print(f"  边距: 上{result.margin_top_cm:.1f} / 下{result.margin_bottom_cm:.1f} / 左{result.margin_left_cm:.1f} / 右{result.margin_right_cm:.1f} cm")
    print(f"  Debug 方向锁定: {result.debug.get('dir_locked', {}) if isinstance(result.debug, dict) else 'N/A'}")

print(f"\n==== Step4 方向标签识别关键日志 ====")
step4_lines = [l for l in full_log.splitlines() if '[Step4]' in l or 'color_enh' in l or '小数字补漏' in l or '单token' in l or '双token' in l or '颜色增强' in l]
for l in step4_lines:
    print(l)

# 最终判定
margin_bottom = result.margin_bottom_cm if result.success else -1
print(f"\n==== 结论: 下边距识别值 = {margin_bottom:.1f} cm  (真实值 8.0) ====")
if abs(margin_bottom - 8.0) < 0.5:
    print("✅ 修复成功！'下8'已被正确识别")
elif abs(margin_bottom) < 0.3:
    print("❌ 修复失败：仍识别为 0 或接近 0")
else:
    print(f"⚠️  识别为其他值: {margin_bottom:.1f}")
