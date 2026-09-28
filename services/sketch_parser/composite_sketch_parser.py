"""综合形状（rect_lshape_hole）草图解析器 —— 阶段3：OCR 十类角色映射 + 内轮廓层级策略。

识别目标（10 类角色 + 挖角）：
  外框2 (outer_w/outer_h) | 挖角2×N角 (经 parse_lshape_sketch 委派) | 洞2 (hole_w/hole_h) | 边距4 (margin_*)

架构：
  1. 几何提取：RETR_CCOMP 轮廓层级 —— 外框 = 最大 0 级轮廓；中心洞 = 严格位于外框 bbox 内
     的最大 0 级轮廓（笔迹草图中洞矩形是独立连通域，其外边界为 0 级而非外框子轮廓）。
     层级策略失败时回退 RETR_LIST 双矩形配对（原 V1 行为）。
  2. 尺寸基准：外框真值始终信任目标文件名（与 L 形/水池一致），OCR 仅用于交叉验证/调试。
  3. OCR 十类角色映射：外框裁剪区多尺度 OCR → 区域判定函数 → 数值聚合到角色桶；
     按「轴」整体决策（宽轴 ml+hw+mr、高轴 mt+hh+mb），C4 误归属风险由两等式闸口
     （|Σ − 外框真值| ≤ 0.5cm，源空间）拦截：全在且成立 → ocr；缺一且减法回填仍成立
     且与像素 hint 偏差 ≤30% → ocr_fill；否则整轴回退 pixel_ratio。
  4. 挖角经 parse_lshape_sketch 委派（不修改其逻辑）。
  5. 输出 CompositeSketchParseResult（G1 字段 + self_consistency 兜底 + sources 溯源）。

公开函数 parse_composite_sketch(...) 永不抛异常。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging

import numpy as np

from .sketch_parser_base import _PARSE_TIMEOUT_SEC, validate_sketch_file
from .sketch_parser_vision import (
    _load_image,
    _multi_scale_ocr_scan,
    _safe_import_cv2,
    _safe_import_tesseract,
    _spatial_map_values,
    _to_gray,
)
from .lshape_sketch_parser import parse_lshape_sketch

logger = logging.getLogger(__name__)

OUTPUT_LOSS_CM = 1.0

_ALGO_VERSION = "composite_v3"  # 2026-09-28: 阶段3 OCR 角色映射 + RETR_CCOMP 层级策略

_EQ_GATE_CM = 0.5       # 两等式闸口容差（源空间）
_FILL_HINT_RATIO = 0.3  # 减法回填值与像素 hint 的最大相对偏差


@dataclass
class CompositeSketchParseResult:
    success: bool = False
    message: str = ""
    method: str = _ALGO_VERSION
    outer_w_cm: float = 0.0
    outer_h_cm: float = 0.0
    hole_w_cm: float = 0.0
    hole_h_cm: float = 0.0
    margin_top_cm: float = 0.0
    margin_bottom_cm: float = 0.0
    margin_left_cm: float = 0.0
    margin_right_cm: float = 0.0
    cuts_cm: list[dict] = field(default_factory=list)
    hole_detected: bool = False
    hole_consumed: bool = False
    self_consistency: float = 0.0
    sources: dict = field(default_factory=dict)
    debug: dict = field(default_factory=dict)


def _rectangular_contours(cv2, gray):
    """返回按面积排序的近似矩形轮廓（V1 回退路径，保留原行为）。"""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 8 or h < 8:
            continue
        area = float(cv2.contourArea(contour))
        fill = area / max(1.0, float(w * h))
        if fill >= 0.45:
            found.append((w * h, (x, y, w, h), fill))
    return sorted(found, reverse=True)


def _extract_outer_and_hole_hierarchy(cv2, gray):
    """RETR_CCOMP 层级策略：返回 (outer_bbox, hole_bbox_or_None, method_tag)。

    笔迹草图拓扑：洞矩形笔画是独立前景连通域，其外边界是 0 级轮廓（不是外框的子轮廓）。
    故洞 = 严格位于外框 bbox 内、面积占比 1%~90%、矩形充实度合格的最大 0 级轮廓。
    """
    try:
        edges = cv2.Canny(gray, 30, 120)
        edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
        contours, hierarchy = cv2.findContours(edges, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    except Exception:
        logger.warning("[composite] 层级轮廓提取失败", exc_info=True)
        return None, None, "none"
    if hierarchy is None or len(contours) == 0:
        return None, None, "none"
    h = hierarchy[0]
    outers = []
    for i in range(len(contours)):
        if h[i][3] != -1:
            continue  # 只取 0 级（外边界）
        bbox = cv2.boundingRect(contours[i])
        area = float(cv2.contourArea(contours[i]))
        fill = area / max(1.0, float(bbox[2] * bbox[3]))
        if bbox[2] < 8 or bbox[3] < 8 or fill < 0.45:
            continue
        outers.append((area, bbox))
    if not outers:
        return None, None, "none"
    outers.sort(key=lambda t: t[0], reverse=True)
    outer_area, outer = outers[0]
    ox, oy, ow, oh = outer
    outer_box_area = max(1.0, float(ow * oh))
    hole = None
    for area, bbox in outers[1:]:
        x, y, w, hh = bbox
        if x <= ox or y <= oy or x + w >= ox + ow or y + hh >= oy + oh:
            continue  # 必须严格在外框 bbox 内部
        ratio = (w * hh) / outer_box_area
        if not (0.01 <= ratio <= 0.90):
            continue
        if hole is None or area > hole[0]:
            hole = (area, bbox)
    return outer, (hole[1] if hole else None), ("hierarchy" if hole else "outer_only")


def _composite_zone_func(outer, hole):
    """综合形状区域判定函数：把数值归属到 outer_w/h、hole_w/h、margin_* 八个角色桶。

    口径与 _divide_8_zones 一致：洞矩形内部按象限分（下半=hole_w、左半=hole_h），
    内外框之间外环按到洞四边就近归入边距；外框外侧下方=outer_w、左侧=outer_h。
    """
    ox, oy, ow, oh = outer
    hx, hy, hw, hh = hole

    def zone_of(cx, cy):
        if cy > oy + oh and ox <= cx <= ox + ow:
            return "outer_w"
        if cx < ox and oy <= cy <= oy + oh:
            return "outer_h"
        if ox <= cx <= ox + ow and oy <= cy <= oy + oh:
            if hx <= cx <= hx + hw and hy <= cy <= hy + hh:
                hcx = hx + hw / 2
                hcy = hy + hh / 2
                if cy > hcy:
                    return "hole_w"
                if cx < hcx:
                    return "hole_h"
                return "hole_w"
            if cx < hx and hy <= cy <= hy + hh:
                return "margin_left"
            if cx > hx + hw and hy <= cy <= hy + hh:
                return "margin_right"
            if cy < hy and hx <= cx <= hx + hw:
                return "margin_top"
            if cy > hy + hh and hx <= cx <= hx + hw:
                return "margin_bottom"
            d_top = abs(cy - hy)
            d_bottom = abs(cy - (hy + hh))
            d_left = abs(cx - hx)
            d_right = abs(cx - (hx + hw))
            min_d = min(d_top, d_bottom, d_left, d_right)
            if min_d == d_top:
                return "margin_top"
            if min_d == d_bottom:
                return "margin_bottom"
            if min_d == d_left:
                return "margin_left"
            return "margin_right"
        return None

    return zone_of


def _pick_field_value(candidates):
    """单角色桶定值：按 round(v,1) 去重取最高置信度，返回 (value, found)。"""
    best = {}
    for val, conf, _bbox in candidates:
        key = round(val, 1)
        if key not in best or conf > best[key][1]:
            best[key] = (val, conf)
    if not best:
        return None, False
    # 同置信度时取数值较大者（草图标注字号大、完整数值置信度通常更高）
    top = sorted(best.values(), key=lambda t: (t[1], t[0]), reverse=True)
    return top[0][0], True


def _resolve_axis(outer_cm, pixel_parts_cm, ocr_parts, label):
    """单轴（宽：ml+hw+mr / 高：mt+hh+mb）整体决策。

    pixel_parts_cm / ocr_parts 均为 (a, b, c) 三元组（margin, hole, margin），
    其中 b 恒有像素值；OCR 值可缺失（None）。
    返回 (a_cm, b_cm, c_cm, source)：
      ocr        —— 三个角色 OCR 全在且两等式成立（|Σ−外框| ≤ 0.5cm）
      ocr_fill   —— 恰缺一个角色，减法回填后两等式成立且与像素 hint 偏差 ≤ 30%
      pixel_ratio —— 整轴回退像素比例值
    """
    pa, pb, pc = pixel_parts_cm
    oa, ob, oc = ocr_parts
    present = [v for v in (oa, ob, oc) if v is not None]
    if len(present) == 3 and abs(oa + ob + oc - outer_cm) <= _EQ_GATE_CM:
        return oa, ob, oc, "ocr"
    if len(present) == 2:
        missing_idx = next(i for i, v in enumerate((oa, ob, oc)) if v is None)
        fill = outer_cm - sum(v for v in (oa, ob, oc) if v is not None)
        hint = (pa, pb, pc)[missing_idx]
        if fill > 0 and (hint <= 0 or abs(fill - hint) / hint <= _FILL_HINT_RATIO):
            parts = [oa, ob, oc]
            parts[missing_idx] = fill
            return parts[0], parts[1], parts[2], "ocr_fill"
    logger.info("[composite] 轴[%s] OCR 闸口未通过，回退像素比例（OCR=%s, pixel=%s, outer=%.1f）",
                label, ocr_parts, pixel_parts_cm, outer_cm)
    return pa, pb, pc, "pixel_ratio"


def parse_composite_sketch(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
    progress_callback=None,
    external_cancel_check=None,
) -> CompositeSketchParseResult:
    """解析单洞多角综合形状草图，永不向调用方抛出异常。"""
    result = CompositeSketchParseResult()
    try:
        ok, reason = validate_sketch_file(image_path)
        if not ok:
            result.message = reason
            return result
        cv2 = _safe_import_cv2()
        if cv2 is None:
            result.message = "未安装 OpenCV"
            return result
        if target_outer_w_cm <= 0 or target_outer_h_cm <= 0:
            result.message = "缺少外框尺寸基准"
            return result
        img, err = _load_image(image_path)
        if err:
            result.message = err
            return result
        gray = _to_gray(img)
        if progress_callback:
            progress_callback(15, "检测综合形状轮廓...")

        # ---- 1) 几何提取：层级策略优先，RETR_LIST 配对回退 ----
        outer, hole, method = _extract_outer_and_hole_hierarchy(cv2, gray)
        if outer is None or hole is None:
            rects = _rectangular_contours(cv2, gray)
            if len(rects) >= 2:
                outer_fb = rects[0][1]
                outer_area = float(outer_fb[2] * outer_fb[3])
                holes_fb = []
                for it in rects[1:]:
                    x, y, w, h = it[1]
                    if not (w < outer_fb[2] and h < outer_fb[3]):
                        continue
                    # 与层级策略同口径：须严格在外框内且 bbox 面积占比 1%~90%，
                    # 排除 L 形环带内缘（占比 ~97%）被误当成中心洞
                    if not (outer_fb[0] < x and outer_fb[1] < y
                            and x + w < outer_fb[0] + outer_fb[2]
                            and y + h < outer_fb[1] + outer_fb[3]):
                        continue
                    ratio = (w * h) / max(1.0, outer_area)
                    if 0.01 <= ratio <= 0.90:
                        holes_fb.append(it)
                if holes_fb:
                    outer, hole = outer_fb, holes_fb[0][1]
                    method = "retr_list_fallback"
        if outer is None or hole is None:
            result.message = "未检测到外框和中心洞"
            result.debug["method"] = method
            return result

        ox, oy, ow, oh = outer
        hx, hy, hw, hh = hole
        sx = target_outer_w_cm / float(ow)
        sy = target_outer_h_cm / float(oh)
        pixel_ml = (hx - ox) * sx
        pixel_mt = (hy - oy) * sy
        pixel_mr = (ow - (hx - ox + hw)) * sx
        pixel_mb = (oh - (hy - oy + hh)) * sy
        pixel_hw = hw * sx
        pixel_hh = hh * sy
        if progress_callback:
            progress_callback(35, "OCR 十类角色映射...")

        # ---- 2) OCR 角色映射（整轴闸口决策，OCR 不可用时静默降级） ----
        tesseract = _safe_import_tesseract()
        zone_func = _composite_zone_func(outer, hole)
        role_sources = {"method": method, "width_axis": "pixel_ratio", "height_axis": "pixel_ratio"}
        if tesseract is not None:
            try:
                import time as _time
                band = int(min(max(30, 0.12 * max(ow, oh)), 150))
                x0 = max(0, ox - band)
                y0 = max(0, oy - band)
                x1 = min(gray.shape[1], ox + ow + band)
                y1 = min(gray.shape[0], oy + oh + band)
                region = gray[y0:y1, x0:x1]
                deadline = _time.monotonic() + _PARSE_TIMEOUT_SEC

                def _cancel():
                    if external_cancel_check is not None and external_cancel_check():
                        return True
                    return _time.monotonic() > deadline

                ocr_hits = _multi_scale_ocr_scan(
                    cv2, tesseract, region, fast_mode=False, check_cancel=_cancel)
                # 裁剪区坐标还原到全图，并按尺寸合理性过滤（数值不能超过外框 1.2 倍、
                # 文本框不能超过外框面积的 15% —— 拦截把矩形边框误读成大数字的情况）
                shifted = []
                max_cm = max(target_outer_w_cm, target_outer_h_cm) * 1.2
                outer_area = float(ow * oh)
                for val, conf, (bx, by, bw, bh) in ocr_hits:
                    if val <= 0 or val > max_cm:
                        continue
                    if bw * bh > 0.15 * outer_area:
                        continue
                    gx, gy = bx + x0, by + y0
                    shifted.append((val, conf, (gx, gy, bw, bh)))
                buckets = _spatial_map_values(
                    shifted, zone_func, exclude_fields=(), exclude_values=(), tolerance=0.1)
                ocr_ml, f_ml = _pick_field_value(buckets.get("margin_left", []))
                ocr_mr, f_mr = _pick_field_value(buckets.get("margin_right", []))
                ocr_mt, f_mt = _pick_field_value(buckets.get("margin_top", []))
                ocr_mb, f_mb = _pick_field_value(buckets.get("margin_bottom", []))
                ocr_hw, f_hw = _pick_field_value(buckets.get("hole_w", []))
                ocr_hh, f_hh = _pick_field_value(buckets.get("hole_h", []))
                ml, hw_cm, mr, src_w = _resolve_axis(
                    target_outer_w_cm,
                    (pixel_ml, pixel_hw, pixel_mr),
                    (ocr_ml if f_ml else None, ocr_hw if f_hw else None, ocr_mr if f_mr else None),
                    "w")
                mt, hh_cm, mb, src_h = _resolve_axis(
                    target_outer_h_cm,
                    (pixel_mt, pixel_hh, pixel_mb),
                    (ocr_mt if f_mt else None, ocr_hh if f_hh else None, ocr_mb if f_mb else None),
                    "h")
                role_sources["width_axis"] = src_w
                role_sources["height_axis"] = src_h
                result.sources.update({
                    "width_axis": src_w, "height_axis": src_h,
                    "margin_left": src_w, "hole_w": src_w, "margin_right": src_w,
                    "margin_top": src_h, "hole_h": src_h, "margin_bottom": src_h,
                    "hole_detected": True, "method": method,
                })
                result.debug["ocr_buckets"] = {k: [(v, c, b) for v, c, b in vs]
                                               for k, vs in buckets.items()}
            except Exception:
                logger.warning("[composite] OCR 角色映射失败，回退像素比例", exc_info=True)
                ml, hw_cm, mr = pixel_ml, pixel_hw, pixel_mr
                mt, hh_cm, mb = pixel_mt, pixel_hh, pixel_mb
                result.sources.update({"width_axis": "pixel_ratio", "height_axis": "pixel_ratio",
                                       "method": method, "hole_detected": True})
        else:
            ml, hw_cm, mr = pixel_ml, pixel_hw, pixel_mr
            mt, hh_cm, mb = pixel_mt, pixel_hh, pixel_mb
            result.sources.update({"width_axis": "pixel_ratio", "height_axis": "pixel_ratio",
                                   "method": method, "hole_detected": True,
                                   "ocr": "unavailable"})

        # 草图尺寸是标注真值；输出尺寸沿用项目既有规则，宽、高各补 1cm 损耗。
        result.outer_w_cm = target_outer_w_cm + OUTPUT_LOSS_CM
        result.outer_h_cm = target_outer_h_cm + OUTPUT_LOSS_CM
        result.hole_w_cm = round(hw_cm + OUTPUT_LOSS_CM, 2)
        result.hole_h_cm = round(hh_cm + OUTPUT_LOSS_CM, 2)
        result.margin_left_cm = round(ml, 2)
        result.margin_top_cm = round(mt, 2)
        result.margin_right_cm = round(mr, 2)
        result.margin_bottom_cm = round(mb, 2)

        if progress_callback:
            progress_callback(70, "委派 L 形挖角识别...")
        lshape = parse_lshape_sketch(
            image_path,
            target_outer_w_cm=target_outer_w_cm,
            target_outer_h_cm=target_outer_h_cm,
            external_cancel_check=external_cancel_check,
        )
        result.cuts_cm = list(lshape.cuts_cm)
        result.hole_detected = True
        result.hole_consumed = True
        width_error = abs(result.margin_left_cm + result.hole_w_cm + result.margin_right_cm - result.outer_w_cm)
        height_error = abs(result.margin_top_cm + result.hole_h_cm + result.margin_bottom_cm - result.outer_h_cm)
        result.self_consistency = 1.0 if width_error <= 0.5 and height_error <= 0.5 else 0.0
        result.success = bool(result.cuts_cm) and result.self_consistency > 0
        result.message = "识别成功" if result.success else "中心洞已识别，但综合几何自洽校验未通过"
        result.debug.update({
            "outer_bbox": outer, "hole_bbox": hole, "method": method,
            "pixel_cm": {"margin_left": round(pixel_ml, 2), "margin_top": round(pixel_mt, 2),
                         "margin_right": round(pixel_mr, 2), "margin_bottom": round(pixel_mb, 2),
                         "hole_w": round(pixel_hw, 2), "hole_h": round(pixel_hh, 2)},
            "source_outer_w_cm": target_outer_w_cm,
            "source_outer_h_cm": target_outer_h_cm,
            "source_hole_w_cm": round(hw_cm, 2),
            "source_hole_h_cm": round(hh_cm, 2),
            "output_loss_cm": OUTPUT_LOSS_CM,
            "width_error_cm": width_error, "height_error_cm": height_error,
            "lshape": lshape.debug,
        })
        return result
    except Exception as exc:
        logger.warning("[composite] 解析失败（安全降级）: %s", exc)
        result.message = f"解析失败: {exc}"
        return result
