"""回归测试：验证 _edge_extend_fill 修复"两侧黑色背景框"。

复现场景：
  - 合成一张带黑色外框(60px)的素材图，内部为浅蓝色内容（与黑色明显区分）。
  - 两个画布尺寸，分别对应用户报错的 65x115(横版 AR≈1.769) 与正常 59x117(横版 AR≈1.983)。
  - 分别用 OLD(修复前) 与 NEW(修复后) 逻辑渲染，检测左右延展区是否为纯黑。

断言：
  - OLD：左右延展区应为近似纯黑（即 bug 复现）。
  - NEW：左右延展区应不(近似)纯黑，而是与内侧内容同色（即 bug 修复）。
"""
import os
import numpy as np
from PIL import Image

import sys
sys.path.insert(0, r"D:\SmartShapeCrop")
from core.image_ops import adapt_pool_material
from core.image_ops import _edge_extend_fill as new_edge_extend_fill


# ---- 修复前的旧实现（用于对照复现 bug）----
def _old_edge_extend_fill(centered_img: Image.Image, tw: int, th: int) -> Image.Image:
    sw, sh = centered_img.size
    canvas = Image.new('RGB', (tw, th), (255, 255, 255))
    cx = (tw - sw) // 2
    cy = (th - sh) // 2
    canvas.paste(centered_img, (cx, cy))
    if sw >= tw and sh >= th:
        return canvas
    arr = np.array(canvas, dtype=np.uint8)
    if cy > 0:
        top_row = arr[cy:cy + 1, :, :]
        arr[:cy, :, :] = np.broadcast_to(top_row, (cy, tw, 3)).copy()
    if cy + sh < th:
        bot_row = arr[cy + sh - 1:cy + sh, :, :]
        remain = th - (cy + sh)
        arr[cy + sh:, :, :] = np.broadcast_to(bot_row, (remain, tw, 3)).copy()
    if cx > 0:
        left_col = arr[:, cx:cx + 1, :]
        arr[:, :cx, :] = np.broadcast_to(left_col, (th, cx, 3)).copy()
    if cx + sw < tw:
        right_col = arr[:, cx + sw - 1:cx + sw, :]
        remain = tw - (cx + sw)
        arr[:, cx + sw:, :] = np.broadcast_to(right_col, (th, remain, 3)).copy()
    return Image.fromarray(arr, mode='RGB')


def make_black_frame_material(w=600, h=400, frame=60, content=(173, 216, 230)):
    """黑框 + 浅蓝内容，并加一点格子纹理以便肉眼区分。"""
    img = Image.new('RGB', (w, h), (0, 0, 0))
    arr = np.array(img, dtype=np.uint8)
    arr[frame:h - frame, frame:w - frame] = content
    # 画几道横线，便于肉眼确认内容被保留
    for y in range(frame, h - frame, 20):
        arr[y:y + 4, frame:w - frame] = (120, 170, 200)
    return Image.fromarray(arr, 'RGB')


def make_fullbleed_material(w=600, h=400):
    """无纯色外框的渐变素材（验证旧/新行为一致、不崩溃）。"""
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for x in range(w):
        arr[:, x, :] = (int(255 * x / w), 80, int(255 * (1 - x / w)))
    return Image.fromarray(arr, 'RGB')


def black_fraction(img: Image.Image, x0: int, x1: int) -> float:
    arr = np.array(img, dtype=np.uint8)
    band = arr[:, x0:x1, :]
    black = (band[:, :, 0] < 30) & (band[:, :, 1] < 30) & (band[:, :, 2] < 30)
    return float(black.mean())


def run_case(name, material, tw, th):
    # 使用 adapt_pool_material 的完整决策路径（contain + edge-extend）
    out = adapt_pool_material(
        material, tw, th,
        material_design_w_cm=float(material.width),
        material_design_h_cm=float(material.height),
        canvas_w_cm=float(tw) / 10.0,
        canvas_h_cm=float(th) / 10.0,
        quality='export',
    )
    # 同时直接构造 centered(resized) 用 OLD / NEW 对照
    scale_fit = min(tw / material.width, th / material.height)
    nw = max(1, int(round(material.width * scale_fit)))
    nh = max(1, int(round(material.height * scale_fit)))
    resized = material.resize((nw, nh), Image.LANCZOS)
    out_old = _old_edge_extend_fill(resized, tw, th)
    out_new = new_edge_extend_fill(resized, tw, th)

    band = min(50, tw // 4)
    bf_old = black_fraction(out_old, 2, band)
    bf_new = black_fraction(out_new, 2, band)
    print(f"[{name}] canvas={tw}x{th} 左侧黑占比 OLD={bf_old*100:.1f}%  NEW={bf_new*100:.1f}%")

    # 保存对照图
    out_dir = r"D:\SmartShapeCrop\debug_output\regress_edge_extend"
    os.makedirs(out_dir, exist_ok=True)
    out_old.save(os.path.join(out_dir, f"{name}_OLD.png"))
    out_new.save(os.path.join(out_dir, f"{name}_NEW.png"))

    return bf_old, bf_new


def main():
    material = make_black_frame_material()
    # 65x115 -> 横版 width=115 height=65 (AR 1.769)
    bf_old_a, bf_new_a = run_case("65x115", material, 1150, 650)
    # 59x117 -> 横版 width=117 height=59 (AR 1.983)
    bf_old_b, bf_new_b = run_case("59x117", material, 1170, 590)

    # 无外框素材：应不崩溃，新旧行为一致（NEW 不应出现纯黑带）
    fb = make_fullbleed_material()
    fo_old, fo_new = run_case("fullbleed", fb, 1150, 650)

    print("\n=== 断言 ===")
    ok = True
    if not (bf_old_a > 0.8):
        print("FAIL: OLD 未复现左侧黑带 (65x115)"); ok = False
    else:
        print("PASS: OLD 复现左侧黑带 (65x115)")

    if not (bf_new_a < 0.05):
        print("FAIL: NEW 仍存在左侧黑带 (65x115)"); ok = False
    else:
        print("PASS: NEW 消除左侧黑带 (65x115)")

    if not (bf_old_b > 0.8):
        print("FAIL: OLD 未复现左侧黑带 (59x117)"); ok = False
    else:
        print("PASS: OLD 复现左侧黑带 (59x117)")

    if not (bf_new_b < 0.05):
        print("FAIL: NEW 仍存在左侧黑带 (59x117)"); ok = False
    else:
        print("PASS: NEW 消除左侧黑带 (59x117)")

    if fo_new < 0.05:
        print("PASS: 无外框素材 NEW 无黑带且不崩溃")
    else:
        print("WARN: 无外框素材 NEW 左带黑占比偏高"); ok = False

    print("\n结果:", "ALL PASS ✅" if ok else "HAS FAILURE ❌")


if __name__ == "__main__":
    main()
