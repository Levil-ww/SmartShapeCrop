# -*- coding: utf-8 -*-
"""debug Layer 6 尖角为什么没被裁掉"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image, ImageDraw
from core.image_cropper import detect_nested_rect_layers, _layer_rounded_mask_arr

DPI = 150
W, H = 1200, 900
r_cm = 8.5
r_px = max(0, int(round(r_cm * DPI / 2.54)))
print(f"r_px={r_px}")

# ========== 构造合成图（与测试脚本一致） ==========
img = Image.new('RGB', (W, H), (255, 255, 255))
d = ImageDraw.Draw(img)
import random
random.seed(0)
d.rectangle([30, 30, W-30, H-30], outline=(0,0,0), width=24)
d.rectangle([56, 56, W-53, H-53], outline=(220,30,30), width=6)
d.rectangle([64, 64, W-60, H-60], outline=(200,30,30), width=3)
for _ in range(300):
    x, y = random.randint(70, W-70), random.randint(70, H-70)
    if not (220 <= x <= W-220 and 220 <= y <= H-220):
        s = random.randint(4,18)
        d.ellipse([x,y,x+s,y+s], fill=tuple(random.choice([(245,200,80),(200,140,50),(180,100,40),(230,210,160),(210,180,120)])))
d.rectangle([236,236,W-232,H-232], outline=(120,110,90), width=2)
d.rectangle([238,238,W-234,H-234], fill=(250,245,230))
d.rectangle([524,248,W-524,H-242], outline=(30,30,30), width=3)
d.rectangle([526,250,W-526,H-244], fill=(30,30,30))
d.rectangle([W-526-1, H-244-40, W-526-1+20, H-244-1], fill=(220,30,30))
d.rectangle([W-526-1-40, H-244-1, W-526-1, H-244-1+20], fill=(220,30,30))

# ========== 检测 + 过滤 ==========
layers = detect_nested_rect_layers(img)
filtered = []
for (x1,y1,x2,y2) in layers:
    bw, bh = x2-x1, y2-y1
    if bw<=40 or bh<=40: continue
    ratio = bw/max(1, bh)
    if ratio>12 or ratio<1/12: continue
    filtered.append((x1,y1,x2,y2))
layers = filtered

# 检查点：
check_x, check_y = 676, 601
print(f"\n关键检查点 P=({check_x},{check_y}): 应在 Layer6 挖空正方形内且 sector 外 → mask=0")

# 打印所有层
unified = np.ones((H, W), dtype=np.uint8) * 255
# full_rect
full_rc = (0, 0, W, H)
fm = _layer_rounded_mask_arr(W, H, full_rc, {'br': r_px})
v = int(fm[check_y, check_x])
print(f"  整图 full_rect {full_rc}: mask[{check_y},{check_x}] = {v}   (min前 unified={int(unified[check_y,check_x])} → min后={min(v, int(unified[check_y,check_x]))})")
unified = np.minimum(unified, fm)

for i, (x1,y1,x2_idx,y2_idx) in enumerate(layers):
    rc = (x1, y1, x2_idx+1, y2_idx+1)
    # 计算 safe_r
    rw, rh = rc[2]-rc[0], rc[3]-rc[1]
    safe_r = max(0, min(r_px, max(1, min(rw, rh)//2)))
    lm = _layer_rounded_mask_arr(W, H, rc, {'br': r_px})
    v = int(lm[check_y, check_x])
    u_before = int(unified[check_y, check_x])
    unified = np.minimum(unified, lm)
    u_after = int(unified[check_y, check_x])
    # 计算该点在该层的位置
    in_sq = False
    in_sector = False
    if safe_r > 0:
        sx1, sy1, sx2, sy2 = rc[2]-safe_r, rc[3]-safe_r, rc[2], rc[3]
        if sx1 <= check_x < sx2 and sy1 <= check_y < sy2:
            in_sq = True
            cx, cy = rc[2]-safe_r, rc[3]-safe_r
            dx, dy = check_x - cx, check_y - cy
            dist2 = dx*dx + dy*dy
            if dist2 <= safe_r*safe_r and dx >= 0 and dy >= 0:
                in_sector = True
    print(f"  Layer{i} rect_canvas={rc}  safe_r={safe_r:<4}  in_sq?{str(in_sq):<5} in_sector?{str(in_sector):<5}  mask[P]={v:<3}  unified {u_before} → {u_after}")

print(f"\n最终 unified mask 在 P 点: {int(unified[check_y,check_x])}")
print(f"   (0=被裁→白, 255=保留→原图色 (30,30,30) 黑色)")
if int(unified[check_y,check_x]) != 0:
    print("   ✗ 未被正确裁掉！需要排查原因。")
else:
    print("   ✓ 已被正确裁掉（应显示为白色背景）")
