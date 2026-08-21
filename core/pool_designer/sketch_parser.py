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
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_PARSE_TIMEOUT_SEC = 20
_ALGO_VERSION = 7  # 2026-08-20: 严格7步法重构版

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


def _safe_import_tesseract():
    try:
        import pytesseract
    except Exception as e:
        logger.info(f"[sketch_parser] pytesseract 未安装: {e}")
        return None
    exe_candidates = []
    if os.name == 'nt':
        base_dirs = [
            r'C:\Program Files\Tesseract-OCR',
            r'C:\Program Files (x86)\Tesseract-OCR',
            os.path.expanduser(r'~\AppData\Local\Programs\Tesseract-OCR'),
            r'D:\Tesseract-OCR', r'E:\Tesseract-OCR', r'F:\Tesseract-OCR', r'G:\Tesseract-OCR',
        ]
        for bd in base_dirs:
            exe_candidates.append(os.path.join(bd, 'tesseract.exe'))
    exe_candidates.append('tesseract')
    found_exe = None
    found_tessdata = None
    for exe in exe_candidates:
        if exe == 'tesseract':
            import shutil
            if shutil.which('tesseract'):
                found_exe = 'tesseract'
                break
            continue
        if os.path.isfile(exe):
            found_exe = exe
            td = os.path.join(os.path.dirname(exe), 'tessdata')
            if os.path.isdir(td):
                found_tessdata = td
            break
    if found_exe and found_exe != 'tesseract':
        try:
            pytesseract.pytesseract.tesseract_cmd = found_exe
            logger.info(f"[sketch_parser] 已配置 Tesseract: {found_exe}")
        except Exception:
            pass
    if found_tessdata:
        try:
            os.environ['TESSDATA_PREFIX'] = found_tessdata
        except Exception:
            pass
    try:
        _ = pytesseract.get_tesseract_version()
        return pytesseract
    except Exception as e:
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
        pass
    try:
        e = cv2.Canny(gray_img, 15, 80)
        masks.append(("canny", cv2.morphologyEx(e, cv2.MORPH_CLOSE, ks, iterations=1)))
    except Exception:
        pass
    try:
        m = cv2.adaptiveThreshold(gray_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, blockSize=21, C=5)
        masks.append(("adaptive", cv2.morphologyEx(m, cv2.MORPH_CLOSE, km, iterations=1)))
    except Exception:
        pass
    try:
        _, m = cv2.threshold(gray_img, 127, 255, cv2.THRESH_BINARY_INV)
        masks.append(("high127", cv2.morphologyEx(m, cv2.MORPH_CLOSE, ks, iterations=1)))
    except Exception:
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
def _make_preprocess_variants(cv2, gray_img):
    """3种预处理变体：原始 / 自适应二值化 / CLAHE增强。"""
    vs = [('orig', gray_img)]
    try:
        bin_img = cv2.adaptiveThreshold(gray_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                         cv2.THRESH_BINARY, 21, 5)
        vs.append(('bin', bin_img))
    except Exception:
        pass
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        vs.append(('clahe', clahe.apply(gray_img)))
    except Exception:
        pass
    return vs


def _multi_scale_ocr_scan(cv2, tesseract, region_img, fast_mode=False, **kwargs):
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
                data = tesseract.image_to_data(pil, config=cfg, output_type=tesseract.Output.DICT)
            except Exception:
                continue
            if not data or 'text' not in data:
                continue
            n = len(data.get('text', []))
            for i in range(n):
                text = str(data.get('text', ['']*n)[i]).strip()
                if not text:
                    continue
                try:
                    conf = int(data.get('conf', [0]*n)[i])
                except Exception:
                    conf = 0
                if conf < 10:  # 极低置信度直接丢
                    continue
                try:
                    bx = int(data.get('left', [0]*n)[i])
                    by = int(data.get('top', [0]*n)[i])
                    bw = int(data.get('width', [0]*n)[i])
                    bh = int(data.get('height', [0]*n)[i])
                except Exception:
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
                        pass
        return out

    # 主配置：3个尺度(1x/2.5x/4x) × 3种预处理 × 核心PSM[6,8]（简化：去掉大量tier）
    psm_core = [6, 8, 11]
    scales = [1.0, 2.5, 4.0] if not fast_mode else [1.0, 2.5]
    seen_bbox = {}
    for scale in scales:
        try:
            if scale == 1.0:
                scaled = region_img
            else:
                scaled = cv2.resize(region_img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        except Exception:
            continue
        for vname, vimg in _make_preprocess_variants(cv2, scaled):
            for val, conf, bbox in _run_one(vimg, scale, psm_core):
                # 同一bbox位置相同值去重，保留最高置信度
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
                    pass
            else:  # 整十数：90→9.0（末尾可能是多加的0），置信度降低更多
                try:
                    vv = float(s[0])
                    if 0.5 <= vv <= 9.9:
                        phase3.append((vv, conf * 0.6, bb))
                        logger.debug(f"[OCR整十去0] {val} → {vv}")
                except ValueError:
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
                    pass
            else:  # 末位是0：120→12.0
                try:
                    vv1b = float(s[:2])
                    if 5.0 <= vv1b <= 99.0:
                        phase3.append((vv1b, conf * 0.55, bb))
                        logger.debug(f"[OCR整十去0(3位)] {val} → {vv1b}")
                except ValueError:
                    pass
            # 百位后加小数点：146 → 1.46
            try:
                vv2 = float(f"{s[0]}.{s[1:]}")
                if 0.5 <= vv2 <= 9.99:
                    phase3.append((vv2, conf * 0.5, bb))
                    logger.debug(f"[OCR小数点修复(3位前)] {val} → {vv2}")
            except ValueError:
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
                pass
    merged.extend(phase4)
    return merged


# ---------------------------------------------------------------------------
# Step 4: 方向标签优先锁定（带"上/下/左/右"前缀 → 直接锁定对应边距）
# ---------------------------------------------------------------------------
_DIR_CHAR_MAP = {'上': 'margin_top', '下': 'margin_bottom', '左': 'margin_left', '右': 'margin_right'}


def _extract_direction_label_numbers(cv2, tesseract, gray_img):
    """从全局OCR的原始文本中提取"上6""左36""右112"这类方向+数值组合。

    返回: dict {field_name: (value, conf, bbox)}
    """
    from PIL import Image as PILImage
    result = {}
    if tesseract is None:
        return result
    scan_list = [(gray_img, 1.0)]
    try:
        s25 = cv2.resize(gray_img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        scan_list.append((s25, 2.5))
        s40 = cv2.resize(gray_img, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)
        scan_list.append((s40, 4.0))
    except Exception:
        pass

    # 语言配置：优先 chi_sim+eng（中文方向字+数字），失败回退 eng
    lang_options = ['chi_sim+eng', 'eng']
    psm_list = [6, 4, 11, 12]

    for img, scale in scan_list:
        try:
            pil = PILImage.fromarray(img)
        except Exception:
            continue
        for lang in lang_options:
            for psm in psm_list:
                try:
                    data = tesseract.image_to_data(
                        pil, lang=lang,
                        config=f'--oem 3 --psm {psm}',
                        output_type=tesseract.Output.DICT)
                except Exception:
                    continue
                if not data or 'text' not in data:
                    continue
                texts = data.get('text', [])
                n = len(texts)
                confs = data.get('conf', ['0']*n)
                lefts = data.get('left', [0]*n)
                tops = data.get('top', [0]*n)
                widths = data.get('width', [0]*n)
                heights = data.get('height', [0]*n)
                for i in range(n):
                    raw = str(texts[i]).strip()
                    if not raw:
                        continue
                    try:
                        ci = max(0, int(str(confs[i])))
                    except Exception:
                        ci = 0
                    # 形式A：单token "上6" "右112" "下9.5"
                    m = re.match(r'^([上下左右])\s*(\d+\.?\d*|\.\d+)', raw)
                    if not m:
                        # 形式B：双token "上" + "6"
                        if raw in _DIR_CHAR_MAP and i + 1 < n:
                            ntxt = str(texts[i+1]).strip()
                            nm = re.match(r'^(\d+\.?\d*|\.\d+)', ntxt)
                            if nm:
                                field = _DIR_CHAR_MAP[raw]
                                try:
                                    val = float(nm.group(1))
                                    if 0.3 <= val <= 500:
                                        bx = int(lefts[i])/scale
                                        by = int(tops[i])/scale
                                        bw = (int(lefts[i+1]) + int(widths[i+1]) - int(lefts[i]))/scale
                                        bh = max(int(heights[i]), int(heights[i+1]))/scale
                                        if field not in result or ci > result[field][1]:
                                            result[field] = (val, ci, (bx, by, bw, bh))
                                            logger.info(f"[Step4] 方向标签(双token {lang} psm{psm}): {raw}+{nm.group(1)}={val} → {field}")
                                except ValueError:
                                    pass
                        continue
                    dchar = m.group(1)
                    field = _DIR_CHAR_MAP[dchar]
                    try:
                        val = float(m.group(2))
                    except ValueError:
                        continue
                    if 0.3 <= val <= 500:
                        bx = int(lefts[i]) / scale
                        by = int(tops[i]) / scale
                        bw = int(widths[i]) / scale
                        bh = int(heights[i]) / scale
                        if field not in result or ci > result[field][1]:
                            result[field] = (val, ci, (bx, by, bw, bh))
                            logger.info(f"[Step4] 方向标签(单token {lang} psm{psm}): {raw}={val} → {field}")
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

    assignment dict: 每个字段为 (value, conf) 元组，字段名：
      total_w, total_h, inner_w, inner_h, margin_top, margin_bottom, margin_left, margin_right
    """
    tw = assignment.get('total_w', (0, 0))[0]
    th = assignment.get('total_h', (0, 0))[0]
    iw = assignment.get('inner_w', (0, 0))[0]
    ih = assignment.get('inner_h', (0, 0))[0]
    mt = assignment.get('margin_top', (0, 0))[0]
    mb = assignment.get('margin_bottom', (0, 0))[0]
    ml = assignment.get('margin_left', (0, 0))[0]
    mr = assignment.get('margin_right', (0, 0))[0]

    h_score = 0.0
    v_score = 0.0
    if tw > 0 and iw > 0 and ml > 0 and mr > 0:
        lhs = ml + iw + mr
        err_h = abs(lhs - tw) / max(tw, 1)
        h_score = max(0.0, 1.0 - err_h)
    if th > 0 and ih > 0 and mt > 0 and mb > 0:
        lhs = mt + ih + mb
        err_v = abs(lhs - th) / max(th, 1)
        v_score = max(0.0, 1.0 - err_v)

    if h_score > 0 and v_score > 0:
        return (h_score + v_score) / 2
    return max(h_score, v_score)


def _validate_and_fix_margins(assignment, target_outer_w=0.0, target_outer_h=0.0, dir_locked_fields=None):
    """用几何约束修正边距：缺失反推 / 异常裁剪。

    1. outer_w 优先用 target（若提供），其次用 OCR 值
    2. 若 left+right+inner_w ≠ outer_w，找最可疑值反推
    3. 垂直方向同理
    4. 边距值不得超过外框对应边的 80%
    5. 方向标签锁定的字段不修改，改为反推外框尺寸
    """
    if dir_locked_fields is None:
        dir_locked_fields = set()

    def get(name, default=0.0):
        return assignment.get(name, (default, 0.5))[0]

    def put(name, val, conf=0.4):
        assignment[name] = (val, conf)

    tw = target_outer_w if target_outer_w > 0 else get('total_w')
    th = target_outer_h if target_outer_h > 0 else get('total_h')

    # 如果有方向标签锁定的边距，用它们反推外框尺寸
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

        # 横向：如果有方向标签边距，用它们和 inner_w 计算 total_w
        h_sum = sum(v for v in (ml, mr) if v > 0)
        if h_sum > 0 and iw > 0:
            new_tw = iw + h_sum
            if new_tw > 0:
                tw = new_tw
                put('total_w', tw, 0.90)
                logger.info(f"[Step6] 横向外框修正(方向标签): total_w={tw:.1f} (inner={iw:.1f} left={ml:.1f} right={mr:.1f})")

        # 纵向：同理
        v_sum = sum(v for v in (mt, mb) if v > 0)
        if v_sum > 0 and ih > 0:
            new_th = ih + v_sum
            if new_th > 0:
                th = new_th
                put('total_h', th, 0.90)
                logger.info(f"[Step6] 纵向外框修正(方向标签): total_h={th:.1f} (inner={ih:.1f} top={mt:.1f} bottom={mb:.1f})")
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
            if abs(lhs - tw) / max(tw, 1) > 0.05:
                # 找最可疑值：与其他两个的组合偏差最大者，用公式反推
                candidates = [
                    ('margin_left', tw - iw - mr),
                    ('margin_right', tw - iw - ml),
                    ('inner_w', tw - ml - mr),
                ]
                # 选反推后物理最合理的（>0 且 < outer*0.9）
                # 跳过方向标签锁定的字段
                best = None
                best_err = float('inf')
                for fn, fv in candidates:
                    if fn in dir_locked_fields:
                        continue  # 方向标签锁定的字段不修改
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
            missing = 3 - len(known)  # placeholder
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
        elif len(known) == 1:
            remaining = tw - sum(known)
            if remaining > 0:
                if iw > 0:
                    half = remaining / 2
                    put('margin_left', half, 0.3)
                    put('margin_right', half, 0.3)
                    logger.info(f"[Step6] 横向对称(已知inner): left=right={half:.1f}")
                elif ml > 0:
                    put('margin_right', ml, 0.3)
                    inner = remaining - ml
                    if inner > 0:
                        put('inner_w', inner, 0.3)
                        logger.info(f"[Step6] 横向对称(已知left): right={ml:.1f} inner={inner:.1f}")
                elif mr > 0:
                    put('margin_left', mr, 0.3)
                    inner = remaining - mr
                    if inner > 0:
                        put('inner_w', inner, 0.3)
                        logger.info(f"[Step6] 横向对称(已知right): left={mr:.1f} inner={inner:.1f}")
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
                        continue  # 方向标签锁定的字段不修改
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
        elif len(known) == 1:
            remaining = th - sum(known)
            if remaining > 0:
                if ih > 0:
                    half = remaining / 2
                    put('margin_top', half, 0.3)
                    put('margin_bottom', half, 0.3)
                    logger.info(f"[Step6] 纵向对称(已知inner): top=bottom={half:.1f}")
                elif mt > 0:
                    put('margin_bottom', mt, 0.3)
                    inner = remaining - mt
                    if inner > 0:
                        put('inner_h', inner, 0.3)
                        logger.info(f"[Step6] 纵向对称(已知top): bottom={mt:.1f} inner={inner:.1f}")
                elif mb > 0:
                    put('margin_top', mb, 0.3)
                    inner = remaining - mb
                    if inner > 0:
                        put('inner_h', inner, 0.3)
                        logger.info(f"[Step6] 纵向对称(已知bottom): top={mb:.1f} inner={inner:.1f}")
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
    """测试兼容包装：转调 _validate_and_fix_margins。"""
    asg = {}
    for k, v in margins.items():
        asg[k] = (v[0], v[1])
    asg['total_w'] = (target_outer_w_cm if target_outer_w_cm > 0 else result.get('outer_w', 0), 0.8)
    asg['total_h'] = (target_outer_h_cm if target_outer_h_cm > 0 else result.get('outer_h', 0), 0.8)
    asg['inner_w'] = (result.get('inner_w', 0), 0.6)
    asg['inner_h'] = (result.get('inner_h', 0), 0.6)
    fixed = _validate_and_fix_margins(asg, target_outer_w_cm, target_outer_h_cm,
                                       dir_locked_fields=set(margins.keys()))
    fm = {}
    for k in ('margin_top', 'margin_bottom', 'margin_left', 'margin_right'):
        if k in fixed:
            fm[k] = (fixed[k][0], fixed[k][1])
    result['outer_w'] = fixed.get('total_w', (0, 0))[0]
    result['outer_h'] = fixed.get('total_h', (0, 0))[0]
    result['inner_w'] = fixed.get('inner_w', (0, 0))[0]
    result['inner_h'] = fixed.get('inner_h', (0, 0))[0]
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
                 target_outer_w_cm=0.0, target_outer_h_cm=0.0):
    """严格7步法草图解析。"""
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

    # Step 3: 全局OCR扫描
    ocr_raw = _multi_scale_ocr_scan(cv2, tesseract, gray_img,
                                    target_w_cm=target_outer_w_cm,
                                    target_h_cm=target_outer_h_cm)
    if not ocr_raw:
        return {'success': False, 'message': '全局OCR未识别到任何数值'}
    ocr_raw = _merge_split_decimals(ocr_raw)

    # Step 4: 方向标签优先锁定
    dir_locked = _extract_direction_label_numbers(cv2, tesseract, gray_img)
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
                pass

    result = SketchParseResult(method="7step_v7")
    _progress(5, "加载图片...")
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

    ok, reason = validate_sketch_file(image_path)
    if not ok:
        result.message = reason
        return result

    _progress(15, "7步法识别中...")
    tesseract = _safe_import_tesseract()

    try:
        geo = _7step_parse(cv2, gray, img, tesseract,
                           target_outer_w_cm=target_outer_w_cm,
                           target_outer_h_cm=target_outer_h_cm)
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
