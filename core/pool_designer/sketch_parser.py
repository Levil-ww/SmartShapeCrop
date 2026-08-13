"""尺寸草图解析器（水池设计器用）。

目标：从"图二"这类边距标注草图中，自动提取外框尺寸和内挖的 4 个边距。

三层策略（从稳健到增强，任何一层成功都会把值填入结果，且允许 UI 覆盖）：
    L1 几何检测（默认启用）：检测外矩形 + 内矩形的轮廓，用目标尺寸换算得到 cm 边距。
    L2 可选 OCR（需安装 pytesseract，未安装直接跳过）：识别数字作为校验/补全。
    L3 手动回退：UI 上始终允许用户编辑 4 个 SpinBox，解析失败不阻塞。

所有公开函数都不会抛异常；失败时返回带有 success=False 的结果，调用方据此处理。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SketchParseResult:
    """草图解析结果（全部用厘米，缺项为 0 表示未知）。"""
    success: bool = False              # 是否至少拿到了一组可参考的值
    message: str = ""                  # 给用户的提示（成功/失败原因）
    method: str = ""                   # 用了哪一层："geometry" / "ocr" / "manual" / "none"

    # 外框尺寸（画布）
    outer_w_cm: float = 0.0
    outer_h_cm: float = 0.0

    # 内挖区域尺寸
    inner_w_cm: float = 0.0
    inner_h_cm: float = 0.0

    # 四个边距（核心输出）
    margin_top_cm: float = 0.0
    margin_bottom_cm: float = 0.0
    margin_left_cm: float = 0.0
    margin_right_cm: float = 0.0

    # 调试信息（UI 可展示给用户做校验）
    debug: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# L1：OpenCV 几何检测
# ---------------------------------------------------------------------------

def _safe_import_cv2():
    """按需导入 cv2，避免环境没装直接 import 时崩。"""
    try:
        import cv2  # type: ignore
        return cv2
    except Exception as e:  # pragma: no cover - 环境相关
        logger.warning(f"[sketch_parser] OpenCV 未安装，几何检测已跳过: {e}")
        return None


def _find_two_largest_rectangles(cv2, gray_image, min_area_ratio: float = 0.005):
    """在图上找两个最大的嵌套矩形轮廓（内外框）。

    多策略检测：
      A. HSV 红色线检测（宽阈值，兼容淡红）
      B. Canny 边缘 + 形态学闭运算（兜底，不限颜色）
      C. 直接灰度阈值 + 形态学（白底黑线场景）

    返回 [(outer_rect, area), (inner_rect, area)]，按面积降序；
    找不到两个时返回空列表。
    """
    h, w = gray_image.shape[:2]
    full_area = h * w
    # 细线条轮廓的面积很小（约为周长×线宽），阈值需要很低
    min_area = max(100, int(full_area * min_area_ratio))

    # —— 构建多策略 mask ——
    masks = []
    if len(gray_image.shape) == 3:
        hsv = cv2.cvtColor(gray_image, cv2.COLOR_BGR2HSV)
        # 红色范围：低 Hue (0-15) + 高 Hue (165-180)，放宽 S/V
        lower1 = (0, 20, 30); upper1 = (15, 255, 255)
        lower2 = (165, 20, 30); upper2 = (180, 255, 255)
        mask_r1 = cv2.inRange(hsv, lower1, upper1)
        mask_r2 = cv2.inRange(hsv, lower2, upper2)
        mask_red = cv2.bitwise_or(mask_r1, mask_r2)
        masks.append(('red', mask_red))

        # Canny 边缘（低阈值，抓细线条）
        gray = cv2.cvtColor(gray_image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 20, 80)
        masks.append(('canny', edges))

        # 白色背景上的暗线（灰度反转）
        _, binary_dark = cv2.threshold(gray, 210, 255, cv2.THRESH_BINARY_INV)
        masks.append(('dark', binary_dark))
    else:
        gray = gray_image
        masks.append(('canny', cv2.Canny(gray, 20, 80)))
        _, binary_dark = cv2.threshold(gray, 210, 255, cv2.THRESH_BINARY_INV)
        masks.append(('dark', binary_dark))

    # —— 第一轮：用 RETR_CCOMP 检测所有轮廓（包括嵌套的）——
    rects_raw = []
    for mask_name, mask in masks:
        # 用 RETR_CCOMP 检测所有层级轮廓
        contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area:
                continue
            x, y, rw, rh = cv2.boundingRect(cnt)
            if rw < 20 or rh < 20:
                continue
            # 同一个矩形会产生 2 个 contour（外边界+内边界），面积近似
            # 用 boundingRect 面积做参考
            rect_area = rw * rh
            if rect_area <= 0:
                continue
            rects_raw.append((x, y, rw, rh, area, rect_area))

        # 第二轮：轻度形态学处理（连接断线）
        kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask_closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_close, iterations=1)
        kernel_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        mask_final = cv2.dilate(mask_closed, kernel_dilate, iterations=1)

        contours2, _ = cv2.findContours(mask_final, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours2:
            area = cv2.contourArea(cnt)
            if area < min_area:
                continue
            x, y, rw, rh = cv2.boundingRect(cnt)
            if rw < 20 or rh < 20:
                continue
            rect_area = rw * rh
            if rect_area <= 0:
                continue
            rects_raw.append((x, y, rw, rh, area, rect_area))

    # —— 去重：用 boundingRect 尺寸去重（同一矩形的内外 contour 近似）——
    rects_raw.sort(key=lambda r: r[5], reverse=True)  # sort by rect_area
    rects_dedup = []
    for r in rects_raw:
        x, y, rw, rh, area, rect_area = r
        duplicate = False
        for d in rects_dedup:
            dx, dy, dw, dh = d[0], d[1], d[2], d[3]
            if abs(x - dx) < 15 and abs(y - dy) < 15 and abs(rw - dw) < 15 and abs(rh - dh) < 15:
                duplicate = True
                break
        if not duplicate:
            rects_dedup.append((x, y, rw, rh, area))

    # —— 按面积排序，取最大的两个（外框最大，内框次之）——
    rects_dedup.sort(key=lambda r: r[4], reverse=True)

    # —— 找嵌套关系：验证第二份矩形确实在第一份内部 ——
    if len(rects_dedup) >= 2:
        outer = rects_dedup[0]
        ox, oy, ow, oh, oa = outer
        inner = rects_dedup[1]
        ix, iy, iw, ih, ia = inner
        # 验证嵌套关系：内框在外框内部
        if ix >= ox - 10 and iy >= oy - 10 and ix + iw <= ox + ow + 10 and iy + ih <= oy + oh + 10:
            return [outer, inner]
        # 未嵌套：尝试在外框内部找一个更好的内框
        for r in rects_dedup[2:]:
            rx, ry, rw, rh, ra = r
            if rx >= ox - 10 and ry >= oy - 10 and rx + rw <= ox + ow + 10 and ry + rh <= oy + oh + 10:
                if ra > ia:
                    return [outer, r]

    # 兜底：直接返回面积最大的两个
    return rects_dedup[:2]


def parse_sketch_geometry(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
) -> SketchParseResult:
    """L1 几何检测：找两个嵌套矩形，用目标尺寸换算得到 cm 边距。

    Args:
        image_path: 草图文件路径（任意 PIL/OpenCV 可读格式）。
        target_outer_w_cm: 已知的目标外框宽度（cm），比如从文件名解析出来的 60.5。
        target_outer_h_cm: 已知的目标外框高度（cm），比如从文件名解析出来的 133。

    Returns:
        SketchParseResult；只要能算出 4 个边距且都 > 0，就认为 success=True。
    """
    result = SketchParseResult(method="geometry")
    cv2 = _safe_import_cv2()
    if cv2 is None:
        result.message = "未安装 OpenCV，几何检测已跳过。请手动输入边距。"
        return result
    if not image_path or not os.path.isfile(image_path):
        result.message = f"草图文件不存在: {image_path}"
        return result

    try:
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.warning(f"[sketch_parser] cv2.imread 失败: {e}")
        result.message = "读取草图失败（文件格式或权限问题）。请手动输入边距。"
        return result

    if img is None:
        # 兜底：尝试用 PIL 读再转 numpy（解决中文路径在某些 cv2 版本读不出的问题）
        try:
            from PIL import Image
            import numpy as np
            with Image.open(image_path) as pil:
                pil = pil.convert("RGB")
                img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        except Exception as e2:
            logger.warning(f"[sketch_parser] PIL 兜底读也失败: {e2}")
            result.message = "读取草图失败（中文路径或损坏）。请手动输入边距。"
            return result

    top2 = _find_two_largest_rectangles(cv2, img)
    if len(top2) < 2:
        result.message = f"只检测到 {len(top2)} 个矩形轮廓，无法推导出内外关系。请手动输入边距。"
        result.debug["rects_found"] = len(top2)
        return result

    (ox, oy, ow, oh, oa), (ix, iy, iw, ih, ia) = top2
    result.debug["outer_rect_px"] = (ox, oy, ow, oh)
    result.debug["inner_rect_px"] = (ix, iy, iw, ih)

    # 像素→厘米换算：优先用"目标尺寸/外框像素"，因为目标尺寸是从文件名精确解析的
    cm_per_px_w = cm_per_px_h = 0.0
    if target_outer_w_cm > 0 and ow > 0:
        cm_per_px_w = target_outer_w_cm / ow
    if target_outer_h_cm > 0 and oh > 0:
        cm_per_px_h = target_outer_h_cm / oh
    # 如果目标尺寸只有一边，另一边按外框比例推
    if cm_per_px_w and not cm_per_px_h:
        cm_per_px_h = cm_per_px_w
    elif cm_per_px_h and not cm_per_px_w:
        cm_per_px_w = cm_per_px_h

    if not cm_per_px_w or not cm_per_px_h:
        # 没有目标尺寸，就按"相对比例"返回（UI 侧会显示但需要用户给目标尺寸）
        result.message = "几何检测找到内外框，但缺少目标外框尺寸（请先在左侧输入画布尺寸）。"
        return result

    # 边距 = 内框相对外框的偏移（像素 × cm_per_px）
    top_m = (iy - oy) * cm_per_px_h
    left_m = (ix - ox) * cm_per_px_w
    bottom_m = ((oy + oh) - (iy + ih)) * cm_per_px_h
    right_m = ((ox + ow) - (ix + iw)) * cm_per_px_w

    # 内挖尺寸
    inner_w = iw * cm_per_px_w
    inner_h = ih * cm_per_px_h

    if top_m < 0 or left_m < 0 or bottom_m < 0 or right_m < 0:
        result.message = "检测到的内框不在外框内部，结果不可信。请手动输入边距。"
        result.debug["negative_margins"] = (top_m, left_m, bottom_m, right_m)
        return result

    result.outer_w_cm = round(target_outer_w_cm or (ow * cm_per_px_w), 2)
    result.outer_h_cm = round(target_outer_h_cm or (oh * cm_per_px_h), 2)
    result.inner_w_cm = round(inner_w, 2)
    result.inner_h_cm = round(inner_h, 2)
    result.margin_top_cm = round(top_m, 2)
    result.margin_bottom_cm = round(bottom_m, 2)
    result.margin_left_cm = round(left_m, 2)
    result.margin_right_cm = round(right_m, 2)
    result.success = True
    result.message = (
        f"几何检测成功：上{result.margin_top_cm}/下{result.margin_bottom_cm}/"
        f"左{result.margin_left_cm}/右{result.margin_right_cm} cm；"
        f"内空 {result.inner_w_cm}×{result.inner_h_cm} cm。请检查数值后继续。"
    )
    return result


# ---------------------------------------------------------------------------
# L2：OCR 识别（可选增强）
# ---------------------------------------------------------------------------

def _safe_import_tesseract():
    try:
        import pytesseract  # type: ignore
        return pytesseract
    except Exception as e:  # pragma: no cover - 环境相关
        logger.info(f"[sketch_parser] pytesseract 未安装，OCR 已跳过: {e}")
        return None


def _parse_sketch_ocr(image_path: str) -> SketchParseResult:
    """OCR 识别草图上的数字。当前实现作为校验提示，不覆盖几何检测的主输出。"""
    result = SketchParseResult(method="ocr", message="OCR 跳过或未识别到数值。")
    pytesseract = _safe_import_tesseract()
    if pytesseract is None:
        return result
    try:
        from PIL import Image
        text = ""
        with Image.open(image_path) as pil:
            text = pytesseract.image_to_string(pil, lang="chi_sim+eng")
        numbers = []
        for token in text.replace(",", ".").split():
            try:
                numbers.append(float(token))
            except ValueError:
                continue
        if numbers:
            result.debug["ocr_numbers"] = numbers
            result.message = f"OCR 识别到 {len(numbers)} 个数字，作为参考。"
            # 不作为 success=True 的主依据，只给 debug 让 UI 提示
    except Exception as e:
        logger.warning(f"[sketch_parser] OCR 异常: {e}")
    return result


# ---------------------------------------------------------------------------
# 对外：组合调用
# ---------------------------------------------------------------------------

def parse_sketch(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
) -> SketchParseResult:
    """解析尺寸草图，返回 SketchParseResult（永不抛异常）。

    流程：L1 几何检测 → L2 OCR 辅助（只填 debug，不改主数值）。
    """
    geo = parse_sketch_geometry(
        image_path,
        target_outer_w_cm=target_outer_w_cm,
        target_outer_h_cm=target_outer_h_cm,
    )
    # OCR 辅助（不覆盖几何结果，失败了也不影响）
    ocr = _parse_sketch_ocr(image_path)
    if ocr.debug:
        for k, v in ocr.debug.items():
            geo.debug.setdefault(k, v)
    if not geo.success and ocr.message:
        geo.message += "（" + ocr.message + "）"
    return geo
