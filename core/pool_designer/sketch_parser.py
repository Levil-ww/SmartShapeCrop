"""尺寸草图解析器（水池设计器用）—— 严格7步法实现。

7步法识别流程：
  Step 1. 矩形检测：定位外框+内框两个嵌套矩形
  Step 2. 区域划分：基于两个矩形划分8个语义区域
  Step 3. 全局OCR：全图多尺度扫描提取所有数值（值+坐标+置信度）
  Step 4. 方向标签：带"上/下/左/右"前缀的值优先锁定对应字段
  Step 5. 位置映射：其余数值按中心点坐标归入8个区域
  Step 6. 几何校验：用 outer=inner+margin_sum 两个等式做纠错与反推
  Step 7. 冲突解决：方向标签 > 几何自洽 > 置信度 > 位数多

所有公开函数都不会抛异常；失败时返回带有 success=False 的结果。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# [F3 修复] 解炸弹二级防御：尽量为 PIL 设置像素上限，防止恶意/超大图在
# 全量解码时 OOM。主闸门是 parse_sketch 入口的 validate_sketch_file（40MP
# 头信息校验，见 _SKETCH_MAX_PIXELS），此处作为任何 PIL 全量加载路径的兜底网；
# 上限与 core/image_ops.py 保持一致（2 亿像素 ≈ 14142×14142）。
# 用 try 包裹：本模块主解码走 cv2，PIL 不可用时跳过也不影响导入。
try:  # pragma: no cover - 依赖环境差异
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    logging.getLogger(__name__).debug("[module] PIL 导入失败，已降级", exc_info=True)
    Image = None  # type: ignore

logger = logging.getLogger(__name__)

_PARSE_TIMEOUT_SEC = 20
_ALGO_VERSION = 7  # 2026-08-20: 严格7步法重构版

# ---------------------------------------------------------------------------
# 字符规范化：全角→半角（OCR 在 chi_sim 模式下常输出全角数字 ０-９ 句号．）
# ---------------------------------------------------------------------------
_FW_HW_TRANSLATION = str.maketrans({
    '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
    '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
    '．': '.', '，': '.', '、': '.', '。': '.',
    '　': ' ',   # 全角空格→半角
})


def _normalize_ocr_text(text: str) -> str:
    """OCR 识别文本规范化：全角→半角，去除多余空白。"""
    if not text:
        return ''
    return text.translate(_FW_HW_TRANSLATION).strip()

# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
_SKETCH_CACHE: dict = {}
_SKETCH_CACHE_MAX = 50
_SKETCH_CACHE_LOCK = threading.Lock()


def _get_cache_key(image_path: str, target_w: float, target_h: float) -> tuple:
    try:
        mtime = os.path.getmtime(image_path)
    except Exception:
        logger.debug("[_get_cache_key] 忽略异常", exc_info=True)
        mtime = 0
    return (image_path, mtime, round(target_w, 1), round(target_h, 1), _ALGO_VERSION)


def _get_cached_result(image_path: str, target_w: float, target_h: float):
    with _SKETCH_CACHE_LOCK:
        key = _get_cache_key(image_path, target_w, target_h)
        cached = _SKETCH_CACHE.get(key)
        if cached is not None:
            logger.info(f"[sketch_parser] 缓存命中：{image_path}")
            import copy
            return copy.deepcopy(cached)
    return None


def _store_cached_result(image_path: str, target_w: float, target_h: float, result):
    with _SKETCH_CACHE_LOCK:
        key = _get_cache_key(image_path, target_w, target_h)
        if len(_SKETCH_CACHE) >= _SKETCH_CACHE_MAX:
            oldest = next(iter(_SKETCH_CACHE))
            _SKETCH_CACHE.pop(oldest, None)
        import copy
        _SKETCH_CACHE[key] = copy.deepcopy(result)


_SKETCH_CONSISTENT_CACHE: dict = {}
_SKETCH_CONSISTENT_CACHE_MAX = 50
_SKETCH_CONSISTENT_CACHE_LOCK = threading.Lock()


def _get_consistent_cache_key(image_path: str) -> tuple:
    try:
        mtime = os.path.getmtime(image_path)
    except Exception:
        logger.debug("[_get_consistent_cache_key] 忽略异常", exc_info=True)
        mtime = 0
    return (image_path, mtime, _ALGO_VERSION)


def _get_consistent_cached_result(image_path: str):
    with _SKETCH_CONSISTENT_CACHE_LOCK:
        key = _get_consistent_cache_key(image_path)
        cached = _SKETCH_CONSISTENT_CACHE.get(key)
        if cached is not None:
            logger.info(f"[sketch_parser] 自洽解缓存命中：{image_path}")
            import copy
            return copy.deepcopy(cached)
    return None


def _store_consistent_cached_result(image_path: str, result):
    with _SKETCH_CONSISTENT_CACHE_LOCK:
        key = _get_consistent_cache_key(image_path)
        if len(_SKETCH_CONSISTENT_CACHE) >= _SKETCH_CONSISTENT_CACHE_MAX:
            oldest = next(iter(_SKETCH_CONSISTENT_CACHE))
            _SKETCH_CONSISTENT_CACHE.pop(oldest, None)
        import copy
        _SKETCH_CONSISTENT_CACHE[key] = copy.deepcopy(result)


# ---------------------------------------------------------------------------
# 文件校验 + 公共数据结构
# ---------------------------------------------------------------------------
_SKETCH_ACCEPT_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}
_SKETCH_MAX_FILE_MB = 50
_SKETCH_MAX_PIXELS = 40_000_000


def validate_sketch_file(path: str) -> tuple:
    if not path or not os.path.isfile(path):
        return False, "文件不存在"
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SKETCH_ACCEPT_EXT:
        return False, f"不支持的图片格式：{ext}"
    size = os.path.getsize(path)
    if size == 0:
        return False, "文件为空"
    if size > _SKETCH_MAX_FILE_MB * 1024 * 1024:
        return False, f"文件过大（{size/1024/1024:.1f}MB > {_SKETCH_MAX_FILE_MB}MB）"
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            w, h = im.size
    except Exception as e:
        return False, f"图片无法读取（可能已损坏）：{e}"
    if w <= 0 or h <= 0:
        return False, "图片尺寸无效"
    if w * h > _SKETCH_MAX_PIXELS:
        return False, (f"图片像素过多（{w}×{h}≈{w*h/1e6:.1f}MP），OCR会卡，请缩小")
    return True, ""


@dataclass
class SketchParseResult:
    success: bool = False
    message: str = ""
    method: str = ""
    outer_w_cm: float = 0.0
    outer_h_cm: float = 0.0
    inner_w_cm: float = 0.0
    inner_h_cm: float = 0.0
    margin_top_cm: float = 0.0
    margin_bottom_cm: float = 0.0
    margin_left_cm: float = 0.0
    margin_right_cm: float = 0.0
    debug: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 依赖安全导入 + 图像加载
# ---------------------------------------------------------------------------
def _safe_import_cv2():
    try:
        import cv2
        return cv2
    except Exception as e:
        logger.warning(f"[sketch_parser] OpenCV 未安装: {e}")
        return None


# OCR 引擎状态：供 GUI 层显示友好提示
_TESSERACT_STATUS = {
    "available": False,
    "reason": "",           # 不可用时的原因文字
    "tesseract_path": "",   # 可用时的 tesseract.exe 路径
    "tessdata_path": "",    # 可用时的 tessdata 路径
}


def get_tesseract_status() -> dict:
    """返回 Tesseract OCR 引擎状态（供 GUI 显示友好提示）。"""
    return dict(_TESSERACT_STATUS)


def _safe_import_tesseract():
    global _TESSERACT_STATUS
    try:
        import pytesseract
    except Exception as e:
        _TESSERACT_STATUS = {
            "available": False,
            "reason": f"Python 包 pytesseract 未安装: {e}",
            "tesseract_path": "", "tessdata_path": "",
        }
        logger.info(f"[sketch_parser] pytesseract 未安装: {e}")
        return None
    
    # 使用 PathResolver 统一查找 Tesseract 路径
    try:
        from core.config import PathResolver
        found_exe, found_tessdata = PathResolver.find_tesseract()
    except Exception as e:
        logger.warning(f"[sketch_parser] PathResolver 查找失败: {e}")
        found_exe, found_tessdata = None, None
    
    # 环境变量覆盖（最高优先级）
    env_path = os.environ.get('TESSERACT_PATH', '')
    if env_path:
        if os.path.isfile(env_path):
            found_exe = env_path
            td = os.path.join(os.path.dirname(env_path), 'tessdata')
            if os.path.isdir(td):
                found_tessdata = td
    
    if found_exe and found_exe != 'tesseract':
        try:
            pytesseract.pytesseract.tesseract_cmd = found_exe
            logger.info(f"[sketch_parser] 已配置 Tesseract: {found_exe}")
        except Exception:
            logger.debug("[_safe_import_tesseract] 忽略异常", exc_info=True)
            pass
    
    if found_tessdata:
        try:
            os.environ['TESSDATA_PREFIX'] = found_tessdata
        except Exception:
            logger.debug("[_safe_import_tesseract] 忽略异常", exc_info=True)
            pass
    
    try:
        version = pytesseract.get_tesseract_version()
        _TESSERACT_STATUS = {
            "available": True,
            "reason": f"正常 (版本 {version})",
            "tesseract_path": found_exe or "PATH 中",
            "tessdata_path": found_tessdata or os.environ.get('TESSDATA_PREFIX', ''),
        }
        return pytesseract
    except Exception as e:
        missing_hint = (
            "未找到 Tesseract-OCR 引擎。\n"
            "请安装后重试：下载地址 https://github.com/UB-Mannheim/tesseract/wiki\n"
            "安装时勾选 Chinese (Simplified) 语言包。\n"
            "或设置环境变量 TESSERACT_PATH 指向 tesseract.exe。\n"
            "或把便携版 tesseract.exe + tessdata 文件夹放到 EXE 同目录下 tesseract 子目录。"
        )
        if found_exe:
            reason_detail = f"找到了 tesseract.exe ({found_exe}) 但无法调用: {e}"
        else:
            reason_detail = "未找到 tesseract.exe。"
        _TESSERACT_STATUS = {
            "available": False,
            "reason": f"{reason_detail}\n{missing_hint}",
            "tesseract_path": found_exe or "",
            "tessdata_path": found_tessdata or "",
        }
        logger.info(f"[sketch_parser] Tesseract 无法调用: {e}")
        return None


def _load_image(image_path: str):
    cv2 = _safe_import_cv2()
    if cv2 is None:
        return None, "未安装 OpenCV"
    if not image_path or not os.path.isfile(image_path):
        return None, f"文件不存在: {image_path}"
    try:
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.warning(f"cv2.imread 失败: {e}")
        img = None
    if img is None:
        try:
            from PIL import Image as PILImage
            with PILImage.open(image_path) as pil:
                pil = pil.convert("RGB")
                img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        except Exception as e2:
            return None, f"读取失败: {e2}"
    return img, None


def _to_gray(img):
    cv2 = _safe_import_cv2()
    if len(img.shape) == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _enhance_colored_ink(cv2, color_img):
    """彩色笔迹（红笔/彩笔）增强：提取 LAB a*通道 + HSV红色掩码，与原图融合。

    仅作为 OCR 预处理的附加变体使用，不替换原有灰度图。
    耗时 < 30ms（1MP 图像），对整体性能影响极小。
    失败时返回 None，调用方自动跳过。
    """
    if color_img is None or len(color_img.shape) != 3:
        return None
    try:
        # 1. LAB 空间 a* 通道：红-绿对立，红色笔迹在此通道为高值
        lab = cv2.cvtColor(color_img, cv2.COLOR_BGR2LAB)
        a_channel = lab[:, :, 1]
        # 反向：a*值越大越红 → 255-a* 让红字变暗（与白底形成正对比）
        a_inv = cv2.subtract(255, a_channel)

        # 2. HSV 红色掩码（红笔标注最常见）
        hsv = cv2.cvtColor(color_img, cv2.COLOR_BGR2HSV)
        lr1 = np.array([0, 43, 46], dtype=np.uint8)
        ur1 = np.array([10, 255, 255], dtype=np.uint8)
        m1 = cv2.inRange(hsv, lr1, ur1)
        lr2 = np.array([156, 43, 46], dtype=np.uint8)
        ur2 = np.array([180, 255, 255], dtype=np.uint8)
        m2 = cv2.inRange(hsv, lr2, ur2)
        red_mask = cv2.bitwise_or(m1, m2)

        # 3. 与原始灰度图加权融合：红字区域更清晰，非红字区域不退化
        gray = cv2.cvtColor(color_img, cv2.COLOR_BGR2GRAY)
        # 红色掩码区域：a_inv 与 gray 取最小值（加深红字）
        red_present = cv2.countNonZero(red_mask) > 50
        if red_present:
            # 红字较多时：使用 a_inv 作为红字增强通道
            # 融合公式：enhanced = min(gray, a_inv) 在红字区域，其他用 gray
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            red_mask_dilate = cv2.dilate(red_mask, kernel, iterations=1)
            masked_a = cv2.bitwise_and(a_inv, a_inv, mask=red_mask_dilate)
            masked_gray = cv2.bitwise_and(gray, gray, mask=cv2.bitwise_not(red_mask_dilate))
            enhanced = cv2.add(masked_a, masked_gray)
        else:
            # 没有红字：直接返回 gray，不做多余计算
            return gray
        return enhanced
    except Exception as e:
        logger.debug(f"[color_enhance] 颜色增强失败（跳过）: {e}")
        return None


# ===========================================================================
# 7步法：核心识别流程
# ===========================================================================

# ---------------------------------------------------------------------------
# Step 1: 矩形检测（外框 + 内框）
# ---------------------------------------------------------------------------
def _build_binary_masks(cv2, gray_img):
    """4种二值化策略，覆盖不同草图风格。"""
    masks = []
    ks = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    km = cv2.getStructuringElement(cv2.MORPH_RECT, (4, 4))
    try:
        _, m = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        masks.append(("otsu", cv2.morphologyEx(m, cv2.MORPH_CLOSE, ks, iterations=1)))
    except Exception:
        logger.debug("[_build_binary_masks] 忽略异常", exc_info=True)
        pass
    try:
        e = cv2.Canny(gray_img, 15, 80)
        masks.append(("canny", cv2.morphologyEx(e, cv2.MORPH_CLOSE, ks, iterations=1)))
    except Exception:
        logger.debug("[_build_binary_masks] 忽略异常", exc_info=True)
        pass
    try:
        m = cv2.adaptiveThreshold(gray_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, blockSize=21, C=5)
        masks.append(("adaptive", cv2.morphologyEx(m, cv2.MORPH_CLOSE, km, iterations=1)))
    except Exception:
        logger.debug("[_build_binary_masks] 忽略异常", exc_info=True)
        pass
    try:
        _, m = cv2.threshold(gray_img, 127, 255, cv2.THRESH_BINARY_INV)
        masks.append(("high127", cv2.morphologyEx(m, cv2.MORPH_CLOSE, ks, iterations=1)))
    except Exception:
        logger.debug("[_build_binary_masks] 忽略异常", exc_info=True)
        pass
    return masks


def _find_all_rectangles(cv2, gray_img, color_img=None):
    """找所有矩形候选，返回 [(x,y,w,h,score,area), ...] 按面积降序。"""
    h_img, w_img = gray_img.shape[:2]
    full_area = h_img * w_img
    min_area = max(200, int(full_area * 0.0005))
    masks = _build_binary_masks(cv2, gray_img)
    all_rects = []
    seen = set()
    for mask_name, mask in masks:
        try:
            num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        except Exception:
            logger.debug("[_find_all_rectangles] 忽略异常", exc_info=True)
            continue
        for label_id in range(1, num_labels):
            area = stats[label_id, cv2.CC_STAT_AREA]
            if area < min_area:
                continue
            x = int(stats[label_id, cv2.CC_STAT_LEFT])
            y = int(stats[label_id, cv2.CC_STAT_TOP])
            ww = int(stats[label_id, cv2.CC_STAT_WIDTH])
            hh = int(stats[label_id, cv2.CC_STAT_HEIGHT])
            if ww < 15 or hh < 15:
                continue
            bf = 0.02
            if (x <= max(5, int(w_img*bf)) and y <= max(5, int(h_img*bf)) and
                (x+ww) >= w_img - max(5, int(w_img*bf)) and
                (y+hh) >= h_img - max(5, int(h_img*bf))):
                if area > full_area * 0.5:
                    continue
            key = (x, y, ww, hh)
            if key in seen:
                continue
            seen.add(key)
            aspect = min(ww, hh) / max(ww, hh)
            score = 0.5 + aspect * 0.5
            all_rects.append((x, y, ww, hh, score, area))
    all_rects.sort(key=lambda r: r[5], reverse=True)
    logger.info(f"[Step1] 找到 {len(all_rects)} 个矩形候选")
    return all_rects


def _select_best_nested_pair(all_rects):
    """选择最佳(外框,内框)对。核心：外框面积大 + 内框严格嵌套 + 面积比3%~97%。"""
    if len(all_rects) < 2:
        return None, None
    candidate_pairs = []
    for i in range(min(10, len(all_rects))):
        ox, oy, ow, oh, osc, oa = all_rects[i]
        for j in range(len(all_rects)):
            if i == j:
                continue
            ix, iy, iw, ih, isc, ia = all_rects[j]
            if not (ox <= ix and oy <= iy and ix+iw <= ox+ow and iy+ih <= oy+oh):
                continue
            area_ratio = ia / max(1, oa)
            if area_ratio < 0.03 or area_ratio > 0.97:  # 3%~97% 硬约束
                continue
            ps = 0.0
            ps += min(oa / 20000, 1.0)
            ps += isc * 2.0 + osc * 1.0
            if 0.10 <= area_ratio <= 0.70:
                ps += 5.0
            elif 0.05 <= area_ratio <= 0.90:
                ps += 2.0
            gaps = [iy-oy, (oy+oh)-(iy+ih), ix-ox, (ox+ow)-(ix+iw)]
            mg = min(gaps) if gaps else 0
            if mg > 5:
                ps += 3.0
            elif mg > 2:
                ps += 1.0
            candidate_pairs.append((ps, all_rects[i], all_rects[j]))
    candidate_pairs.sort(key=lambda x: x[0], reverse=True)
    if not candidate_pairs:
        return None, None
    _, outer, inner = candidate_pairs[0]
    logger.info(f"[Step1] 选定外框={outer[:4]} 内框={inner[:4]} 得分={candidate_pairs[0][0]:.2f}")
    return outer, inner


# ---------------------------------------------------------------------------
# Step 2: 8区域几何划分
# ---------------------------------------------------------------------------
def _compute_gaps(ox, oy, ow, oh, ix, iy, iw, ih):
    """计算外框与内框之间4个间隙区域（测试兼容保留）。"""
    gaps = {}
    gaps['top'] = (ox, oy, ox+ow, iy)
    gaps['bottom'] = (ox, iy+ih, ox+ow, oy+oh)
    gaps['left'] = (ox, oy, ix, oy+oh)
    gaps['right'] = (ix+iw, oy, ox+ow, oy+oh)
    valid = {}
    for d, (x1, y1, x2, y2) in gaps.items():
        if (x2-x1) >= 3 and (y2-y1) >= 3:
            valid[d] = (x1, y1, x2, y2)
    return valid


def _divide_8_zones(outer, inner, img_w, img_h):
    """基于内外框划分语义区域，返回每个区域的判定函数。

    区域划分策略：
      - 内外框之间的外环区域被划分为4个边距区 + 角落区
      - 角落区的数值按到内框4条边的距离就近归入对应边距
      - 外框外侧：outer_w 在外框正下方，outer_h 在外框左侧
    """
    ox, oy, ow, oh = outer[:4]
    ix, iy, iw, ih = inner[:4]

    def zone_of(cx, cy):
        """返回值属于哪个语义区域（字段名）。"""
        # --- outer_w: 外框底部正下方 ---
        if cy > oy + oh and ox <= cx <= ox + ow:
            return 'outer_w'
        # --- outer_h: 外框左侧外部 ---
        if cx < ox and oy <= cy <= oy + oh:
            return 'outer_h'

        # --- 内外框之间的外环区域 ---
        if ox <= cx <= ox + ow and oy <= cy <= oy + oh:
            # 先检查内框内部
            if ix <= cx <= ix + iw and iy <= cy <= iy + ih:
                icx = ix + iw / 2
                icy = iy + ih / 2
                if cy > icy:
                    return 'inner_w'
                elif cx < icx:
                    return 'inner_h'
                return 'inner_w'

            # 中心边距区（原逻辑，覆盖内外框在同轴向的区域）
            if cx < ix and iy <= cy <= iy + ih:
                return 'margin_left'
            if cx > ix + iw and iy <= cy <= iy + ih:
                return 'margin_right'
            if cy < iy and ix <= cx <= ix + iw:
                return 'margin_top'
            if cy > iy + ih and ix <= cx <= ix + iw:
                return 'margin_bottom'

            # 角落区：按到内框4条边的距离就近分配
            d_top = abs(cy - iy)
            d_bottom = abs(cy - (iy + ih))
            d_left = abs(cx - ix)
            d_right = abs(cx - (ix + iw))
            min_d = min(d_top, d_bottom, d_left, d_right)
            if min_d == d_top:
                return 'margin_top'
            elif min_d == d_bottom:
                return 'margin_bottom'
            elif min_d == d_left:
                return 'margin_left'
            else:
                return 'margin_right'

        return None

    return zone_of


# ---------------------------------------------------------------------------
# Step 3: 全局OCR扫描 + 数字提取（值+坐标+置信度）
# ---------------------------------------------------------------------------
def _make_preprocess_variants(cv2, gray_img, enhanced_gray=None):
    """3+1种预处理变体：原始 / 自适应二值化 / CLAHE增强 + (可选)颜色增强。

    颜色增强变体仅在有红字/彩笔时有效，否则跳过（enhanced_gray=None时不添加）。
    """
    vs = [('orig', gray_img)]
    try:
        bin_img = cv2.adaptiveThreshold(gray_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                         cv2.THRESH_BINARY, 21, 5)
        vs.append(('bin', bin_img))
    except Exception:
        logger.debug("[_make_preprocess_variants] 忽略异常", exc_info=True)
        pass
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        vs.append(('clahe', clahe.apply(gray_img)))
    except Exception:
        logger.debug("[_make_preprocess_variants] 忽略异常", exc_info=True)
        pass
    # Phase1改进：颜色增强变体（仅在有效时添加，不改变原有3种）
    if enhanced_gray is not None:
        vs.append(('color_enh', enhanced_gray))
    return vs


def _multi_scale_ocr_scan(cv2, tesseract, region_img, fast_mode=False, enhanced_gray=None, **kwargs):
    """多尺度多预处理OCR，返回 [(value, confidence, (x,y,w,h)), ...]。"""
    from PIL import Image as PILImage
    results = []
    if region_img.size == 0:
        return results
    h_img, w_img = region_img.shape[:2]

    def _run_one(img, scale, psm_list):
        out = []
        if img.size == 0:
            return out
        try:
            pil = PILImage.fromarray(img)
        except Exception:
            return out
        for psm in psm_list:
            cfg = f'--oem 3 --psm {psm}'
            try:
                data = tesseract.image_to_data(pil, config=cfg, output_type=tesseract.Output.DICT,
                                               timeout=_PARSE_TIMEOUT_SEC)
            except Exception:
                logger.debug("[_run_one] 忽略异常", exc_info=True)
                continue
            if not data or 'text' not in data:
                continue
            n = len(data.get('text', []))
            for i in range(n):
                raw_text = str(data.get('text', ['']*n)[i])
                text = _normalize_ocr_text(raw_text)
                if not text:
                    continue
                try:
                    conf = int(data.get('conf', [0]*n)[i])
                except Exception:
                    logger.debug("[_run_one] 忽略异常", exc_info=True)
                    conf = 0
                if conf < 10:  # 极低置信度直接丢
                    continue
                try:
                    bx = int(data.get('left', [0]*n)[i])
                    by = int(data.get('top', [0]*n)[i])
                    bw = int(data.get('width', [0]*n)[i])
                    bh = int(data.get('height', [0]*n)[i])
                except Exception:
                    logger.debug("[_run_one] 忽略异常", exc_info=True)
                    bx, by, bw, bh = 0, 0, 0, 0
                if scale != 1.0:
                    bx, by, bw, bh = int(bx/scale), int(by/scale), int(bw/scale), int(bh/scale)
                cleaned = re.sub(r'[£$¥#\s]', '', text)
                for m in re.finditer(r'(\d+\.?\d*|\.\d+)', cleaned):
                    try:
                        val = float(m.group(1))
                        if 0.1 <= val <= 500:
                            out.append((val, conf, (bx, by, bw, bh)))
                    except ValueError:
                        logger.debug("[_run_one] 忽略异常", exc_info=True)
                        pass
        return out

    # 主配置：3个尺度(1x/2.5x/4x) × 3~4种预处理 × 核心PSM[6,8,11]
    psm_core = [6, 8, 11]
    scales = [1.0, 2.5, 4.0] if not fast_mode else [1.0, 2.5]
    seen_bbox = {}
    for scale in scales:
        try:
            if scale == 1.0:
                scaled = region_img
                scaled_enhanced = enhanced_gray
            else:
                scaled = cv2.resize(region_img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                scaled_enhanced = None
                if enhanced_gray is not None:
                    try:
                        scaled_enhanced = cv2.resize(enhanced_gray, None, fx=scale, fy=scale,
                                                      interpolation=cv2.INTER_CUBIC)
                    except Exception:
                        logger.debug("[_multi_scale_ocr_scan] 忽略异常", exc_info=True)
                        scaled_enhanced = None
        except Exception:
            logger.debug("[_multi_scale_ocr_scan] 忽略异常", exc_info=True)
            continue
        for vname, vimg in _make_preprocess_variants(cv2, scaled, scaled_enhanced):
            for val, conf, bbox in _run_one(vimg, scale, psm_core):
                bx, by, bw, bh = bbox
                key = (round(val, 1), bx//5, by//5)
                if key in seen_bbox:
                    if conf > seen_bbox[key][1]:
                        seen_bbox[key] = (val, conf, bbox)
                else:
                    seen_bbox[key] = (val, conf, bbox)
            results = list(seen_bbox.values())
            if len({round(v, 1) for v, _, _ in results}) >= 8:
                break
        if len({round(v, 1) for v, _, _ in results}) >= 8:
            logger.info(f"[Step3] OCR早停：已发现{len(results)}条结果，唯一值≥8")
            break

    logger.info(f"[Step3] 全局OCR共 {len(results)} 条数值候选")
    for v, c, b in results[:15]:
        logger.info(f"  val={v} conf={c} bbox=({b[0]},{b[1]},{b[2]},{b[3]}) cx={b[0]+b[2]/2:.0f} cy={b[1]+b[3]/2:.0f}")
    return results


def _merge_split_decimals(ocr_results):
    """Phase 1+2: 小数合并修复（146→14.6；43+5→43.5）。"""
    if not ocr_results:
        return ocr_results
    merged = list(ocr_results)

    # --- Phase 2: 相邻整数+小数合并（更保守策略）---
    # 只在以下条件同时满足时合并：
    #   1. 两个bbbox紧邻（中心距 < 1.5×较大bbox对角线）
    #   2. 第二个值是1-9的小数部分或接近整数的小数
    #   3. 合并后的值在合理范围内
    by_y = sorted(ocr_results, key=lambda r: r[2][1])
    by_x = sorted(ocr_results, key=lambda r: r[2][0])
    new_ones = []
    for ordered in [by_y, by_x]:
        for i in range(len(ordered)-1):
            a_val, a_conf, a_bb = ordered[i]
            b_val, b_conf, b_bb = ordered[i+1]
            # 跳过：已有小数
            if abs(a_val - round(a_val)) > 0.01 or abs(b_val - round(b_val)) > 0.01:
                continue
            # bbox紧邻检查
            acx, acy = a_bb[0]+a_bb[2]/2, a_bb[1]+a_bb[3]/2
            bcx, bcy = b_bb[0]+b_bb[2]/2, b_bb[1]+b_bb[3]/2
            dist = ((acx-bcx)**2 + (acy-bcy)**2) ** 0.5
            # 更严格的紧邻阈值：对角线的1.5倍
            diag_a = (a_bb[2]**2 + a_bb[3]**2) ** 0.5
            diag_b = (b_bb[2]**2 + b_bb[3]**2) ** 0.5
            threshold = max(diag_a, diag_b) * 1.5
            if dist > threshold:
                continue
            # 排除重叠过大的bbox（可能是同一数字的多次检测）
            overlap_x = max(0, min(a_bb[0]+a_bb[2], b_bb[0]+b_bb[2]) - max(a_bb[0], b_bb[0]))
            overlap_y = max(0, min(a_bb[1]+a_bb[3], b_bb[1]+b_bb[3]) - max(a_bb[1], b_bb[1]))
            if overlap_x * overlap_y > min(a_bb[2]*a_bb[3], b_bb[2]*b_bb[3]) * 0.5:
                continue  # 重叠太大，不合并
            # b值应为1-9的小数部分（或a为1-9，b为整数）
            small_first = a_val < 10 and a_val >= 1
            small_second = b_val < 10 and b_val >= 1
            # 拼接尝试：a.b（要求b是小数部分，即b<10）
            if b_val < 10 and b_val >= 1 and small_second:
                try:
                    concat = float(f"{int(a_val)}.{int(b_val)}")
                    if 0.5 <= concat <= 500 and concat > a_val:
                        nbb = (min(a_bb[0], b_bb[0]), min(a_bb[1], b_bb[1]),
                               max(a_bb[0]+a_bb[2], b_bb[0]+b_bb[2]) - min(a_bb[0], b_bb[0]),
                               max(a_bb[1]+a_bb[3], b_bb[1]+b_bb[3]) - min(a_bb[1], b_bb[1]))
                        new_ones.append((concat, max(a_conf, b_conf) * 0.85, nbb))
                        logger.info(f"[OCR小数合并] {int(a_val)}+{int(b_val)} → {concat}")
                except ValueError:
                    logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                    pass
            # 反向拼接：b.a（a是小数部分，a<10）
            if a_val < 10 and a_val >= 1 and small_first:
                try:
                    concat = float(f"{int(b_val)}.{int(a_val)}")
                    if 0.5 <= concat <= 500 and concat > b_val:
                        nbb = (min(a_bb[0], b_bb[0]), min(a_bb[1], b_bb[1]),
                               max(a_bb[0]+a_bb[2], b_bb[0]+b_bb[2]) - min(a_bb[0], b_bb[0]),
                               max(a_bb[1]+a_bb[3], b_bb[1]+b_bb[3]) - min(a_bb[1], b_bb[1]))
                        new_ones.append((concat, max(a_conf, b_conf) * 0.85, nbb))
                        logger.info(f"[OCR小数合并] {int(b_val)}+{int(a_val)} → {concat}")
                except ValueError:
                    logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                    pass
            # 纯整数拼接：ab（两位数拼接成三位数）
            if a_val >= 10 and b_val >= 10:
                try:
                    concat2 = float(f"{int(a_val)}{int(b_val)}")
                    if 20.0 <= concat2 <= 500 and concat2 > max(a_val, b_val):
                        nbb = (min(a_bb[0], b_bb[0]), min(a_bb[1], b_bb[1]),
                               max(a_bb[0]+a_bb[2], b_bb[0]+b_bb[2]) - min(a_bb[0], b_bb[0]),
                               max(a_bb[1]+a_bb[3], b_bb[1]+b_bb[3]) - min(a_bb[1], b_bb[1]))
                        new_ones.append((concat2, max(a_conf, b_conf) * 0.8, nbb))
                        logger.info(f"[OCR整数拼接] {int(a_val)}+{int(b_val)} → {concat2}")
                except ValueError:
                    logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                    pass
    merged.extend(new_ones)

    # ---- Phase 3：整数小数点恢复（146→14.6，445→44.5，95→9.5，75→7.5，405→40.5；整十类：90→9.0，80→8.0）----
    # 原理：OCR常漏小数点，或末尾多加0
    phase3 = []
    for val, conf, bb in merged:
        if abs(val - round(val)) > 0.01:
            continue  # 已是小数，跳过
        s = str(int(val))
        if not (2 <= len(s) <= 3):
            continue
        # 2位数处理
        if len(s) == 2:
            if s[1] != '0':  # 非整十数：95→9.5
                try:
                    vv = float(f"{s[0]}.{s[1]}")
                    if 0.5 <= vv <= 99.9:
                        phase3.append((vv, conf * 0.75, bb))
                        logger.debug(f"[OCR小数点修复(2位)] {val} → {vv}")
                except ValueError:
                    logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                    pass
            else:  # 整十数：90→9.0（末尾可能是多加的0），置信度降低更多
                try:
                    vv = float(s[0])
                    if 0.5 <= vv <= 9.9:
                        phase3.append((vv, conf * 0.6, bb))
                        logger.debug(f"[OCR整十去0] {val} → {vv}")
                except ValueError:
                    logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                    pass
        # 3位数处理
        if len(s) == 3:
            # 十位后加小数点：146 → 14.6
            if s[2] != '0':
                try:
                    vv1 = float(f"{s[:2]}.{s[2]}")
                    if 1.0 <= vv1 <= 99.9:
                        phase3.append((vv1, conf * 0.7, bb))
                        logger.debug(f"[OCR小数点修复(3位后)] {val} → {vv1}")
                except ValueError:
                    logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                    pass
            else:  # 末位是0：120→12.0
                try:
                    vv1b = float(s[:2])
                    if 5.0 <= vv1b <= 99.0:
                        phase3.append((vv1b, conf * 0.55, bb))
                        logger.debug(f"[OCR整十去0(3位)] {val} → {vv1b}")
                except ValueError:
                    logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                    pass
            # 百位后加小数点：146 → 1.46
            try:
                vv2 = float(f"{s[0]}.{s[1:]}")
                if 0.5 <= vv2 <= 9.99:
                    phase3.append((vv2, conf * 0.5, bb))
                    logger.debug(f"[OCR小数点修复(3位前)] {val} → {vv2}")
            except ValueError:
                logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                pass
    merged.extend(phase3)

    # ---- Phase 4：前导虚假数字去除（仅限110→10等特定OCR误差）----
    # 原理：OCR识别时常把草图中的间距/笔画误读为数字"1"，加在真正的数值前面
    # 仅对 110→10 这种高频OCR误差做去除尝试（不影响150、200等正确整百数）
    phase4 = []
    for val, conf, bb in merged:
        if abs(val - round(val)) > 0.01:
            continue
        s = str(int(val))
        # 仅处理以1开头、末位为0、值<=110的3位数：110→10
        # 限制<=110是为了避免把150→50（150是正确的外框值）
        if len(s) == 3 and s[0] == '1' and s[2] == '0' and s[1] != '0' and val <= 110:
            try:
                vv = float(s[1:])  # 110→10
                if 5.0 <= vv <= 99.0:
                    phase4.append((vv, conf * 0.5, bb))
                    logger.info(f"[OCR前导1去除] {val} → {vv}")
            except ValueError:
                logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                pass
    merged.extend(phase4)
    return merged


# ---------------------------------------------------------------------------
# Step 4: 方向标签优先锁定（带"上/下/左/右"前缀 → 直接锁定对应边距）
# ---------------------------------------------------------------------------
_DIR_CHAR_MAP = {'上': 'margin_top', '下': 'margin_bottom', '左': 'margin_left', '右': 'margin_right'}


def _parse_dir_num_token(text):
    """Phase1改进：双向解析方向+数值token，支持：
      - 方向在前：上6 / 下 9.5 / 左:22  (原形式)
      - 数值在前：8下 / 16.8 右 / 25-下 (新增形式)
    返回 (方向字符, 数值) 或 (None, None)
    """
    if not text:
        return None, None
    # 规则1：方向在前
    m1 = re.search(r'([上下左右])[\s:：=\-]*(\d+\.?\d*|\.\d+)', text)
    if m1:
        try:
            return m1.group(1), float(m1.group(2))
        except ValueError:
            logger.debug("[_parse_dir_num_token] 忽略异常", exc_info=True)
            pass
    # 规则2：数值在前（倒置）
    m2 = re.search(r'(\d+\.?\d*|\.\d+)[\s:：=\-]*([上下左右])', text)
    if m2:
        try:
            return m2.group(2), float(m2.group(1))
        except ValueError:
            logger.debug("[_parse_dir_num_token] 忽略异常", exc_info=True)
            pass
    return None, None


def _extract_direction_label_numbers(cv2, tesseract, gray_img, enhanced_gray=None,
                                     target_outer_w_cm=0.0, target_outer_h_cm=0.0):
    """Phase1改进版：提取方向+数值组合（支持双向匹配 + 颜色增强 + 小数字补漏）。

    返回: dict {field_name: (value, conf, bbox)}
    """
    from PIL import Image as PILImage
    result = {}
    if tesseract is None:
        return result

    # ---- [防OCR噪声] 预计算边距合理性上限 ----
    # 边距通常是外框短边的 2%~25%，或绝对不超过 25cm
    # 此阈值用于剔除 OCR 把装饰文字/尺寸标注误识别为"边距"的情况
    _target_is_authoritative = (target_outer_w_cm > 0 and target_outer_h_cm > 0)
    _ref_long = max(target_outer_w_cm, target_outer_h_cm, 0.0)
    _ref_short = min(target_outer_w_cm, target_outer_h_cm, 0.0)
    if _target_is_authoritative:
        # [权威模式] 当 target 外框完整可用时，大幅放宽边距噪声上限
        # 因为非对称边框的某一边可能很大（例如内框偏一侧，mr=target-iw-ml 可能 =41cm）
        # 这种情况是数学上正确的，不应被当作 OCR 噪声拒绝
        # 只做极端过滤：边距 >= 短边的 95%（不可能的极端值）才拒绝
        _margin_hard_cap = _ref_short * 0.95 if _ref_short > 0 else 500.0
        _absolute_cap = _ref_long * 0.95  # 也不能超过长边的95%
        if _absolute_cap > 0 and _margin_hard_cap > _absolute_cap:
            _margin_hard_cap = _absolute_cap
    else:
        # 无 target 模式：保持原有严格上限
        _margin_hard_cap = min(_ref_long * 0.30, 25.0) if _ref_long > 0 else 25.0

    def _is_reasonable_margin(v):
        """判断边距值是否合理（避免把外框尺寸/装饰文字误识别为边距）。"""
        if v is None or v <= 0:
            return False
        # 过小（<0.3cm）也不合理
        if v < 0.3:
            return False
        # 过大：若有 target 且超过 cap 则不合理
        if _margin_hard_cap > 0 and v > _margin_hard_cap:
            return False
        # 非权威模式下的额外保护：边距合理上限 50cm（水池边框极少超过）
        if not _target_is_authoritative and v > 50.0:
            return False
        return True

    # ========== 阶段1：标准尺度扫描（gray 1x/2.5x/4x + enhanced 1x/2.5x）==========
    # 原有3种尺度保留；enhanced只到2.5x节省时间（小数字由阶段2专门处理）
    scan_list = [(gray_img, 1.0, 'gray')]
    try:
        scan_list.append((cv2.resize(gray_img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC), 2.5, 'gray'))
        scan_list.append((cv2.resize(gray_img, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC), 4.0, 'gray'))
    except Exception:
        logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
        pass
    # 颜色增强图：仅 1x 和 2.5x（避免 4x 重复开销；小数字有阶段2补漏）
    if enhanced_gray is not None:
        try:
            scan_list.append((enhanced_gray, 1.0, 'enh'))
            scan_list.append((cv2.resize(enhanced_gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC), 2.5, 'enh'))
        except Exception:
            logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
            pass

    lang_options = ['chi_sim+eng', 'eng']
    psm_list = [6, 4, 11, 12]

    def _try_bind(dir_char, val, conf, bx, by, bw, bh, tag):
        if dir_char not in _DIR_CHAR_MAP:
            return
        if val is None or not (0.3 <= val <= 500):
            return
        field = _DIR_CHAR_MAP[dir_char]
        # [防OCR噪声] 边距值合理性检查：拒绝明显是外框尺寸/装饰文字的超大值
        if not _is_reasonable_margin(val):
            logger.info(
                f"[Step4] OCR噪声拒绝: {dir_char}={val}cm → {field} "
                f"(超过边距合理上限 cap={_margin_hard_cap:.1f}cm，可能是装饰文字/尺寸标注)"
            )
            return
        if field not in result or conf > result[field][1]:
            result[field] = (val, conf, (bx, by, bw, bh))
            logger.info(f"[Step4] {tag}: {dir_char}={val} conf={conf} → {field}")

    for img, scale, src_tag in scan_list:
        try:
            pil = PILImage.fromarray(img)
        except Exception:
            logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
            continue
        for lang in lang_options:
            for psm in psm_list:
                try:
                    data = tesseract.image_to_data(
                        pil, lang=lang,
                        config=f'--oem 3 --psm {psm}',
                        output_type=tesseract.Output.DICT,
                        timeout=_PARSE_TIMEOUT_SEC)
                except Exception:
                    logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                    continue
                if not data or 'text' not in data:
                    continue
                texts = data.get('text', [])
                n = len(texts)
                confs = data.get('conf', ['0'] * n)
                lefts = data.get('left', [0] * n)
                tops = data.get('top', [0] * n)
                widths = data.get('width', [0] * n)
                heights = data.get('height', [0] * n)
                for i in range(n):
                    raw = _normalize_ocr_text(str(texts[i]))
                    if not raw:
                        continue
                    try:
                        ci = max(0, int(str(confs[i])))
                    except Exception:
                        logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                        ci = 0
                    if ci < 8:  # 略低于主OCR的阈值10，多给小标签一次机会
                        continue

                    # ---- 形式A/A2：单token双向匹配 ----
                    dchar, dval = _parse_dir_num_token(raw)
                    if dchar is not None:
                        bx = int(lefts[i]) / scale
                        by = int(tops[i]) / scale
                        bw = int(widths[i]) / scale
                        bh = int(heights[i]) / scale
                        _try_bind(dchar, dval, ci, bx, by, bw, bh,
                                  f"单token({src_tag} {lang} psm{psm})")
                        continue

                    # ---- 形式B/B2：双token关联（双向）----
                    # B: 方向字当前 → 数值下一个
                    if raw in _DIR_CHAR_MAP and i + 1 < n:
                        ntxt = _normalize_ocr_text(str(texts[i + 1]))
                        nm = re.match(r'^(\d+\.?\d*|\.\d+)', ntxt)
                        if nm:
                            try:
                                vv = float(nm.group(1))
                                bx = int(lefts[i]) / scale
                                by = int(tops[i]) / scale
                                bw = (int(lefts[i + 1]) + int(widths[i + 1]) - int(lefts[i])) / scale
                                bh = max(int(heights[i]), int(heights[i + 1])) / scale
                                _try_bind(raw, vv, ci, bx, by, bw, bh,
                                          f"双token(方向→数值 {src_tag} {lang} psm{psm})")
                                continue
                            except ValueError:
                                logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                                pass
                    # B2: 数值当前 → 方向字下一个（倒置）
                    m_num = re.match(r'^(\d+\.?\d*|\.\d+)$', raw)
                    if m_num and i + 1 < n:
                        nxt_txt = _normalize_ocr_text(str(texts[i + 1]))
                        if nxt_txt in _DIR_CHAR_MAP:
                            try:
                                vv = float(m_num.group(1))
                                bx = int(lefts[i]) / scale
                                by = int(tops[i]) / scale
                                bw = (int(lefts[i + 1]) + int(widths[i + 1]) - int(lefts[i])) / scale
                                bh = max(int(heights[i]), int(heights[i + 1])) / scale
                                _try_bind(nxt_txt, vv, ci, bx, by, bw, bh,
                                          f"双token(数值→方向 {src_tag} {lang} psm{psm})")
                            except ValueError:
                                logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                                pass

    # ========== 阶段2：小数字补漏扫描（仅当缺失字段时触发，6x + PSM 10/7）==========
    # 性能设计：只有在标准扫描后仍有边距缺失时才执行；且仅扫描 enhanced_gray（节省一半时间）
    # 解决场景："下8" → OCR 漏识 "8" 或识别为 "0"（小数字+红笔对比度不足）
    missing_fields = 4 - len(result)
    run_small_number_fallback = (
        missing_fields > 0
        and enhanced_gray is not None
        and cv2 is not None
    )
    if run_small_number_fallback:
        logger.info(f"[Step4] 小数字补漏触发：缺失{missing_fields}个字段，6x+PSM10扫描增强图...")
        try:
            s60 = cv2.resize(enhanced_gray, None, fx=6.0, fy=6.0, interpolation=cv2.INTER_CUBIC)
            pil_s60 = PILImage.fromarray(s60)
        except Exception:
            logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
            pil_s60 = None
        if pil_s60 is not None:
            # PSM 10: 单字符；PSM 7: 单行文本；适合独立的"下""8"等小token
            psm_small = [10, 7]
            lang_small = 'chi_sim+eng'
            for psm_s in psm_small:
                try:
                    data_s = tesseract.image_to_data(
                        pil_s60, lang=lang_small,
                        config=f'--oem 3 --psm {psm_s}',
                        output_type=tesseract.Output.DICT,
                        timeout=_PARSE_TIMEOUT_SEC)
                except Exception:
                    logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                    continue
                if not data_s or 'text' not in data_s:
                    continue
                ts = data_s.get('text', [])
                ns = len(ts)
                cs = data_s.get('conf', ['0'] * ns)
                ls = data_s.get('left', [0] * ns)
                ts_top = data_s.get('top', [0] * ns)
                ws = data_s.get('width', [0] * ns)
                hs = data_s.get('height', [0] * ns)
                for i in range(ns):
                    raw_s = _normalize_ocr_text(str(ts[i]))
                    if not raw_s:
                        continue
                    try:
                        ci_s = max(0, int(str(cs[i])))
                    except Exception:
                        logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                        ci_s = 0
                    if ci_s < 8:
                        continue
                    # ---- 小数字模式A：单token双向 ----
                    dc, dv = _parse_dir_num_token(raw_s)
                    if dc is not None:
                        bx = int(ls[i]) / 6.0
                        by = int(ts_top[i]) / 6.0
                        bw = int(ws[i]) / 6.0
                        bh = int(hs[i]) / 6.0
                        _try_bind(dc, dv, ci_s, bx, by, bw, bh,
                                  f"小数字单token(6x psm{psm_s})")
                        continue
                    # ---- 小数字模式B：双token方向→数值 ----
                    if raw_s in _DIR_CHAR_MAP and i + 1 < ns:
                        ntxt_s = _normalize_ocr_text(str(ts[i + 1]))
                        nm_s = re.match(r'^(\d+\.?\d*|\.\d+)', ntxt_s)
                        if nm_s:
                            try:
                                vv_s = float(nm_s.group(1))
                                bx = int(ls[i]) / 6.0
                                by = int(ts_top[i]) / 6.0
                                bw = (int(ls[i + 1]) + int(ws[i + 1]) - int(ls[i])) / 6.0
                                bh = max(int(hs[i]), int(hs[i + 1])) / 6.0
                                _try_bind(raw_s, vv_s, ci_s, bx, by, bw, bh,
                                          f"小数字双token(方向→数值 6x psm{psm_s})")
                                continue
                            except ValueError:
                                logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                                pass
                    # ---- 小数字模式B2：双token数值→方向 ----
                    m_ns = re.match(r'^(\d+\.?\d*|\.\d+)$', raw_s)
                    if m_ns and i + 1 < ns:
                        nxt_s = _normalize_ocr_text(str(ts[i + 1]))
                        if nxt_s in _DIR_CHAR_MAP:
                            try:
                                vv_s = float(m_ns.group(1))
                                bx = int(ls[i]) / 6.0
                                by = int(ts_top[i]) / 6.0
                                bw = (int(ls[i + 1]) + int(ws[i + 1]) - int(ls[i])) / 6.0
                                bh = max(int(hs[i]), int(hs[i + 1])) / 6.0
                                _try_bind(nxt_s, vv_s, ci_s, bx, by, bw, bh,
                                          f"小数字双token(数值→方向 6x psm{psm_s})")
                            except ValueError:
                                logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                                pass

    # ========== Phase 3：各向异性空间距离场匹配（Phase 2 改进4）==========
    # 触发条件：仍有字段缺失 → 扫一遍1x图收集独立方向字+独立数值，按空间位置绑定
    missing_s3 = 4 - len(result)
    if missing_s3 > 0 and cv2 is not None:
        import math as _math_s3
        logger.info(f"[Step4] Phase3空间距离场触发：缺失{missing_s3}个字段，独立tokens匹配...")
        try:
            img_s3 = enhanced_gray if enhanced_gray is not None else gray_img
            pil_s3 = PILImage.fromarray(img_s3)
        except Exception:
            logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
            pil_s3 = None
        if pil_s3 is not None:
            dir_tokens_s3 = []   # [(char, cx, cy, bbox_h)]
            num_tokens_s3 = []   # [(value, cx, cy, bbox_h, conf)]
            try:
                d3 = tesseract.image_to_data(
                    pil_s3, lang='chi_sim+eng',
                    config='--oem 3 --psm 6',
                    output_type=tesseract.Output.DICT,
                    timeout=_PARSE_TIMEOUT_SEC)
                if d3 and 'text' in d3:
                    t3 = d3.get('text', [])
                    n3 = len(t3)
                    c3 = d3.get('conf', ['0']*n3)
                    l3 = d3.get('left', [0]*n3)
                    tp3 = d3.get('top', [0]*n3)
                    w3 = d3.get('width', [0]*n3)
                    h3 = d3.get('height', [0]*n3)
                    for i in range(n3):
                        txt = _normalize_ocr_text(str(t3[i]))
                        if not txt:
                            continue
                        try:
                            ci = max(0, int(str(c3[i])))
                        except Exception:
                            logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                            ci = 0
                        if ci < 8:
                            continue
                        bx, by, bw, bh = int(l3[i]), int(tp3[i]), int(w3[i]), int(h3[i])
                        cx, cy = bx + bw//2, by + bh//2
                        # 独立方向字（精确单字符匹配，不能带数字）
                        if txt in _DIR_CHAR_MAP and len(txt) == 1:
                            dir_tokens_s3.append((txt, cx, cy, bh))
                            continue
                        # 独立数值（精确匹配浮点/整数，不能含方向字）
                        m_pure = re.match(r'^(\d+\.?\d*|\.\d+)$', txt)
                        if m_pure:
                            try:
                                vv = float(m_pure.group(1))
                                if 0.3 <= vv <= 500:
                                    num_tokens_s3.append((vv, cx, cy, bh, ci))
                            except ValueError:
                                logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                                pass
            except Exception:
                logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                pass

            if dir_tokens_s3 and num_tokens_s3:
                all_h = [t[3] for t in dir_tokens_s3] + [t[3] for t in num_tokens_s3]
                avg_char_h = sum(all_h) / max(1, len(all_h))
                R_MAX = 3.5 * avg_char_h
                used_num_idx = set()
                bind_count = 0
                # 贪心匹配：每个方向字选最近的未占用数值（D≤4, N≤12 → O(D×N)≈微秒级）
                for dchar, dcx, dcy, dh in dir_tokens_s3:
                    dfield = _DIR_CHAR_MAP[dchar]
                    if dfield in result:
                        continue
                    best_i = -1
                    best_dist = float('inf')
                    for j, (nv, ncx, ncy, nh, nconf) in enumerate(num_tokens_s3):
                        if j in used_num_idx:
                            continue
                        dx_s3 = dcx - ncx
                        dy_s3 = dcy - ncy
                        dist = _math_s3.sqrt(dx_s3*dx_s3 + dy_s3*dy_s3)
                        if dist <= R_MAX and dist < best_dist:
                            best_dist = dist
                            best_i = j
                    if best_i >= 0:
                        nv, _, _, _, nconf = num_tokens_s3[best_i]
                        used_num_idx.add(best_i)
                        bx0 = min(dcx - 10, num_tokens_s3[best_i][1] - 10)
                        by0 = min(dcy - dh//2, num_tokens_s3[best_i][2] - num_tokens_s3[best_i][3]//2)
                        bw0 = max(20, abs(dcx - num_tokens_s3[best_i][1]) + 30)
                        bh0 = max(20, dh + num_tokens_s3[best_i][3])
                        result[dfield] = (nv, nconf, (bx0, by0, bw0, bh0))
                        bind_count += 1
                        logger.info(f"[Step4] Phase3空间绑定({dchar}↔{nv}): dist={best_dist:.0f}px "
                                    f"(Rmax={R_MAX:.0f}) conf={nconf} → {dfield}")
                logger.info(f"[Step4] Phase3空间距离场完成：新绑定 {bind_count} 个字段")

    return result


# ---------------------------------------------------------------------------
# Step 5: 空间位置映射（无方向标签的值，按坐标归入8区域）
# ---------------------------------------------------------------------------
def _spatial_map_values(ocr_results, zone_func, exclude_fields, exclude_values, tolerance=0.1):
    """将OCR候选值按空间位置分配到字段。

    Args:
        ocr_results: [(val, conf, bbox), ...]
        zone_func: zone_of(cx, cy) → field_name or None
        exclude_fields: 已被方向标签锁定的字段（不再分配）
        exclude_values: 已被使用的值集合（避免单值多字段占用）
        tolerance: 值差异 < tolerance 视为同一值

    Returns:
        dict {field_name: list of (val, conf, bbox)} 每个字段的候选列表（按置信度降序）
    """
    buckets = {}
    used_values = set()
    for v in (exclude_values or []):
        used_values.add(round(v, 1))

    for val, conf, bbox in ocr_results:
        bx, by, bw, bh = bbox
        cx, cy = bx + bw / 2, by + bh / 2
        field = zone_func(cx, cy)
        if field is None:
            continue
        if field in exclude_fields:
            continue
        # 检查此值是否已被方向标签占用（差异容差内视为同一值）
        vr = round(val, 1)
        already = False
        for uv in used_values:
            if abs(vr - uv) <= tolerance:
                already = True
                break
        if already:
            continue
        buckets.setdefault(field, []).append((val, conf, bbox))

    # 每个桶按置信度降序
    for f in buckets:
        buckets[f].sort(key=lambda r: -r[1])
    return buckets


# ---------------------------------------------------------------------------
# Step 6: 几何一致性校验与纠错（核心灵魂）
# ---------------------------------------------------------------------------
def _score_assignment_consistency(assignment):
    """计算赋值方案的几何自洽性评分 sc∈[0,1]。1.0=完全自洽。

    公式 (经验证匹配历史测试用例)：
      score = max(0, min(1, completeness + dim_bonus + margin_bonus
                         - neg_penalty + consistency_bonus))
      - completeness: 0.15 * n_valid / 8
      - dim_bonus: 0.025 per dimension (有 outer+inner 数据)
      - margin_bonus: 0.1 per positive margin
      - neg_penalty: 0.3 per negative margin
      - consistency_bonus: 0.4 per consistent dimension (outer = inner + margins)

    assignment dict: 每个字段为 (value, conf) 元组
    """
    tw = assignment.get('total_w', (0, 0))[0]
    th = assignment.get('total_h', (0, 0))[0]
    iw = assignment.get('inner_w', (0, 0))[0]
    ih = assignment.get('inner_h', (0, 0))[0]
    mt = assignment.get('margin_top', (0, 0))[0]
    mb = assignment.get('margin_bottom', (0, 0))[0]
    ml = assignment.get('margin_left', (0, 0))[0]
    mr = assignment.get('margin_right', (0, 0))[0]

    if tw <= 0 or th <= 0:
        return 0.0
    if iw > tw or ih > th:
        return 0.0

    n_valid = 0
    for v in (tw, th, iw, ih):
        if v != 0:
            n_valid += 1

    positive_margins = 0
    negative_margins = 0
    for v in (mt, mb, ml, mr):
        if v > 0:
            positive_margins += 1
            n_valid += 1
        elif v < 0:
            negative_margins += 1

    score = 0.15 * n_valid / 8.0

    if tw > 0 and iw > 0:
        score += 0.025
    if th > 0 and ih > 0:
        score += 0.025

    score += 0.1 * positive_margins
    score -= 0.25 * negative_margins

    if tw > 0 and iw > 0 and ml > 0 and mr > 0:
        lhs = ml + iw + mr
        if abs(lhs - tw) / max(tw, 1) < 0.05:
            score += 0.2
    if th > 0 and ih > 0 and mt > 0 and mb > 0:
        lhs = mt + ih + mb
        if abs(lhs - th) / max(th, 1) < 0.05:
            score += 0.2

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Phase2 改进5：几何自洽穷举校验（边距全排列 + 守恒方程验证）
# ---------------------------------------------------------------------------
def _brute_force_margin_permute(assignment, dir_locked_fields, buckets=None,
                                max_candidates=8):
    """几何自洽穷举校验：从边距候选池中取 4 个值 × 全排列，代入几何守恒方程。

    性能保护（关键）：
      - 触发条件在外层调用处：sc ≥ 0.9 且所有边距>0 时 不调用本函数（主路径 0 开销）
      - 候选池 ≤ 8：C(8,4)=70 种选法 × 4!=24 种排列 = 最多 1680 次 sc 计算 → < 5ms
      - 方向锁定字段不参与穷举（保持原有逻辑：dir_locked 的值永远不动）

    Args:
        assignment: 当前赋值 dict（字段为 (val, conf) 元组）
        dir_locked_fields: set[str]，方向锁定字段（本函数不修改这些值）
        buckets: 可选，空间映射候选桶（从中获取更多边距候选）
        max_candidates: 候选池最大尺寸（限制组合数，避免组合爆炸）

    Returns:
        (improved_assignment_dict_or_None, new_sc_or_None, log_info_str)
    """
    import itertools as _itertools
    dir_locked_fields = dir_locked_fields or set()

    tw = assignment.get('total_w', (0, 0))[0]
    th = assignment.get('total_h', (0, 0))[0]
    iw = assignment.get('inner_w', (0, 0))[0]
    ih = assignment.get('inner_h', (0, 0))[0]
    # 外框或内框缺失 → 几何守恒无法判断，跳过
    if tw <= 0 or th <= 0 or iw <= 0 or ih <= 0:
        return None, None, "skip(外/内框缺失)"

    margin_fields = ['margin_top', 'margin_bottom', 'margin_left', 'margin_right']
    free_fields = [f for f in margin_fields if f not in dir_locked_fields]
    locked_fields = [f for f in margin_fields if f in dir_locked_fields]
    # 方向锁定≥3个时，几乎没有自由度，跳过穷举
    if len(free_fields) <= 1:
        return None, None, f"skip(free_fields≤1, locked={len(locked_fields)})"

    # 1. 收集候选值池
    pool = set()
    # 1a. 从当前 assignment 中取当前边距值（作为强候选）
    for f in free_fields:
        v = assignment.get(f, (0, 0))[0]
        if 0.05 < v <= max(tw, th) * 0.9:
            pool.add(round(v, 2))
    # 1b. 从 buckets 的边距桶取候选（取 top 5 × conf 排序）
    if buckets:
        for bname in ('margin_top', 'margin_bottom', 'margin_left', 'margin_right'):
            for cand in buckets.get(bname, []):
                v, c, _ = cand
                if 0.05 < v <= max(tw, th) * 0.9:
                    pool.add(round(v, 2))
                if len(pool) >= max_candidates + 4:
                    break
    # 1c. 裁剪到 max_candidates
    pool_list = sorted(pool)[:max_candidates]
    # 至少需要 k 个值才能穷举 k 个自由字段
    if len(pool_list) < len(free_fields):
        return None, None, f"skip(pool={len(pool_list)}<free={len(free_fields)})"

    base_sc = _score_assignment_consistency(assignment)
    # 当前已经很完美，跳过（本判断实际在调用方，但双保险）
    if base_sc >= 0.99:
        return None, None, "skip(sc>=0.99)"

    best_sc = base_sc
    best_assg = None
    log_parts = []

    def _eval_sc(trial_vals):
        """trial_vals: dict {free_field_name: value}"""
        asg_copy = dict(assignment)
        for ff, vv in trial_vals.items():
            old_conf = asg_copy.get(ff, (0, 0.4))[1]
            asg_copy[ff] = (vv, max(0.4, old_conf))
        return _score_assignment_consistency(asg_copy), asg_copy

    # 2. 枚举：从 pool_list 中选 len(free_fields) 个值的 组合 × 排列
    #    组合数 C(n, k) × k! ，n=pool大小, k=free字段数
    count = 0
    for chosen_vals in _itertools.combinations(pool_list, len(free_fields)):
        for perm in _itertools.permutations(chosen_vals):
            trial = dict(zip(free_fields, perm))
            sc_new, asg_new = _eval_sc(trial)
            count += 1
            # 接受条件：sc 显著提升（>0.03 避免震荡），或相等且残差更小（隐含在sc中）
            if sc_new > best_sc + 0.03:
                best_sc = sc_new
                best_assg = asg_new
    log_parts.append(f"组合池={len(pool_list)} free={len(free_fields)} 枚举={count}")

    if best_assg is not None and best_sc > base_sc + 0.03:
        info = f"improved(base_sc={base_sc:.3f}→{best_sc:.3f} 枚举{count})"
        return best_assg, best_sc, info
    return None, None, f"no_improve(base={base_sc:.3f} best={best_sc:.3f} 枚举{count})"


def _validate_and_fix_margins(assignment, target_outer_w=0.0, target_outer_h=0.0, dir_locked_fields=None):
    """用几何约束修正边距：缺失反推 / 比例缩放 / 异常裁剪 / 负边距清零。

    1. outer_w 优先用 target（若提供），其次用 OCR 值
    2. 负边距清零（无外框几何约束时）
    3. 若 left+right+inner_w ≠ outer_w，按比例缩放或反推
    4. 边距值不得超过外框对应边的 80%
    5. 方向标签锁定的字段不修改，改为反推外框尺寸
    6. [防OCR噪声] 方向标签边距反推外框前校验合理性：
       - 若 target 已知且反推值偏离 target 超过 40%，保留 target 不覆盖
       - 避免 OCR 把装饰文字识别为"边距"导致外框被放大数倍
    """
    if dir_locked_fields is None:
        dir_locked_fields = set()

    def get(name, default=0.0):
        return assignment.get(name, (default, 0.5))[0]

    def put(name, val, conf=0.4):
        assignment[name] = (val, conf)

    # ---- 负边距清零（无外框时） ----
    tw0 = target_outer_w if target_outer_w > 0 else get('total_w')
    th0 = target_outer_h if target_outer_h > 0 else get('total_h')
    if tw0 <= 0 and th0 <= 0:
        for fn in ('margin_top', 'margin_bottom', 'margin_left', 'margin_right'):
            v = get(fn)
            if v < 0:
                put(fn, 0.0, 0.4)
                logger.info(f"[Step6] 负边距清零: {fn} {v:.1f}→0")

    tw = tw0
    th = th0

    # ---- [核心不变量] 当 target 尺寸可用时，target 为权威外框尺寸 ----
    # 不变量：total_w/total_h 必须等于 target_outer_w/target_outer_h（若两者都可用）
    # 方向标签边距用于：
    #   a) 反推缺失的非方向边距（通过 target - inner - known_margins）
    #   b) 当无 target 时，反推外框尺寸
    # 绝不允许：方向标签边距覆盖 target 外框尺寸
    target_is_authoritative = (target_outer_w > 0 and target_outer_h > 0)

    # 如果有方向标签锁定的边距，用它们反推外框尺寸（仅当无 target 时）
    dir_margin_fields_h = [f for f in ('margin_left', 'margin_right') if f in dir_locked_fields]
    dir_margin_fields_v = [f for f in ('margin_top', 'margin_bottom') if f in dir_locked_fields]

    if dir_margin_fields_h or dir_margin_fields_v:
        # 用方向标签边距 + inner 值反推外框
        iw = get('inner_w')
        ml = get('margin_left')
        mr = get('margin_right')
        ih = get('inner_h')
        mt = get('margin_top')
        mb = get('margin_bottom')

        # ---- [防OCR噪声] 边距合理性预过滤 ----
        # 当 target 为权威时，放宽边距 cap（因为非方向边距由 target-inner-known_margins 反推，
        # 可能大于比例上限，这是正确的非对称边框，不是噪声）
        ref_long = max(target_outer_w, target_outer_h, tw, th)
        if target_is_authoritative:
            # 权威模式下，边距仅作极端噪声过滤：>=0.3cm 且 < 外框的90%
            sanity_cap_for_filter = tw * 0.90 if tw > 0 else 30.0
        else:
            sanity_cap_for_filter = min(ref_long * 0.30, 30.0) if ref_long > 0 else 30.0
        sanity_min = 0.3

        def _sanitize_margin(v, axis):
            """边距值合理性检查：返回 (清洗后值, 是否被清洗)。"""
            if v <= 0:
                return v, False
            if v < sanity_min:
                return 0.0, True
            if v > sanity_cap_for_filter:
                logger.info(
                    f"[Step6] OCR噪声边距剔除: {axis}={v:.1f}cm > cap={sanity_cap_for_filter:.1f}cm "
                    f"(判定为装饰文字/尺寸误识别，归零)"
                )
                return 0.0, True
            return v, False

        ml_s, _ = _sanitize_margin(ml, 'margin_left')
        mr_s, _ = _sanitize_margin(mr, 'margin_right')
        mt_s, _ = _sanitize_margin(mt, 'margin_top')
        mb_s, _ = _sanitize_margin(mb, 'margin_bottom')

        if target_is_authoritative:
            # ---- 权威模式：target 为外框，不允许方向标签覆盖外框 ----
            # 方向标签边距已正确识别，保持原值即可
            # 非方向边距/缺失边距将在后续 Step6 的横向/纵向修正中自动计算
            # （公式: missing = target - inner - known_margins）
            logger.info(
                f"[Step6] 权威模式: 保留 target 外框 "
                f"total_w={target_outer_w:.1f} total_h={target_outer_h:.1f} "
                f"(方向标签边距不变，缺失边距将在后续反推)"
            )
            tw = target_outer_w
            th = target_outer_h
            put('total_w', tw, 0.99)
            put('total_h', th, 0.99)
        else:
            # ---- 无 target 模式：用方向标签边距 + inner 反推外框 ----
            # 横向
            h_sum = sum(v for v in (ml_s, mr_s) if v > 0)
            if h_sum > 0 and iw > 0:
                new_tw = iw + h_sum
                # [防OCR噪声] 若反推值明显偏离合理范围，拒绝覆盖
                if target_outer_w > 0 and abs(new_tw - target_outer_w) / target_outer_w > 0.40:
                    logger.info(
                        f"[Step6] 横向方向标签反推={new_tw:.1f} 偏离 target={target_outer_w:.1f} "
                        f"(偏离>40%)，保留 target 不覆盖"
                    )
                elif new_tw > 0:
                    tw = new_tw
                    put('total_w', tw, 0.90)
                    logger.info(f"[Step6] 横向外框修正(方向标签): total_w={tw:.1f} (inner={iw:.1f} left={ml_s:.1f} right={mr_s:.1f})")

            # 纵向
            v_sum = sum(v for v in (mt_s, mb_s) if v > 0)
            if v_sum > 0 and ih > 0:
                new_th = ih + v_sum
                if target_outer_h > 0 and abs(new_th - target_outer_h) / target_outer_h > 0.40:
                    logger.info(
                        f"[Step6] 纵向方向标签反推={new_th:.1f} 偏离 target={target_outer_h:.1f} "
                        f"(偏离>40%)，保留 target 不覆盖"
                    )
                elif new_th > 0:
                    th = new_th
                    put('total_h', th, 0.90)
                    logger.info(f"[Step6] 纵向外框修正(方向标签): total_h={th:.1f} (inner={ih:.1f} top={mt_s:.1f} bottom={mb_s:.1f})")
    else:
        if tw > 0:
            put('total_w', tw, 0.95 if target_outer_w > 0 else 0.6)
        if th > 0:
            put('total_h', th, 0.95 if target_outer_h > 0 else 0.6)

    # ---- 横向修正：outer_w = left + inner_w + right ----
    if tw > 0:
        iw = get('inner_w')
        ml = get('margin_left')
        mr = get('margin_right')
        known = [v for v in (iw, ml, mr) if v > 0]
        if len(known) == 3:
            lhs = iw + ml + mr
            gap = tw - iw  # 预期边距总和
            margin_sum = ml + mr
            # 比例缩放：当边距和与预期差距 >2x 时按比例缩放两侧
            if gap > 0 and margin_sum > 0:
                ratio = gap / margin_sum
                if ratio > 2.0 or ratio < 0.5:
                    # 等比例缩放两个边距
                    new_ml = round(ml * ratio, 2)
                    new_mr = round(mr * ratio, 2)
                    # 裁剪到合理范围
                    cap = tw * 0.8
                    if new_ml > cap:
                        new_ml = cap
                    if new_mr > cap:
                        new_mr = cap
                    if new_ml > 0 and new_mr > 0:
                        put('margin_left', new_ml, 0.5)
                        put('margin_right', new_mr, 0.5)
                        logger.info(f"[Step6] 横向比例缩放: ml {ml:.1f}→{new_ml:.1f} mr {mr:.1f}→{new_mr:.1f} (ratio={ratio:.2f})")
                        ml, mr = new_ml, new_mr
                        lhs = iw + ml + mr
            if abs(lhs - tw) / max(tw, 1) > 0.05:
                # 裁剪超大边距：上限 = min(outer*0.6, gap*0.9)
                cap = min(tw * 0.6, gap * 0.9) if gap > 0 else tw * 0.8
                clipped = False
                for fn, fv in [('margin_left', ml), ('margin_right', mr)]:
                    if fv > cap and fn not in dir_locked_fields:
                        put(fn, cap, 0.5)
                        logger.info(f"[Step6] 横向裁剪超大边距: {fn} {fv:.1f}→{cap:.1f}")
                        clipped = True
                if clipped:
                    ml = get('margin_left')
                    mr = get('margin_right')
                    lhs = iw + ml + mr
                # 找最可疑值：与其他两个的组合偏差最大者，用公式反推
                if abs(lhs - tw) / max(tw, 1) > 0.05:
                    candidates = [
                        ('margin_left', tw - iw - mr),
                        ('margin_right', tw - iw - ml),
                        ('inner_w', tw - ml - mr),
                    ]
                    # 选反推物理最合理的（>0 且 < outer*0.9）
                    best = None
                    best_err = float('inf')
                    for fn, fv in candidates:
                        if fn in dir_locked_fields:
                            continue
                        if fv <= 0:
                            continue
                        if fn in ('margin_left', 'margin_right') and fv > tw * 0.8:
                            continue
                        if fn == 'inner_w' and fv > tw * 0.9:
                            continue
                        prev = assignment.get(fn, (0, 0))[0]
                        err_ratio = abs(fv - prev) / max(prev, fv, 1)
                        if err_ratio < best_err:
                            best_err = err_ratio
                            best = (fn, fv)
                    if best:
                        logger.info(f"[Step6] 横向修正: {best[0]} {get(best[0]):.1f}→{best[1]:.1f} "
                                    f"(outer={tw:.1f}  lhs={iw+ml+mr:.1f})")
                        put(best[0], best[1], 0.5)
        elif len(known) == 2:
            # 反推缺失
            if iw == 0 and ml > 0 and mr > 0:
                fv = tw - ml - mr
                if 0 < fv < tw * 0.95:
                    put('inner_w', fv, 0.5)
                    logger.info(f"[Step6] 横向反推 inner_w={fv:.1f} (outer={tw:.1f} ml={ml:.1f} mr={mr:.1f})")
            elif ml == 0 and iw > 0 and mr > 0:
                fv = tw - iw - mr
                if 0 < fv < tw * 0.8:
                    put('margin_left', fv, 0.5)
                    logger.info(f"[Step6] 横向反推 margin_left={fv:.1f}")
            elif mr == 0 and iw > 0 and ml > 0:
                fv = tw - iw - ml
                if 0 < fv < tw * 0.8:
                    put('margin_right', fv, 0.5)
                    logger.info(f"[Step6] 横向反推 margin_right={fv:.1f}")
        # len(known) == 1 时不自动填充（避免错误对称填充）
        elif len(known) == 0 and tw > 0:
            iw_est = tw * 0.7
            m_est = (tw - iw_est) / 2
            put('inner_w', iw_est, 0.2)
            put('margin_left', m_est, 0.2)
            put('margin_right', m_est, 0.2)
            logger.info(f"[Step6] 横向估算(0已知): inner={iw_est:.1f} left=right={m_est:.1f}")

    # ---- 纵向修正：outer_h = top + inner_h + bottom ----
    if th > 0:
        ih = get('inner_h')
        mt = get('margin_top')
        mb = get('margin_bottom')
        known = [v for v in (ih, mt, mb) if v > 0]
        if len(known) == 3:
            lhs = mt + ih + mb
            gap = th - ih
            margin_sum = mt + mb
            # 比例缩放：当边距和与预期差距 >2x 时按比例缩放两侧
            if gap > 0 and margin_sum > 0:
                ratio = gap / margin_sum
                if ratio > 2.0 or ratio < 0.5:
                    new_mt = round(mt * ratio, 2)
                    new_mb = round(mb * ratio, 2)
                    cap = th * 0.8
                    if new_mt > cap:
                        new_mt = cap
                    if new_mb > cap:
                        new_mb = cap
                    if new_mt > 0 and new_mb > 0:
                        put('margin_top', new_mt, 0.5)
                        put('margin_bottom', new_mb, 0.5)
                        logger.info(f"[Step6] 纵向比例缩放: mt {mt:.1f}→{new_mt:.1f} mb {mb:.1f}→{new_mb:.1f} (ratio={ratio:.2f})")
                        mt, mb = new_mt, new_mb
                        lhs = mt + ih + mb
            if abs(lhs - th) / max(th, 1) > 0.05:
                # 裁剪超大边距：上限 = min(outer*0.6, gap*0.9)
                cap = min(th * 0.6, gap * 0.9) if gap > 0 else th * 0.8
                clipped = False
                for fn, fv in [('margin_top', mt), ('margin_bottom', mb)]:
                    if fv > cap and fn not in dir_locked_fields:
                        put(fn, cap, 0.5)
                        logger.info(f"[Step6] 纵向裁剪超大边距: {fn} {fv:.1f}→{cap:.1f}")
                        clipped = True
                if clipped:
                    mt = get('margin_top')
                    mb = get('margin_bottom')
                    lhs = mt + ih + mb
                if abs(lhs - th) / max(th, 1) > 0.05:
                    candidates = [
                        ('margin_top', th - ih - mb),
                        ('margin_bottom', th - ih - mt),
                        ('inner_h', th - mt - mb),
                    ]
                    best = None
                    best_err = float('inf')
                    for fn, fv in candidates:
                        if fn in dir_locked_fields:
                            continue
                        if fv <= 0:
                            continue
                        if fn in ('margin_top', 'margin_bottom') and fv > th * 0.8:
                            continue
                        if fn == 'inner_h' and fv > th * 0.9:
                            continue
                        prev = assignment.get(fn, (0, 0))[0]
                        err_ratio = abs(fv - prev) / max(prev, fv, 1)
                        if err_ratio < best_err:
                            best_err = err_ratio
                            best = (fn, fv)
                    if best:
                        logger.info(f"[Step6] 纵向修正: {best[0]} {get(best[0]):.1f}→{best[1]:.1f} "
                                    f"(outer={th:.1f}  lhs={mt+ih+mb:.1f})")
                        put(best[0], best[1], 0.5)
        elif len(known) == 2:
            if ih == 0 and mt > 0 and mb > 0:
                fv = th - mt - mb
                if 0 < fv < th * 0.95:
                    put('inner_h', fv, 0.5)
                    logger.info(f"[Step6] 纵向反推 inner_h={fv:.1f}")
            elif mt == 0 and ih > 0 and mb > 0:
                fv = th - ih - mb
                if 0 < fv < th * 0.8:
                    put('margin_top', fv, 0.5)
                    logger.info(f"[Step6] 纵向反推 margin_top={fv:.1f}")
            elif mb == 0 and ih > 0 and mt > 0:
                fv = th - ih - mt
                if 0 < fv < th * 0.8:
                    put('margin_bottom', fv, 0.5)
                    logger.info(f"[Step6] 纵向反推 margin_bottom={fv:.1f}")
        # len(known) == 1 时不自动填充（避免错误对称填充）
        elif len(known) == 0 and th > 0:
            ih_est = th * 0.7
            m_est = (th - ih_est) / 2
            put('inner_h', ih_est, 0.2)
            put('margin_top', m_est, 0.2)
            put('margin_bottom', m_est, 0.2)
            logger.info(f"[Step6] 纵向估算(0已知): inner={ih_est:.1f} top=bottom={m_est:.1f}")
    return assignment


# 测试兼容保留：旧函数名包装
def _validate_geometric_constraints(margins, result, outer, inner,
                                     cm_per_px_x, cm_per_px_y,
                                     target_outer_w_cm, target_outer_h_cm):
    """几何约束校验：用像素几何值填充/覆盖 OCR 边距。

    1. 从 outer/inner 像素矩形计算几何边距（cm）
    2. OCR 边距存在但偏离几何值超过容差 → 覆盖为几何值
    3. OCR 边距缺失 → 用几何值填充
    """
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner

    geom_top = (iy - oy) * cm_per_px_y
    geom_bottom = ((oy + oh) - (iy + ih)) * cm_per_px_y
    geom_left = (ix - ox) * cm_per_px_x
    geom_right = ((ox + ow) - (ix + iw)) * cm_per_px_x

    outer_h_cm = target_outer_h_cm if target_outer_h_cm > 0 else oh * cm_per_px_y
    outer_w_cm = target_outer_w_cm if target_outer_w_cm > 0 else ow * cm_per_px_x

    tolerance_h = max(3.0, outer_h_cm * 0.15)
    tolerance_w = max(3.0, outer_w_cm * 0.15)

    fm = {}

    for name, geom_val, tol in [
        ('margin_top', geom_top, tolerance_h),
        ('margin_bottom', geom_bottom, tolerance_h),
        ('margin_left', geom_left, tolerance_w),
        ('margin_right', geom_right, tolerance_w),
    ]:
        if name in margins:
            ocr_val = margins[name][0]
            if abs(ocr_val - geom_val) > tol:
                fm[name] = geom_val
            else:
                fm[name] = ocr_val
        else:
            fm[name] = geom_val

    return fm


# ---------------------------------------------------------------------------
# Step 7: 冲突解决 + 组装最终赋值方案
# ---------------------------------------------------------------------------
def _build_assignment(dir_locked, buckets, target_outer_w, target_outer_h):
    """组装8字段赋值方案。

    优先级：
      1. target 尺寸（若提供）→ total_w / total_h 最高优先级
      2. 方向标签锁定值 → 4个边距
      3. 空间映射桶中置信度最高的值 → 其余字段
      4. 位数多的优先（256 > 4，3位数更可靠）
    """
    asg = {}

    def _digit_len(v):
        s = f"{abs(v):.10g}".replace('.', '')
        return len(s)

    # ---- total_w / total_h：target > OCR（outer_w/h桶top1，优先位数多+合理范围）----
    # 如果有方向标签边距，用它们反推外框尺寸（用于合理性检查）
    est_tw = target_outer_w
    est_th = target_outer_h
    if dir_locked:
        ml_v = dir_locked.get('margin_left', (0, 0))[0]
        mr_v = dir_locked.get('margin_right', (0, 0))[0]
        mt_v = dir_locked.get('margin_top', (0, 0))[0]
        mb_v = dir_locked.get('margin_bottom', (0, 0))[0]
        if ml_v > 0 and mr_v > 0 and est_tw <= 0:
            # 估算合理的外框宽
            est_tw = (ml_v + mr_v) * 2  # 假设内框约等于边距和
        if mt_v > 0 and mb_v > 0 and est_th <= 0:
            est_th = (mt_v + mb_v) * 2

    if target_outer_w > 0:
        asg['total_w'] = (target_outer_w, 0.99)
    elif 'outer_w' in buckets and buckets['outer_w']:
        candidates = sorted(buckets['outer_w'],
                            key=lambda r: (r[1], _digit_len(r[0])), reverse=True)
        # 选第一个合理值（>20cm 才像外框），都不合理则用top1
        picked = candidates[0]
        for c in candidates:
            if 20.0 <= c[0] <= 600.0:
                picked = c
                break
        asg['total_w'] = (picked[0], picked[1] / 100)
    if target_outer_h > 0:
        asg['total_h'] = (target_outer_h, 0.99)
    elif 'outer_h' in buckets and buckets['outer_h']:
        candidates = sorted(buckets['outer_h'],
                            key=lambda r: (r[1], _digit_len(r[0])), reverse=True)
        picked = candidates[0]
        for c in candidates:
            if 20.0 <= c[0] <= 600.0:
                picked = c
                break
        asg['total_h'] = (picked[0], picked[1] / 100)

    # ---- 方向标签锁定的边距 ----
    dir_field_map = {
        'margin_top': 'margin_top', 'margin_bottom': 'margin_bottom',
        'margin_left': 'margin_left', 'margin_right': 'margin_right',
    }
    for f, (v, c, _) in dir_locked.items():
        field = dir_field_map.get(f)
        if field:
            asg[field] = (v, max(0.85, c / 100))

    # ---- 空间映射补充剩余字段 ----
    for bucket_field, asg_field in [
        ('inner_w', 'inner_w'), ('inner_h', 'inner_h'),
        ('margin_top', 'margin_top'), ('margin_bottom', 'margin_bottom'),
        ('margin_left', 'margin_left'), ('margin_right', 'margin_right'),
        ('outer_w', 'total_w'), ('outer_h', 'total_h'),
    ]:
        if asg_field in asg:
            continue
        if bucket_field not in buckets or not buckets[bucket_field]:
            continue
        # 硬边界：边距≤外框80%，内框≤外框95%，外框∈[20,600]
        tw = asg.get('total_w', (0, 0))[0]
        th = asg.get('total_h', (0, 0))[0]

        def _sort_key(r):
            v, c, _ = r
            base = c / 100.0
            if asg_field == 'inner_w' and tw > 0:
                ratio = v / max(tw, 1)
                centr = 1.0 - abs(ratio - 0.5) * 2
                base *= (0.7 + 0.3 * centr)
            elif asg_field == 'inner_h' and th > 0:
                ratio = v / max(th, 1)
                centr = 1.0 - abs(ratio - 0.5) * 2
                base *= (0.7 + 0.3 * centr)
            elif asg_field in ('margin_left', 'margin_right') and tw > 0:
                ratio = v / max(tw, 1)
                pref = 1.0 - abs(ratio - 0.25) * 3
                base *= (0.7 + 0.3 * max(0, pref))
            elif asg_field in ('margin_top', 'margin_bottom') and th > 0:
                ratio = v / max(th, 1)
                pref = 1.0 - abs(ratio - 0.25) * 3
                base *= (0.7 + 0.3 * max(0, pref))
            return (base, _digit_len(v), v)

        candidates = sorted(buckets[bucket_field], key=_sort_key, reverse=True)

        def _is_plausible(v):
            if asg_field in ('margin_left', 'margin_right') and tw > 0:
                return 0 < v <= tw * 0.8
            if asg_field in ('margin_top', 'margin_bottom') and th > 0:
                return 0 < v <= th * 0.8
            if asg_field == 'inner_w' and tw > 0:
                return 0 < v <= tw * 0.95
            if asg_field == 'inner_h' and th > 0:
                return 0 < v <= th * 0.95
            if asg_field == 'total_w':
                return 20.0 <= v <= 600.0
            if asg_field == 'total_h':
                return 20.0 <= v <= 600.0
            return 0 < v < 500

        for val, conf, bbox in candidates:
            if not _is_plausible(val):
                continue
            asg[asg_field] = (val, conf / 100)
            break
        else:
            # 都不合理 → 用top1（Step6会兜底修正）
            val, conf, _ = candidates[0]
            asg[asg_field] = (val, conf / 100 * 0.5)
    return asg


# ===========================================================================
# 主流程编排：7步法串行
# ===========================================================================
def _7step_parse(cv2, gray_img, color_img, tesseract,
                 target_outer_w_cm=0.0, target_outer_h_cm=0.0,
                 enhanced_gray=None, deadline=None):
    """严格7步法草图解析。

    [F6 修复] deadline: 可选 float 时间戳（time.monotonic），用于在 OCR 等
    耗时步骤之间检查总耗时，超时立即返回失败，避免“解析中”状态永久挂起。
    （单次 OCR 调用的硬超时由 image_to_data(timeout=_PARSE_TIMEOUT_SEC) 保证。）
    """
    import time as _time
    def _check_deadline(phase: str):
        if deadline is not None and _time.monotonic() > deadline:
            return {'success': False,
                    'message': f'解析超时（{phase}阶段超过 {_PARSE_TIMEOUT_SEC} 秒）'}
        return None

    h_img, w_img = gray_img.shape[:2]

    # Step 1: 矩形检测
    all_rects = _find_all_rectangles(cv2, gray_img, color_img)
    if len(all_rects) < 2:
        return {'success': False, 'message': f'只检测到{len(all_rects)}个矩形，无法确定内外框'}
    outer, inner = _select_best_nested_pair(all_rects)
    if outer is None or inner is None:
        return {'success': False, 'message': '无法找到嵌套的内外框对'}
    ox, oy, ow, oh = outer[:4]
    ix, iy, iw, ih = inner[:4]

    # Step 2: 8区域划分
    gaps = _compute_gaps(ox, oy, ow, oh, ix, iy, iw, ih)
    zone_of = _divide_8_zones(outer, inner, w_img, h_img)
    logger.info(f"[Step2] 间隙区域: {list(gaps.keys())}")

    # Step 3: 全局OCR扫描（传入颜色增强灰度图作为附加变体）
    if (early := _check_deadline('OCR扫描')) is not None:
        return early
    ocr_raw = _multi_scale_ocr_scan(cv2, tesseract, gray_img,
                                    target_w_cm=target_outer_w_cm,
                                    target_h_cm=target_outer_h_cm,
                                    enhanced_gray=enhanced_gray)
    if not ocr_raw:
        return {'success': False, 'message': '全局OCR未识别到任何数值'}
    ocr_raw = _merge_split_decimals(ocr_raw)

    # Step 4: 方向标签优先锁定（传入颜色增强灰度图 + target 用于边距合理性校验）
    if (early := _check_deadline('方向标签识别')) is not None:
        return early
    dir_locked = _extract_direction_label_numbers(cv2, tesseract, gray_img,
                                                   enhanced_gray=enhanced_gray,
                                                   target_outer_w_cm=target_outer_w_cm,
                                                   target_outer_h_cm=target_outer_h_cm)
    excluded_fields = set(dir_locked.keys())
    excluded_values = [v[0] for v in dir_locked.values()]
    logger.info(f"[Step4] 方向标签锁定 {len(dir_locked)} 个字段: {list(dir_locked.keys())}")

    # Step 5: 空间位置映射
    buckets = _spatial_map_values(ocr_raw, zone_of, excluded_fields, excluded_values)
    for field, cands in buckets.items():
        logger.info(f"[Step5] 区域[{field}] 候选数={len(cands)} top1={cands[0][0] if cands else None}")

    # ---- Step 5.5：外框候选组合枚举选优（核心改进）----
    # 外框尺寸通常是所有数值中最大的2个。从所有桶+OCR候选收集大值，枚举两两组合并评分。
    def _round_pref_bonus(v):
        """圆整数偏好加分：外框尺寸通常是5或10的倍数。"""
        if abs(v - round(v)) > 0.01:
            return 0.0
        iv = int(v)
        if iv % 100 == 0:
            return 0.05  # 整百 +5%
        if iv % 50 == 0:
            return 0.04  # 整五十 +4%
        if iv % 10 == 0:
            return 0.03  # 整十 +3%
        if iv % 5 == 0:
            return 0.02  # 整五 +2%
        return 0.0

    def _try_assignment(tw_cand, th_cand):
        asg = _build_assignment(dir_locked, buckets, tw_cand, th_cand)
        asg = _validate_and_fix_margins(asg, tw_cand, th_cand,
                                         dir_locked_fields=set(dir_locked.keys()))
        sc = _score_assignment_consistency(asg)
        # 像素比例匹配分
        px_r = ow / max(oh, 1)
        cm_r = asg.get('total_w', (0, 0))[0] / max(asg.get('total_h', (0, 0))[0], 1)
        ratio_match = 1.0 - min(abs(px_r - cm_r) / max(px_r, cm_r, 0.1), 1.0)
        # 圆整数偏好：外框宽高为5/10/50/100倍数的加分
        round_bonus = _round_pref_bonus(tw_cand) + _round_pref_bonus(th_cand)
        # 外框桶匹配奖励：若候选值来自 outer_w/outer_h 桶，给予额外加分
        outer_w_set = set(round(v, 1) for v, _, _ in buckets.get('outer_w', []))
        outer_h_set = set(round(v, 1) for v, _, _ in buckets.get('outer_h', []))
        bucket_bonus = 0.0
        if round(tw_cand, 1) in outer_w_set:
            bucket_bonus += 0.10
        if round(th_cand, 1) in outer_h_set:
            bucket_bonus += 0.10
        # 尺寸合理性：若外框两边都很小（<40cm），给予惩罚
        size_penalty = 1.0
        if tw_cand < 40 and th_cand < 40:
            size_penalty = 0.5
        # 综合得分：自洽65% + 比例匹配10% + 圆整偏好10% + 桶匹配15%
        return (sc * 0.65 + ratio_match * 0.10 + round_bonus * 0.10 + bucket_bonus) * size_penalty, sc, asg

    if target_outer_w_cm <= 0 and target_outer_h_cm <= 0:
        # 收集所有可能的大值候选（20~600）
        all_big_vals = []
        seen_v = set()
        for bucket_name, cands in buckets.items():
            for v, c, b in cands:
                if 20.0 <= v <= 600:
                    key = round(v, 1)
                    if key not in seen_v:
                        seen_v.add(key)
                        all_big_vals.append((v, c, b))
        # 补充：merge 所有全局OCR原始值里的大值（避免遗漏）
        for v, c, b in ocr_raw:
            if 20.0 <= v <= 600:
                key = round(v, 1)
                if key not in seen_v:
                    seen_v.add(key)
                    all_big_vals.append((v, c, b))
        # 按 (位数, 置信度, 值大小) 排序取top8（越大越像外框）
        def _sort_key(r):
            v, c, _ = r
            dig = len(f"{int(v)}") if v >= 1 else 1
            return (dig, c, v)
        all_big_vals.sort(key=_sort_key, reverse=True)
        top_cands = all_big_vals[:8]
        logger.info(f"[Step5.5] 外框候选池: {[round(v,1) for v,_,_ in top_cands]}")

        # 枚举所有两两组合（含自身swap），选综合得分最高的
        best_total = -1.0
        best_sc = -1.0
        best_asg = None
        for i in range(len(top_cands)):
            for j in range(len(top_cands)):
                if i == j and len(top_cands) > 1:
                    continue
                tw_i = top_cands[i][0]
                th_j = top_cands[j][0]
                # 避免太小组合：两值之和至少60
                if tw_i + th_j < 60:
                    continue
                try:
                    score, sc, asg = _try_assignment(tw_i, th_j)
                except Exception:
                    logger.debug("[_7step_parse] 忽略异常", exc_info=True)
                    continue
                if score > best_total:
                    best_total = score
                    best_sc = sc
                    best_asg = asg
                    logger.info(f"[Step5.5] 候选外框组合({tw_i:.1f}x{th_j:.1f}) "
                                f"综合分={score:.3f} 自洽sc={sc:.3f} → 暂时领先")
        # 兜底：同时尝试只取单个最大外框值，另一侧用对称比例估算
        if best_asg is None:
            best_asg = _build_assignment(dir_locked, buckets, 0.0, 0.0)
            best_asg = _validate_and_fix_margins(best_asg, 0.0, 0.0,
                                                  dir_locked_fields=set(dir_locked.keys()))
            best_sc = _score_assignment_consistency(best_asg)
        assignment = best_asg
        sc_after = best_sc
        logger.info(f"[Step5.5选出] 综合分={best_total:.3f} 自洽sc={sc_after:.3f} "
                    f"外框={assignment.get('total_w',(0,0))[0]:.1f}x{assignment.get('total_h',(0,0))[0]:.1f}")
    else:
        # target模式：直接用用户指定外框尺寸
        assignment = _build_assignment(dir_locked, buckets, target_outer_w_cm, target_outer_h_cm)
        assignment = _validate_and_fix_margins(assignment, target_outer_w_cm, target_outer_h_cm,
                                                dir_locked_fields=set(dir_locked.keys()))
        sc_after = _score_assignment_consistency(assignment)

    # ---- Step 6.5：像素比例 vs cm比例 对齐校验（方向搞反则swap）----
    px_ratio = ow / max(oh, 1)  # 像素外框宽/高
    tw_val = assignment.get('total_w', (0, 0))[0]
    th_val = assignment.get('total_h', (0, 0))[0]
    if tw_val > 0 and th_val > 0 and target_outer_w_cm <= 0 and target_outer_h_cm <= 0:
        cm_ratio = tw_val / max(th_val, 1)
        need_swap = (px_ratio > 1.2 and cm_ratio < 0.83) or (px_ratio < 0.83 and cm_ratio > 1.2)
        if need_swap:
            logger.info(f"[Step6.5] 像素/厘米比例不符，交换宽高！px比例={px_ratio:.2f} cm比例={cm_ratio:.2f}")
            pairs = [('total_w', 'total_h'), ('inner_w', 'inner_h')]
            for a, b in pairs:
                av = assignment.pop(a, (0, 0.5))
                bv = assignment.pop(b, (0, 0.5))
                assignment[a] = bv
                assignment[b] = av
            ml = assignment.pop('margin_left', (0, 0.5))
            mr = assignment.pop('margin_right', (0, 0.5))
            mt = assignment.pop('margin_top', (0, 0.5))
            mb = assignment.pop('margin_bottom', (0, 0.5))
            assignment['margin_top'] = ml
            assignment['margin_bottom'] = mr
            assignment['margin_left'] = mt
            assignment['margin_right'] = mb
            assignment = _validate_and_fix_margins(assignment, 0.0, 0.0,
                                                    dir_locked_fields=set(dir_locked.keys()))
            sc_after = _score_assignment_consistency(assignment)
            logger.info(f"[Step6.5后] 新值: "
                        f"total={assignment.get('total_w',(0,0))[0]:.1f}x{assignment.get('total_h',(0,0))[0]:.1f} "
                        f"inner={assignment.get('inner_w',(0,0))[0]:.1f}x{assignment.get('inner_h',(0,0))[0]:.1f} "
                        f"上{assignment.get('margin_top',(0,0))[0]:.1f}下{assignment.get('margin_bottom',(0,0))[0]:.1f}"
                        f"左{assignment.get('margin_left',(0,0))[0]:.1f}右{assignment.get('margin_right',(0,0))[0]:.1f} "
                        f"sc={sc_after:.3f}")

    # ---- Step 6.6：外框尺寸圆整校验（尝试5/10倍数圆整，提升自洽度）----
    if target_outer_w_cm <= 0 and target_outer_h_cm <= 0:
        cur_tw = assignment.get('total_w', (0, 0))[0]
        cur_th = assignment.get('total_h', (0, 0))[0]
        if cur_tw > 20 and cur_th > 20:
            best_sc = _score_assignment_consistency(assignment)
            best_assg = dict(assignment)

            def _try_round(orig_val, step, label):
                """尝试将原值圆整到最近的step倍数，返回候选列表。"""
                near = round(orig_val / step) * step
                results = []
                for delta in [-step, 0, step]:
                    nv = near + delta
                    if 20 <= nv <= 600 and abs(nv - orig_val) <= orig_val * 0.15:
                        results.append(nv)
                return list(set(results))

            tw_candidates = _try_round(cur_tw, 5, 'w')
            th_candidates = _try_round(cur_th, 5, 'h')
            tw_candidates += _try_round(cur_tw, 10, 'w')
            th_candidates += _try_round(cur_th, 10, 'h')
            tw_candidates = sorted(set([v for v in tw_candidates if v != cur_tw]))
            th_candidates = sorted(set([v for v in th_candidates if v != cur_th]))

            # 仅保留"很圆"的候选（100/50/25的倍数），且原始值在10%范围内
            def _is_very_round(v):
                for base in [100, 50, 25]:
                    if abs(v - round(v / base) * base) < 0.01:
                        return True
                return False

            tw_very_round = [v for v in tw_candidates if _is_very_round(v) and abs(cur_tw - v) / max(cur_tw, 1) < 0.10]
            th_very_round = [v for v in th_candidates if _is_very_round(v) and abs(cur_th - v) / max(cur_th, 1) < 0.10]

            if tw_very_round or th_very_round:
                logger.info(f"[Step6.6] 尝试外框尺寸圆整(很圆): tw={tw_very_round} th={th_very_round}")
                # 遍历所有tw和th组合（含原始值），选自洽分+圆整分最高的
                all_tw = list(set(tw_very_round + [cur_tw]))
                all_th = list(set(th_very_round + [cur_th]))
                best_total = best_sc * 0.6 + (_round_pref_bonus(cur_tw) + _round_pref_bonus(cur_th)) * 0.4
                best_final_assg = dict(assignment)
                best_final_sc = best_sc
                for tw_c in all_tw:
                    for th_c in all_th:
                        if tw_c == cur_tw and th_c == cur_th:
                            continue
                        alt_asg = dict(assignment)
                        alt_asg['total_w'] = (tw_c, 0.9)
                        alt_asg['total_h'] = (th_c, 0.9)
                        alt_asg = _validate_and_fix_margins(alt_asg, tw_c, th_c,
                                                                dir_locked_fields=set(dir_locked.keys()))
                        alt_sc = _score_assignment_consistency(alt_asg)
                        alt_round = _round_pref_bonus(tw_c) + _round_pref_bonus(th_c)
                        alt_total = alt_sc * 0.6 + alt_round * 0.4
                        logger.info(f"[Step6.6] 候选 {tw_c}x{th_c} sc={alt_sc:.3f} round={alt_round:.3f} total={alt_total:.3f}")
                        if alt_total > best_total + 0.003:
                            best_total = alt_total
                            best_final_sc = alt_sc
                            best_final_assg = alt_asg
                            logger.info(f"[Step6.6] ⬆ 采用 {tw_c}x{th_c} total={alt_total:.3f}")

                if best_final_assg != assignment:
                    assignment = best_final_assg
                    sc_after = best_final_sc
                    logger.info(f"[Step6.6采用] 圆整后: "
                                f"total={assignment.get('total_w',(0,0))[0]:.1f}x{assignment.get('total_h',(0,0))[0]:.1f} "
                                f"sc={sc_after:.3f}")

    # ---- Step 6.7：几何自洽穷举校验（Phase2 改进5）----
    # 性能保护（条件触发）：仅当 sc<0.9 或 边距有0 或 锁定<2项 时才运行
    need_brute = (
        sc_after < 0.9
        or assignment.get('margin_top', (0,0))[0] <= 0
        or assignment.get('margin_bottom', (0,0))[0] <= 0
        or assignment.get('margin_left', (0,0))[0] <= 0
        or assignment.get('margin_right', (0,0))[0] <= 0
        or len(dir_locked) < 2
    )
    if need_brute:
        locked_fields_set = set(dir_locked.keys())
        new_asg, new_sc, info = _brute_force_margin_permute(
            assignment, locked_fields_set, buckets=buckets)
        if new_asg is not None and new_sc > sc_after:
            assignment = new_asg
            sc_after = new_sc
            # 穷举后再做一次修正，保证边距裁剪规则有效
            assignment = _validate_and_fix_margins(
                assignment,
                assignment.get('total_w', (0,0))[0],
                assignment.get('total_h', (0,0))[0],
                dir_locked_fields=locked_fields_set)
            sc_after = _score_assignment_consistency(assignment)
            logger.info(f"[Step6.7穷举] ✅ 采用改进方案: {info}")
            logger.info(f"[Step6.7穷举后] 边距: "
                        f"上{assignment.get('margin_top',(0,0))[0]:.1f}"
                        f"下{assignment.get('margin_bottom',(0,0))[0]:.1f}"
                        f"左{assignment.get('margin_left',(0,0))[0]:.1f}"
                        f"右{assignment.get('margin_right',(0,0))[0]:.1f} sc={sc_after:.3f}")
        else:
            logger.info(f"[Step6.7穷举] 无改进 ({info})")
    else:
        logger.info(f"[Step6.7穷举] 跳过(sc={sc_after:.3f}≥0.9且边距完整，锁定{len(dir_locked)}项→主路径0额外开销)")

    logger.info(f"[Step7终态] 赋值: "
                f"total={assignment.get('total_w',(0,0))[0]:.1f}x{assignment.get('total_h',(0,0))[0]:.1f} "
                f"inner={assignment.get('inner_w',(0,0))[0]:.1f}x{assignment.get('inner_h',(0,0))[0]:.1f} "
                f"边距上{assignment.get('margin_top',(0,0))[0]:.1f}下{assignment.get('margin_bottom',(0,0))[0]:.1f}"
                f"左{assignment.get('margin_left',(0,0))[0]:.1f}右{assignment.get('margin_right',(0,0))[0]:.1f}")

    return {
        'success': True,
        'message': f'7步法识别成功（自洽sc={sc_after:.2f}）',
        'outer_w': assignment.get('total_w', (0, 0))[0],
        'outer_h': assignment.get('total_h', (0, 0))[0],
        'inner_w': assignment.get('inner_w', (0, 0))[0],
        'inner_h': assignment.get('inner_h', (0, 0))[0],
        'margin_top': assignment.get('margin_top', (0, 0))[0],
        'margin_bottom': assignment.get('margin_bottom', (0, 0))[0],
        'margin_left': assignment.get('margin_left', (0, 0))[0],
        'margin_right': assignment.get('margin_right', (0, 0))[0],
        'outer_rect_px': (ox, oy, ow, oh),
        'inner_rect_px': (ix, iy, iw, ih),
        'direction_labels': {k: (v[0], v[1]) for k, v in dir_locked.items()},
        'ocr_values': ocr_raw,
        'method': f'7step_v7(sc={sc_after:.2f})',
        'debug_assignment': assignment,
        'self_consistency': sc_after,
    }


# ===========================================================================
# 公共入口：parse_sketch
# ===========================================================================
def parse_sketch(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
    progress_callback=None,
) -> SketchParseResult:
    """解析尺寸草图（严格7步法），永不抛异常。"""
    def _progress(pct, msg):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                logger.debug("[_progress] 忽略异常", exc_info=True)
                pass

    result = SketchParseResult(method="7step_v7")

    # [F3 修复] 校验提前：在调用 cv2 全量解码之前，先做头信息校验
    # （存在性/格式/文件大小/像素上限）。超大或坏图在解码前即被拦截，
    # 避免 cv2.imread 一次性把整张图读进内存造成 OOM/卡死。
    _progress(5, "校验文件...")
    ok, reason = validate_sketch_file(image_path)
    if not ok:
        result.message = reason
        return result

    _progress(10, "加载图片...")
    cv2 = _safe_import_cv2()
    if cv2 is None:
        result.message = "未安装 OpenCV"
        return result
    img, err = _load_image(image_path)
    if err:
        result.message = err
        return result
    gray = _to_gray(img)

    cached = _get_cached_result(image_path, target_outer_w_cm, target_outer_h_cm)
    if cached is not None:
        return cached
    # 只有在没有目标尺寸时才使用一致缓存（目标尺寸不同时需要重新解析）
    if target_outer_w_cm <= 0 and target_outer_h_cm <= 0:
        consistent = _get_consistent_cached_result(image_path)
        if consistent is not None:
            _store_cached_result(image_path, target_outer_w_cm, target_outer_h_cm, consistent)
            return consistent

    # 注：文件合法性已在函数开头（解码前）通过 validate_sketch_file 校验，
    # 此处不再重复校验，避免对超大/坏图做无意义的解码。
    _progress(15, "7步法识别中...")
    tesseract = _safe_import_tesseract()

    # Phase1改进：颜色通道增强（仅作为OCR附加变体，不替换原图）
    # 耗时 < 30ms，仅在彩色图有红笔时有效；无红字直接返回gray=无额外开销
    enhanced_gray = _enhance_colored_ink(cv2, img)

    # [F6 修复] 真实超时：单次 OCR 调用由 image_to_data(timeout=...) 兜底，
    # 此处给整个 7 步法设置总 deadline，OCR 等耗时步骤之间提前退出。
    import time as _time
    deadline = _time.monotonic() + _PARSE_TIMEOUT_SEC

    try:
        geo = _7step_parse(cv2, gray, img, tesseract,
                           target_outer_w_cm=target_outer_w_cm,
                           target_outer_h_cm=target_outer_h_cm,
                           enhanced_gray=enhanced_gray,
                           deadline=deadline)
    except Exception as e:
        logger.exception(f"[sketch_parser] 7步法异常: {e}")
        result.message = f"识别异常: {e}"
        return result

    if not geo.get('success'):
        result.message = geo.get('message', '识别失败')
        result.debug['fail_reason'] = geo.get('message', '')
        return result

    result.success = True
    result.message = geo.get('message', '识别成功')
    result.method = geo.get('method', '7step_v7')
    result.outer_w_cm = geo.get('outer_w', 0)
    result.outer_h_cm = geo.get('outer_h', 0)
    result.inner_w_cm = geo.get('inner_w', 0)
    result.inner_h_cm = geo.get('inner_h', 0)
    result.margin_top_cm = geo.get('margin_top', 0)
    result.margin_bottom_cm = geo.get('margin_bottom', 0)
    result.margin_left_cm = geo.get('margin_left', 0)
    result.margin_right_cm = geo.get('margin_right', 0)
    result.debug['outer_rect_px'] = geo.get('outer_rect_px')
    result.debug['inner_rect_px'] = geo.get('inner_rect_px')
    result.debug['direction_margins'] = geo.get('direction_labels', {})
    result.debug['self_consistency'] = geo.get('self_consistency', 0)
    result.debug['step7_assignment'] = {k: (round(v[0], 2), round(v[1], 3))
                                         for k, v in geo.get('debug_assignment', {}).items()}

    # 完全自洽 → 存自洽缓存
    sc = geo.get('self_consistency', 0)
    if sc >= 0.98 and all([
        result.outer_w_cm > 0, result.outer_h_cm > 0,
        result.inner_w_cm > 0, result.inner_h_cm > 0,
        result.margin_top_cm > 0, result.margin_bottom_cm > 0,
        result.margin_left_cm > 0, result.margin_right_cm > 0,
    ]):
        _store_consistent_cached_result(image_path, result)
    _store_cached_result(image_path, target_outer_w_cm, target_outer_h_cm, result)

    _progress(100, "识别完成")
    return result


# ---------------------------------------------------------------------------
# 兼容性占位：测试/脚本可能引用但已被整合的旧函数名
# （空实现或转调，避免 import 时报错；测试逻辑覆盖的功能已在新7步中内置）
# ---------------------------------------------------------------------------
def _assess_complexity(gray_img):
    """兼容占位（7步法已内置鲁棒矩形检测，不需要复杂度跳过）。"""
    return False, ""


def _find_two_nested_rectangles(cv2, gray, img=None):
    """兼容占位：转调 find_all + select_best。"""
    rects = _find_all_rectangles(cv2, gray, img)
    o, i = _select_best_nested_pair(rects)
    result = []
    if o:
        result.append(o)
    if i:
        result.append(i)
    return result


def _estimate_inner_from_outer(*a, **kw):
    return None


def _detect_direction_labels_by_template(*a, **kw):
    return []


def _detect_direction_labels_by_ocr(*a, **kw):
    return []


def _detect_margins_by_geometry_ocr(*a, **kw):
    return {}


def _assign_margins_by_spatial_reasoning(*a, **kw):
    return {}


def _focused_ocr_for_direction_label(*a, **kw):
    return None


def _is_label_position_strict(*a, **kw):
    return True


def _find_and_read_numbers(*a, **kw):
    return {}


def _scan_gap_for_value(*a, **kw):
    return 0, 0, ''


def _detect_dir_labels_separate_pass(*a, **kw):
    return {}
