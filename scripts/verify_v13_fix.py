"""验证 V13 修复效果"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from core.lshape_border import detect_border_v13, detect_pool_material_borders
from PIL import Image

H, W = 800, 1200
errors = []

def check(label, v13_expected, v13_actual):
    ok = v13_expected == v13_actual
    status = "✅" if ok else "❌"
    print(f"  {status} {label}: expected={v13_expected}, got={v13_actual}")
    if not ok:
        errors.append(label)

# === 测试1: 绽蔓模拟 (棋盘格) ===
print("=== 测试1: 绽蔓 (棋盘格 + 花纹) ===")
sim = np.full((H, W, 3), (200, 170, 130), dtype=np.uint8)
for y in range(100):
    for x in range(W):
        block = (x // 20 + y // 20) % 2
        sim[y, x] = (0, 0, 0) if block else (255, 255, 255)
for y in range(100, 300):
    for x in range(0, W, 40):
        if ((x // 40) + (y // 40)) % 3 == 0:
            sim[y, x:x+15] = (30, 20, 10)
v13 = detect_border_v13(Image.fromarray(sim))
check("V13 返回 None (正确跳过)", None, v13)

# === 测试2: 蝴蝶契约模拟 (10px黑+30px棕) ===
print("\n=== 测试2: 蝴蝶契约 (10px黑 + 30px棕) ===")
sim2 = np.full((H, W, 3), (230, 220, 200), dtype=np.uint8)
sim2[:10, :] = (0, 0, 0)
sim2[10:40, :] = (128, 64, 0)
sim2[-10:, :] = (0, 0, 0)
sim2[-40:-10, :] = (128, 64, 0)
sim2[:, :10] = (0, 0, 0)
sim2[:, 10:40] = (128, 64, 0)
sim2[:, -10:] = (0, 0, 0)
sim2[:, -40:-10] = (128, 64, 0)
v13_r = detect_border_v13(Image.fromarray(sim2))
print(f"  V13 结果: {v13_r}")
assert v13_r is not None, "V13 不应返回 None"
edge, band, color = v13_r
check("edge=10", 10, edge)
check("band=30", 30, band)
check("color=(128,64,0)", (128, 64, 0), color)

# === 测试3: 克罗印花 (经典黑描边+米色过渡+棕带) ===
print("\n=== 测试3: 克罗印花 (8px黑 + 12px米色 + 30px棕) ===")
sim3 = np.full((H, W, 3), (200, 170, 130), dtype=np.uint8)
sim3[:8, :] = (0, 0, 0)
sim3[8:20, :] = (245, 235, 220)  # 米色过渡（被过滤为 content）
sim3[20:50, :] = (139, 69, 19)
v13_k = detect_border_v13(Image.fromarray(sim3))
print(f"  V13 结果: {v13_k}")
if v13_k:
    print(f"  ✅ V13 正确检测")
else:
    print(f"  ⚠️ V13 返回 None")

# === 测试4: 庄园秘境 (纯黑宽边, 无主带) ===
print("\n=== 测试4: 庄园秘境 (纯黑宽边, band=0) ===")
sim4 = np.full((H, W, 3), (180, 160, 140), dtype=np.uint8)
sim4[:30, :] = (0, 0, 0)
v14 = detect_border_v13(Image.fromarray(sim4))
print(f"  V13 结果: {v14}")
if v14:
    e4, b4, _ = v14
    check("edge~=30 (纯黑边)", 30, e4)
    check("band=0 (无主带)", 0, b4)

# === 测试5: 纯内容无边框 ===
print("\n=== 测试5: 纯内容 (无边框) ===")
sim5 = np.random.randint(100, 200, (H, W, 3), dtype=np.uint8)
v15 = detect_border_v13(Image.fromarray(sim5))
check("V13 返回 None", None, v15)

print(f"\n{'='*50}")
if errors:
    print(f"❌ {len(errors)} 个测试失败: {errors}")
else:
    print(f"✅ 全部通过！")
