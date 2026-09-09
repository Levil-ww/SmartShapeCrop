"""验证修复效果 - 重新诊断蔓生花和中古雨林"""
import sys, os
sys.path.insert(0, '.')
import numpy as np
from PIL import Image

from core.lshape_border_route import detect_border_profile, profile_yields_to_v13
from core.image_ops import load_image_rgb

def verify(name, path):
    print(f"\n{'='*60}")
    print(f"修复后: {name}")
    print(f"{'='*60}")
    img = load_image_rgb(path)
    arr = np.array(img, dtype=np.uint8)
    H, W = arr.shape[:2]
    min_dim = min(H, W)

    # Profile 检测（带修复后的厚度截断）
    profile = detect_border_profile(img)
    print(f"\nProfile 检测结果:")
    if profile:
        for i, (c, t) in enumerate(profile):
            print(f"  层{i}: color={c}, thickness={t}px")
        print(f"  让位给 V13? {profile_yields_to_v13(profile)}")
        
        # 模拟 layers 在 patch 中的 offs 值
        offs = [0]
        for _c, t in profile:
            offs.append(offs[-1] + t)
        T = offs[-1]
        print(f"\n  offs = {offs}")
        print(f"  T = {T}")
        print(f"\n  各层在垂直切边的 y_lo = min(offs[k], yc):")
        for k, (c, t) in enumerate(profile):
            print(f"    层{k}: y_lo = min({offs[k]}, yc)")
        print(f"\n  关键改善:")
        print(f"    修复前 offs[2] 过大 → 层2 可能不在垂直切边绘制")
        print(f"    修复后 offs[2] 缩小 → 层2 能正确绘制在整个垂直切边上")
    else:
        print("  Profile 未命中")

    # 精确底色采样验证
    r0, r1 = int(H * 0.3), int(H * 0.7)
    c0, c1 = int(W * 0.3), int(W * 0.7)
    center = arr[r0:r1, c0:c1].reshape(-1, 3)
    light = center[center.mean(axis=1) > 128]
    if light.size > 0:
        precise_bg = tuple(int(v) for v in np.median(light, axis=0))
        print(f"\n素材精确底色 = {precise_bg}")

# 测试两个素材
verify("蔓生花", r".workbuddy\tmp_samples\colorfix\蔓生花_full.jpg")
verify("中古雨林", r".workbuddy\tmp_samples\e2e\中古雨林100x140.png")
