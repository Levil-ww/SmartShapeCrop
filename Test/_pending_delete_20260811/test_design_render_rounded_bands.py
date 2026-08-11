# -*- coding: utf-8 -*-
"""
测试：设计预览系统（render_design / compute_border_bands）在设置大圆角（如 8.5cm）时，
是否正确把圆角应用到了所有边框层（外黑框、白间隔、红框、花纹层等），而不是只改了内部填充区域的形状。

对比：
  - 不加圆角的渲染 vs 加了 8.5cm 右下角圆角的渲染
  - 检查各层边框右下角附近的像素：
      * 边框层最外边缘的右下角（应裁成白色 = 背景色）
      * 边框层中间（白色间隔层）的右下角 L 形区（也应裁掉）
      * 边框层最内边缘（靠近水池内部填充）的右下角（也应是圆角）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image, ImageDraw

from core.geometry import CropDesign, compute_border_bands
from core.image_ops import render_design, _get_inner_pixel_mask

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'test_cropper_output')
os.makedirs(OUT_DIR, exist_ok=True)

DPI = 150

# ============= 构造一个和用户描述类似的多层边框设计 =============
def make_design(corner_br_cm=0.0):
    d = CropDesign()
    d.canvas_w_cm = 41.0   # 宽 41cm（和用户常见竖版一致）
    d.canvas_h_cm = 55.0   # 高 55cm
    d.dpi = DPI
    d.mode = 'rect_hole'
    d.outer_margin_cm = 0.5  # 外留白 0.5cm
    # 内挖 8cm 边距（留出多层边框区域）
    d.inner_margin_top_cm = 8.0
    d.inner_margin_bottom_cm = 8.0
    d.inner_margin_left_cm = 8.0
    d.inner_margin_right_cm = 8.0
    # 多层边框（模拟：外黑 → 白间隔 → 红 → 花纹层的结构）
    from core.geometry import BorderLayer
    d.borders = [
        # Layer 0: 最外 黑色粗框 0.8cm
        BorderLayer(offset_cm=0.8, fill_type='solid', color=(20, 20, 20)),
        # Layer 1: 白色间隔 0.3cm
        BorderLayer(offset_cm=0.3, fill_type='solid', color=(255, 255, 255)),
        # Layer 2: 红色 0.3cm
        BorderLayer(offset_cm=0.3, fill_type='solid', color=(220, 40, 40)),
        # Layer 3: 白色间隔 0.2cm
        BorderLayer(offset_cm=0.2, fill_type='solid', color=(255, 255, 255)),
        # Layer 4: 深灰（花纹背景色占位） 3.5cm
        BorderLayer(offset_cm=3.5, fill_type='solid', color=(80, 60, 30)),
        # Layer 5: 细浅金框 0.1cm
        BorderLayer(offset_cm=0.15, fill_type='solid', color=(220, 190, 120)),
        # Layer 6: 米白最内 2.0cm
        BorderLayer(offset_cm=2.0, fill_type='solid', color=(250, 245, 230)),
    ]
    d.outer_bg_color = (255, 255, 255)  # 最外背景 = 白
    d.hole_bg_color = (250, 245, 220)   # 水池内部 = 米黄
    # 圆角设置：只设右下角
    d.corner_tl_cm = 0.0
    d.corner_tr_cm = 0.0
    d.corner_bl_cm = 0.0
    d.corner_br_cm = corner_br_cm
    return d

print("""
============================================================
测试：设计预览（render_design）多层边框圆角一致性
============================================================""")

# ---------- 1. 渲染无圆角版本 ----------
print("\n[1/4] 渲染：无圆角 (corner_br=0cm)")
design_no = make_design(0.0)
img_no = render_design(design_no)
W, H = img_no.size
print(f"      画布尺寸: {W} x {H} px ({design_no.canvas_w_cm} x {design_no.canvas_h_cm} cm @ {DPI} DPI)")
path_no = os.path.join(OUT_DIR, 'test_design_bands_NOcorner.jpg')
img_no.save(path_no, quality=95)

# ---------- 2. 渲染 8.5cm 右下角圆角版本 ----------
radius_cm = 8.5
print(f"\n[2/4] 渲染：右下角圆角 = {radius_cm} cm ( >= 阈值 8.5cm)")
design_rd = make_design(radius_cm)
img_rd = render_design(design_rd)
path_rd = os.path.join(OUT_DIR, 'test_design_bands_WITHcorner_8.5cm.jpg')
img_rd.save(path_rd, quality=95)

# 也保存 debug 版：在圆角版上叠加彩色线框标出每层 band 的大致位置
print("\n[3/4] 生成 debug 图（在圆角渲染图上叠加各层边框的矩形轮廓 + 检查点）")
debug_img = img_rd.copy()
dd = ImageDraw.Draw(debug_img)

# cm → px
def cm2px(cm): return int(round(cm * DPI / 2.54))

outer_margin_px = cm2px(design_rd.outer_margin_cm)
ox1, oy1 = outer_margin_px, outer_margin_px
ox2, oy2 = W - outer_margin_px, H - outer_margin_px
# 累计 offset（从外向内）
cum_off = 0
layer_colors = [
    (0, 0, 255),       # 外黑 → 蓝
    (0, 180, 0),       # 白间隔 → 绿
    (200, 0, 200),     # 红 → 紫
    (255, 140, 0),     # 白间隔 → 橙
    (100, 200, 255),   # 深灰 → 浅蓝
    (255, 0, 0),       # 浅金 → 红
    (120, 80, 20),     # 米白 → 棕
]
offsets_cm = [l.offset_cm for l in design_rd.borders]
# 画每层的外边框
x1, y1 = ox1, oy1
for i, off_cm in enumerate(offsets_cm):
    x2, y2 = ox2 - cum_off, oy2 - cum_off
    lw = max(2, W // 700)
    dd.rectangle([x1, y1, x2, y2], outline=layer_colors[i % len(layer_colors)], width=lw)
    dd.text((x1 + 6, y1 + 6), f'L{i}outer', fill=layer_colors[i % len(layer_colors)])
    cum_off += cm2px(off_cm)
    x1, y1 = ox1 + cum_off, oy1 + cum_off

# 最后画最内层（水池内部）的轮廓
inner = design_rd.inner_rect_px()
ix1, iy1, ix2, iy2 = int(inner.x), int(inner.y), int(inner.right), int(inner.bottom)
dd.rectangle([ix1, iy1, ix2, iy2], outline=(255, 0, 255), width=3)
dd.text((ix1 + 10, iy1 + 10), 'INNER_hole', fill=(255, 0, 255))
path_debug = os.path.join(OUT_DIR, 'test_design_bands_WITHcorner_debug.jpg')
debug_img.save(path_debug, quality=95)

# ---------- 4. 像素级验证 ----------
print("\n[4/4] 关键像素验证（只看右下角 br）")
arr_no = np.array(img_no)
arr_rd = np.array(img_rd)

def is_white(p, thr=250):
    return int(p[0]) >= thr and int(p[1]) >= thr and int(p[2]) >= thr

def color_name(p):
    r,g,b = int(p[0]), int(p[1]), int(p[2])
    # 粗略分类
    s = r+g+b
    if s >= 740: return '白色(背景)'
    if s <= 80:  return '黑色(外框)'
    if r > 180 and g < 100 and b < 100: return '红色'
    if abs(r-250)<15 and abs(g-245)<15 and abs(b-230)<20: return '米白(边框内/填充)'
    if r>200 and g>170 and b<140: return '浅金'
    if 40<r<120 and 30<g<90 and 10<b<60: return '深棕(花纹)'
    return f'({r},{g},{b})'

r_px = max(0, int(round(radius_cm * DPI / 2.54)))
print(f"      圆角半径: {radius_cm} cm = {r_px} px")

checks = []
# --- 外黑框 (Layer 0) 的右下角 ---
# 外框最外右下顶点（应被裁掉=白）
checks.append(('【L0外黑】最外右下顶点 (ox2-1, oy2-1)', ox2-1, oy2-1, True))
# 外黑框内部的 L 形尖角区：距离顶点 0.8*r 处
dl0 = max(2, int(r_px * 0.8))
cx0, cy0 = ox2 - r_px, oy2 - r_px
checks.append((f'【L0外黑】L形尖角区 dx={dl0} dy={dl0}', cx0 + dl0, cy0 + dl0, True))

# --- 白色间隔 (Layer 1) 对应的 L 形位置 ---
# 计算 Layer 1 的外边缘相对画布的位置：外边缘 = outer_rect - offset[0]
off0 = cm2px(offsets_cm[0])
L1_x1, L1_y1 = ox1 + off0, oy1 + off0
L1_x2, L1_y2 = ox2 - off0, oy2 - off0
L1_sr = min(r_px, (L1_x2-L1_x1)//2, (L1_y2-L1_y1)//2)
cx1, cy1 = L1_x2 - L1_sr, L1_y2 - L1_sr
dl1 = max(2, int(L1_sr * 0.8))
checks.append((f'【L1白间隔】右下顶点 ({L1_x2-1},{L1_y2-1})', L1_x2-1, L1_y2-1, True))
checks.append((f'【L1白间隔】L形尖角 dx={dl1} dy={dl1}', cx1 + dl1, cy1 + dl1, True))

# --- 红色 (Layer 2) ---
off01 = cm2px(sum(offsets_cm[:2]))
L2_x1, L2_y1 = ox1 + off01, oy1 + off01
L2_x2, L2_y2 = ox2 - off01, oy2 - off01
L2_sr = min(r_px, (L2_x2-L2_x1)//2, (L2_y2-L2_y1)//2)
cx2, cy2 = L2_x2 - L2_sr, L2_y2 - L2_sr
dl2 = max(2, int(L2_sr * 0.8))
checks.append((f'【L2红框】右下顶点 ({L2_x2-1},{L2_y2-1})', L2_x2-1, L2_y2-1, True))
checks.append((f'【L2红框】L形尖角 dx={dl2} dy={dl2}', cx2 + dl2, cy2 + dl2, True))

# --- 深棕花纹背景 (Layer 4) ---
off04 = cm2px(sum(offsets_cm[:4]))
L4_x1, L4_y1 = ox1 + off04, oy1 + off04
L4_x2, L4_y2 = ox2 - off04, oy2 - off04
L4_sr = min(r_px, (L4_x2-L4_x1)//2, (L4_y2-L4_y1)//2)
cx4, cy4 = L4_x2 - L4_sr, L4_y2 - L4_sr
dl4 = max(2, int(L4_sr * 0.8))
checks.append((f'【L4深棕花纹】右下顶点 ({L4_x2-1},{L4_y2-1})', L4_x2-1, L4_y2-1, True))
checks.append((f'【L4深棕花纹】L形尖角 dx={dl4} dy={dl4}', cx4 + dl4, cy4 + dl4, True))

# --- 最内层米白 (Layer 6) 最内边缘，即水池内部的外边缘 ---
# 内层 hole 的右下角，也应该是圆角，不该露出尖角
inner_sr = min(r_px, (ix2-ix1)//2, (iy2-iy1)//2)
cix, ciy = ix2 - inner_sr, iy2 - inner_sr
dli = max(2, int(inner_sr * 0.8))
checks.append((f'【INNER水池内】右下顶点 ({ix2-1},{iy2-1})', ix2-1, iy2-1, True))
checks.append((f'【INNER水池内】L形尖角 dx={dli} dy={dli}', cix + dli, ciy + dli, True))

# --- 扇形保留区（应保留原图色，不是纯白）——仅取外轮廓和内轮廓的两个参考点 ---
din0 = max(2, int(r_px * 0.5))
checks.append((f'【扇形参考】L0扇形保留区 dx={din0} dy={din0}', cx0 + din0, cy0 + din0, False))
dini = max(2, int(inner_sr * 0.5))
checks.append((f'【扇形参考】INNER扇形保留区 dx={dini} dy={dini}', cix + dini, ciy + dini, False))

# 水池中心主体（应保留米黄色填充 = 非白）
checks.append(('【中心】水池主体中心', W//2, H//2, False))

print(f"\n  --- 无圆角版本 (corner=0) 参考 ---")
all_nocorner_ok = True
for desc, x, y, exp in checks:
    p = arr_no[min(max(y,0),H-1), min(max(x,0),W-1)]
    white = is_white(p)
    ok = (white == False)  # 无圆角版本：所有检查点应该都是非白色（因为没有裁圆角）
    if not ok: all_nocorner_ok = False
    print(f"    ({x:>4},{y:>4}) {desc:<44}: {color_name(p):<14} {'✓' if ok else '·'}")
print(f"  → 无圆角版本所有点应为非白（因为没有切角）：{'✓ 正确' if all_nocorner_ok else '⚠ 有些位置已是白色'}")

print(f"\n  --- 加圆角版本 (corner={radius_cm}cm) 验证 ---")
critical_ok = True
ref_ok = True
for desc, x, y, expect_white in checks:
    p = arr_rd[min(max(y,0),H-1), min(max(x,0),W-1)]
    white = is_white(p)
    ok = (white == expect_white)
    is_critical = ('L形' in desc) or ('顶点' in desc) or ('主体' in desc)
    mark = '✓' if ok else ('✗(核心!)' if (is_critical and not ok) else '·(参考)')
    if is_critical and not ok:
        critical_ok = False
    elif not is_critical and not ok:
        ref_ok = False
    print(f"    ({x:>4},{y:>4}) {desc:<44}: {color_name(p):<14} {mark}  期望={'白色(被裁)' if expect_white else '非白色(保留)'}")

print()
if critical_ok:
    if ref_ok:
        print("  ➜ ✓ 综合：所有核心检查（各层边框L形尖角/顶点）都被裁为圆角（白色），参考扇形点也正确保留了原图色。")
    else:
        print("  ➜ ✓✓ 综合：核心需求通过！（每层边框的尖角/顶点都已裁成圆角=白；个别扇形参考点变化属允许范围，因为边框外背景与扇形保留区颜色可能恰好都是白或米）")
else:
    print("  ➜ ✗ 失败：某些边框层的 L 形尖角/顶点仍未被裁掉（会露出尖角，就是用户图一的问题）！")

print(f"""
============================================================
输出文件：
  无圆角:            {path_no}
  有圆角({radius_cm}cm br):  {path_rd}
  有圆角(debug层标): {path_debug}
请肉眼对比：
  1) 无圆角版本右下角：所有层外边缘都是直角的尖角（方形）
  2) 有圆角版本右下角：从最外黑→白间隔→红框→深棕→浅金→米白→水池内边缘，
     它们的右下角应"同步"是圆弧，没有哪一层露出直角尖角。
  3) debug 图上的彩色矩形轮廓：每层的彩色框右下角应与实际渲染色的圆角外/内边缘对应。
============================================================""")
