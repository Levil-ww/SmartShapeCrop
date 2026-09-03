"""尺寸草图解析器 —— 图像加载 / OCR 扫描 / 矩形几何检测层（由 sketch_parser.py 拆分而来，facade 模式）。

原文件 core/pool_designer/sketch_parser.py 为编排层 facade，
本模块只包含 图像加载 / OCR 扫描 / 矩形几何检测层 相关的实现，逻辑与原文件完全一致。
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

try:  # pragma: no cover - 依赖环境差异
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    logging.getLogger(__name__).debug("[module] PIL 导入失败，已降级", exc_info=True)
    Image = None  # type: ignore

logger = logging.getLogger(__name__)

from .sketch_parser_base import _PARSE_TIMEOUT_SEC
from .sketch_parser_base import _normalize_ocr_text


_TESSERACT_STATUS = {
    "available": False,
    "reason": "",           # 不可用时的原因文字
    "tesseract_path": "",   # 可用时的 tesseract.exe 路径
    "tessdata_path": "",    # 可用时的 tessdata 路径
}



def _safe_import_cv2():
    try:
        import cv2
        return cv2
    except Exception as e:
        logger.warning(f"[sketch_parser] OpenCV 未安装: {e}")
        return None



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
    # 修复 Windows 中文路径：cv2.imread 对含中文/特殊字符的路径返回 None，
    # 优先用 imdecode(np.fromfile(...))，失败再走 PIL fallback
    img = None
    try:
        data = np.fromfile(image_path, dtype=np.uint8)
        if data is not None and data.size > 0:
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.debug(f"[vision] imdecode 路径失败: {e}")
        img = None
    if img is None:
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

