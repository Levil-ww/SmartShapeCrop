# -*- coding: utf-8 -*-
"""
修正版测试：设计预览（render_design / compute_border_bands）在大圆角下的正确性。

核心验证点（不会错的）：
  1) 外轮廓圆角切除区（L 形 + 顶点）：只能出现白色（outer_bg_color），
     任何其他颜色（黑/红/深棕/米白…）都代表某层边框漏出了尖角 = 失败！
  2) 内部水池主体中心：应保留米黄色（没被切掉）。
  3) 各层边框的"带颜色区域"在角附近应该"向内收缩"，没有方形尖角。

人工直观：对比 NOcorner 与 WITHcorner 两张 JPG 的右下角，
  - NOcorner: 所有层都是直角的方形。
  - WITHcorner: 右下角是一个大圆弧，没有任何方形尖刺/凸角。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image, ImageDraw

from core.geometry import CropDesign, BorderLayer
from core.image_ops import render_design

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'test_cropper_output')
os.makedirs(OUT_DIR, exist_ok=True)

DPI = 150

def make_design(corner_br_cm=0.0):
    d = CropDesign()
    d.canvas_w_cm = 41.0
    d.canvas_h_cm = 55.0
    d.dpi = DPI
    d.mode = 'rect_hole'
    d.outer_margin_cm = 0.5
    d.inner_margin_top_cm = 8.0
    d.inner_margin_bottom_cm = 8.0
    d.inner_margin_left_cm = 8.0
    d.inner_margin_right_cm = 8.0
    d.borders = [
        BorderLayer(offset_cm=0.8,  fill_type='solid', color=(20, 20, 20)),      # L0 外黑
        BorderLayer(offset_cm=0.3,  fill_type='solid', color=(255, 255, 255)),   # L1 白间隔
        BorderLayer(offset_cm=0.3,  fill_type='solid', color=(220, 40, 40)),     # L2 红
        BorderLayer(offset_cm=0.2,  fill_type='solid', color=(255, 255, 255)),   # L3 白间隔
        BorderLayer(offset_cm=3.5,  fill_type='solid', color=(80, 60, 30)),      # L4 深棕(花纹)
        BorderLayer(offset_cm=0.15, fill_type='solid', color=(220, 190, 120)),   # L5 浅金
        BorderLayer(offset_cm=2.0,  fill_type='solid', color=(250, 245, 230)),   # L6 最内米白
    ]
    d.outer_bg_color = (255, 255, 255)
    d.hole_bg_color  = (250, 245, 220)
    d.corner_tl_cm = 0.0; d.corner_tr_cm = 0.0
    d.corner_bl_cm = 0.0; d.corner_br_cm = corner_br_cm
    return d

def cm2px(cm): return int(round(cm * DPI / 2.54))

# ---- 粗略颜色分类（用于诊断）----
def classify(p):
    r,g,b = int(p[0]), int(p[1]), int(p[2])
    s = r+g+b
    if s >= 750: return '白色(背景/白间隔)'
    if s <= 80:  return '黑色(外框)'
    if r>180 and g<100 and b<100: return '红色'
    if abs(r-250)<18 and abs(g-245)<18 and abs(b-230)<30: return '米白/米黄(内边)'
    if r>200 and g>170 and b<140: return '浅金'
    if 40<r<130 and 30<g<100 and 10<b<70: return '深棕(花纹)'
    return f'杂色({r},{g},{b})'

print("""
============================================================
测试：设计预览渲染 多层边框圆角（核心：外层L形切除区只能是白色）
============================================================""")

radius_cm = 8.5
design_no = make_design(0.0)
design_rd = make_design(radius_cm)

img_no = render_design(design_no)
img_rd = render_design(design_rd)
W, H = img_no.size

p_no = os.path.join(OUT_DIR, 'test_design_bands_NOcorner_v2.jpg')
p_rd = os.path.join(OUT_DIR, 'test_design_bands_WITHcorner_v2.jpg')
img_no.save(p_no, quality=95)
img_rd.save(p_rd, quality=95)

# 关键几何
outer_margin_px = cm2px(design_rd.outer_margin_cm)
ox1, oy1 = outer_margin_px, outer_margin_px
ox2, oy2 = W - outer_margin_px, H - outer_margin_px  # 外层边框的最外右下顶点（画布索引语义）

# 圆角：右下角的圆心 = (ox2 - r, oy2 - r)
r_px = max(0, int(round(radius_cm * DPI / 2.54)))
cx, cy = ox2 - r_px, oy2 - r_px

print(f"\n画布 {W}x{H} px  |  outer_rect ({ox1},{oy1})-({ox2},{oy2})  |  br r={r_px} px")
print(f"圆角圆心 = ({cx}, {cy})  |  最外右下顶点 = ({ox2-1}, {oy2-1}) [像素索引最后1个]")

# ============ 核心检查：外层 L 形切除区 ============
arr_no = np.array(img_no)
arr_rd = np.array(img_rd)

def is_white(p, thr=250):
    return (int(p[0]) >= thr and int(p[1]) >= thr and int(p[2]) >= thr)

print("\n==== 外层切除区（必须全部为白色背景）====")
checks = []
# 顶点
checks.append(('最外右下顶点',             ox2-1,              oy2-1))
checks.append(('outer_rect+1 画布边界',     W-1,                H-1))
# L 形（贴右边一列 & 贴底下一行），从顶点向内 5/10/20/30px 采样
for step in [5, 10, 20, 30, 45, 60, 90, 120]:
    # 贴右边：x = ox2-1 (最外层黑框的右边)，y = oy2 - step
    checks.append((f'贴右边列 y↩{step}px',   ox2-1,              oy2 - step))
    # 贴底边：x = ox2 - step，y = oy2-1
    checks.append((f'贴底边行 x↩{step}px',   ox2 - step,         oy2-1))
# 在 L 形正方形内、扇形外的典型点：dx=dy=0.8r （一定在扇形外，
# 因为 0.8²+0.8²=1.28 > 1；且在 r×r 正方形内，所以必定在"应切除=白"）
for k in [0.55, 0.65, 0.75, 0.82, 0.9, 0.98]:
    dx = int(r_px * k)
    dy = int(r_px * k)
    # 判断是否在扇形外：dx² + dy² > r²
    in_sector = (dx*dx + dy*dy) <= (r_px*r_px)
    tag = 'sector外(L形必白!)' if not in_sector else 'sector内(带色正常)'
    checks.append((f'方形内 dx={dx} dy={dy} [{tag}]', cx + dx, cy + dy))

critical_ok = True
for desc, x, y in checks:
    xc = min(max(x, 0), W-1); yc = min(max(y, 0), H-1)
    p_no = arr_no[yc, xc]
    p_rd = arr_rd[yc, xc]
    w_no = is_white(p_no); w_rd = is_white(p_rd)
    # 这个检查点的属性：以 k 标识在扇形内/外
    should_be_white = ('sector外' in desc) or ('顶点' in desc) or ('列' in desc) or ('行' in desc) or ('边界' in desc)
    if should_be_white:
        ok = w_rd
        mark = '✓' if ok else '✗✗(核心失败-有色边框露尖角!)'
        if not ok: critical_ok = False
    else:
        ok = not w_rd  # 扇形内部应该保留图色（非白=边框带颜色之一）
        mark = '·参考✓' if ok else '·参考·'
    print(f"  ({xc:>4},{yc:>4}) {desc:<42}  无圆角:{classify(p_no):<16}  有圆角:{classify(p_rd):<16} {mark}")

# ============ 中心及扇形内部 ============
print("\n==== 内部保留区（不能被误切） ====")
inner_checks = []
inner_checks.append(('水池主体中心', W//2, H//2, False))  # 不能是白
inner_checks.append(('扇形内部参考(dx=0.35r,dy=0.35r)', cx + int(0.35*r_px), cy + int(0.35*r_px), False))  # 扇形内部应保留（非白）
inner_checks.append(('扇形内部参考(dx=0.15r,dy=0.6r)',  cx + int(0.15*r_px), cy + int(0.60*r_px), False))
inner_checks.append(('扇形内部参考(dx=0.6r,dy=0.15r)',  cx + int(0.60*r_px), cy + int(0.15*r_px), False))
# inner border band 区域（扇形内深棕或浅金或米白都是非白，正确）
for desc, x, y, exp_white in inner_checks:
    xc = min(max(x,0),W-1); yc = min(max(y,0),H-1)
    p = arr_rd[yc,xc]
    w = is_white(p)
    ok = (w == exp_white)
    if not ok and not exp_white: critical_ok = False
    mark = '✓' if ok else '✗✗(核心失败!)' if not exp_white else '·(参考)'
    print(f"  ({xc:>4},{yc:>4}) {desc:<42}  有圆角:{classify(p):<16} 期望={'白' if exp_white else '非白(图色)'} {mark}")

print()
if critical_ok:
    print("  ➜ ✓✓ 核心通过：外层切除区（最外顶点+贴右列+贴底行+扇形外L形）全部为纯白背景，")
    print("           没有任何黑色/红色/深棕色/米白色从某层边框露出来。")
else:
    print("  ➜ ✗ 失败：外层切除区出现了非白色，说明某层边框的方形尖角没有被裁掉。")

print(f"""
============================================================
输出文件（肉眼对比重点）：
  无圆角对照: {p_no}
  有圆角({radius_cm}cm br): {p_rd}

✦ 肉眼快速判定法 ✦：
  打开两张 JPG，把右下角放大到 400% 对齐对比：
   1) 无圆角版：右下角是一层层直角的台阶（黑→白→红→白→深棕→浅金→米白），全是方的。
   2) 有圆角版：右下角是一条 连续的大圆弧，没有任何方形的角/尖刺/凸边凸出来。
      圆弧外面：只有纯白（背景）。
      圆弧里面：从外到内按顺序是黑/白/红/白/深棕/浅金/米白，各层都跟着圆弧走，
                没有哪一层在圆弧外多出来一块方形。
============================================================""")
