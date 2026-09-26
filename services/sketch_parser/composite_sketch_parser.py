"""综合形状草图解析：L 形外轮廓 + 单个中心矩形洞。

本模块只负责识别层，输出建议值；渲染和参数落地由上层完成。外框尺寸优先
信任文件名/调用方传入的 target 值，洞尺寸和边距由像素几何比例换算。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os

import numpy as np

from .sketch_parser_base import validate_sketch_file
from .sketch_parser_vision import _load_image, _safe_import_cv2, _to_gray
from .lshape_sketch_parser import parse_lshape_sketch

logger = logging.getLogger(__name__)

OUTPUT_LOSS_CM = 1.0


@dataclass
class CompositeSketchParseResult:
    success: bool = False
    message: str = ""
    method: str = "composite_v1"
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
    debug: dict = field(default_factory=dict)


def _rectangular_contours(cv2, gray):
    """返回按面积排序的近似矩形轮廓。"""
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
        img, err = _load_image(image_path)
        if err:
            result.message = err
            return result
        gray = _to_gray(img)
        if progress_callback:
            progress_callback(20, "检测综合形状轮廓...")

        rects = _rectangular_contours(cv2, gray)
        if len(rects) < 2:
            result.message = "未检测到外框和中心洞"
            return result
        outer = rects[0][1]
        holes = [item for item in rects[1:] if item[1][2] < outer[2] and item[1][3] < outer[3]]
        if not holes:
            result.message = "未检测到中心洞"
            return result
        _, (hx, hy, hw, hh), _ = holes[0]
        ox, oy, ow, oh = outer
        if target_outer_w_cm <= 0 or target_outer_h_cm <= 0:
            result.message = "缺少外框尺寸基准"
            return result

        sx = target_outer_w_cm / float(ow)
        sy = target_outer_h_cm / float(oh)
        source_hole_w_cm = round(hw * sx, 2)
        source_hole_h_cm = round(hh * sy, 2)
        # 草图尺寸是标注真值；输出尺寸沿用项目既有规则，宽、高各补 1cm 损耗。
        result.outer_w_cm = target_outer_w_cm + OUTPUT_LOSS_CM
        result.outer_h_cm = target_outer_h_cm + OUTPUT_LOSS_CM
        result.hole_w_cm = source_hole_w_cm + OUTPUT_LOSS_CM
        result.hole_h_cm = source_hole_h_cm + OUTPUT_LOSS_CM
        result.margin_left_cm = round((hx - ox) * sx, 2)
        result.margin_top_cm = round((hy - oy) * sy, 2)
        result.margin_right_cm = round((ow - (hx - ox + hw)) * sx, 2)
        result.margin_bottom_cm = round((oh - (hy - oy + hh)) * sy, 2)

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
        result.debug.update({"outer_bbox": outer, "hole_bbox": (hx, hy, hw, hh),
                             "source_outer_w_cm": target_outer_w_cm,
                             "source_outer_h_cm": target_outer_h_cm,
                             "source_hole_w_cm": source_hole_w_cm,
                             "source_hole_h_cm": source_hole_h_cm,
                             "output_loss_cm": OUTPUT_LOSS_CM,
                             "width_error_cm": width_error, "height_error_cm": height_error,
                             "lshape": lshape.debug})
        return result
    except Exception as exc:
        logger.warning("[composite] 解析失败（安全降级）: %s", exc)
        result.message = f"解析失败: {exc}"
        return result
