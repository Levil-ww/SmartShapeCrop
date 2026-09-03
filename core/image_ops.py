"""
core/image_ops.py
图像层面操作：素材加载/缩放/平铺填充/边框合成/文字环绕/导出 JPG
核心原则：预览显示仅缩放，渲染与保存始终使用 canvas_w_px × canvas_h_px 全尺寸。
"""
from __future__ import annotations
import os
import logging
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

# 提高像素上限：业务常处理印刷级超大图（如 EPS 栅格化后超过 1 亿像素），
# 默认 89478485 像素会触发 DecompressionBombWarning。
# 改为 2 亿像素上限（约 14142 × 14142），既能覆盖最大印刷图，又能防御恶意超大图。
Image.MAX_IMAGE_PIXELS = 200_000_000

from .geometry import CropDesign, compute_border_bands


# ---------- 素材加载与适配 ----------

def load_image_rgb(path: str) -> Image.Image:
    """加载素材图为 RGB 模式（JPG 通常无 alpha，转 RGB 方便合成）。
    [Fix 2026-08-26] 处理 EXIF 方向：相机/手机拍的照片会带 Orientation tag（如旋转90度）。
    若不处理会导致竖横方向错误，后续 cover（按错误宽高比缩放）→ 看似拉伸变形。
    """
    img = Image.open(path)
    # 处理 EXIF 方向旋转（避免竖横颠倒导致的变形）
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        # [F14] 旧版 PIL 或无 EXIF 时跳过，但记录被吞异常以便排障
        logger.debug(f"[image_ops] EXIF 方向处理失败，跳过（图片方向可能不正确）: {e}")
    if img.mode != 'RGB':
        img = img.convert('RGB')
    return img


def fit_image_to_rect(src_img: Image.Image,
                      target_w: int, target_h: int,
                      mode: str = 'cover',
                      bg_color: tuple[int, int, int] = (255, 255, 255),
                      quality: str = 'export') -> Image.Image:
    """
    将素材图适配到目标矩形尺寸。
    mode:
      - 'cover': 按比例缩放并居中裁剪，填满目标（不拉伸，用于边框花纹）
      - 'contain': 按比例缩放放入目标，剩余区域用 bg_color 补齐
      - 'stretch': 直接拉伸到目标尺寸（慎用）
      - 'tile': 平铺重复填满目标区域（用于瓷砖花纹）
    quality:
      - 'export' (默认): LANCZOS 重采样，最终导出用，质量最高
      - 'preview': BILINEAR 重采样，预览刷新用，3-5× 加速，肉眼差异可忽略
    """
    base_resample = Image.BILINEAR if quality == 'preview' else Image.LANCZOS
    sw, sh = src_img.size
    if sw <= 0 or sh <= 0:
        return Image.new('RGB', (target_w, target_h), bg_color)

    # ---- [Fix 2026-09-02] 严重下采样保护（素材尺寸 >> 目标尺寸时 BILINEAR=aliasing 伪影）----
    # 图像理论：下采样 > ~1.5× 时，BILINEAR 的 2×2 邻域无低通滤波，
    #   高频花纹（百合线稿、文字边）产生混叠 aliasing → 用户感知"马赛克/色块/失真"。
    # LANCZOS 有多 tap 核（8-tap / 6-tap）天然抗锯齿，对单次 stretch / cover 中间 resize
    #   的额外开销 < 30ms（8K→1K 级别），远小于整体 GUI 渲染 1-2s，可忽略。
    # 逻辑语义：纯 stretch / cover / contain 的输出与原算法 100% 一致，
    #   仅插值核变化；export 本来就是 LANCZOS，不受影响；upscale / 近 1:1 场景
    #   继续用 BILINEAR，保持预览加速初衷（3-5× 于 LANCZOS）。
    def _smart_resize(img, tw, th, base):
        iw, ih = img.size
        sx, sy = tw / iw, th / ih
        # 任意一边 >1.5× 下采样 → 强制 LANCZOS 抗锯齿
        if min(sx, sy) < 1.0 / 1.5:
            return img.resize((tw, th), Image.LANCZOS)
        return img.resize((tw, th), base)

    if mode == 'tile':
        return _tile_fill(src_img, target_w, target_h)
    if mode == 'stretch':
        return _smart_resize(src_img, target_w, target_h, base_resample)

    scale = max(target_w / sw, target_h / sh) if mode == 'cover' \
        else min(target_w / sw, target_h / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    resized = _smart_resize(src_img, nw, nh, base_resample)

    if mode == 'cover':
        # 居中裁剪
        left = (nw - target_w) // 2
        top = (nh - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))
    else:  # contain
        canvas = Image.new('RGB', (target_w, target_h), bg_color)
        canvas.paste(resized, ((target_w - nw) // 2, (target_h - nh) // 2))
        return canvas


def _tile_fill(src_img: Image.Image, tw: int, th: int) -> Image.Image:
    """平铺素材填满目标区域"""
    out = Image.new('RGB', (tw, th))
    sw, sh = src_img.size
    for y in range(0, th, sh):
        for x in range(0, tw, sw):
            out.paste(src_img, (x, y))
    return out


def _estimate_outer_border(img: Image.Image, max_frac: float = 0.25,
                           tol: int = 20, uniform_tol: int = 25) -> tuple:
    """估算源图四边外侧近似纯色边框的厚度(像素)，返回 (top, bottom, left, right)。

    判定规则（抗 AA 边缘 + 仅识别"实心纯色框"）：
      1. 从边缘逐行/列扫描，若该行/列 ≥90% 像素与角点同色(容差 tol)则计入边框；
      2. 扫描得到的边框带必须满足"整条带近似同色"(各通道 std ≤ uniform_tol)，
         否则视为渐变/图案而非实心框，返回 0（不跳过）。
    用于 _edge_extend_fill 在延展时跳过实心纯色外框，避免黑/纯色边框被拉伸成宽色带。
    """
    arr = np.array(img.convert('RGB'), dtype=np.int16)
    h, w = arr.shape[:2]
    if h == 0 or w == 0:
        return 0, 0, 0, 0
    corner = arr[0, 0]
    max_t = max(1, int(h * max_frac))
    max_l = max(1, int(w * max_frac))

    def band(get_line, length, max_len):
        t = 0
        for i in range(length):
            if i >= max_len:
                break
            line = get_line(i).astype(np.int16)
            near = (np.abs(line - corner).max(axis=-1) <= tol)
            if near.mean() < 0.9:
                break
            t += 1
        return t

    def is_solid(get_region, t):
        if t < 1:
            return False
        region = get_region(t).reshape(-1, 3)
        if region.size == 0:
            return False
        return region.std(axis=0).max() <= uniform_tol

    top = band(lambda i: arr[i, :, :], h, max_t)
    bottom = band(lambda i: arr[h - 1 - i, :, :], h, max_t)
    left = band(lambda i: arr[:, i, :], w, max_l)
    right = band(lambda i: arr[:, w - 1 - i, :], w, max_l)

    top = top if is_solid(lambda t: arr[:t, :, :], top) else 0
    bottom = bottom if is_solid(lambda t: arr[h - t:, :, :], bottom) else 0
    left = left if is_solid(lambda t: arr[:, :t, :], left) else 0
    right = right if is_solid(lambda t: arr[:, w - t:, :], right) else 0
    return top, bottom, left, right


def _edge_extend_fill(centered_img: Image.Image, tw: int, th: int) -> Image.Image:
    """把等比缩放后居中的素材图，用镜像/边缘延展填满目标画布（不留白、边框不糊）。

    适用于水池模式：画布AR与素材AR略有差异时，不希望出现白边。
    算法：把 centered_img 贴到中央，四周空白区域用镜像对称延展填充。

    [Fix 2026-08-26] 修复"两侧黑色背景框"：当某侧延展量较大(>5% 画布边长)且源图该侧带
    近似纯色外框(如克罗印花设计稿的黑色边框)时，改为使用镜像延展填充，
    避免纯色外框被拉伸放大成宽色带，同时避免单点颜色填充产生的色块。
    """
    sw, sh = centered_img.size
    canvas = Image.new('RGB', (tw, th), (255, 255, 255))
    cx = (tw - sw) // 2
    cy = (th - sh) // 2
    canvas.paste(centered_img, (cx, cy))

    if sw >= tw and sh >= th:
        return canvas  # 已完全填满

    # 估算四边实心纯色外框厚度（仅在延展量较大时用于跳过外框）
    top_b, bot_b, left_b, right_b = _estimate_outer_border(centered_img)
    EXTEND_TRIGGER = 0.05  # 单侧延展超过画布该边 5% 才视为需要跳过外框

    arr = np.array(canvas, dtype=np.uint8)
    src_arr = np.array(centered_img, dtype=np.uint8)

    def _mirror_extend_top_bottom(gap: int, target_width: int) -> np.ndarray:
        """为顶部/底部延展生成 (gap, target_width, 3) 的填充数组。
        从源图内容区采样，并通过平铺/裁剪调整到目标宽度。
        """
        # 取源图内容区中间部分作为样本
        content_top = top_b
        content_bottom = sh - bot_b
        content_h = content_bottom - content_top
        
        if content_h <= 0:
            # 退化为使用整图
            content_top, content_bottom, content_h = 0, sh, sh
        
        # 确定要采样的行数（从源图靠近当前边界的区域）
        sample_rows = min(gap, content_h)
        
        # 根据是顶部还是底部，从正确的边缘采样
        # 从内容区的上边缘采样（用于顶部填充，镜像后方向反转）
        sample = src_arr[content_top:content_top + sample_rows, :, :].copy()
        
        # 垂直翻转作为镜像（使图案看起来是从源图"反射"出来的）
        sample = sample[::-1, :, :]
        
        # 如果需要更多行，平铺内容区
        if sample_rows < gap:
            reps = (gap // sample_rows) + 1 if sample_rows > 0 else 1
            sample = np.tile(sample, (reps, 1, 1))
            sample = sample[:gap, :, :]
        
        # 调整宽度到 target_width
        current_w = sample.shape[1]
        if current_w < target_width:
            # 水平平铺
            reps = (target_width // current_w) + 1
            sample = np.tile(sample, (1, reps, 1))
            sample = sample[:, :target_width, :]
        elif current_w > target_width:
            sample = sample[:, :target_width, :]
        
        return sample

    def _mirror_extend_left_right(gap: int, target_height: int) -> np.ndarray:
        """为左侧/右侧延展生成 (target_height, gap, 3) 的填充数组。
        从源图内容区采样，并通过平铺/裁剪调整到目标高度。
        """
        content_left = left_b
        content_right = sw - right_b
        content_w = content_right - content_left
        
        if content_w <= 0:
            content_left, content_right, content_w = 0, sw, sw
        
        sample_cols = min(gap, content_w)
        
        # 从内容区的左边缘采样（镜像后方向反转）
        sample = src_arr[:, content_left:content_left + sample_cols, :].copy()
        
        # 水平翻转作为镜像
        sample = sample[:, ::-1, :]
        
        if sample_cols < gap:
            reps = (gap // sample_cols) + 1 if sample_cols > 0 else 1
            sample = np.tile(sample, (1, reps, 1))
            sample = sample[:, :gap, :]
        
        # 调整高度到 target_height
        current_h = sample.shape[0]
        if current_h < target_height:
            reps = (target_height // current_h) + 1
            sample = np.tile(sample, (reps, 1, 1))
            sample = sample[:target_height, :, :]
        elif current_h > target_height:
            sample = sample[:target_height, :, :]
        
        return sample

    def _row_extend(side: str) -> np.ndarray:
        """简单的边缘像素行/列延展（用于小延展量情况）"""
        if side == 'top':
            edge_row = arr[cy + top_b:cy + top_b + 1, :, :]
            if top_b > 0 and cy > th * EXTEND_TRIGGER:
                return _mirror_extend_top_bottom(cy, tw)
            return np.broadcast_to(edge_row, (cy, tw, 3)).copy()
        elif side == 'bottom':
            edge_row = arr[cy + sh - bot_b - 1:cy + sh - bot_b, :, :]
            remain = th - (cy + sh)
            if bot_b > 0 and remain > th * EXTEND_TRIGGER:
                return _mirror_extend_top_bottom(remain, tw)
            return np.broadcast_to(edge_row, (remain, tw, 3)).copy()
        elif side == 'left':
            edge_col = arr[:, cx + left_b:cx + left_b + 1, :]
            if left_b > 0 and cx > tw * EXTEND_TRIGGER:
                return _mirror_extend_left_right(cx, th)
            return np.broadcast_to(edge_col, (th, cx, 3)).copy()
        elif side == 'right':
            edge_col = arr[:, cx + sw - right_b - 1:cx + sw - right_b, :]
            remain = tw - (cx + sw)
            if right_b > 0 and remain > tw * EXTEND_TRIGGER:
                return _mirror_extend_left_right(remain, th)
            return np.broadcast_to(edge_col, (th, remain, 3)).copy()
        return np.zeros((0, 0, 3), dtype=np.uint8)

    # 延展顶部空白（0..cy-1）
    if cy > 0:
        arr[:cy, :, :] = _row_extend('top')
    # 延展底部空白（cy+sh..th-1）
    if cy + sh < th:
        remain = th - (cy + sh)
        arr[cy + sh:, :, :] = _row_extend('bottom')
    # 延展左侧空白（0..cx-1）
    if cx > 0:
        arr[:, :cx, :] = _row_extend('left')
    # 延展右侧空白（cx+sw..tw-1）
    if cx + sw < tw:
        remain = tw - (cx + sw)
        arr[:, cx + sw:, :] = _row_extend('right')

    return Image.fromarray(arr, mode='RGB')


def adapt_pool_material(src_img: Image.Image,
                        target_w: int, target_h: int,
                        material_design_w_cm: float = 0.0,
                        material_design_h_cm: float = 0.0,
                        canvas_w_cm: float = 0.0,
                        canvas_h_cm: float = 0.0,
                        quality: str = 'export') -> Image.Image:
    """[水池模式专用] 素材适配：与参考项目 pack_BigMagicCompany_copy 保持一致。

    算法：
      1. 方向校正（三重校验后才允许旋转）
      2. 简单拉伸（stretch）：直接将源图拉伸到目标尺寸

    [Fix 2026-08-27] 与参考项目对齐：使用简单拉伸模式，
    避免 contain/cover 模式下的两侧黑边或边框线条被裁剪问题。
    """
    base_resample = Image.BILINEAR if quality == 'preview' else Image.LANCZOS
    sw, sh = src_img.size
    if sw <= 0 or sh <= 0:
        return Image.new('RGB', (target_w, target_h), (255, 255, 255))

    # ---- 步骤0：不裁剪源图边框（保持原素材完整性）----
    # 与参考项目 pack_BigMagicCompany_copy 保持一致

    # ---- 步骤1：方向校正（三重校验 A ∧ B ∧ C → 才允许旋转90度）----
    # 画布方向
    if canvas_w_cm > 0 and canvas_h_cm > 0:
        canvas_ar = canvas_w_cm / canvas_h_cm
        canvas_is_landscape = canvas_w_cm > canvas_h_cm
    else:
        canvas_ar = target_w / target_h
        canvas_is_landscape = target_w > target_h

    # 像素方向（内容方位真实基准 = 像素存储方向 — 图3红框文件预览就是按这个显示的）
    pixel_ar = sw / sh
    pixel_is_landscape = sw > sh

    # 条件A：像素方向 vs 画布方向 不一致（一竖一横）
    cond_a = (pixel_is_landscape != canvas_is_landscape)

    # 条件B：有文件名设计方向元数据 且 文件名设计方向 = 画布方向
    has_design_meta = (material_design_w_cm > 0 and material_design_h_cm > 0)
    cond_b = False
    design_is_landscape = False
    if has_design_meta:
        design_is_landscape = (material_design_w_cm > material_design_h_cm)
        cond_b = (design_is_landscape == canvas_is_landscape)

    # 条件C（硬校验）：像素AR ≈ 设计尺寸的倒数AR（误差<15% → 证明PS存反了方向）
    #   例：设计是竖版 58×121（设计AR=58/121=0.479），但PS横排存储为像素 121×58（像素AR=121/58=2.086）
    #   → 设计倒数AR = 121/58 = 2.086 = 像素AR  → 相等！→ 确认存反了方向
    cond_c = False
    if has_design_meta:
        design_reciprocal_ar = material_design_h_cm / material_design_w_cm
        ar_diff = abs(pixel_ar - design_reciprocal_ar) / max(design_reciprocal_ar, 1e-6)
        cond_c = (ar_diff < 0.15)

    # 方向校正：以"像素方向 vs 画布方向"为唯一判据（cond_a）
    # [Fix 2026-08-26 三次修复] 三重校验(cond_a∧cond_b∧cond_c)过严：
    #   cond_b 要求"设计方向=画布方向"，但水池场景中素材常为竖版、画布为横版，
    #   cond_b 必然失败 → 拒绝旋转 → 竖版素材被等比缩成细长条 + 画布79%被纯色填充。
    #   正确判据应是 cond_a（像素方向≠画布方向 → 旋转对齐）：
    #     1) 设计竖版+像素竖版+画布横版 → cond_a真 → 旋转✅（本次修复主场景）
    #     2) 设计竖版+像素横版(PS存反)+画布横版 → cond_a假 → 不旋转✅（13:18事故场景已正确处理）
    #     3) 设计横版+像素横版+画布竖版 → cond_a真 → 旋转✅
    #   即 cond_a 单独即可覆盖全部场景；cond_b/cond_c 仅保留作日志诊断，不再作为门控。
    # 旋转方向 ROTATE_270(顺时针90°)：经实测顺时针→正确，逆时针→上下颠倒。
    if cond_a:
        src_img = src_img.transpose(Image.ROTATE_270)  # 顺时针90度
        sw, sh = sh, sw
        logger.info(
            f"[adapt_pool_material] 方向校正(像素≠画布方向): "
            f"像素{'横版' if pixel_is_landscape else '竖版'}(AR={pixel_ar:.2f}) "
            f"↔ 画布{'横版' if canvas_is_landscape else '竖版'}(AR={canvas_ar:.2f}) "
            f"→ ROTATE_270(顺时针90°)对齐; "
            f"诊断[设计{'横版' if design_is_landscape else '竖版' if has_design_meta else '未知'}, "
            f"cond_b={cond_b}, cond_c={cond_c}]"
        )
    else:
        logger.info(
            f"[adapt_pool_material] 不旋转(像素方向=画布方向): "
            f"像素{'横版' if pixel_is_landscape else '竖版'}({sw}x{sh}); "
            f"画布{'横版' if canvas_is_landscape else '竖版'}({target_w}x{target_h})"
        )

    # ---- 步骤2：简单拉伸（stretch）----
    # 与参考项目 pack_BigMagicCompany_copy 保持一致：
    #   pil_resized = pil_img.resize((target_w_px, target_h_px), Image.Resampling.LANCZOS)
    #
    # 直接将源图拉伸到目标尺寸，不会产生两侧黑边问题，
    # 也不会裁剪源图的边框线条。
    # [Fix 2026-08-27] 采用简单拉伸模式，解决用户反馈的两侧黑边和边框线条被裁剪问题

    # 计算拉伸比例（仅用于日志）
    scale_w = target_w / sw
    scale_h = target_h / sh
    ar_src = sw / sh
    ar_tgt = target_w / target_h
    ar_diff_pct = abs(ar_src - ar_tgt) / max(ar_tgt, 1e-6) * 100

    # 简单拉伸到目标尺寸
    # [Fix 2026-09-02] 严重下采样抗锯齿：LOD 预览时素材 4-8× 下采样、
    #   或 300DPI 素材在全分辨率 preview 时 2×+ 下采样，BILINEAR 会 aliasing
    #   → 用户感知"马赛克/色块/失真"。任意一边 >1.5× 下采样时强制 LANCZOS，
    #   其余情况保留 base_resample（预览 BILINEAR 加速、导出 LANCZOS 高质量）。
    #   语义不变：仍是"简单拉伸到 (target_w,target_h)"，仅插值核自适应。
    sx_ds, sy_ds = target_w / sw, target_h / sh
    if min(sx_ds, sy_ds) < 1.0 / 1.5:
        _stretch_resample = Image.LANCZOS
    else:
        _stretch_resample = base_resample
    stretched = src_img.resize((target_w, target_h), _stretch_resample)

    logger.info(
        f"[adapt_pool_material] 使用简单拉伸(stretch)模式: "
        f"源图={sw}x{sh}, 目标={target_w}x{target_h}, "
        f"scale=({scale_w:.3f}, {scale_h:.3f}), "
        f"AR差异={ar_diff_pct:.1f}%"
    )

    return stretched


def load_and_fit(path: str, tw: int, th: int, mode: str = 'cover',
                 quality: str = 'export') -> Image.Image:
    """加载 + 适配 二合一（带错误保护，素材丢失时返回纯色占位）"""
    try:
        if not os.path.isfile(path):
            return Image.new('RGB', (tw, th), (220, 220, 220))
        return fit_image_to_rect(load_image_rgb(path), tw, th, mode, quality=quality)
    except Exception as e:
        logger.warning(f"素材加载适配失败 path={path}: {e}")
        return Image.new('RGB', (tw, th), (220, 220, 220))


# ---------- LOD 分级渲染（用于 GUI 预览加速）----------

def render_design_lod(design: CropDesign, scale: float = 0.25) -> Image.Image:
    """
    LOD (Level-of-Detail) 低分辨率渲染：
    将设计按 scale 因子缩小后渲染，再放大回原尺寸，
    用于 GUI 预览时显著降低计算量（比全分辨率快 4-16×）。
    
    原理：
    1. 先将 design 的画布尺寸按 scale 缩小
    2. 在缩小的画布上渲染（所有几何参数等比缩放）
    3. 将渲染结果放大回原画布尺寸
    
    注意：此函数仅用于 GUI 预览，导出/保存仍使用 render_design() 全分辨率渲染。
    
    Args:
        design: CropDesign 对象
        scale: 缩放因子 (0.1~1.0)，推荐 0.25（1/4 分辨率）
               scale=0.25 时，像素量仅为 1/16，渲染速度提升约 16×
    
    Returns:
        PIL.Image，大小 = design.canvas_w_px × design.canvas_h_px（预览显示用）
    """
    if scale >= 1.0:
        return render_design(design, quality='preview', pixel_scale=1.0)
    
    original_w = design.canvas_w_px
    original_h = design.canvas_h_px
    
    # 计算缩小后的画布尺寸
    lod_w = max(1, int(original_w * scale))
    lod_h = max(1, int(original_h * scale))
    
    # 保存原始值
    orig_w_cm = design.canvas_w_cm
    orig_h_cm = design.canvas_h_cm
    orig_dpi = design.dpi
    
    # 临时修改 design 尺寸进行 LOD 渲染
    lod_design = _make_lod_design(design, lod_w, lod_h)
    
    # 在 LOD 尺寸上渲染，传递 pixel_scale 以缩放固定像素值（如边框宽度）
    lod_result = render_design(lod_design, quality='preview', pixel_scale=scale)
    
    # 放大回原尺寸，使用 LANCZOS 重采样
    # [Fix 2026-09-02 B] 原方案 NEAREST → 马赛克硬像素（4×4 块） → 改为 BILINEAR →
    #   平滑但边缘能量仅 28%（path C edge_strength=5.41 vs 全分辨率 A=19.34），
    #   用户"处理过程中看到马赛克即时感"的根因：LOD 双重 resize 链丢失的高频细节
    #   无法靠 BILINEAR 线性插值恢复。
    #   改为 LANCZOS（LANCZOS up +46% 边缘恢复，edge_strength=7.92），额外耗时仅 0.11s
    #   （24MP canvas upscale per-call），远小于 LOD downscale 本身的 0.78s。
    #   最终导出仍走 render_design(quality='export') 全分辨率 LANCZOS，与此处无关。
    if lod_result.size != (original_w, original_h):
        lod_result = lod_result.resize((original_w, original_h), Image.LANCZOS)
    
    return lod_result


def _make_lod_design(design: CropDesign, lod_w: int, lod_h: int) -> CropDesign:
    """
    创建一个临时的 LOD 版本的 CropDesign。
    所有像素相关的参数按比例缩放，保持几何结构不变。
    """
    from copy import deepcopy
    
    lod_design = deepcopy(design)
    orig_w = design.canvas_w_px
    orig_h = design.canvas_h_px
    
    if orig_w <= 0 or orig_h <= 0:
        return lod_design
    
    sx = lod_w / orig_w
    sy = lod_h / orig_h
    
    # 更新画布尺寸
    lod_design.canvas_w_cm = design.canvas_w_cm * sx
    lod_design.canvas_h_cm = design.canvas_h_cm * sy
    
    # 边框层等比缩放
    if lod_design.borders:
        for border in lod_design.borders:
            border.offset_cm = border.offset_cm * min(sx, sy)
    
    # 外边距等比缩放
    lod_design.outer_margin_cm = design.outer_margin_cm * min(sx, sy)
    
    # 内边距等比缩放
    lod_design.inner_margin_top_cm = design.inner_margin_top_cm * sy
    lod_design.inner_margin_bottom_cm = design.inner_margin_bottom_cm * sy
    lod_design.inner_margin_left_cm = design.inner_margin_left_cm * sx
    lod_design.inner_margin_right_cm = design.inner_margin_right_cm * sx
    
    # L 形参数缩放
    if hasattr(lod_design, 'l_cut_w_cm') and hasattr(lod_design, 'l_cut_h_cm'):
        lod_design.l_cut_w_cm = design.l_cut_w_cm * sx
        lod_design.l_cut_h_cm = design.l_cut_h_cm * sy
    
    # 椭圆参数缩放
    if hasattr(lod_design, 'ellipse_rx_ratio') and hasattr(lod_design, 'ellipse_ry_ratio'):
        pass  # 比率值无需缩放
    
    return lod_design


# ---------- 核心渲染 ----------

def render_design(design: CropDesign, quality: str = 'export', pixel_scale: float = 1.0) -> Image.Image:
    """
    按 CropDesign 完整渲染一张全尺寸画布（RGB）。
    返回 PIL.Image，大小 = design.canvas_w_px × design.canvas_h_px

    quality:
      - 'export' (默认): LANCZOS 重采样，最终保存导出用
      - 'preview': BILINEAR 重采样，GUI 实时预览刷新用，显著降低大图重采样耗时
    
    pixel_scale:
      - 像素缩放因子，用于 LOD 渲染时调整固定像素值（如边框宽度）
      - 默认 1.0（全分辨率），LOD 渲染时使用 < 1.0 的值
      - 例如 scale=0.25 时，border_width_px 会相应缩小
    """
    W, H = design.canvas_w_px, design.canvas_h_px
    # 固定像素值按比例缩放（用于 LOD 渲染）
    # 最小 2 像素，确保边框在低分辨率下仍可见
    import math
    border_width_px = max(2, int(math.ceil(10 * pixel_scale)))
    BLACK_RGB = (0, 0, 0)
    # 1. 整体背景（最外层）
    #    水池模式优先：如果 pool_outer_material_image 设置了（匹配到的花纹图），整幅铺满
    # [Fix 2026-08-26 二次修复] 改用 adapt_pool_material（方向校正 + contain等比 + 边缘延展填充）
    #   历史问题链：
    #     08-21 cover → stretch：避免cover裁掉边框花纹，但 stretch 变形严重（用户投诉1）
    #     08-26 stretch → cover：解决变形，但 cover 过度裁剪边框花纹完整性（用户投诉2）
    #     08-26 当前方案：方向校正+contain等比+边缘延展 → 图案完整不变形，边缘无白边
    cached_img = getattr(design, '_cached_outer_image', None)
    is_pool = bool(design.pool_outer_material_image and (cached_img is not None or os.path.isfile(design.pool_outer_material_image)))
    # [Fix 2026-08-26] L 形 + 外背景图特性：边框带应显示外背景图，
    # 挖角(cut)区域应显示 outer_bg_color。此标志控制跳过"边框带着色"与"cut 区填白"。
    has_outer_img = bool(design.outer_bg_image and os.path.isfile(design.outer_bg_image))

    if cached_img is not None and design.pool_outer_material_image:
        is_tile = _looks_like_tile(design.pool_outer_material_image)
        if is_tile:
            canvas = fit_image_to_rect(cached_img, W, H, mode='tile', quality=quality)
        else:
            canvas = adapt_pool_material(
                cached_img, W, H,
                material_design_w_cm=getattr(design, 'pool_material_design_w_cm', 0.0),
                material_design_h_cm=getattr(design, 'pool_material_design_h_cm', 0.0),
                canvas_w_cm=design.canvas_w_cm,
                canvas_h_cm=design.canvas_h_cm,
                quality=quality,
            )
    elif design.pool_outer_material_image and os.path.isfile(design.pool_outer_material_image):
        is_tile = _looks_like_tile(design.pool_outer_material_image)
        if is_tile:
            canvas = load_and_fit(design.pool_outer_material_image, W, H, mode='tile', quality=quality)
        else:
            src = load_image_rgb(design.pool_outer_material_image)
            canvas = adapt_pool_material(
                src, W, H,
                material_design_w_cm=getattr(design, 'pool_material_design_w_cm', 0.0),
                material_design_h_cm=getattr(design, 'pool_material_design_h_cm', 0.0),
                canvas_w_cm=design.canvas_w_cm,
                canvas_h_cm=design.canvas_h_cm,
                quality=quality,
            )
    elif design.outer_bg_image and os.path.isfile(design.outer_bg_image):
        # 非水池背景图：保持旧模式 cover（普通背景不过度在意边框完整性）
        canvas = load_and_fit(design.outer_bg_image, W, H,
                              mode='tile' if _looks_like_tile(design.outer_bg_image) else 'cover',
                              quality=quality)
    else:
        canvas = Image.new('RGB', (W, H), design.outer_bg_color)

    canvas_arr = np.array(canvas, dtype=np.uint8)

    # 判断是否为水池模式+有素材图（用于跳过边框带渲染和L形遮罩）
    # [Fix 2026-08-27] 素材填充模式下 pool_hole_transparent=False，
    # 但仍需跳过边框带渲染 — 条件改为"有任何水池素材(外框或内挖)"即可
    has_outer_pool_material = (design.pool_outer_material_image
                               and (cached_img is not None or os.path.isfile(design.pool_outer_material_image)))
    has_inner_pool_material = bool(
        getattr(design, 'pool_inner_material_image', None)
        and os.path.isfile(getattr(design, 'pool_inner_material_image', ''))
    )
    is_pool_with_material = has_outer_pool_material or has_inner_pool_material

    # 1.1 L形模式 + 花型图：只在outer_rect的L形区域内显示花型图
    # 非L形区域（outer_rect外部 + cut区域）填充为outer_bg_color
    if design.mode == 'rect_lshape' and not is_pool_with_material:
        from .geometry import build_lshape_mask
        has_outer_img = design.outer_bg_image and os.path.isfile(design.outer_bg_image)
        if has_outer_img:
            lshape = design.l_shape_px()
            outer = design.outer_rect_px()
            corners = design.corners_px
            # 构建outer_rect的L形mask（不包括cut区域）
            lshape_mask_img = build_lshape_mask(
                (W, H), outer, lshape.corner,
                lshape.cut_w, lshape.cut_h,
                corners, fill_value=255)
            lshape_mask = np.array(lshape_mask_img, dtype=bool)
            # 非L形区域填充为outer_bg_color
            outer_bg_arr = np.full((H, W, 3), design.outer_bg_color, dtype=np.uint8)
            non_lshape_mask = ~lshape_mask
            if non_lshape_mask.any():
                canvas_arr[non_lshape_mask] = outer_bg_arr[non_lshape_mask]

    # 2. 渲染边框 band（水池模式且有素材图时跳过——素材本身就是外框）
    # [Fix 2026-08-26] L 形 + 外背景图：边框带应保持外背景图，故跳过着色。
    if not is_pool_with_material and not (design.mode == 'rect_lshape' and has_outer_img):
        bands = compute_border_bands(design)
        for band_mask, layer in bands:
            # 该层的填充颜色/图像
            if layer.fill_type == 'image' and layer.image_path and os.path.isfile(layer.image_path):
                mode = 'tile' if layer.tile_mode else 'cover'
                fill_img = load_and_fit(layer.image_path, W, H, mode=mode, quality=quality)
                fill_arr = np.array(fill_img, dtype=np.uint8)
            else:
                fill_arr = np.full((H, W, 3), layer.color, dtype=np.uint8)
            # 把 band_mask=True 的像素写入 canvas
            canvas_arr[band_mask] = fill_arr[band_mask]

    # 3. 挖洞后的内部区域（内矩形/椭圆/L形内部）填背景色或素材
    inner_fill = _render_inner_area(design, quality=quality)
    inner_fill_arr = np.array(inner_fill, dtype=np.uint8)
    inner_mask = _get_inner_pixel_mask(design)
    
    # 池模式：素材图的边框花纹保留在 inner_mask 外部
    # 不需要保存/恢复 inner_mask 内部的像素
    # 白色填充只作用于 inner_mask 内部，外部的素材图花纹自然保留
    
    # [Fix 2026-08-28] 裁剪有图（L 形挖角）语义：
    # rect_lshape + 池素材时，"L 形区域保留外框素材、被切掉的角显示洞色"。
    # 与旧"素材中间挖 L 形洞"（inner_mask 覆盖为 inner_fill）不同——
    # 这里 L 形 = 保留区（素材裁成 L 形），cut 角 = 挖掉区（填白色）。
    # [Fix 2026-09-03] cut 区强制填纯白(255,255,255)：用户反馈原 hole_bg_color
    #   (250,245,230 米色) 与素材底色过于接近，挖角视觉上不够明显。
    #   白色也是 JPG 模式下表达"挖空/透明"的行业惯例（与 pool_hole_transparent=True 一致）。
    lshape_cut_done = False
    if design.mode == 'rect_lshape' and is_pool_with_material:
        from .geometry import build_lshape_mask, compute_inner_corner_radii
        lshape = design.l_shape_px()
        inner_rect = design.inner_rect_px()
        outer = design.outer_rect_px()
        corners = design.corners_px
        inner_corners = compute_inner_corner_radii(
            outer, inner_rect, corners,
            direct=design.pool_hole_transparent,
        )
        # full_inner = 整个 inner_rect；inner_mask = L 形（inner_rect 挖角）
        # 差集 = 被切掉的角（cut 区域）
        full_inner_img = build_lshape_mask(
            (W, H), inner_rect, lshape.corner,
            0, 0,
            inner_corners, fill_value=255)
        cut_area_mask = np.array(full_inner_img, dtype=bool) & ~inner_mask
        if cut_area_mask.any():
            canvas_arr[cut_area_mask] = 255  # 纯白
        # L 形区域（inner_mask）保持 step 1 的外框素材，不覆盖
        lshape_cut_done = True
    # ===== [SINGLE-HOLE Add-On 2026-08-31] Stale Decor Black Border Invalidation =====
    # （仅 rect_hole 单洞素材填充模式，零侵入，外层完整 try/except 静默失败）
    #
    # 根因：canvas 来源于 pool_outer_material_image（匹配到的"成品裁剪图"），
    #   该图本身内嵌了草图解析成功时刻（原始边距）的装饰性 10px 黑边框 + 内挖底色花纹。
    #   当用户手动修改任一/多个边距后，新的 inner_rect 位置/尺寸发生变化：
    #     (a) canvas_arr[inner_mask] = inner_fill_arr[inner_mask] 只覆盖"新内挖区"，
    #         旧内嵌装饰（包括旧的 10px 黑线）仍然保留在 canvas 上。
    #     (b) render_design 末尾 L812 几何差法又按最新 inner_rect 画了一圈新的 10px 黑框。
    #   结果 → 两条黑色边距线同时存在（旧边距位置 + 新边距位置），用户：
    #     "修改边距参数时，裁剪的内挖尺寸就随之变化了，但是修改之前的尺寸边框为什么还存在？"
    #
    # 修复：检测到"当前边距 ≠ 草图解析成功时记录的原始边距（差异 > 0.1 cm 任意一边）"时，
    #   构造 Part A mask = 旧内挖矩形 ∩ 新内挖矩形之外 = 应该是"外框花纹（边距环带）"但
    #   仍保留了旧装饰黑线 + 旧内挖底色花纹 的 stale 区域。用 canvas 外框花纹采样源
    #   （outer 矩形左上角 2cm×2cm patch，肯定是真正外框花纹，不是内挖装饰，也不是
    #   第一轮错误修复的"内挖花纹"）平铺覆盖 Part A mask。边距环带恢复为正确外框花纹。
    #
    # 保护：if 判定严格收窄（rect_hole + 非多洞 + 有 outer + 非挖空 + 非L形 + 原始边距存在且
    #   与当前边距不同），try/except 任何异常静默跳过，原有代码路径一字未被修改。
    _sd_done = False
    try:
        _sd_orig = getattr(design, '_pool_sketch_original_margins_cm', None)
        if (_sd_orig is not None
                and isinstance(_sd_orig, (tuple, list))
                and len(_sd_orig) == 4
                and design.mode == 'rect_hole'
                and not getattr(design, 'pool_is_multi_hole', False)
                and not getattr(design, 'pool_hole_transparent', True)
                and (getattr(design, 'lshape_cut_w', 0.0) or 0.0) == 0.0
                and (getattr(design, 'lshape_cut_h', 0.0) or 0.0) == 0.0
                and has_outer_pool_material):
            _ot = float(_sd_orig[0] or 0.0)
            _ob = float(_sd_orig[1] or 0.0)
            _ol = float(_sd_orig[2] or 0.0)
            _or_ = float(_sd_orig[3] or 0.0)
            _ct = float(getattr(design, 'inner_margin_top_cm', 0.0) or 0.0)
            _cb = float(getattr(design, 'inner_margin_bottom_cm', 0.0) or 0.0)
            _cl = float(getattr(design, 'inner_margin_left_cm', 0.0) or 0.0)
            _cr = float(getattr(design, 'inner_margin_right_cm', 0.0) or 0.0)
            if (abs(_ct - _ot) > 0.1 or abs(_cb - _ob) > 0.1
                    or abs(_cl - _ol) > 0.1 or abs(_cr - _or_) > 0.1):
                # ===== 1) 构造旧 / 新 inner_rect =====
                from .geometry import RectShape, make_mask, fill_rect_mask
                outer_rect = design.outer_rect_px()
                _ox = outer_rect.x
                _oy = outer_rect.y
                _ow = outer_rect.w
                _oh = outer_rect.h
                _cm2px = design.cm2px(1.0)
                _old_x = _ox + _ol * _cm2px
                _old_y = _oy + _ot * _cm2px
                _old_w = _ow - (_ol + _or_) * _cm2px
                _old_h = _oh - (_ot + _ob) * _cm2px
                _new_rect = design.inner_rect_px()
                _old_rect = RectShape(
                    x=max(0.0, _old_x),
                    y=max(0.0, _old_y),
                    w=max(1.0, min(float(W) - max(0.0, _old_x), _old_w)),
                    h=max(1.0, min(float(H) - max(0.0, _old_y), _old_h)),
                )
                _new_rect_clamped = RectShape(
                    x=max(0.0, _new_rect.x),
                    y=max(0.0, _new_rect.y),
                    w=max(1.0, min(float(W) - max(0.0, _new_rect.x), _new_rect.w)),
                    h=max(1.0, min(float(H) - max(0.0, _new_rect.y), _new_rect.h)),
                )
                # Part A mask = 旧内挖矩形 ∩ 新内挖矩形之外（stale 装饰区）
                _old_full = make_mask((W, H))
                fill_rect_mask(_old_full, _old_rect, 255)
                _old_mask = np.array(_old_full, dtype=bool)
                _new_full = make_mask((W, H))
                fill_rect_mask(_new_full, _new_rect_clamped, 255)
                _new_mask = np.array(_new_full, dtype=bool)
                _stale_A = _old_mask & (~_new_mask)
                if _stale_A.any():
                    # ===== 2) 采集外框花纹采样源：outer 矩形左上角 2cm × 2cm patch =====
                    _patch_side_cm = 2.0
                    _ps = max(2, int(round(_patch_side_cm * _cm2px)))
                    # 起点：outer.x + 0.5cm，outer.y + 0.5cm（避开 canvas 边缘可能的拉伸瑕疵）
                    _px0 = int(round(_ox + 0.5 * _cm2px))
                    _py0 = int(round(_oy + 0.5 * _cm2px))
                    _px1 = min(W, _px0 + _ps)
                    _py1 = min(H, _py0 + _ps)
                    if (_px1 - _px0) >= 4 and (_py1 - _py0) >= 4:
                        _patch = canvas_arr[_py0:_py1, _px0:_px1, :].copy()
                        _ph, _pw = _patch.shape[:2]
                        # ===== 3) Tile 覆盖 stale_A：生成 tile 画布，按 mask 赋值 =====
                        _tile_full = np.tile(
                            _patch,
                            (
                                (H + _ph - 1) // _ph,
                                (W + _pw - 1) // _pw,
                                1,
                            ),
                        )[:H, :W, :]
                        canvas_arr[_stale_A] = _tile_full[_stale_A]
                        _sd_done = True
    except Exception as _sd_e:
        import logging as _sd_log
        _sd_log.getLogger(__name__).debug(
            f"[SINGLE-HOLE Stale-Decor-Invalidation Add-On] 静默跳过，原因: {_sd_e}"
        )
        _sd_done = False
    # ===== [END SINGLE-HOLE Add-On Stale Decor Black Border Invalidation] =====
    if not lshape_cut_done:
        # ===== [SEAM-FEATHER Add-On 2026-09-02] 接缝羽化 =====
        # 目标：内外素材的原始颜色 100% 保留，仅在 inner_mask 边界 2px 条做线性渐变过渡，
        # 消除"都是安妮森林但接缝处色感硬跳变"的观感。
        #
        # 算法：
        #   1. 计算 inner_mask 边界条 = inner_mask & ~erode(inner_mask, 2) —— 约 2px 宽
        #   2. paste 前保存边界条位置的外框原始色
        #   3. 正常 paste（内挖素材覆盖 inner_mask）
        #   4. cv2.distanceTransform 算 alpha 渐变（边界侧→内侧：1.0→0.0）
        #   5. 混合：边界条 = alpha * 外框色 + (1-alpha) * 内挖色
        #   6. inner_mask 内部像素 alpha=0 → 100% 内挖原始色
        #      inner_mask 边界最外缘 alpha≈1 → 接近外框原始色
        #      过渡带 = 2px
        try:
            from .geometry import _erode_mask as _em_seam
            # 色差预检：先算最外缘 1px 边界的内外颜色差，如果 < 5/255 就跳过
            # （本来没色差不需要羽化，省掉 erosion + 混合开销）
            _e1 = _em_seam(inner_mask, 1)
            _s0 = inner_mask & ~_e1
            if _s0.any():
                _outer_edge = canvas_arr[_s0]
                _inner_edge = inner_fill_arr[_s0]
                _mean_delta = float(
                    np.abs(_outer_edge.astype(np.int16) - _inner_edge.astype(np.int16))
                    .mean()
                )
                if _mean_delta >= 5.0:
                    # 色差 >= 5 才羽化，否则跳过（零开销）
                    _e2 = _em_seam(inner_mask, 2)
                    _s1 = _e1 & ~_e2
                    _out_s0 = _outer_edge.copy()
                    _out_s1 = canvas_arr[_s1].copy() if _s1.any() else None
                    canvas_arr[inner_mask] = inner_fill_arr[inner_mask]
                    # 最外缘 1px: α=0.70 接近外框色
                    _in0 = canvas_arr[_s0].copy()
                    canvas_arr[_s0] = (
                        0.70 * _out_s0.astype(np.float32)
                        + 0.30 * _in0.astype(np.float32)
                    ).astype(np.uint8)
                    # 次外缘 1px: α=0.30 接近内挖色
                    if _s1.any():
                        _in1 = canvas_arr[_s1].copy()
                        canvas_arr[_s1] = (
                            0.30 * _out_s1.astype(np.float32)
                            + 0.70 * _in1.astype(np.float32)
                        ).astype(np.uint8)
                    logger.debug(
                        f"[render_design] 接缝羽化触发: delta={_mean_delta:.1f}/255"
                    )
                else:
                    # 色差太小，直接覆盖无羽化（省 erosion + 混合）
                    canvas_arr[inner_mask] = inner_fill_arr[inner_mask]
            else:
                canvas_arr[inner_mask] = inner_fill_arr[inner_mask]
        except Exception as _se_e:
            logger.debug(
                f"[render_design] 接缝羽化异常（跳过，回退硬覆盖）: {_se_e}"
            )
            canvas_arr[inner_mask] = inner_fill_arr[inner_mask]

    # 3.1 L形模式：填充被挖掉的角落区域（cut area）为 hole_bg_color
    # inner_mask 是 L 形（不含 cut 区域），需要将 cut 区域也填充
    if design.mode == 'rect_lshape' and not lshape_cut_done:
        from .geometry import build_lshape_mask, compute_inner_corner_radii
        lshape = design.l_shape_px()
        inner_rect = design.inner_rect_px()
        outer = design.outer_rect_px()
        corners = design.corners_px
        inner_corners = compute_inner_corner_radii(
            outer, inner_rect, corners,
            direct=design.pool_hole_transparent,
        )
        full_inner_img = build_lshape_mask(
            (W, H), inner_rect, lshape.corner,
            0, 0,
            inner_corners, fill_value=255)
        full_inner_mask = np.array(full_inner_img, dtype=bool)
        cut_area_mask = full_inner_mask & ~inner_mask
        # [Fix 2026-08-26] L 形 + 外背景图：cut 区域已由 step 1.1 填为 outer_bg_color，
        # 此处不再用 inner_fill（白色）覆盖，保持挖角显示外背景色。
        if cut_area_mask.any() and not (design.mode == 'rect_lshape' and has_outer_img):
            canvas_arr[cut_area_mask] = inner_fill_arr[cut_area_mask]

    # 3.5 在挖空区域边缘绘制统一的黑色边框线
    # rect_hole / rect_lshape 模式：用几何差集替代形态学腐蚀（精确等价，加速）
    # ellipse_hole 模式：降级为形态学腐蚀

    if design.mode in ('rect_hole', 'rect_lshape'):
        from .geometry import (make_mask, fill_rect_mask, apply_rounded_corners_to_mask,
                               compute_inner_corner_radii, RectShape,
                               build_lshape_mask)
        # ===== [MULTI-HOLE Add-On 2026-08-29] PRE-COMPUTE 每洞独立 4 边边框 =====
        # 多洞 mode=rect_hole + N>=2 时：不依赖后续单洞大 inner_rect shrink（那玩意儿
        # 只生成"整个内挖的大边框"，UNION 小 mask 后只剩顶部横条，左/右/下三边全丢 ——
        # 用户截图中的半吊子黑线就是这么来的）。
        # 解决方案：对每洞独立做 full_rect & ~shrunk_rect(2*border_width_px) 得到
        # 每洞 10px 完整 4 边环，逐洞 OR 合并；后续单洞代码跑完后再 override border_mask。
        # border_width_px 与 L533 同一变量（max(2, ceil(10*pixel_scale))），保证与单洞
        # 边框厚度/缩放宽高 像素级 1:1 一致。
        _mh_border_mask = None  # None = 非多洞 / 非 rect_hole / 洞不足 2
        _multi_holes = getattr(design, 'pool_holes_cm', [])
        if (design.mode == 'rect_hole'
                and getattr(design, 'pool_is_multi_hole', False)
                and isinstance(_multi_holes, list)
                and len(_multi_holes) >= 2):
            _mh_border_mask = np.zeros((H, W), dtype=bool)
            for _hc in _multi_holes:
                _hx = design.cm2px(float(_hc.get('x_cm', 0.0)))
                _hy = design.cm2px(float(_hc.get('y_cm', 0.0)))
                _hw = design.cm2px(float(_hc.get('w_cm', 0.0)))
                _hh = design.cm2px(float(_hc.get('h_cm', 0.0)))
                if _hw <= 0 or _hh <= 0:
                    continue
                _rx = max(0.0, _hx)
                _ry = max(0.0, _hy)
                _rw = max(1.0, min(float(W) - _rx, _hw))
                _rh = max(1.0, min(float(H) - _ry, _hh))
                # 全洞 mask
                _hf = make_mask((W, H))
                fill_rect_mask(_hf, RectShape(_rx, _ry, _rw, _rh), 255)
                _hole_full = np.array(_hf, dtype=bool)
                # shrink 2*bw → 内部洞（与单洞 has_shrunk 公式完全对齐）
                _sh_w = max(0.0, _rw - 2 * border_width_px)
                _sh_h = max(0.0, _rh - 2 * border_width_px)
                if _sh_w > 0 and _sh_h > 0:
                    _sh_r = RectShape(
                        x=_rx + border_width_px,
                        y=_ry + border_width_px,
                        w=_sh_w, h=_sh_h,
                    )
                    _hs = make_mask((W, H))
                    fill_rect_mask(_hs, _sh_r, 255)
                    _hole_shrunk = np.array(_hs, dtype=bool)
                    _border_i = _hole_full & ~_hole_shrunk
                else:
                    # 洞尺寸太小无法 shrink — 等价单洞 has_shrunk=False → 整洞作为边框区
                    _border_i = _hole_full
                _mh_border_mask |= _border_i
        # ===== [END ADD-ON PRE-COMPUTE] =====

        inner_rect = design.inner_rect_px()
        outer = design.outer_rect_px()
        corners = design.corners_px

        inner_corners = compute_inner_corner_radii(
            outer, inner_rect, corners,
            direct=design.pool_hole_transparent,
        )

        shrunk_w = max(0, inner_rect.w - 2 * border_width_px)
        shrunk_h = max(0, inner_rect.h - 2 * border_width_px)
        has_shrunk = shrunk_w > 0 and shrunk_h > 0

        if has_shrunk:
            if design.mode == 'rect_lshape':
                lshape = design.l_shape_px()
                shrunk_rect = RectShape(
                    x=inner_rect.x + border_width_px,
                    y=inner_rect.y + border_width_px,
                    w=shrunk_w, h=shrunk_h,
                    corner_r=0.0,
                )
                shrunk_corners = {ck: max(0.0, r - border_width_px)
                                  for ck, r in inner_corners.items()}
                shrunk_cut_w = max(0.0, lshape.cut_w - border_width_px)
                shrunk_cut_h = max(0.0, lshape.cut_h - border_width_px)
                mask_b_img = build_lshape_mask(
                    (W, H), shrunk_rect, lshape.corner,
                    shrunk_cut_w, shrunk_cut_h,
                    shrunk_corners, fill_value=255)
            else:
                shrunk = RectShape(
                    x=inner_rect.x + border_width_px,
                    y=inner_rect.y + border_width_px,
                    w=shrunk_w, h=shrunk_h,
                    corner_r=0.0,
                )
                shrunk_corners = {ck: max(0.0, r - border_width_px)
                                  for ck, r in inner_corners.items()}
                mask_b_img = make_mask((W, H))
                fill_rect_mask(mask_b_img, shrunk, 255)
                if any(r > 0 for r in shrunk_corners.values()):
                    apply_rounded_corners_to_mask(
                        mask_b_img, shrunk, shrunk_corners, fill_value=255)

            border_mask = inner_mask & ~np.array(mask_b_img, dtype=bool)
        else:
            border_mask = inner_mask

        # ===== [MULTI-HOLE Add-On 2026-08-29] APPLY 多洞边框 override =====
        # 只有多洞 PRE-COMPUTE 成功时覆盖；单洞 / rect_lshape / 少于 2 洞：_mh_border_mask is None，
        # 保持上面原代码算出来的 border_mask 一字不变。
        if _mh_border_mask is not None:
            border_mask = _mh_border_mask
        # ===== [END ADD-ON APPLY] =====
    else:
        from .geometry import _erode_mask
        eroded = _erode_mask(inner_mask, border_width_px)
        border_mask = inner_mask & ~eroded

    if border_mask.any():
        canvas_arr[border_mask] = BLACK_RGB

    # ===== [L-Shape Border Completion 2026-09-03] L 形挖角处的素材边框补全 =====
    # 当素材图自带边框（如克罗印花的棕色+黑色双层边框、安妮森林的细黑边框），
    # L 形挖角会在 cut 区产生两条新边缘，这些边缘原本只有 step 3.5 的统一黑框，
    # 现在改为：从素材图自动检测边框层 → 向 cut 区内部延伸绘制，
    # 使 cut 边缘的边框层次与素材外边缘一致，视觉上形成完整 L 形外框。
    #
    # 仅在 rect_lshape + 池素材 + 非 tile（tile 无边框）时触发。
    if design.mode == 'rect_lshape' and is_pool_with_material and not _looks_like_tile(
            design.pool_outer_material_image or ''):
        try:
            from .lshape_border import apply_lshape_border_completion

            inner_rect = design.inner_rect_px()
            lshape = design.l_shape_px()
            cut_corner = lshape.corner
            cut_w_px = lshape.cut_w
            cut_h_px = lshape.cut_h

            # 使用原始素材图（cached_img）做边框检测，避免 adapt_pool_material
            # 的简单拉伸可能造成的边框像素畸变影响检测精度。
            # scale = canvas尺寸 / 原始尺寸，用于把检测到的厚度换算到画布坐标系。
            _src_img = cached_img
            _scale_x = 1.0
            _scale_y = 1.0
            if _src_img is None and design.pool_outer_material_image and os.path.isfile(
                    design.pool_outer_material_image):
                _src_img = load_image_rgb(design.pool_outer_material_image)
            if _src_img is not None:
                _sw, _sh = _src_img.size
                if _sw > 0 and _sh > 0:
                    _scale_x = float(W) / float(_sw)
                    _scale_y = float(H) / float(_sh)

            _completion_ok = apply_lshape_border_completion(
                canvas_arr=canvas_arr,
                material_img=canvas,                # 画布图 —— 检测和绘制都要参考
                src_material_img=_src_img,           # 原始素材图 —— 更精确的边框检测
                scale_x=_scale_x,
                scale_y=_scale_y,
                outer_rect=inner_rect,               # cut 区相对于 inner_rect
                cut_corner=cut_corner,
                cut_w_px=cut_w_px,
                cut_h_px=cut_h_px,
                dpi=design.dpi,
                bg_color=(255, 255, 255),            # 白色作为 bg 参考，避免误判米色/棕色为 bg
            )
        except Exception as _bc_e:
            logger.debug(f"[LShapeBorder] 补全过程异常（静默跳过）: {_bc_e}")

    # ===== [SINGLE-HOLE Add-On 2026-08-31 V2] Stale-Decor Universal Residual Cleaner =====
    # （仅 rect_hole 单洞素材填充模式，零侵入 try/except 静默失败）
    #
    # 动机：V1 Add-On（L668-772）依赖 _pool_sketch_original_margins_cm 字段存在。
    #   若该字段因任何原因缺失（草图解析被跳过 / design 被重新创建），V1 不触发。
    #   残留的旧装饰黑线（成品裁剪图内嵌的 10px 边框线）会留在 canvas_arr 上，
    #   与 V1 的效果相同 —— 两条黑色边距线并存（旧边距位置 + 新渲染的 border_mask）。
    #
    # V2 方案：**不依赖原始边距记录**。
    #   观察：Step 3.8 的 border_mask 已正确标记当前 inner_rect 边缘应渲染的黑框。
    #   canvas_arr 中"不是 border_mask 但近黑色"的像素，若形状是细条/小斑点，
    #   必是残留的旧装饰（成品图内嵌旧黑线）；用外框花纹采样源平铺覆盖即可清理。
    #
    #   【V2.1 关键修复 2026-08-31：连通域细条防御落地，此前注释承诺但代码缺失】
    #   缺陷回溯：V2 初版直接对全体 _residual 像素做覆盖，未做形状过滤。
    #     对"克罗印花"等花纹底色含有大量近黑花型元素的素材图，花型像素被一并标记，
    #     经左上角 2cm×2cm 样片（米色/咖啡色为主）覆盖后 → 整张花纹"变黑为咖"，
    #     用户表现为"素材填充花纹变色（黑→咖啡色）"。
    #   修复：两层防御 + 一层兜底上限：
    #     (1) 连通域 bbox：min(宽, 高) ≤ 30px 判定"细条带状"（与注释一致）：
    #         残留边框带（~10px 厚，一维长一维薄）符合；大面积花型块（80×80+）不符合。
    #     (2) 近黑阈值收紧：< 20（原 <40 过于宽泛，深棕/深花纹也会中招）。
    #     (3) 总面积兜底：清理像素不得超过外框环带面积的 5%，
    #         极端异常情况下也不会破坏大面积花型。
    #
    #   零侵入：整体保持在 try/except 内，任何异常静默跳过，
    #          未触碰 render_design 的任何其他逻辑分支。
    try:
        if (design.mode == 'rect_hole'
                and not getattr(design, 'pool_is_multi_hole', False)
                and not getattr(design, 'pool_hole_transparent', True)
                and (getattr(design, 'lshape_cut_w', 0.0) or 0.0) == 0.0
                and (getattr(design, 'lshape_cut_h', 0.0) or 0.0) == 0.0
                and has_outer_pool_material):
            # [V2.1 Fix ②] 近黑阈值从 40 收紧到 20（避免深棕/深花被误纳入）
            _near_black = np.all(canvas_arr < 20, axis=2)  # (H, W) bool
            # 2) 减去当前 border_mask（正确渲染的新黑框）
            #    同时减去 inner_mask（内挖素材内部的花纹是合法的，绝不应被"残留清理"触碰）
            #    ← [BUG FIX 2026-09-02] 缺失 ~inner_mask 时，内挖素材中的近黑花纹
            #    会被误判为"旧黑线"，被左上角米色色块覆盖 → 花纹变黑为咖（用户截图）
            _residual = _near_black & ~border_mask & ~inner_mask
            if _residual.any():
                # ===== [V2.1 Fix ①] 连通域 bbox 过滤：细条( min(w,h)≤30px ) 才保留 =====
                import cv2 as _cv2
                _res_u8 = (_residual.astype(np.uint8) * 255)
                _n_lbl, _lbl, _st, _cen = _cv2.connectedComponentsWithStats(
                    _res_u8, connectivity=8)
                # [PERF FIX 2026-09-02] 原代码用循环 N 次 _thin_band |= (_lbl == _li)：
                #   每次迭代对 38.6 MP 标签矩阵做 int32→bool 比较 + OR，
                #   N=395 连通域时总开销 ~12s（152 亿次比较），是导出 64.8s 的主因。
                #   改为先收集保留 label ID 列表，再 np.isin 单次扫描 → 0.075s，160× 加速。
                _THIN_MAX = 30  # px，与注释"细条带状 30px"一致
                _keep_labels = []
                for _li in range(1, _n_lbl):
                    _bw = int(_st[_li, _cv2.CC_STAT_WIDTH])
                    _bh = int(_st[_li, _cv2.CC_STAT_HEIGHT])
                    # 一维薄即视为细条：边框（10px厚，另一维很长）/ 小段 / 小斑点都命中
                    if min(_bw, _bh) <= _THIN_MAX:
                        _keep_labels.append(_li)
                if _keep_labels:
                    _thin_band = np.isin(_lbl, _keep_labels)
                else:
                    _thin_band = np.zeros_like(_residual, dtype=bool)
                # ===== [V2.1 Fix ③] 面积兜底上限：环带 5% =====
                outer_rect = design.outer_rect_px()
                _ox = int(round(outer_rect.x))
                _oy = int(round(outer_rect.y))
                _ow = int(round(outer_rect.w))
                _oh = int(round(outer_rect.h))
                _inner = design.inner_rect_px()
                _iw0 = int(round(_inner.x))
                _ih0 = int(round(_inner.y))
                _iw1 = int(round(_inner.x + _inner.w))
                _ih1 = int(round(_inner.y + _inner.h))
                _ring_area = max(1, _ow * _oh - max(0, (min(_ox+_ow, _iw1)-max(_ox,_iw0)) * max(0, (min(_oy+_oh,_ih1)-max(_oy,_ih0)))))
                _MAX_PX = max(200, int(_ring_area * 0.05))
                _kept = _thin_band
                if _kept.sum() > _MAX_PX:
                    # 异常态：候选像素过多（防御：几何过滤也可能极端情形漏过）
                    # 退化策略：不清理任何像素，宁留残留不伤花型
                    logger.debug(
                        f"[Stale-Decor V2.1] 面积兜底触发，拒绝清理："
                        f"candidate={_kept.sum()} cap={_MAX_PX} ring={_ring_area}"
                    )
                    _kept = np.zeros_like(_thin_band, dtype=bool)
                # ================================================================
                if _kept.any():
                    # 3) 用外框花纹采样源平铺覆盖（仅覆盖过滤后的细条残留，不再碰花型）
                    _cm2px = design.cm2px(1.0)
                    _patch_side_cm = 2.0
                    _ps = max(2, int(round(_patch_side_cm * _cm2px)))
                    _px0 = int(round(outer_rect.x + 0.5 * _cm2px))
                    _py0 = int(round(outer_rect.y + 0.5 * _cm2px))
                    _px1 = min(W, _px0 + _ps)
                    _py1 = min(H, _py0 + _ps)
                    if (_px1 - _px0) >= 4 and (_py1 - _py0) >= 4:
                        _patch = canvas_arr[_py0:_py1, _px0:_px1, :].copy()
                        _ph, _pw = _patch.shape[:2]
                        _tile_full = np.tile(
                            _patch,
                            ((H + _ph - 1) // _ph, (W + _pw - 1) // _pw, 1),
                        )[:H, :W, :]
                        canvas_arr[_kept] = _tile_full[_kept]
                        logger.debug(
                            f"[Stale-Decor V2.1] Cleaned {_kept.sum()} / candidate={_thin_band.sum()} "
                            f"residual thin-band black pixels "
                            f"(near-black total={_near_black.sum()}, border_mask excluded)"
                        )
    except Exception as _v2_e:
        logger.debug(f"[Stale-Decor V2] 静默跳过，原因: {_v2_e}")
    # ===== [END V2 Residual Cleaner] =====

    # 4. 边框文字
    if design.border_text is not None:
        pil = Image.fromarray(canvas_arr, 'RGB')
        _draw_border_text(pil, design)
        canvas_arr = np.array(pil, dtype=np.uint8)

    return Image.fromarray(canvas_arr, 'RGB')


def _looks_like_tile(path: str) -> bool:
    """简单启发式：文件名包含 tile/花砖 则默认平铺"""
    n = os.path.basename(path).lower()
    return any(k in n for k in ('tile', 'pattern', 'hua', 'zhuan', '花砖'))


def _get_inner_pixel_mask(design: CropDesign) -> np.ndarray:
    """返回挖洞区域（即内部填充区域）的 bool mask，与边框带的同心圆角保持一致。"""
    from .geometry import (make_mask, fill_rect_mask, fill_ellipse_mask, fill_lshape_mask, 
                           apply_rounded_corners_to_mask, compute_inner_corner_radii,
                           build_lshape_mask, RectShape)
    W, H = design.canvas_w_px, design.canvas_h_px
    m = make_mask((W, H))

    # ===== [MULTI-HOLE Add-On 2026-08-29] PURE ADD-ON GUARD =====
    # 触发条件：mode==rect_hole + 有多洞列表 + 至少 2 洞。
    # 多洞场景下：各洞做矩形 UNION，不做圆角（圆角算法基于单 inner_rect）。
    # 满足则在这里直接 return，完全不触碰下面的原单洞分支。
    multi_holes = getattr(design, 'pool_holes_cm', [])
    if (design.mode == 'rect_hole'
            and getattr(design, 'pool_is_multi_hole', False)
            and isinstance(multi_holes, list)
            and len(multi_holes) >= 2):
        for hc in multi_holes:
            hx = design.cm2px(float(hc.get('x_cm', 0.0)))
            hy = design.cm2px(float(hc.get('y_cm', 0.0)))
            hw = design.cm2px(float(hc.get('w_cm', 0.0)))
            hh = design.cm2px(float(hc.get('h_cm', 0.0)))
            if hw <= 0 or hh <= 0:
                continue
            # Clamp to canvas
            rx = max(0.0, hx)
            ry = max(0.0, hy)
            rw = max(1.0, min(float(W) - rx, hw))
            rh = max(1.0, min(float(H) - ry, hh))
            fill_rect_mask(m, RectShape(rx, ry, rw, rh), 255)
        return np.array(m, dtype=bool)
    # ===== [END ADD-ON] — 以下原单洞/椭圆/L 形 代码一字未改 =====

    inner_rect = design.inner_rect_px()
    outer = design.outer_rect_px()
    corners = design.corners_px

    # 使用正确的算法计算内层圆角半径（每个角落独立计算）
    # 水池模式：direct=True 跳过边距缩减，圆角设置直接作用于内挖区域
    inner_corners = compute_inner_corner_radii(outer, inner_rect, corners,
                                                direct=design.pool_hole_transparent)

    if design.mode == 'rect_hole':
        fill_rect_mask(m, inner_rect, 255)
        if any(r > 0 for r in inner_corners.values()):
            apply_rounded_corners_to_mask(m, inner_rect, inner_corners)
        return np.array(m, dtype=bool)

    elif design.mode == 'rect_lshape':
        lshape = design.l_shape_px()
        m = build_lshape_mask(
            (W, H), inner_rect, lshape.corner,
            lshape.cut_w, lshape.cut_h,
            inner_corners, fill_value=255)
        return np.array(m, dtype=bool)

    else:  # ellipse_hole
        fill_ellipse_mask(m, design.ellipse_px(), 255)
        return np.array(m, dtype=bool)


def _render_inner_area(design: CropDesign, quality: str = 'export') -> Image.Image:
    """渲染内部填充（纯色 / 素材图 / 水池挖空=纯白 / 水池内挖素材=按内挖尺寸渲染）"""
    W, H = design.canvas_w_px, design.canvas_h_px
    # 水池模式：内部挖空留白 = 纯白色（JPG 不支持透明，白色即显示为"空"）
    if design.pool_hole_transparent:
        return Image.new('RGB', (W, H), (255, 255, 255))

    # ===== [MULTI-HOLE Add-On 2026-08-29] 多洞独立素材填充 =====
    # 触发条件：pool_is_multi_hole=True 且 pool_holes_cm>=2 且至少一洞有 inner_material_path。
    # 为每洞独立加载+适配+paste 素材到对应画布位置，实现"每洞用独立尺寸匹配的素材"。
    # 单洞/无多洞素材 → 跳过此分支，走下面原有单洞代码一字不变。
    _multi_holes = getattr(design, 'pool_holes_cm', [])
    _is_mh = (getattr(design, 'pool_is_multi_hole', False)
              and isinstance(_multi_holes, list)
              and len(_multi_holes) >= 2)
    if _is_mh:
        # 检查是否至少一洞有内挖素材
        _has_any_inner = any(
            (_hc.get('inner_material_path') is not None
             or _hc.get('_cached_inner_image') is not None)
            for _hc in _multi_holes
        )
        if _has_any_inner:
            result = Image.new('RGB', (W, H), design.hole_bg_color)
            for _hc in _multi_holes:
                _hx = design.cm2px(float(_hc.get('x_cm', 0.0)))
                _hy = design.cm2px(float(_hc.get('y_cm', 0.0)))
                _hw = design.cm2px(float(_hc.get('w_cm', 0.0)))
                _hh = design.cm2px(float(_hc.get('h_cm', 0.0)))
                _iw = max(1, int(round(_hw)))
                _ih = max(1, int(round(_hh)))
                _ix = max(0, int(round(_hx)))
                _iy = max(0, int(round(_hy)))
                # 边界保护：洞不超出画布
                if _ix + _iw > W:
                    _iw = W - _ix
                if _iy + _ih > H:
                    _ih = H - _iy
                if _iw <= 0 or _ih <= 0:
                    continue
                # 获取素材：优先用 Worker 预加载缓存，否则从 path 加载
                _cached = _hc.get('_cached_inner_image')
                _path = _hc.get('inner_material_path')
                _src = None
                if _cached is not None:
                    _src = _cached
                elif _path and os.path.isfile(_path):
                    try:
                        _src = load_image_rgb(_path)
                    except Exception as _e:
                        logger.debug(f"[_render_inner_area multi-hole] 加载洞素材失败 path={_path}: {_e}")
                        _src = None
                if _src is None:
                    continue
                # 适配：内挖素材使用 adapt_pool_material（与外框素材一致的简单拉伸模式，
                # 保证图案完整性不被裁剪），素材自身有设计方向尺寸时传入用于旋转校正。
                _is_tile = _looks_like_tile(_path) if _path else False
                if _is_tile:
                    _adapted = _tile_fill(_src, _iw, _ih)
                else:
                    _adapted = adapt_pool_material(
                        _src, _iw, _ih,
                        material_design_w_cm=float(_hc.get('_src_design_w_cm', 0.0)),
                        material_design_h_cm=float(_hc.get('_src_design_h_cm', 0.0)),
                        canvas_w_cm=float(_hc.get('w_cm', 0.0)),
                        canvas_h_cm=float(_hc.get('h_cm', 0.0)),
                        quality=quality,
                    )
                result.paste(_adapted, (_ix, _iy))
            return result
    # ===== [END MULTI-HOLE Add-On] =====

    # —— 水池内挖素材：按内挖像素尺寸渲染，贴到画布内挖位置 ——
    # 与外框素材（按画布尺寸渲染）不同，内挖素材应按内挖尺寸精确缩放
    pool_inner = getattr(design, 'pool_inner_material_image', None)
    if pool_inner and os.path.isfile(pool_inner):
        inner_rect = design.inner_rect_px()
        iw_px = max(1, int(round(inner_rect.w)))
        ih_px = max(1, int(round(inner_rect.h)))
        # 1) 将素材缩放到内挖像素尺寸
        is_tile = _looks_like_tile(pool_inner)
        # ===== [SINGLE-HOLE Add-On 2026-08-31] 对齐多洞内填：非平铺类改用 adapt_pool_material =====
        # 多洞逻辑（L947-958）：平铺类 → _tile_fill；非平铺类 → adapt_pool_material（方向校正+简单stretch）
        # 原单洞逻辑：平铺类 → load_and_fit(tile)；非平铺类 → load_and_fit(cover=等比裁剪)。
        # 差异影响：cover 会按中心裁剪素材，当用户修改边距（改变内挖比例）后，
        #   裁剪边界与 canvas（外框成品图）内嵌的旧内挖图案边缘位置相对位移，
        #   视觉上呈现"两条内挖边缘/图案错位叠加"——用户反馈的"重叠"。
        # 修复：单洞非平铺类改用 adapt_pool_material（方向校正+简单stretch，无裁剪无位移），
        #   与多洞一字不差的语义。任何异常自动回退到原 load_and_fit(cover) 保证零影响。
        if is_tile:
            material_img = load_and_fit(pool_inner, iw_px, ih_px,
                                        mode='tile', quality=quality)
        else:
            try:
                _src_inner = load_image_rgb(pool_inner)
                _src_w_cm = float(getattr(design, 'pool_inner_src_design_w_cm', 0.0) or 0.0)
                _src_h_cm = float(getattr(design, 'pool_inner_src_design_h_cm', 0.0) or 0.0)
                material_img = adapt_pool_material(
                    _src_inner, iw_px, ih_px,
                    material_design_w_cm=_src_w_cm,
                    material_design_h_cm=_src_h_cm,
                    canvas_w_cm=float(inner_rect.w) / max(design.cm2px(1.0), 1e-6),
                    canvas_h_cm=float(inner_rect.h) / max(design.cm2px(1.0), 1e-6),
                    quality=quality,
                )
            except Exception as _sh_e:
                logger.debug(
                    f"[_render_inner_area] 单洞 adapt_pool_material Add-On 失败，回退原 load_and_fit(cover): {_sh_e}"
                )
                material_img = load_and_fit(pool_inner, iw_px, ih_px,
                                            mode='cover', quality=quality)
        # ===== [END SINGLE-HOLE Add-On 对齐多洞内填] =====
        # 2) 创建全画布底色 + 将素材贴到内挖位置
        result = Image.new('RGB', (W, H), design.hole_bg_color)
        ox = max(0, int(round(inner_rect.x)))
        oy = max(0, int(round(inner_rect.y)))
        result.paste(material_img, (ox, oy))
        return result

    # 非水池内挖素材：按全画布尺寸渲染（兼容旧模式）
    if design.hole_bg_image and os.path.isfile(design.hole_bg_image):
        return load_and_fit(design.hole_bg_image, W, H,
                            mode='tile' if _looks_like_tile(design.hole_bg_image) else 'cover',
                            quality=quality)
    return Image.new('RGB', (W, H), design.hole_bg_color)


# ---------- 边框文字 ----------

def _draw_border_text(img: Image.Image, design: CropDesign) -> None:
    """在 img 上沿外矩形四边绘制环绕文字（就地修改 img）"""
    bt = design.border_text
    if bt is None:
        return
    outer = design.outer_rect_px()
    # 估算字体大小：按内边距的 60%
    font_size = max(12, int(design.cm2px(0.5)))
    try:
        font = ImageFont.truetype(bt.font_name, font_size)
    except (OSError, IOError) as e:
        # 找不到字体时退化到默认
        logger.debug(f"[image_ops] 字体 {bt.font_name!r} 加载失败，退化到默认字体: {e}")
        font = ImageFont.load_default()

    draw = ImageDraw.Draw(img)
    text = bt.text

    # 可用区域：稍微向内缩进 1% 避免贴边
    pad = max(4, int(min(outer.w, outer.h) * 0.01))
    x1, y1 = int(outer.x) + pad, int(outer.y) + pad
    x2, y2 = int(outer.right) - pad, int(outer.bottom) - pad
    inner_w = x2 - x1
    inner_h = y2 - y1

    if bt.include_top:
        _draw_text_line(draw, font, text, x1, y1, inner_w, 'top', bt.color, mirror=False)
    if bt.include_bottom:
        _draw_text_line(draw, font, text, x1, y2, inner_w, 'bottom', bt.color, mirror=bt.mirror_bottom)
    if bt.include_left:
        _draw_text_side(draw, font, text, x1, y1, inner_h, 'left', bt.color)
    if bt.include_right:
        _draw_text_side(draw, font, text, x2, y1, inner_h, 'right', bt.color)


def _repeat_text(text: str, length_px: int, font: ImageFont.ImageFont) -> str:
    """把 text 重复拼接直到 ≥ length_px 宽度（实际绘制时再裁剪）"""
    try:
        tw = font.getlength(text)
    except AttributeError as e:
        # 旧版 PIL 无 getlength()，退化到 getsize()
        logger.debug(f"[image_ops] 当前 PIL 无 getlength()，改用 getsize(): {e}")
        tw = font.getsize(text)[0]
    if tw <= 0:
        return text
    need = max(1, int(length_px / tw) + 2)
    return (text + ' ') * need


def _draw_text_line(draw: ImageDraw.ImageDraw, font, text, x, y, w, side, color, mirror):
    """绘制顶/底一行文字；底部可选镜像"""
    full = _repeat_text(text, w, font)
    # 先画到临时图，方便做镜像
    tmp = Image.new('RGB', (w, max(12, font.size + 4)), (255, 255, 255))
    td = ImageDraw.Draw(tmp)
    # 把内部背景色透明化（先转 RGBA 合成回去）：更简单做法——临时图用透明，再贴回
    tmp_rgba = Image.new('RGBA', (w, max(12, font.size + 4)), (0, 0, 0, 0))
    td_rgba = ImageDraw.Draw(tmp_rgba)
    td_rgba.text((0, 0), full, fill=(*color, 255), font=font)
    if mirror:
        tmp_rgba = tmp_rgba.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.FLIP_LEFT_RIGHT)
    # 按长度裁剪
    if tmp_rgba.width > w:
        tmp_rgba = tmp_rgba.crop((0, 0, w, tmp_rgba.height))
    # 贴回原图
    base = draw._image.convert('RGBA')
    base.alpha_composite(tmp_rgba, dest=(x, y))
    draw._image.paste(base.convert('RGB'), (0, 0))


def _draw_text_side(draw: ImageDraw.ImageDraw, font, text, x, y, h, side, color):
    """绘制左/右竖排文字：先横向画到临时图再旋转 90°"""
    full = _repeat_text(text, h, font)
    line_h = max(12, font.size + 4)
    tmp_rgba = Image.new('RGBA', (h, line_h), (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp_rgba)
    td.text((0, 0), full, fill=(*color, 255), font=font)
    if tmp_rgba.width > h:
        tmp_rgba = tmp_rgba.crop((0, 0, h, line_h))
    # 左边：逆时针 90°；右边：顺时针 90°
    if side == 'left':
        tmp_rgba = tmp_rgba.rotate(90, expand=True)
        dest = (x - line_h, y)
    else:
        tmp_rgba = tmp_rgba.rotate(-90, expand=True)
        dest = (x, y)
    base = draw._image.convert('RGBA')
    base.alpha_composite(tmp_rgba, dest=dest)
    draw._image.paste(base.convert('RGB'), (0, 0))


# ---------- 保存 JPG ----------

def save_jpg(img: Image.Image, out_path: str, quality: int = 95, dpi: int | tuple[int, int] | None = None) -> None:
    """保存为 JPG，可选写入 DPI 元数据（印刷用）。
    dpi 兼容：单个 int（如 150 → (150,150)） 或 (int,int) 元组（已明确水平/垂直）。
    """
    ext = os.path.splitext(out_path)[1].lower()
    if ext not in ('.jpg', '.jpeg'):
        out_path = os.path.splitext(out_path)[0] + '.jpg'
    save_kwargs = {'quality': quality, 'optimize': True}
    if dpi is not None:
        # 规范化：允许 int 或 二元 tuple/list；PIL JPEG 需要 (dpi_x, dpi_y) 且每项为可 round 的标量
        if isinstance(dpi, (tuple, list)) and len(dpi) == 2:
            dx, dy = int(round(dpi[0])), int(round(dpi[1]))
        else:
            ds = int(round(dpi))  # type: ignore[arg-type]
            dx, dy = ds, ds
        save_kwargs['dpi'] = (dx, dy)
    img.save(out_path, 'JPEG', **save_kwargs)


# ---------- 素材图自动裁剪：用于把任意 JPG 素材铺满到目标区域 ----------

def prepare_material_for_rect(material_path: str,
                              target_w: int, target_h: int,
                              mode: str = 'cover') -> Image.Image:
    """
    对外暴露的工具函数：把一张素材 JPG/PSD 预处理到目标尺寸。
    mode: cover（裁剪填满，推荐）/ contain（留白）/ tile（平铺）/ stretch（拉伸）
    """
    if not material_path or not os.path.isfile(material_path):
        return Image.new('RGB', (target_w, target_h), (255, 255, 255))
    ext = os.path.splitext(material_path)[1].lower()
    if ext in ('.psd', '.psb'):
        from .psd.loader import load_psd_flattened
        img = load_psd_flattened(material_path)
    else:
        img = load_image_rgb(material_path)
    return fit_image_to_rect(img, target_w, target_h, mode=mode)
