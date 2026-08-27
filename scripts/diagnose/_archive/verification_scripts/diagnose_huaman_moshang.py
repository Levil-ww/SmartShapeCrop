"""
诊断花幔和墨上花开的圆角问题
构造与这两个产品相同结构的测试图，分析边框层检测和重绘逻辑
"""
import numpy as np
from PIL import Image
import sys
sys.path.insert(0, "d:\\SmartShapeCrop")

from core.image_cropper import apply_border_only_corners

def diagnose_image_structure(name, w, h, layers):
    """构造测试图并分析结构"""
    print(f"\n===== 诊断 {name} =====")
    print(f"尺寸: {w}x{h}px")
    
    # 构造图像
    img_arr = np.zeros((h, w, 3), dtype=np.uint8)
    bg_color = (255, 255, 255)  # 白色背景
    
    # 最外层白色背景
    img_arr[:, :] = bg_color
    
    # 构建边框层
    cumulative = 0
    for color, thickness in layers:
        # 上边
        img_arr[cumulative:cumulative+thickness, cumulative:w-cumulative, :] = color
        # 下边
        img_arr[h-cumulative-thickness:h-cumulative, cumulative:w-cumulative, :] = color
        # 左边
        img_arr[cumulative:h-cumulative, cumulative:cumulative+thickness, :] = color
        # 右边
        img_arr[cumulative:h-cumulative, w-cumulative-thickness:w-cumulative, :] = color
        cumulative += thickness
    
    # 最后一层内部填充内容色
    inner_color = layers[-1][0] if len(layers) > 0 else (245, 235, 220)
    inner_thickness = cumulative
    img_arr[inner_thickness:h-inner_thickness, inner_thickness:w-inner_thickness, :] = inner_color
    
    test_img = Image.fromarray(img_arr, 'RGB')
    
    # 检测边框层 (DPI=150, 3.6cm ≈ 213px, 5cm ≈ 295px)
    dpi = 150
    corners_3_6cm = {'tl': 0, 'tr': 0, 'bl': 3.6, 'br': 3.6}
    corners_5cm = {'tl': 5.0, 'tr': 5.0, 'bl': 5.0, 'br': 5.0}
    
    for corner_set_name, corners in [("3.6cm", corners_3_6cm), ("5cm", corners_5cm)]:
        r_bl = int(round(corners['bl'] * dpi / 2.54))
        r_br = int(round(corners['br'] * dpi / 2.54))
        r_max = max(r_bl, r_br)
        
        if r_max == 0:
            continue
        
        print(f"\n--- 圆角设置: {corner_set_name} ---")
        print(f"  左下角半径: {r_bl}px, 右下角半径: {r_br}px")
        
        # 运行裁剪
        try:
            result = apply_border_only_corners(test_img, corners, dpi, bg_color)
            result_arr = np.array(result)
            rw, rh = result.size
            
            # 检查左下角 (BL)
            if corners['bl'] > 0:
                cx, cy = r_bl, rh - r_bl
                # 检查左下角圆角区域
                roi_y1 = max(0, cy - r_bl)
                roi_x1 = max(0, cx - r_bl)
                roi_y2 = min(rh, cy + r_bl + 1)
                roi_x2 = min(rw, cx + r_bl + 1)
                
                if roi_y2 > roi_y1 and roi_x2 > roi_x1:
                    roi_colors = result_arr[roi_y1:roi_y2, roi_x1:roi_x2, :].astype(np.float64)
                    
                    # 统计颜色分布
                    unique_colors, counts = np.unique(roi_colors.reshape(-1, 3), axis=0, return_counts=True)
                    print(f"  BL角 颜色分布 (top 5):")
                    sorted_idx = np.argsort(-counts)[:5]
                    for idx in sorted_idx:
                        color = tuple(unique_colors[idx].astype(int))
                        count = counts[idx]
                        print(f"    {color}: {count}px")
            
            # 检查右下角 (BR)
            if corners['br'] > 0:
                cx, cy = rw - r_br, rh - r_br
                roi_y1 = max(0, cy - r_br)
                roi_x1 = max(0, cx - r_br)
                roi_y2 = min(rh, cy + r_br + 1)
                roi_x2 = min(rw, cx + r_br + 1)
                
                if roi_y2 > roi_y1 and roi_x2 > roi_x1:
                    roi_colors = result_arr[roi_y1:roi_y2, roi_x1:roi_x2, :].astype(np.float64)
                    
                    unique_colors, counts = np.unique(roi_colors.reshape(-1, 3), axis=0, return_counts=True)
                    print(f"  BR角 颜色分布 (top 5):")
                    sorted_idx = np.argsort(-counts)[:5]
                    for idx in sorted_idx:
                        color = tuple(unique_colors[idx].astype(int))
                        count = counts[idx]
                        print(f"    {color}: {count}px")
            
            print(f"  ✓ 裁剪成功")
            result.save(f"d:\\SmartShapeCrop\\scripts\\diagnose\\test_{name}_{corner_set_name}.png")
            
        except Exception as e:
            print(f"  ✗ 错误: {e}")
            import traceback
            traceback.print_exc()

# 花幔结构: 黑边框(4px) + 浅色间隙(8px) + 深内容色
print("=" * 60)
print("花幔结构: 黑边框 + 浅色间隙 + 深色内容")
print("=" * 60)
huaman_layers = [
    ((30, 25, 20), 4),      # 外层黑/深棕边框 4px
    ((230, 225, 210), 8),    # 浅米色间隙 8px
    ((210, 195, 170), 20),   # 内层内容 20px
]
diagnose_image_structure("huaman", 800, 1600, huaman_layers)

# 墨上花开结构: 黑边框(5px) + 米色间隙(10px) + 花卉内容
print("\n" + "=" * 60)
print("墨上花开结构: 黑边框 + 米色间隙 + 花卉内容")
print("=" * 60)
moshang_layers = [
    ((25, 20, 15), 5),      # 外层黑边框 5px
    ((240, 230, 215), 10),  # 米色间隙 10px
    ((220, 200, 180), 30),  # 内层内容 30px
]
diagnose_image_structure("moshang", 790, 1590, moshang_layers)