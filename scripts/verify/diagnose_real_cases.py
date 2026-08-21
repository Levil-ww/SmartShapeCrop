"""
深度诊断：真实案例 vs 合成测试的差异
150 DPI 下模拟安妮森林、中古花园、素锦、墨上花开的真实边框结构
检测 border_layers 识别精度、间隙层判定、force_clear 效果
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image
from core.image_cropper import (
    apply_border_only_corners, _get_border_layers_robust,
    _build_multi_layer_corner_mask,
)
from core.corner.sector_render import _redraw_border_on_corner
from core.corner.algorithm import CORNER_ANGLES
from core.corner.detection import classify_gap_layers


def analyze_case(name, w_cm, h_cm, r_cm, dpi, build_img_fn,
                 expected_border_layers_count=None,
                 expected_outer_color=None):
    """深度分析一个案例"""
    print("\n" + "=" * 75)
    print(f"案例: {name}  ({w_cm}x{h_cm}cm, r={r_cm}cm, {dpi}dpi)")
    print("=" * 75)
    
    w_px = int(w_cm * dpi / 2.54)
    h_px = int(h_cm * dpi / 2.54)
    r_px = int(r_cm * dpi / 2.54)
    
    print(f"  像素尺寸: {w_px} x {h_px}")
    print(f"  圆角半径: {r_px} px")
    
    bg_color = (255, 255, 255)
    img = build_img_fn(w_px, h_px)
    arr = np.array(img)
    
    # Step 1: 检测边框层
    border_layers = _get_border_layers_robust(img, bg_color)
    print(f"\n  [检测] border_layers ({len(border_layers)}层):")
    for i, (c, t) in enumerate(border_layers):
        tag = "OUT" if i == 0 else "IN" if i == len(border_layers)-1 else "MID"
        print(f"    Layer {i} [{tag}]: color={c}, thickness={t}px, maxRGB={max(c)}")
    
    # [Fix v7] 统一使用 classify_gap_layers 判定间隙层
    gap_layers_idx = []
    solid_layers_idx = []
    _is_gap = classify_gap_layers(border_layers, bg_color=bg_color)
    for i, (c, t) in enumerate(border_layers):
        if i < len(_is_gap) and _is_gap[i]:
            gap_layers_idx.append(i)
            mark = "GAP"
            reason = "classify_gap_layers判定为间隙"
        else:
            solid_layers_idx.append(i)
            mark = "SOLID"
            reason = "classify_gap_layers判定为实心"
        print(f"    -> [{mark}] {reason}")
    
    print(f"\n  间隙层: {gap_layers_idx}")
    print(f"  实心边框层: {solid_layers_idx}")
    
    # Step 2: 执行圆角裁剪
    corners = {k: r_cm for k in ['tl', 'tr', 'bl', 'br']}
    
    try:
        result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    except Exception as e:
        import traceback
        print(f"  [ERROR] apply_border_only_corners 失败: {e}")
        traceback.print_exc()
        return False
    
    result_arr = np.array(result)
    
    # Step 3: 角落分析
    all_ok = True
    print("\n  [角落验证] 四角不变量检测:")
    
    for ck in ['tl', 'tr', 'bl', 'br']:
        ang_min, ang_max = CORNER_ANGLES[ck]
        if ck == 'tl':
            cx, cy = r_px, r_px
        elif ck == 'tr':
            cx, cy = w_px - r_px, r_px
        elif ck == 'bl':
            cx, cy = r_px, h_px - r_px
        else:
            cx, cy = w_px - r_px, h_px - r_px
        
        yy, xx = np.mgrid[0:h_px, 0:w_px]
        dx = xx.astype(np.float64) - cx
        dy = yy.astype(np.float64) - cy
        dist = np.sqrt(dx**2 + dy**2)
        angle = np.degrees(np.arctan2(dy, dx))
        angle = np.mod(angle, 360.0)
        
        valid_angle = (angle >= ang_min) & (angle <= ang_max)
        
        # 1. 弧边界深色像素比例（S-2: 弧边界边框完整）
        # [Fix v7] 使用 <= 150 (与 SENTINEL_OUTER_DARK_MAX_RGB 一致)，
        #   素锦边框色 (120,105,85) maxRGB=120，原 < 120 阈值漏检
        DARK_THRESH = 150
        arc_band = valid_angle & (dist >= float(r_px) - 2.0) & (dist <= float(r_px) + 1.0)
        arc_pixels = result_arr[arc_band] if np.any(arc_band) else np.zeros((0, 3))
        dark_ratio = np.mean(np.max(arc_pixels, axis=1) <= DARK_THRESH) if len(arc_pixels) > 0 else 0
        total_arc = len(arc_pixels)
        
        # 2. 边框完整度采样（10个点）
        samples = np.linspace(ang_min, ang_max, 10, dtype=np.float64)
        complete = True
        miss_points = []
        for sp_i, sp in enumerate(samples):
            sp_wrapped = np.mod(sp, 360.0)
            angle_diff = np.minimum(np.abs(angle - sp_wrapped), 360 - np.abs(angle - sp_wrapped))
            near = angle_diff <= 2.5
            near_arc = near & valid_angle & (dist >= float(r_px) - 2.0) & (dist <= float(r_px) + 1.5)
            if np.any(near_arc):
                has_dark = np.any(np.max(result_arr[near_arc], axis=1) <= DARK_THRESH)
                if not has_dark:
                    complete = False
                    miss_points.append(f"pt{sp_i}({sp:.0f}deg)")
        
        # 3. 弧外侧背景色比例（L-2）
        outside_zone = valid_angle & (dist > float(r_px) + 2.0)
        if np.any(outside_zone):
            outside_pixels = result_arr[outside_zone]
            d2bg = np.sqrt(np.sum((outside_pixels.astype(np.float64) - np.array(bg_color, dtype=np.float64)) ** 2, axis=1))
            outside_bg_ratio = np.mean(d2bg <= 5.0)
        else:
            outside_bg_ratio = 1.0
        
        # 4. 边框厚度比较（L-3）：直边 vs 圆角弧
        # [Fix v7] 修复厚度比计算：分子分母统一使用 per-pixel 密度
        if ck in ['bl', 'br']:
            sy = h_px - 5
            straight_region = result_arr[sy-3:sy, w_px//4:3*w_px//4, :]
        else:
            sy = 2
            straight_region = result_arr[sy:sy+3, w_px//4:3*w_px//4, :]
        straight_total_pixels = straight_region.shape[0] * straight_region.shape[1]
        straight_dark_count = np.sum(np.max(straight_region, axis=2) <= DARK_THRESH)
        straight_density = straight_dark_count / max(1, straight_total_pixels)
        
        arc_region = valid_angle & (dist >= float(r_px) - 5.0) & (dist <= float(r_px) + 0.5)
        if np.any(arc_region):
            arc_dark_count = np.sum(np.max(result_arr[arc_region], axis=1) <= DARK_THRESH)
            arc_pixel_total = np.sum(arc_region)
            arc_density = arc_dark_count / max(1, arc_pixel_total)
            thickness_ratio = arc_density / max(0.001, straight_density)
        else:
            arc_dark_count = 0
            thickness_ratio = 0.0
        
        s2_fail = dark_ratio < 0.3
        s3_fail = thickness_ratio > 1.2 or thickness_ratio < 0.6
        l2_fail = outside_bg_ratio < 0.98
        border_miss_fail = not complete
        
        corner_ok = not (s2_fail or l2_fail or border_miss_fail)
        if not corner_ok:
            all_ok = False
        
        status = "✓" if corner_ok else "✗"
        fails = []
        if s2_fail: fails.append(f"S-2黑边缺失({dark_ratio:.0%})")
        if border_miss_fail: fails.append(f"缺采样点:{','.join(miss_points)}")
        if l2_fail: fails.append(f"L-2外侧脏({outside_bg_ratio:.0%})")
        if s3_fail: fails.append(f"S-3厚度比{thickness_ratio:.2f}")
        
        print(f"    {ck}角 {status}: 边界深色{dark_ratio:.0%}({total_arc}px) "
              f"厚度比{thickness_ratio:.2f} 外侧BG{outside_bg_ratio:.0%} "
              f"采样完整:{complete} "
              f"{' → 违规: ' + '; '.join(fails) if fails else ''}")
    
    if all_ok:
        print(f"\n  → 案例 {name}: ✓ PASS")
    else:
        print(f"\n  → 案例 {name}: ✗ FAIL")
    
    return all_ok


def build_annie_forest(w, h):
    """安妮森林: 深棕外边框(12) → 白色间隙(5) → 米色间隙(15) → 浅棕文字边框(20) → 米色内容
    真实150dpi结构，多层间隙+装饰文字带"""
    bg = (255, 255, 255)
    img = Image.new('RGB', (w, h), bg)
    arr = np.array(img)
    
    # L0: 深棕色主边框 (外层)
    c_outer = (60, 45, 35)
    t_outer = 12
    arr[:t_outer, :, :] = c_outer
    arr[-t_outer:, :, :] = c_outer
    arr[:, :t_outer, :] = c_outer
    arr[:, -t_outer:, :] = c_outer
    
    # L1: 白色间隙
    c_gap1 = (248, 248, 248)
    t_gap1 = 5
    arr[t_outer:t_outer+t_gap1, :, :] = c_gap1
    arr[-(t_outer+t_gap1):-t_outer, :, :] = c_gap1
    arr[:, t_outer:t_outer+t_gap1, :] = c_gap1
    arr[:, -(t_outer+t_gap1):-t_outer] = c_gap1
    
    # L2: 米色间隙
    c_gap2 = (230, 220, 200)
    t_gap2 = 15
    s2 = t_outer + t_gap1
    arr[s2:s2+t_gap2, :, :] = c_gap2
    arr[-(s2+t_gap2):-s2, :, :] = c_gap2
    arr[:, s2:s2+t_gap2, :] = c_gap2
    arr[:, -(s2+t_gap2):-s2] = c_gap2
    
    # L3: 文字装饰带 (浅棕色) - 设计元素，近似实心边框
    c_text_border = (180, 170, 140)
    t_text = 20
    s3 = s2 + t_gap2
    arr[s3:s3+t_text, :, :] = c_text_border
    arr[-(s3+t_text):-s3, :, :] = c_text_border
    arr[:, s3:s3+t_text, :] = c_text_border
    arr[:, -(s3+t_text):-s3] = c_text_border
    
    # 内容: 绿色花纹背景
    content_color = (140, 160, 110)
    cs = s3 + t_text
    arr[cs:-cs, cs:-cs, :] = content_color
    
    # 添加花斑纹点点缀
    np.random.seed(1)
    for _ in range(2000):
        y = np.random.randint(cs, h - cs)
        x = np.random.randint(cs, w - cs)
        arr[y, x, :] = (70, 90, 60)
    
    return Image.fromarray(arr)


def build_zhongguohuayuan(w, h):
    """中古花园: 黑色外边框(10) → 白色间隙(5) → 米色间隙(10) → 黑色内边框(8)
    150dpi 细多层边框"""
    bg = (255, 255, 255)
    img = Image.new('RGB', (w, h), bg)
    arr = np.array(img)
    
    # L0: 黑色外边框
    c_outer = (20, 18, 16)
    t_outer = 10
    arr[:t_outer, :, :] = c_outer
    arr[-t_outer:, :, :] = c_outer
    arr[:, :t_outer, :] = c_outer
    arr[:, -t_outer:, :] = c_outer
    
    # L1: 白色间隙
    c_gap1 = (246, 246, 246)
    t_gap1 = 5
    s1 = t_outer
    arr[s1:s1+t_gap1, :, :] = c_gap1
    arr[-(s1+t_gap1):-s1, :, :] = c_gap1
    arr[:, s1:s1+t_gap1, :] = c_gap1
    arr[:, -(s1+t_gap1):-s1] = c_gap1
    
    # L2: 米色间隙
    c_gap2 = (215, 205, 185)
    t_gap2 = 10
    s2 = s1 + t_gap1
    arr[s2:s2+t_gap2, :, :] = c_gap2
    arr[-(s2+t_gap2):-s2, :, :] = c_gap2
    arr[:, s2:s2+t_gap2, :] = c_gap2
    arr[:, -(s2+t_gap2):-s2] = c_gap2
    
    # L3: 黑色内边框
    c_inner = (22, 20, 18)
    t_inner = 8
    s3 = s2 + t_gap2
    arr[s3:s3+t_inner, :, :] = c_inner
    arr[-(s3+t_inner):-s3, :, :] = c_inner
    arr[:, s3:s3+t_inner, :] = c_inner
    arr[:, -(s3+t_inner):-s3] = c_inner
    
    # 内容: 白色内容区 + 花纹
    cs = s3 + t_inner
    arr[cs:-cs, cs:-cs, :] = (252, 252, 252)
    np.random.seed(2)
    for _ in range(5000):
        y = np.random.randint(cs, h - cs)
        x = np.random.randint(cs, w - cs)
        arr[y, x, :] = (40, 38, 36)
    
    return Image.fromarray(arr)


def build_sujin(w, h):
    """素锦: 棕色外边框(10) → 米色间隙(12) → 棕色内边框(10) → 米色内容
    案例要求: 只留黑色(棕色)弧形边框线，间隙必须100%清除，不得有白色缺口"""
    bg = (255, 255, 255)
    img = Image.new('RGB', (w, h), bg)
    arr = np.array(img)
    
    # L0: 棕色外边框 (深色 maxRGB<150)
    c_outer = (120, 105, 85)
    t_outer = 10
    arr[:t_outer, :, :] = c_outer
    arr[-t_outer:, :, :] = c_outer
    arr[:, :t_outer, :] = c_outer
    arr[:, -t_outer:, :] = c_outer
    
    # L1: 米色间隙
    c_gap = (230, 218, 198)
    t_gap = 12
    s1 = t_outer
    arr[s1:s1+t_gap, :, :] = c_gap
    arr[-(s1+t_gap):-s1, :, :] = c_gap
    arr[:, s1:s1+t_gap, :] = c_gap
    arr[:, -(s1+t_gap):-s1] = c_gap
    
    # L2: 棕色内边框
    c_inner = (125, 110, 90)
    t_inner = 10
    s2 = s1 + t_gap
    arr[s2:s2+t_inner, :, :] = c_inner
    arr[-(s2+t_inner):-s2, :, :] = c_inner
    arr[:, s2:s2+t_inner, :] = c_inner
    arr[:, -(s2+t_inner):-s2] = c_inner
    
    # 内容: 米色花纹
    cs = s2 + t_inner
    arr[cs:-cs, cs:-cs, :] = (235, 225, 205)
    np.random.seed(3)
    for _ in range(3000):
        y = np.random.randint(cs, h - cs)
        x = np.random.randint(cs, w - cs)
        arr[y, x, :] = (200, 185, 160)
    
    return Image.fromarray(arr)


def build_moshanghuakai(w, h):
    """墨上花开: 黑色双边框(外10+间隙+内8) → 大圆角r=10cm
    问题: 边框线过粗、白色弧形缺口"""
    bg = (255, 255, 255)
    img = Image.new('RGB', (w, h), bg)
    arr = np.array(img)
    
    # L0: 黑色粗外边框
    c_outer = (15, 15, 15)
    t_outer = 10
    arr[:t_outer, :, :] = c_outer
    arr[-t_outer:, :, :] = c_outer
    arr[:, :t_outer, :] = c_outer
    arr[:, -t_outer:, :] = c_outer
    
    # L1: 米色间隙
    c_gap = (235, 228, 215)
    t_gap = 18
    s1 = t_outer
    arr[s1:s1+t_gap, :, :] = c_gap
    arr[-(s1+t_gap):-s1, :, :] = c_gap
    arr[:, s1:s1+t_gap, :] = c_gap
    arr[:, -(s1+t_gap):-s1] = c_gap
    
    # L2: 黑色细内边框
    c_inner = (18, 18, 18)
    t_inner = 8
    s2 = s1 + t_gap
    arr[s2:s2+t_inner, :, :] = c_inner
    arr[-(s2+t_inner):-s2, :, :] = c_inner
    arr[:, s2:s2+t_inner, :] = c_inner
    arr[:, -(s2+t_inner):-s2] = c_inner
    
    # 内容: 浅色+花纹
    cs = s2 + t_inner
    arr[cs:-cs, cs:-cs, :] = (248, 245, 240)
    np.random.seed(4)
    for _ in range(4000):
        y = np.random.randint(cs, h - cs)
        x = np.random.randint(cs, w - cs)
        arr[y, x, :] = (50, 45, 42)
    
    return Image.fromarray(arr)


def main():
    dpi = 150  # 真实案例 DPI
    
    cases = [
        ("安妮森林", 70, 120, 3.5, dpi, build_annie_forest),
        ("中古花园", 65, 120, 2.0, dpi, build_zhongguohuayuan),
        ("素锦",     90, 160, 4.5, dpi, build_sujin),
        ("墨上花开", 100, 200, 10.0, dpi, build_moshanghuakai),
    ]
    
    results = {}
    for args in cases:
        try:
            results[args[0]] = analyze_case(*args)
        except Exception as e:
            import traceback
            print(f"\n[ERROR] {args[0]} 异常: {e}")
            traceback.print_exc()
            results[args[0]] = False
    
    print("\n" + "=" * 75)
    print("诊断汇总 (150dpi 真实结构)")
    print("=" * 75)
    for name, ok in results.items():
        s = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {name}: {s}")
    all_pass = all(results.values())
    print(f"\n总体: {'✓ 全部通过' if all_pass else '✗ 存在失败'}")
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
