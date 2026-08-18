"""尺寸草图解析器（水池设计器用）。

目标：从边距标注草图中，自动提取外框尺寸、内挖尺寸和 4 个边距。

三层策略（协同工作）：
  L1 复杂度评估：检测草图是否过于混乱（如大量文字/花纹），给出跳过建议。
  L2 几何检测：用多边形近似找两个嵌套矩形，作为空间参考框架。
  L3 数字识别：在矩形周围区域定位并读取标注数字（OCR + 几何回退），
               直接得到各边距/尺寸值。

所有公开函数都不会抛异常；失败时返回带有 success=False 的结果。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.config import (
    GOLDEN_INNER_VALUES,
    GOLDEN_MARGIN_VALUES,
    GOLDEN_MARGIN_PAIR_H,
    GOLDEN_MARGIN_PAIR_V,
    GOLDEN_TOLERANCE_CM,
    T0_MAX_ITERATIONS,
)

logger = logging.getLogger(__name__)

import hashlib
import time

# ---------------------------------------------------------------------------
# 草图识别结果缓存（性能优化）
# ---------------------------------------------------------------------------
# 缓存 key = (文件路径, 文件修改时间, 目标宽, 目标高)
# 缓存 value = SketchParseResult
# 同一张草图切换不同目标文件名时，若文件内容相同则直接返回缓存结果。
_SKETCH_CACHE: dict = {}
_SKETCH_CACHE_MAX = 50  # 最多缓存 50 条


def _get_cache_key(image_path: str, target_w: float, target_h: float) -> tuple:
    """生成缓存 key：文件路径 + 修改时间 + 目标尺寸。"""
    try:
        mtime = os.path.getmtime(image_path)
    except Exception:
        mtime = 0
    return (image_path, mtime, round(target_w, 1), round(target_h, 1))


def _get_cached_result(image_path: str, target_w: float, target_h: float):
    """查找缓存中的识别结果，找到则返回 SketchParseResult 副本，否则返回 None。"""
    key = _get_cache_key(image_path, target_w, target_h)
    cached = _SKETCH_CACHE.get(key)
    if cached is not None:
        logger.info(f"[sketch_parser] 缓存命中：{image_path} target={target_w:.1f}x{target_h:.1f}cm")
        # 返回副本，避免调用方修改缓存
        import copy
        return copy.deepcopy(cached)
    return None


def _store_cached_result(image_path: str, target_w: float, target_h: float, result):
    """存储识别结果到缓存。"""
    key = _get_cache_key(image_path, target_w, target_h)
    if len(_SKETCH_CACHE) >= _SKETCH_CACHE_MAX:
        # 简单 LRU：移除最早的一个
        oldest = next(iter(_SKETCH_CACHE))
        _SKETCH_CACHE.pop(oldest, None)
    import copy
    _SKETCH_CACHE[key] = copy.deepcopy(result)


# ---------------------------------------------------------------------------
# 自洽解缓存（与 target 尺寸无关）
# ---------------------------------------------------------------------------
# 当 OCR 识别到全部 8 字段且几何自洽（sc≈1.0）时，最终结果与 target 无关。
# 缓存 key = (文件路径, 文件修改时间)，不含 target 尺寸。
# 更换目标文件名（不同 target）时直接命中，毫秒级响应。
_SKETCH_CONSISTENT_CACHE: dict = {}
_SKETCH_CONSISTENT_CACHE_MAX = 50


def _get_consistent_cache_key(image_path: str) -> tuple:
    """自洽解缓存 key：只依赖文件路径和修改时间，与 target 无关。"""
    try:
        mtime = os.path.getmtime(image_path)
    except Exception:
        mtime = 0
    return (image_path, mtime)


def _get_consistent_cached_result(image_path: str):
    """查找自洽解缓存。命中则返回 SketchParseResult 副本，否则 None。"""
    key = _get_consistent_cache_key(image_path)
    cached = _SKETCH_CONSISTENT_CACHE.get(key)
    if cached is not None:
        logger.info(f"[sketch_parser] 自洽解缓存命中（与target无关）：{image_path}")
        import copy
        return copy.deepcopy(cached)
    return None


def _store_consistent_cached_result(image_path: str, result):
    """存储自洽解到缓存（仅当 8 字段全部自洽时调用）。"""
    key = _get_consistent_cache_key(image_path)
    if len(_SKETCH_CONSISTENT_CACHE) >= _SKETCH_CONSISTENT_CACHE_MAX:
        oldest = next(iter(_SKETCH_CONSISTENT_CACHE))
        _SKETCH_CONSISTENT_CACHE.pop(oldest, None)
    import copy
    _SKETCH_CONSISTENT_CACHE[key] = copy.deepcopy(result)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SketchParseResult:
    """草图解析结果（全部用厘米，缺项为 0 表示未知）。"""
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
# 工具函数
# ---------------------------------------------------------------------------

def _safe_import_cv2():
    try:
        import cv2
        return cv2
    except Exception as e:
        logger.warning(f"[sketch_parser] OpenCV 未安装: {e}")
        return None


def _safe_import_tesseract():
    """安全导入 pytesseract 并自动配置 tesseract 路径。

    即使 Tesseract 未加入系统 PATH，也会在常见安装目录中搜索并配置。
    """
    try:
        import pytesseract
    except Exception as e:
        logger.info(f"[sketch_parser] pytesseract 未安装: {e}")
        return None

    # 尝试确定 tesseract 可执行文件位置
    exe_candidates = []
    if os.name == 'nt':  # Windows
        # 常见安装路径
        base_dirs = [
            r'C:\Program Files\Tesseract-OCR',
            r'C:\Program Files (x86)\Tesseract-OCR',
            os.path.expanduser(r'~\AppData\Local\Programs\Tesseract-OCR'),
            r'D:\Tesseract-OCR',
            r'E:\Tesseract-OCR',
            r'F:\Tesseract-OCR',
            r'G:\Tesseract-OCR',
        ]
        for bd in base_dirs:
            exe_candidates.append(os.path.join(bd, 'tesseract.exe'))
    exe_candidates.append('tesseract')  # 默认：依赖 PATH

    found_exe = None
    found_tessdata = None

    for exe in exe_candidates:
        if exe == 'tesseract':
            # 检查是否在 PATH 中
            import shutil
            if shutil.which('tesseract'):
                found_exe = 'tesseract'
                break
            else:
                continue
        if os.path.isfile(exe):
            found_exe = exe
            # 同时设置 tessdata 路径
            td = os.path.join(os.path.dirname(exe), 'tessdata')
            if os.path.isdir(td):
                found_tessdata = td
            break

    if found_exe and found_exe != 'tesseract':
        try:
            pytesseract.pytesseract.tesseract_cmd = found_exe
            logger.info(f"[sketch_parser] 已自动配置 Tesseract: {found_exe}")
        except Exception as e:
            logger.warning(f"[sketch_parser] 配置 Tesseract 路径失败: {e}")

    if found_tessdata:
        # 设置 TESSDATA_PREFIX（通过 pytesseract 环境变量）
        try:
            os.environ['TESSDATA_PREFIX'] = found_tessdata
            # pytesseract 的 TESSDATA_PREFIX env 在其内部读取
            logger.info(f"[sketch_parser] 已配置 TESSDATA_PREFIX: {found_tessdata}")
        except Exception as e:
            logger.warning(f"[sketch_parser] 设置 TESSDATA_PREFIX 失败: {e}")

    # 做一次版本检查，确保可调用
    try:
        _ = pytesseract.get_tesseract_version()
        return pytesseract
    except Exception as e:
        logger.info(f"[sketch_parser] Tesseract 无法调用 (即使安装了): {e}")
        return None


def _load_image(image_path: str):
    """加载图片，返回 (cv2_bgr_image, error_msg)。"""
    cv2 = _safe_import_cv2()
    if cv2 is None:
        return None, "未安装 OpenCV，无法读取草图。"
    if not image_path or not os.path.isfile(image_path):
        return None, f"草图文件不存在: {image_path}"
    try:
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.warning(f"[sketch_parser] cv2.imread 失败: {e}")
        return None, "读取草图失败（文件格式或权限问题）。"
    if img is None:
        try:
            from PIL import Image as PILImage
            with PILImage.open(image_path) as pil:
                pil = pil.convert("RGB")
                img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        except Exception as e2:
            logger.warning(f"[sketch_parser] PIL 兜底读取也失败: {e2}")
            return None, "读取草图失败（中文路径或损坏）。"
    return img, None


def _to_gray(img):
    """将 BGR 或灰度图统一转为灰度。"""
    import cv2
    if len(img.shape) == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


# ---------------------------------------------------------------------------
# L1：复杂度评估 —— 判断草图是否过于混乱
# ---------------------------------------------------------------------------

def _assess_complexity(gray_img) -> tuple[bool, str]:
    """返回 (is_complex, reason)。True 表示建议跳过自动识别。"""
    import cv2
    h, w = gray_img.shape[:2]
    total_pixels = h * w

    # 统计轮廓数量
    edges = cv2.Canny(gray_img, 30, 100)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    total_contours = len(contours)

    # 统计大面积文本/花纹（轮廓面积 > 100px 的）
    large_contours = sum(1 for c in contours if cv2.contourArea(c) > 100)

    # 统计连通域数量（排除背景）
    _, labels = cv2.connectedComponents(255 - edges)
    num_labels = len(np.unique(labels))

    logger.debug(
        f"[sketch_parser complexity] contours={total_contours}, "
        f"large={large_contours}, labels={num_labels}")

    # 判断规则：
    # - 轮廓特别多 (>150) 且大面积轮廓也多 (>30) → 可能是文字/花纹堆积
    # - 连通域数量异常多 (>300) → 图像过于复杂
    if total_contours > 150 and large_contours > 30:
        return True, "草图过于复杂（检测到大量轮廓和图案），建议手动输入边距。"
    if num_labels > 300:
        return True, "草图过于复杂（检测到大量连通区域），建议手动输入边距。"

    return False, ""


# ---------------------------------------------------------------------------
# L2：增强的矩形检测 —— 多边形近似 + 矩形度评分
# ---------------------------------------------------------------------------

def _build_binary_masks(cv2, gray_img, color_img=None):
    """构建二值化 mask —— 双策略（Otsu + Canny），覆盖填充类和线条类草图。

    Args:
        gray_img: 灰度图
        color_img: 原始彩色图（未使用，保留接口兼容）
    """
    masks = []
    kernel_small = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))

    # 策略1：Otsu 自适应阈值 + 形态学闭运算（适合填充类、块状文字草图）
    try:
        _, mask_otsu = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        mask_otsu = cv2.morphologyEx(mask_otsu, cv2.MORPH_CLOSE, kernel_small, iterations=1)
        masks.append(("otsu", mask_otsu))
    except Exception:
        pass

    # 策略2：Canny 边缘检测 + 形态学闭运算（适合线条类、手绘边框草图）
    try:
        edges = cv2.Canny(gray_img, 15, 80)
        mask_canny = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel_small, iterations=1)
        masks.append(("canny", mask_canny))
    except Exception:
        pass

    return masks


def _score_as_rectangle(cv2, contour) -> tuple[float, tuple]:
    """评估轮廓作为矩形的得分（0~1），并返回近似矩形的 (x, y, w, h)。

    评分基于：
    1. 多边形边数是否为 4
    2. 内角是否接近 90°
    3. 对边长度是否相等
    4. 轮廓面积与 boundingRect 面积之比（矩形度）
    """
    area = cv2.contourArea(contour)
    if area < 50:
        return 0.0, (0, 0, 0, 0)

    # 多边形近似
    perimeter = cv2.arcLength(contour, True)
    epsilon = 0.02 * perimeter
    approx = cv2.approxPolyDP(contour, epsilon, True)
    n_vertices = len(approx)

    # boundingRect
    x, y, rw, rh = cv2.boundingRect(contour)
    if rw < 15 or rh < 15:
        return 0.0, (x, y, rw, rh)

    rect_area = rw * rh
    if rect_area <= 0:
        return 0.0, (x, y, rw, rh)

    # 矩形度：轮廓面积 / boundingRect 面积
    rectangularity = min(1.0, area / rect_area)

    # 评分组合
    if n_vertices == 4:
        vertex_score = 0.5
    elif 3 <= n_vertices <= 6:
        vertex_score = 0.3
    else:
        vertex_score = 0.0

    score = rectangularity * 0.6 + vertex_score * 0.4
    return score, (x, y, rw, rh)


def _find_two_nested_rectangles(cv2, gray_img, color_img=None):
    """在图上找两个嵌套矩形（外框+内框）。

    使用连通分量分析法：在二值化图上找到独立的白色区域，
    取面积最大的两个候选，验证嵌套关系后返回。

    Returns:
        list of (x, y, w, h, score)，按面积降序。最多返回 2 个。
        找不到时返回空列表。
    """
    h, w = gray_img.shape[:2]
    full_area = h * w
    min_component_area = max(100, int(full_area * 0.001))

    masks = _build_binary_masks(cv2, gray_img, color_img)
    logger.info(f"[sketch_parser] 几何检测：生成 {len(masks)} 种二值化 mask")

    candidates = []
    seen_bboxes = set()

    for mask_name, mask in masks:
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        mask_candidates = 0
        mask_filtered_small = 0
        mask_filtered_score = 0

        for label_id in range(1, num_labels):
            area = stats[label_id, cv2.CC_STAT_AREA]
            if area < min_component_area:
                mask_filtered_small += 1
                continue

            x = stats[label_id, cv2.CC_STAT_LEFT]
            y = stats[label_id, cv2.CC_STAT_TOP]
            ww = stats[label_id, cv2.CC_STAT_WIDTH]
            hh = stats[label_id, cv2.CC_STAT_HEIGHT]

            if ww < 10 or hh < 10:
                mask_filtered_small += 1
                continue

            rect_area = ww * hh
            if rect_area <= 0:
                continue

            component_mask = (labels == label_id).astype(np.uint8) * 255
            cnts, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            cnt = cnts[0]
            perimeter = cv2.arcLength(cnt, True)
            if perimeter > 0:
                approx = cv2.approxPolyDP(cnt, 0.02 * perimeter, True)
                n_vertices = len(approx)
            else:
                n_vertices = 4

            fill_ratio = area / rect_area if rect_area > 0 else 0
            is_hollow = fill_ratio < 0.15

            if is_hollow:
                vertex_score = 0.6 if n_vertices == 4 else (0.4 if 3 <= n_vertices <= 6 else 0.0)
                aspect = min(ww, hh) / max(ww, hh) if ww > 0 and hh > 0 else 0
                aspect_score = min(1.0, aspect) * 0.3
                score = vertex_score + aspect_score
            else:
                rectangularity = min(1.0, fill_ratio)
                vertex_score = 0.5 if n_vertices == 4 else (0.3 if 3 <= n_vertices <= 6 else 0.0)
                score = rectangularity * 0.6 + vertex_score * 0.4

            if score < 0.2:
                mask_filtered_score += 1
                continue

            rx_q = round(x / 10) * 10
            ry_q = round(y / 10) * 10
            rw_q = round(ww / 10) * 10
            rh_q = round(hh / 10) * 10
            key = (rx_q, ry_q, rw_q, rh_q)
            if key in seen_bboxes:
                continue
            seen_bboxes.add(key)

            candidates.append((x, y, ww, hh, score, rect_area))
            mask_candidates += 1

        logger.info(
            f"[sketch_parser] mask={mask_name}: 连通域数={num_labels - 1}, "
            f"候选={mask_candidates}, 面积过滤={mask_filtered_small}, "
            f"得分过滤={mask_filtered_score}"
        )

    if not candidates:
        logger.warning(
            f"[sketch_parser] 矩形检测失败：所有 mask 均未产生有效候选。"
            f"最小面积阈值={min_component_area}px²，"
            f"建议：草图可能过于模糊或缺少明显的矩形边框。"
        )
        return []

    # 按 boundingRect 面积降序
    candidates.sort(key=lambda c: c[5], reverse=True)

    logger.info(f"[sketch_parser] 矩形候选汇总：{len(candidates)} 个，"
                f"top5面积={[c[5] for c in candidates[:5]]}")

    # 取前 10 个，找最佳嵌套对
    top = candidates[:10]

    best_pair = None
    best_score = -1
    _pair_fail_reasons = []

    for i in range(len(top)):
        outer = top[i]
        ox, oy, ow, oh, os, oa = outer
        for j in range(len(top)):
            if i == j:
                continue
            inner = top[j]
            ix, iy, iw, ih, ins, ina = inner

            if ix < ox - 5 or iy < oy - 5:
                _pair_fail_reasons.append(f"外框外: inner=({ix},{iy}) outer=({ox},{oy})")
                continue
            if ix + iw > ox + ow + 5 or iy + ih > oy + oh + 5:
                _pair_fail_reasons.append(f"外框外2: inner_bottom_right超出outer")
                continue
            if ina >= oa * 0.9:
                _pair_fail_reasons.append(f"面积过近: inner_area={ina:.0f} >= outer_area*0.9={oa*0.9:.0f}")
                continue

            mt = iy - oy
            mb = (oy + oh) - (iy + ih)
            ml = ix - ox
            mr = (ox + ow) - (ix + iw)
            if min(mt, mb, ml, mr) < 2:
                _pair_fail_reasons.append(f"边距<2px: T={mt:.0f} B={mb:.0f} L={ml:.0f} R={mr:.0f}")
                continue

            score_sum = os + ins + 0.1 * (oa / max(1, ina))
            if score_sum > best_score:
                best_score = score_sum
                best_pair = (outer, inner)

    if best_pair:
        outer, inner = best_pair
        logger.info(
            f"[sketch_parser] 嵌套矩形检测成功：outer=({outer[0]},{outer[1]},{outer[2]},{outer[3]}) "
            f"score={outer[4]:.2f}, inner=({inner[0]},{inner[1]},{inner[2]},{inner[3]}) "
            f"score={inner[4]:.2f}, best_score={best_score:.2f}"
        )
        return [
            (outer[0], outer[1], outer[2], outer[3], outer[4]),
            (inner[0], inner[1], inner[2], inner[3], inner[4]),
        ]

    logger.warning(
        f"[sketch_parser] 嵌套验证失败（尝试过{len(_pair_fail_reasons)}对候选），"
        f"原因={_pair_fail_reasons[:5]}"
    )

    # 兜底：面积最大两个（不要求嵌套关系）
    if len(top) >= 2:
        logger.warning(
            f"[sketch_parser] 使用兜底策略：取面积最大的2个候选作为outer/inner"
        )
        return [
            (top[0][0], top[0][1], top[0][2], top[0][3], top[0][4]),
            (top[1][0], top[1][1], top[1][2], top[1][3], top[1][4]),
        ]
    return []


# ---------------------------------------------------------------------------
# L3：数字检测与 OCR 读取
# ---------------------------------------------------------------------------

def _find_number_regions(cv2, gray_img, roi: tuple, max_regions: int = 5) -> list[tuple]:
    """在指定 ROI 区域内找到疑似数字的连通区域（含相邻字符合并）。

    对每个数字找框后，做 X 轴方向邻近合并（把同一个数字的多个字符合并成一个完整区域）。
    这样 OCR 时一次传入 "133" 完整串，而不是单个 "1"/"3"/"3"。
    """
    x, y, w, h = roi
    if w < 5 or h < 5:
        return []

    roi_img = gray_img[y:y + h, x:x + w]

    if roi_img.size == 0:
        return []

    # 尝试多种二值化方法，增加小字检出率
    binaries = []
    # 方法1：OTSU
    try:
        _, binary1 = cv2.threshold(roi_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        binaries.append(binary1)
    except Exception:
        pass
    # 方法2：固定低阈值（针对深黑色文字）
    try:
        median_val = np.median(roi_img)
        thresh_val = max(30, int(median_val * 0.7))
        _, binary2 = cv2.threshold(roi_img, thresh_val, 255, cv2.THRESH_BINARY_INV)
        binaries.append(binary2)
    except Exception:
        pass

    components = []
    for binary in binaries:
        # 形态学：去除噪点，同时膨胀小字符
        kernel_small = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        binary_clean = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel_small, iterations=1)
        # 膨胀：让小数和小点更连通
        binary_dilated = cv2.dilate(binary_clean, kernel_small, iterations=1)

        for b in [binary_clean, binary_dilated]:
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
            roi_area = w * h
            for i in range(1, num_labels):
                rx = stats[i, cv2.CC_STAT_LEFT]
                ry = stats[i, cv2.CC_STAT_TOP]
                rw = stats[i, cv2.CC_STAT_WIDTH]
                rh = stats[i, cv2.CC_STAT_HEIGHT]
                area = stats[i, cv2.CC_STAT_AREA]

                # 过滤：太小的噪点（阈值降低）、太大的非文字区域（矩形边框等）
                if area < 4:
                    continue
                if area > roi_area * 0.3:
                    continue

                # 过滤：宽高比极端（单个字符 0.15~5，合并后整体更宽也接受）
                aspect = rw / max(1, rh)
                if aspect < 0.1 or aspect > 10.0:
                    continue

                components.append([rx, ry, rw, rh, area])

    # 水平方向合并：y 接近且 x 接近的组件合并为同一个"数字串"
    # 多次迭代直到没有可合并的
    changed = True
    while changed:
        changed = False
        for i in range(len(components)):
            for j in range(i + 1, len(components)):
                c1, c2 = components[i], components[j]
                if c1 is None or c2 is None:
                    continue
                cx1, cy1, cw1, ch1, ca1 = c1
                cx2, cy2, cw2, ch2, ca2 = c2
                # y 中心接近（在 1.5 * 较高字符高度内）
                yc1, yc2 = cy1 + ch1 / 2, cy2 + ch2 / 2
                y_tol = max(ch1, ch2) * 1.5
                if abs(yc1 - yc2) > y_tol:
                    continue
                # x 距离不大（在较宽字符宽度的 2 倍以内）
                x_right_1 = cx1 + cw1
                x_right_2 = cx2 + cw2
                x_gap = abs(x_right_1 - cx2) if x_right_1 < cx2 else abs(x_right_2 - cx1)
                if x_gap > max(cw1, cw2) * 2.5:
                    continue
                # 合并
                nx = min(cx1, cx2)
                ny = min(cy1, cy2)
                nw = max(cx1 + cw1, cx2 + cw2) - nx
                nh = max(cy1 + ch1, cy2 + ch2) - ny
                na = ca1 + ca2
                components[i] = [nx, ny, nw, nh, na]
                components[j] = None
                changed = True
        components = [c for c in components if c is not None]

    regions = []
    for comp in components:
        rx, ry, rw, rh, area = comp
        # 过滤掉合并后仍然太小或太大的
        if area < 10:
            continue
        if area > roi_area * 0.4:
            continue
        regions.append((x + rx, y + ry, rw, rh, area))

    # 按面积降序取前 max_regions
    regions.sort(key=lambda r: r[4], reverse=True)
    return regions[:max_regions]


def _ocr_region(cv2, gray_img, region: tuple, tesseract) -> Optional[float]:
    """对指定区域进行 OCR，尝试读出数字。

    策略：尝试多种 PSM（8/6/10/13 等），返回解析出的**合法数字列表中面积最小的那个**
    （避免 ROI 里混进"邻居"的大数字）。

    Returns:
        float or None — 识别出的数字（cm 值），失败返回 None
    """
    x, y, w, h, area = region

    # 扩展区域一些边距
    pad = max(2, int(0.1 * max(w, h)))
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(gray_img.shape[1], x + w + pad)
    y2 = min(gray_img.shape[0], y + h + pad)

    crop = gray_img[y1:y2, x1:x2]

    if crop.size == 0:
        return None

    # 强制放大 3x（小字符/单字符识别率显著提高）
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    # 转为 PIL Image
    from PIL import Image as PILImage
    pil_img = PILImage.fromarray(crop)

    # 多种 PSM 依次尝试：8=单字, 13=原始行, 7=单行, 10=单字符, 6=块, 11=稀疏文本
    psm_list = [8, 13, 7, 10, 6, 11]
    all_nums = []  # (val, bounding_area_proxy)

    for psm in psm_list:
        config = rf'--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789.'
        try:
            text = tesseract.image_to_string(pil_img, config=config).strip()
        except Exception:
            continue

        # 解析出所有合法数字
        text = text.replace(',', '.').replace(' ', '')
        matches = list(re.finditer(r'\d+\.?\d*', text))
        for m in matches:
            try:
                val = float(m.group())
            except ValueError:
                continue
            # 合理范围：0.5 ~ 500 cm
            if 0.5 <= val <= 500:
                # 数字的"字符数+整数位数"越少，越可能是单值（越小越简单）
                complexity = len(m.group()) * 10 + abs(int(val) - int(val))
                all_nums.append((val, complexity))

    if not all_nums:
        return None

    # 返回"最简单（字符少）的那个"；单字符（6、9、8）优先于多字符（14.6）
    # 但对于 [100, 500] 的值，检查是否像是丢了小数点
    repaired_nums = []
    for val, complexity in all_nums:
        repaired_nums.append((val, complexity))
        # 尝试恢复可能丢失的小数点
        if 100 <= val <= 500:
            half_val = val / 10.0
            if 0.5 <= half_val <= 50:
                repaired_nums.append((half_val, complexity + 5))  # 加一点复杂度惩罚

    repaired_nums.sort(key=lambda t: t[1])
    return repaired_nums[0][0]


def _ocr_region_aggressive(cv2, gray_img, region: tuple, tesseract) -> Optional[float]:
    """高鲁棒性区域 OCR：针对小字体/弱对比度文字，使用多种预处理组合。

    Returns:
        float or None — 识别出的数字（cm 值），失败返回 None
    """
    x, y, w, h = region

    if w < 3 or h < 3:
        return None

    # 扩展区域一些边距
    pad = max(3, int(0.15 * max(w, h)))
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(gray_img.shape[1], x + w + pad)
    y2 = min(gray_img.shape[0], y + h + pad)

    crop = gray_img[y1:y2, x1:x2]

    if crop.size == 0:
        return None

    from PIL import Image as PILImage
    import re

    results = []

    # 预处理变体（精简：仅 2 个变体，原版 4 个）
    variants = []

    # 变体 1: 4x 放大 + OTSU 二值化
    scale = 4
    scaled = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    try:
        _, binary = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        binary = cv2.dilate(binary, kernel, iterations=1)
        variants.append(('binary_4x', binary))
    except Exception:
        pass

    # 变体 2: 4x 放大原图
    variants.append(('raw_4x', scaled))

    # 精简 PSM：仅 3 个模式（原版 5 个）
    psm_list = [8, 7, 6]  # 8=单字, 7=单行, 6=块

    for vname, variant in variants:
        pil_img = PILImage.fromarray(variant)
        for psm in psm_list:
            config = rf'--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789.'
            try:
                text = tesseract.image_to_string(pil_img, config=config).strip()
            except Exception:
                continue

            text = text.replace(',', '.').replace(' ', '').replace('\n', '')
            # 尝试解析数字
            for pattern in [r'\d+\.?\d*', r'\d+']:
                matches = list(re.finditer(pattern, text))
                for m in matches:
                    try:
                        val = float(m.group())
                    except ValueError:
                        continue
                    if 0.5 <= val <= 450:
                        results.append((val, vname, psm))

    if not results:
        return None

    # 对结果进行评分：偏好整数值和合理值
    def _score_result(val, vname, psm):
        score = 0.0
        # 整数偏好
        if val == int(val):
            score += 0.2
        # 合理范围偏好 (边距通常 1-60)
        if 1 <= val <= 60:
            score += 0.3
        # PSM 8 (单字) 和 PSM 10 (单字符) 更可靠
        if psm in (8, 10):
            score += 0.2
        # 特定预处理偏好
        if 'binary' in vname or 'adaptive' in vname:
            score += 0.1
        # 偏好有小数点的值 (如果有的话)
        if '.' in str(val):
            score += 0.1
        return score

    results.sort(key=lambda r: _score_result(*r), reverse=True)

    # 选择最高分的值
    best_val = results[0][0]

    # 小数点恢复：仅对明显异常的值 (>450) 尝试
    if 450 < best_val <= 900:
        half_val = best_val / 10.0
        if 0.5 <= half_val <= 300:
            # 检查是否也检测到了 half_val
            for val, _, _ in results:
                if abs(val - half_val) < 0.5:
                    return half_val  # 直接返回检测到的小数值
            return half_val  # 否则返回修正后的值

    return best_val


def _ocr_full_image(cv2, gray_img, tesseract, fast_mode: bool = False) -> list[tuple]:
    """对整张图像做 OCR，返回识别到的所有 (value, x_center, y_center, conf)。

    优化方案（v2 快速版）：
      1. 精简尺度：仅 3x + 5x（覆盖 10-20px 小字 → 30-100px 最佳识别高度）
      2. 精简预处理：仅原始 + OTSU 二值化（覆盖大多数对比度场景）
      3. 精简 PSM：仅 11(sparse) + 6(block) + 8(single word)
      4. 早停机制：找到 ≥6 个不同数值后立即返回
      总 OCR 调用：最多 2×2×3=12 次（原版 ~350 次）

    fast_mode=True: 用于全图兜底，使用 1.5x/2x 低倍率（大图避免内存爆炸）
    fast_mode=False: 用于子图 OCR，使用 3x/5x 高倍率（小图提高小字识别率）
    """
    from PIL import Image as PILImage
    import re

    h_img, w_img = gray_img.shape[:2]

    # 尺度选择：fast_mode 用低倍率（全图兜底），否则用中高倍率（子图 OCR）
    # 2x 快速扫描大字（133/76），4x 精确识别小字（6/10/14.6/42.4）
    if fast_mode:
        scales = [1.5, 2.0]
    else:
        scales = [2.0, 4.0]

    all_raw_chars = []  # (text, x_center, y_center, conf, w, h)
    _seen_values = set()  # 早停用：已发现的不同数值

    for scale in scales:
        gray_scaled = cv2.resize(gray_img, None, fx=scale, fy=scale,
                                 interpolation=cv2.INTER_CUBIC)

        # 精简预处理变体：原始 + OTSU
        variants = [('orig', gray_scaled)]
        try:
            _, otsu = cv2.threshold(gray_scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            variants.append(('otsu', otsu))
        except Exception:
            pass

        for vname, variant in variants:
            pil_img = PILImage.fromarray(variant)
            # 精简 PSM：11(sparse text) + 6(block) + 8(single word)
            for psm in [11, 6, 8]:
                config_data_str = rf'--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789.'
                try:
                    data = tesseract.image_to_data(
                        pil_img, config=config_data_str,
                        output_type=tesseract.Output.DICT,
                    )
                except Exception:
                    continue

                if not data or 'text' not in data:
                    continue

                n = len(data.get('text', []))
                for i in range(n):
                    text = str(data['text'][i]).strip()
                    if not text:
                        continue
                    try:
                        conf = int(data.get('conf', [50] * n)[i])
                    except Exception:
                        conf = 50
                    if conf < -1:
                        continue
                    try:
                        x_left = int(data.get('left', [0] * n)[i]) / scale
                        y_top = int(data.get('top', [0] * n)[i]) / scale
                        ww = int(data.get('width', [0] * n)[i]) / scale
                        hh = int(data.get('height', [0] * n)[i]) / scale
                    except Exception:
                        continue
                    x_c = x_left + ww / 2
                    y_c = y_top + hh / 2
                    if re.fullmatch(r'[.\s]+', text):
                        continue
                    all_raw_chars.append((text, x_c, y_c, conf, ww, hh))
                    # 早停检查：跟踪不同数值
                    for m in re.finditer(r'\d+\.?\d*', text):
                        try:
                            _v = float(m.group())
                            if 0.5 <= _v <= 500:
                                _seen_values.add(round(_v, 1))
                        except ValueError:
                            pass

            # 早停：已找到 ≥6 个不同数值，足够用于 8 字段分配
            if len(_seen_values) >= 6:
                logger.info(f"[sketch_parser] OCR 早停：已发现 {len(_seen_values)} 个不同数值 {_seen_values}，跳过剩余变体")
                break
        if len(_seen_values) >= 6:
            break

    # ---------- 多尺度去重：先把 (几乎同位置+同数值) 的字符合并，只留最高 conf 的 ----------
    # 避免不同尺度重复识别把 "60.5"+"60.9" 串成 "60.560.9"
    merged_chars = []  # (text, xc, yc, conf, ww, hh)
    for ch in all_raw_chars:
        txt, xc, yc, cf, cw, chh = ch
        # 若 text 不是纯数字+小数点，跳过（保持不合并）
        is_pure = bool(re.fullmatch(r'[\d.]+', txt))
        matched = False
        if is_pure:
            # 在 merged_chars 中找"位置相同"的条目（允许 15 像素偏差）且 数值相似 → 合并
            for j, mch in enumerate(merged_chars):
                mtxt, mxc, myc, mcf, mcw, mchh = mch
                if abs(mxc - xc) > max(25, max(mcw, cw)):
                    continue
                if abs(myc - yc) > max(25, max(mchh, chh)):
                    continue
                # 解析为数字比较数值
                try:
                    val_a = float(txt)
                    val_b = float(mtxt)
                except ValueError:
                    continue
                if abs(val_a - val_b) <= max(0.1, max(val_a, val_b) * 0.03):
                    # 同位置同数值：保留 conf 更高的，位置加权平均，宽度高度取 max
                    if cf > mcf:
                        new_xc = (mxc * mcf + xc * cf) / (mcf + cf)
                        new_yc = (myc * mcf + yc * cf) / (mcf + cf)
                        new_cf = cf
                        new_txt = txt  # 选置信度高的那个原文
                    else:
                        new_xc = mxc
                        new_yc = myc
                        new_cf = mcf
                        new_txt = mtxt
                    new_cw = max(mcw, cw)
                    new_chh = max(mchh, chh)
                    merged_chars[j] = (new_txt, new_xc, new_yc, new_cf, new_cw, new_chh)
                    matched = True
                    break
        if not matched:
            merged_chars.append(ch)

    detected_chars = merged_chars
    detected_chars.sort(key=lambda c: c[2])
    lines = []
    for ch in detected_chars:
        txt, xc, yc, cf, cw, chh = ch
        assigned = False
        for line in lines:
            max_h = max(c[5] for c in line['chars'])
            if abs(line['yc_avg'] - yc) < max(max_h * 1.6, 10):
                line['chars'].append(ch)
                line['yc_avg'] = sum(c[2] for c in line['chars']) / len(line['chars'])
                assigned = True
                break
        if not assigned:
            lines.append({'chars': [ch], 'yc_avg': yc})

    results = []
    for line in lines:
        chars = sorted(line['chars'], key=lambda c: c[1])
        tokens = []
        cur_text = ''
        cur_xs = []
        cur_ys = []
        cur_cfs = []
        prev_right = None
        prev_h = None

        for txt, xc, yc, cf, cw, chh in chars:
            x_left = xc - cw / 2
            x_right = xc + cw / 2
            if cur_text:
                gap = x_left - prev_right
                same_cluster = (gap < max(cw, 3) * 2.2 and
                                abs(yc - sum(cur_ys) / len(cur_ys)) < max(chh, prev_h) * 1.8)
                if not same_cluster:
                    tokens.append((cur_text,
                                   sum(cur_xs) / len(cur_xs),
                                   sum(cur_ys) / len(cur_ys),
                                   sum(cur_cfs) / len(cur_cfs)))
                    cur_text = ''
                    cur_xs, cur_ys, cur_cfs = [], [], []

            if not cur_text:
                if not re.match(r'[\d.]', txt):
                    continue

            cur_text += txt
            cur_xs.append(xc)
            cur_ys.append(yc)
            cur_cfs.append(cf)
            prev_right = x_right
            prev_h = chh

        if cur_text:
            tokens.append((cur_text,
                           sum(cur_xs) / len(cur_xs),
                           sum(cur_ys) / len(cur_ys),
                           sum(cur_cfs) / len(cur_cfs)))

        for token_text, x_center, y_center, avg_conf in tokens:
            matches = re.findall(r'\d+\.?\d*', token_text)
            if not matches:
                continue
            try:
                val = float(matches[0])
            except ValueError:
                continue
            if 0.5 <= val <= 500:
                conf = min(0.95, 0.5 + avg_conf / 200.0)
                results.append((val, x_center, y_center, conf))

    # 多尺度导致同一数字可能出现多次 → 去重（同一数值且坐标接近的，只留最高分）
    dedup = {}
    for val, xc, yc, conf in results:
        key = None
        # 找一个已有条目：数值相同（误差<0.05）且坐标距离 < 25 像素 → 认为重复
        for k, (dv, dxc, dyc, dc) in list(dedup.items()):
            if abs(dv - val) < max(0.05, val * 0.02) and abs(dxc - xc) < 30 and abs(dyc - yc) < 30:
                key = k
                break
        if key is None:
            dedup[(val, xc, yc)] = (val, xc, yc, conf)
        else:
            # 保留较高置信度，位置取加权平均
            ov, oxc, oyc, oc = dedup[key]
            if conf > oc:
                new_xc = (oxc * oc + xc * conf) / (oc + conf)
                new_yc = (oyc * oc + yc * conf) / (oc + conf)
                dedup[key] = (val, new_xc, new_yc, conf)
    results = list(dedup.values())

    # 如果上面方法没找到，兜底用 image_to_string 简单扫描整个图
    if not results:
        try:
            pil_img = PILImage.fromarray(gray_img)
            config_all = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789.'
            text_all = tesseract.image_to_string(pil_img, config=config_all)
            for match in re.finditer(r'\d+\.?\d*', text_all):
                try:
                    val = float(match.group())
                    if 0.5 <= val <= 500:
                        results.append((val, w_img / 2, h_img / 2, 0.3))
                except ValueError:
                    continue
        except Exception:
            pass

    # --- 小数点恢复：Tesseract 经常丢失小数点，把 "44.5" 读成 "445" ---
    # 只处理明显不合理的值 (> 450)：333 可能是正确的 total_w，不应误除
    # 445/424 等在 ~450 范围内可能合理，但 4450/3330 必定是丢了小数点
    repaired = []
    removed_originals = set()
    for val, xc, yc, conf in results:
        should_divide = False
        # 仅当值远超合理范围 (>450) 时才尝试除以 10
        # 合理范围：外框最大 ~450, 内框最大 ~400, 边距最大 ~300
        if val > 450:
            half_val = val / 10.0
            # 除以 10 后在合理范围 (0.5 ~ 300) 才认为可能丢了小数点
            if 0.5 <= half_val <= 300:
                # 检查是否已有一个相近的值存在 (说明小数点其实没丢)
                already_has_smaller = False
                for rv, rxc, ryc, rc in results:
                    if rv != val and abs(rv - half_val) < 0.5:
                        already_has_smaller = True
                        break
                if not already_has_smaller:
                    should_divide = True

        if should_divide:
            # 用修正后的值替代原值（降低置信度）
            repaired.append((half_val, xc, yc, conf * 0.75))
            removed_originals.add((round(val, 1), round(xc, 1), round(yc, 1)))
        else:
            # 检查此值是否是某个已修正值的原始版本（在相同位置附近）
            is_original_of_repaired = False
            for rv, rxc, ryc, rc in repaired:
                if abs(rv - val / 10.0) < 0.5 and abs(rxc - xc) < 30 and abs(ryc - yc) < 30:
                    is_original_of_repaired = True
                    break
            if not is_original_of_repaired:
                repaired.append((val, xc, yc, conf))

    # 去重：同一位置只保留一个值
    # 选择规则：位数多 > conf高 > 数值小（OCR更易多读数字而非少读）
    dedup_final = {}
    for val, xc, yc, conf in repaired:
        key = None
        for k, (dv, dxc, dyc, dc) in list(dedup_final.items()):
            if abs(dxc - xc) < 30 and abs(dyc - yc) < 30:
                key = k
                break
        if key is None:
            dedup_final[(val, xc, yc)] = (val, xc, yc, conf)
        else:
            ov, oxc, oyc, oc = dedup_final[key]
            val_digits = len(str(int(val))) if val > 0 else 0
            ov_digits = len(str(int(ov))) if ov > 0 else 0
            if val_digits > ov_digits:
                dedup_final[key] = (val, xc, yc, conf)
            elif val_digits == ov_digits and conf > oc:
                dedup_final[key] = (val, xc, yc, conf)
            elif val_digits == ov_digits and abs(conf - oc) < 0.1 and val < ov:
                dedup_final[key] = (val, xc, yc, conf)
    results = list(dedup_final.values())

    return results


def _assign_ocr_values_to_fields(ocr_hits, outer_rect, inner_rect,
                                 h_img, w_img,
                                 target_w_hint: float = 0.0,
                                 target_h_hint: float = 0.0) -> dict:
    """根据 OCR 识别数值的空间位置，把每个数值分配到最可能的字段。

    使用直接几何间隙区域分配：
    - 内框区域 → inner_w / inner_h
    - 外框与内框之间的四个间隙区域 → margin_top/bottom/left/right
    - 外框外侧 → total_w / total_h

    Args:
        ocr_hits: [(value, x_center, y_center, confidence), ...]
        outer_rect / inner_rect: (x, y, w, h)
        target_w_hint / target_h_hint: 目标尺寸提示

    Returns:
        dict: key → (value, confidence)
    """
    result = {
        'total_w': (0.0, 0),
        'total_h': (0.0, 0),
        'inner_w': (0.0, 0),
        'inner_h': (0.0, 0),
        'margin_top': (0.0, 0),
        'margin_bottom': (0.0, 0),
        'margin_left': (0.0, 0),
        'margin_right': (0.0, 0),
    }

    if not ocr_hits:
        return result

    ox, oy, ow, oh = outer_rect
    ix, iy, iw, ih = inner_rect

    gap_top = max(0, iy - oy)
    gap_bottom = max(0, (oy + oh) - (iy + ih))
    gap_left = max(0, ix - ox)
    gap_right = max(0, (ox + ow) - (ix + iw))

    # Step 1: Classify each OCR hit into a geometric region
    region_hits = {
        'inner': [], 'top': [], 'bottom': [],
        'left': [], 'right': [], 'outside': [],
    }

    for val, xc, yc, conf in ocr_hits:
        if val <= 0:
            continue

        # Inside inner rectangle?
        if ix <= xc <= ix + iw and iy <= yc <= iy + ih:
            region_hits['inner'].append((val, xc, yc, conf))
            continue

        # Top gap: between outer top and inner top
        if oy - gap_top * 0.3 <= yc <= iy + gap_top * 0.3 and ox <= xc <= ox + ow:
            region_hits['top'].append((val, xc, yc, conf))
            continue

        # Bottom gap: between inner bottom and outer bottom
        if iy + ih - gap_bottom * 0.3 <= yc <= oy + oh + gap_bottom * 0.3 and ox <= xc <= ox + ow:
            region_hits['bottom'].append((val, xc, yc, conf))
            continue

        # Left gap: between outer left and inner left
        if ox - gap_left * 0.3 <= xc <= ix + gap_left * 0.3 and oy <= yc <= oy + oh:
            region_hits['left'].append((val, xc, yc, conf))
            continue

        # Right gap: between inner right and outer right
        if ix + iw - gap_right * 0.3 <= xc <= ox + ow + gap_right * 0.3 and oy <= yc <= oy + oh:
            region_hits['right'].append((val, xc, yc, conf))
            continue

        # Outside outer rectangle
        region_hits['outside'].append((val, xc, yc, conf))

    logger.info(f"[sketch_parser] 几何区域分类: inner={len(region_hits['inner'])}, "
                f"top={len(region_hits['top'])}, bottom={len(region_hits['bottom'])}, "
                f"left={len(region_hits['left'])}, right={len(region_hits['right'])}, "
                f"outside={len(region_hits['outside'])}")

    # 计算合理的边距值范围：
    # 1. 排除接近外框总尺寸的值（这些是外框尺寸，不是边距）
    # 2. 如果有target hints，用它们作为参考
    _outer_w_cm = target_w_hint if target_w_hint > 0 else 0
    _outer_h_cm = target_h_hint if target_h_hint > 0 else 0
    
    def _is_suspicious_outer_dim(val):
        """检查值是否可能是外框总尺寸而非边距。"""
        if _outer_w_cm > 0 and abs(val - _outer_w_cm) <= max(3.0, _outer_w_cm * 0.05):
            return True
        if _outer_h_cm > 0 and abs(val - _outer_h_cm) <= max(3.0, _outer_h_cm * 0.05):
            return True
        return False

    def _best_value_for_margin(hits, region_name):
        """选择最合适的边距值，排除外框总尺寸等可疑值。"""
        if not hits:
            return None
        # 去重：相同数值取最高置信度
        dedup = {}
        for val, xc, yc, conf in hits:
            vround = round(val, 1)
            if vround not in dedup or conf > dedup[vround][3]:
                dedup[vround] = (val, xc, yc, conf)
        
        candidates = list(dedup.values())
        
        # 优先选择不那么可疑的值（不是外框总尺寸）
        clean = [(v, x, y, c) for v, x, y, c in candidates 
                 if not _is_suspicious_outer_dim(v)]
        
        if clean:
            # 从干净候选中选择置信度最高的
            return max(clean, key=lambda h: h[3])
        
        # 如果所有值都可疑，返回None让下游处理（几何OCR/方向标签会填充正确值）
        if candidates:
            logger.warning(f"[sketch_parser] {region_name}区域所有候选值都可疑，跳过由下游处理")
            return None
        return None

    def _best_value(hits):
        if not hits:
            return None
        dedup = {}
        for val, xc, yc, conf in hits:
            vround = round(val, 1)
            if vround not in dedup or conf > dedup[vround][3]:
                dedup[vround] = (val, xc, yc, conf)
        best = max(dedup.values(), key=lambda h: h[3])
        return best

    # Step 2: Assign margin values
    for region, key in [('top', 'margin_top'), ('bottom', 'margin_bottom'),
                         ('left', 'margin_left'), ('right', 'margin_right')]:
        best = _best_value_for_margin(region_hits[region], region)
        if best:
            val, xc, yc, conf = best
            result[key] = (val, min(100, int(conf)))
            logger.info(f"[sketch_parser] 分配 {key}={val} (region={region}, conf={conf:.0f})")

    # Step 3: Assign inner values
    inner_hits = region_hits['inner']
    if inner_hits:
        # Inner values can be inside inner rect or in the gaps
        # Try to find inner_w (wide, in the inner area) and inner_h (tall, in the inner area)
        # Strategy: use the two most confident values in the inner region
        sorted_inner = sorted(inner_hits, key=lambda h: h[3], reverse=True)
        if len(sorted_inner) >= 1:
            val, xc, yc, conf = sorted_inner[0]
            # Assign based on position: upper half → inner_w, lower half → inner_h
            if yc < iy + ih * 0.5:
                result['inner_w'] = (val, min(100, int(conf)))
            else:
                result['inner_h'] = (val, min(100, int(conf)))
        if len(sorted_inner) >= 2:
            val, xc, yc, conf = sorted_inner[1]
            if yc < iy + ih * 0.5 and result['inner_w'][0] == 0:
                result['inner_w'] = (val, min(100, int(conf)))
            elif yc >= iy + ih * 0.5 and result['inner_h'][0] == 0:
                result['inner_h'] = (val, min(100, int(conf)))

    # If inner not found, look for values in gap regions that might be inner dimensions
    if result['inner_w'][0] == 0 or result['inner_h'][0] == 0:
        # Inner values might be in the bottom or right gap
        if result['inner_w'][0] == 0:
            for val, xc, yc, conf in region_hits.get('bottom', []):
                if abs(xc - (ix + iw / 2)) < iw * 0.8:
                    result['inner_w'] = (val, min(100, int(conf)))
                    break
        if result['inner_h'][0] == 0:
            for val, xc, yc, conf in region_hits.get('right', []):
                if abs(yc - (iy + ih / 2)) < ih * 0.8:
                    result['inner_h'] = (val, min(100, int(conf)))
                    break

    # Step 4: Assign total_w and total_h from outside region
    outside_hits = region_hits['outside']
    if outside_hits:
        sorted_outside = sorted(outside_hits, key=lambda h: h[3], reverse=True)
        # Find the most horizontal value (wide) → total_w
        # Find the most vertical value (tall) → total_h
        for val, xc, yc, conf in sorted_outside:
            if result['total_w'][0] == 0 and abs(yc - (oy + oh / 2)) < oh * 0.4:
                result['total_w'] = (val, min(100, int(conf)))
                continue
            if result['total_h'][0] == 0 and abs(xc - (ox + ow / 2)) < ow * 0.4:
                result['total_h'] = (val, min(100, int(conf)))
                continue

    # Step 5: Fill missing with target hints or geometry
    if result['total_w'][0] == 0 and target_w_hint > 0:
        result['total_w'] = (target_w_hint, 5)
    if result['total_h'][0] == 0 and target_h_hint > 0:
        result['total_h'] = (target_h_hint, 5)

    # If still missing, use geometry (outer - margin_sum)
    tw = result['total_w'][0]
    th = result['total_h'][0]
    mt = result['margin_top'][0]
    mb = result['margin_bottom'][0]
    ml = result['margin_left'][0]
    mr = result['margin_right'][0]

    if result['inner_w'][0] == 0 and tw > 0 and (ml > 0 or mr > 0):
        result['inner_w'] = (max(0.0, tw - ml - mr), 3)
    if result['inner_h'][0] == 0 and th > 0 and (mt > 0 or mb > 0):
        result['inner_h'] = (max(0.0, th - mt - mb), 3)

    # Log results
    for key in result:
        if result[key][0] > 0:
            logger.info(f"[sketch_parser] 最终分配 {key}={result[key][0]:.1f} (conf={result[key][1]})")

    return result


def _score_assignment_consistency(assignment: dict) -> float:
    """评估一组分配结果的几何自洽性得分（0~1，越高越自洽）。"""
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

    score = 0.0
    fields_with_values = sum(1 for v in [tw, th, iw, ih, mt, mb, ml, mr] if v > 0)
    score += 0.15 * (fields_with_values / 8.0)

    # 奖励：两个内框尺寸都有值
    if iw > 0 and ih > 0:
        score += 0.1

    # 奖励：四个边距都有值
    margins_with_values = sum(1 for v in [mt, mb, ml, mr] if v > 0)
    if margins_with_values >= 3:
        score += 0.1
    if margins_with_values >= 4:
        score += 0.1

    # 惩罚：内框超过外框（不可能）
    if iw > 0 and tw > 0 and iw > tw:
        score -= 0.5
    if ih > 0 and th > 0 and ih > th:
        score -= 0.5

    # 惩罚：边距为负
    if ml < 0 or mr < 0 or mt < 0 or mb < 0:
        score -= 0.3

    # 奖励：内框+边距 ≈ 外框（水平方向）
    if iw > 0 and ml > 0 and mr > 0:
        h_diff = abs(tw - iw - (ml + mr))
        h_tol = max(2.0, tw * 0.10)
        if h_diff <= h_tol:
            score += 0.4
        elif h_diff <= h_tol * 2:
            score += 0.15
        else:
            score -= 0.1

    # 奖励：内框+边距 ≈ 外框（垂直方向）
    if ih > 0 and mt > 0 and mb > 0:
        v_diff = abs(th - ih - (mt + mb))
        v_tol = max(2.0, th * 0.10)
        if v_diff <= v_tol:
            score += 0.4
        elif v_diff <= v_tol * 2:
            score += 0.15
        else:
            score -= 0.1

    # 奖励：边距 < 外框（合理范围）
    if tw > 0 and ml > tw * 0.9:
        score -= 0.2
    if tw > 0 and mr > tw * 0.9:
        score -= 0.2
    if th > 0 and mt > th * 0.9:
        score -= 0.2
    if th > 0 and mb > th * 0.9:
        score -= 0.2

    # [通用修复 2026-08-15] 移除"边距 < 内框"检查
    # 旧代码：边距>内框90%时扣0.1分，但非对称设计(如234x60)中边距(112)远大于内框(86)是合法的
    # 该检查会错误惩罚正确分配，同时无法有效区分错误分配
    # 几何一致性检查(total-inner=margin_sum)已足够验证正确性

    return max(0.0, min(1.0, score))


def _value_based_assignment(ocr_hits, outer_rect, inner_rect,
                            target_w_cm: float, target_h_cm: float) -> dict:
    """基于数值大小和几何约束分配 OCR 值到字段。

    策略：
      1. 按数值大小排序
      2. 用目标尺寸验证：如果最大检测值远小于目标外框，说明检测值是内框/边距
      3. 穷举所有合理分配方案（尝试不同的字段排序组合），选几何自洽性最高的
      4. 利用 OCR 值的空间位置作为分配偏好
      5. 没有目标尺寸时用启发式分配
    """
    result = {
        'total_w': (0.0, 0), 'total_h': (0.0, 0),
        'inner_w': (0.0, 0), 'inner_h': (0.0, 0),
        'margin_top': (0.0, 0), 'margin_bottom': (0.0, 0),
        'margin_left': (0.0, 0), 'margin_right': (0.0, 0),
    }

    if not ocr_hits:
        return result

    # 转换置信度到 1-10 整数范围（与空间映射一致）
    def _to_conf10(c: float) -> int:
        if c <= 1.0:
            return max(1, min(10, int(c * 10)))
        return max(1, min(10, int(c)))

    # 保存原始 OCR 位置信息用于空间偏好
    ocr_with_pos = [(v, x, y, _to_conf10(c)) for v, x, y, c in ocr_hits if v > 0]
    if not ocr_with_pos:
        return result

    # 计算每个值的"边距倾向"：
    # - 靠近右边缘的值 → 更可能是 margin_right
    # - 靠近下边缘的值 → 更可能是 margin_bottom
    # - 靠近中心的值 → 更可能是 inner
    ox, oy, ow, oh = outer_rect
    img_w = ox + ow
    img_h = oy + oh

    def _spatial_margin_bias(x, y):
        """返回空间位置的边距倾向：正值=边距倾向，负值=内框倾向。"""
        right_bias = (x - ox) / max(1, ow)  # 0=left, 1=right
        bottom_bias = (y - oy) / max(1, oh)  # 0=top, 1=bottom
        # 如果靠近右边缘或下边缘，增加边距倾向
        bias = 0.0
        if right_bias > 0.6:
            bias += (right_bias - 0.6) * 2  # 靠近右边
        if bottom_bias > 0.6:
            bias += (bottom_bias - 0.6) * 2  # 靠近下边
        # 如果靠近中心，增加内框倾向
        center_x = (ox + ow / 2)
        center_y = (oy + oh / 2)
        dist_to_center = ((x - center_x) ** 2 + (y - center_y) ** 2) ** 0.5
        center_dist_ratio = dist_to_center / max(1, min(ow, oh) / 2)
        if center_dist_ratio < 0.5:
            bias -= (0.5 - center_dist_ratio) * 2  # 靠近中心 → 内框倾向
        return bias

    # 为每个值计算边距倾向
    spatial_biases = [(v, c, _spatial_margin_bias(x, y), x, y) for v, x, y, c in ocr_with_pos]
    # 按边距倾向排序：倾向值越大 → 越可能是边距
    spatial_biases.sort(key=lambda x: x[2], reverse=True)

    # 按边距倾向重新排列值：倾向最大的（最可能是边距）排在后面
    # 这样在候选生成时，排在后面的值会被分配到边距
    sorted_hits_spatial = [(v, c) for v, c, _, _, _ in spatial_biases]

    # 同时也保留按数值大小排序的版本
    sorted_hits_value = sorted([(v, c) for v, x, y, c in ocr_with_pos if v > 0],
                               key=lambda x: x[0], reverse=True)

    n = len(sorted_hits_value)

    # 如果有目标尺寸，检查检测值与目标的一致性
    if target_w_cm > 0 and target_h_cm > 0 and n >= 2:
        max_val = max(v for v, _, _, _, _ in spatial_biases)
        target_max = max(target_w_cm, target_h_cm)

        # 情况1：最大检测值接近目标外框 → 检测值包含外框尺寸
        if max_val >= target_max * 0.7:
            candidates = _enumerate_assignments(sorted_hits_value, target_w_cm, target_h_cm)
            best = max(candidates, key=_score_assignment_consistency)
            return best

        # 情况2：最大检测值远小于目标外框 → 检测值都是内框/边距
        # 用空间倾向排序的版本生成候选
        else:
            candidates_value = _enumerate_inner_margin_assignments(
                sorted_hits_value, target_w_cm, target_h_cm)
            candidates_spatial = _enumerate_inner_margin_assignments(
                sorted_hits_spatial, target_w_cm, target_h_cm)
            candidates = candidates_value + candidates_spatial
            best = max(candidates, key=_score_assignment_consistency)
            return best
    else:
        # 无目标尺寸：用启发式分配
        candidates = _enumerate_assignments(sorted_hits_value, target_w_cm, target_h_cm)
        best = max(candidates, key=_score_assignment_consistency)
        return best

    return result


def _enumerate_inner_margin_assignments(sorted_hits, target_w_cm, target_h_cm):
    """当检测值都小于目标外框时，穷举内框+边距的分配方案。

    策略：检测值是 inner 和 margin 的组合，需要确定哪个值属于哪个字段。
    通过尝试不同的分配组合，找到几何自洽性最高的方案。
    """
    n = len(sorted_hits)
    candidates = []

    # 固定外框用目标尺寸
    base = {
        'total_w': (target_w_cm, 5),
        'total_h': (target_h_cm, 5),
        'inner_w': (0.0, 0), 'inner_h': (0.0, 0),
        'margin_top': (0.0, 0), 'margin_bottom': (0.0, 0),
        'margin_left': (0.0, 0), 'margin_right': (0.0, 0),
    }

    # 尝试所有可能的 inner_w/inner_h 组合
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            r = dict(base)
            r['inner_w'] = (sorted_hits[i][0], sorted_hits[i][1])
            r['inner_h'] = (sorted_hits[j][0], sorted_hits[j][1])

            # 剩余的值分配给边距
            remaining_indices = [k for k in range(n) if k != i and k != j]
            for idx, field in enumerate(['margin_bottom', 'margin_right', 'margin_left', 'margin_top']):
                if idx < len(remaining_indices):
                    k = remaining_indices[idx]
                    r[field] = (sorted_hits[k][0], sorted_hits[k][1])

            r = _validate_and_fix_margins(r)
            candidates.append(r)

    # 另外：尝试只分配 inner_w，其余全给边距
    for i in range(n):
        r = dict(base)
        r['inner_w'] = (sorted_hits[i][0], sorted_hits[i][1])
        remaining_indices = [k for k in range(n) if k != i]
        for idx, field in enumerate(['margin_bottom', 'margin_right', 'margin_left', 'margin_top', 'inner_h']):
            if idx < len(remaining_indices):
                k = remaining_indices[idx]
                r[field] = (sorted_hits[k][0], sorted_hits[k][1])
        r = _validate_and_fix_margins(r)
        candidates.append(r)

    # 特殊情况：当有3个值时，尝试各种边距分配组合
    # 这样可以找到更多的自洽方案
    if n == 3:
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                for k in range(n):
                    if i == k or j == k:
                        continue
                    r = dict(base)
                    r['inner_w'] = (sorted_hits[i][0], sorted_hits[i][1])
                    r['margin_right'] = (sorted_hits[j][0], sorted_hits[j][1])
                    r['margin_bottom'] = (sorted_hits[k][0], sorted_hits[k][1])
                    r = _validate_and_fix_margins(r)
                    candidates.append(r)

    # 如果只有1-2个值，尝试不同的边距分配
    if n <= 2:
        for i in range(n):
            r = dict(base)
            r['inner_w'] = (sorted_hits[i][0], sorted_hits[i][1])
            if n >= 2:
                r['margin_bottom'] = (sorted_hits[1 - i][0], sorted_hits[1 - i][1])
            r = _validate_and_fix_margins(r)
            candidates.append(r)

    return candidates if candidates else [base]


def _enumerate_assignments(sorted_hits, target_w_cm, target_h_cm):
    """穷举所有合理的分配方案。"""
    n = len(sorted_hits)
    candidates = []

    def _pick_inner_values(hits, target_w, target_h):
        """从 OCR 结果中挑选适合作为 inner_w / inner_h 的值。
        跳过接近 target 外框的值（它们是 total_w/total_h，不是 inner）。
        返回 (iw_idx, ih_idx, excluded_indices) — excluded 是接近 target 的值索引。
        """
        iw_idx, ih_idx = -1, -1
        excluded = set()
        for idx, (v, c) in enumerate(hits):
            near_tw = target_w > 0 and abs(v - target_w) <= target_w * 0.15
            near_th = target_h > 0 and abs(v - target_h) <= target_h * 0.15
            if near_tw or near_th:
                excluded.add(idx)
        for idx, (v, c) in enumerate(hits):
            if idx in excluded:
                continue
            if iw_idx < 0:
                iw_idx = idx
                continue
            if ih_idx < 0:
                ih_idx = idx
                continue
        return iw_idx, ih_idx, excluded

    if n == 0:
        return candidates

    # 方案A：最大两个 → total_w/total_h
    if n >= 2:
        for swap_outer in [False, True]:
            r = {
                'total_w': (sorted_hits[0][0] if not swap_outer else sorted_hits[1][0], 8),
                'total_h': (sorted_hits[1][0] if not swap_outer else sorted_hits[0][0], 8),
                'inner_w': (0.0, 0), 'inner_h': (0.0, 0),
                'margin_top': (0.0, 0), 'margin_bottom': (0.0, 0),
                'margin_left': (0.0, 0), 'margin_right': (0.0, 0),
            }
            remaining = sorted_hits[2:]
            for i, (v, c) in enumerate(remaining):
                if i == 0: r['inner_w'] = (v, 7)
                elif i == 1: r['inner_h'] = (v, 7)
                elif i == 2: r['margin_top'] = (v, 6)
                elif i == 3: r['margin_bottom'] = (v, 6)
                elif i == 4: r['margin_left'] = (v, 6)
                elif i == 5: r['margin_right'] = (v, 6)
            candidates.append(r)

    # 方案B：目标外框 + 智能挑选 inner/margin 值
    if n >= 2 and target_w_cm > 0 and target_h_cm > 0:
        iw_idx, ih_idx, excluded = _pick_inner_values(sorted_hits, target_w_cm, target_h_cm)
        used = set(excluded)
        r = {
            'total_w': (target_w_cm, 5), 'total_h': (target_h_cm, 5),
            'inner_w': (0.0, 0), 'inner_h': (0.0, 0),
            'margin_top': (0.0, 0), 'margin_bottom': (0.0, 0),
            'margin_left': (0.0, 0), 'margin_right': (0.0, 0),
        }
        if iw_idx >= 0:
            r['inner_w'] = (sorted_hits[iw_idx][0], sorted_hits[iw_idx][1])
            used.add(iw_idx)
        if ih_idx >= 0:
            r['inner_h'] = (sorted_hits[ih_idx][0], sorted_hits[ih_idx][1])
            used.add(ih_idx)
        remaining_indices = [idx for idx in range(n) if idx not in used]

        # 根据几何间隙大小智能分配边距值
        h_gap = max(0, target_w_cm - r['inner_w'][0])
        v_gap = max(0, target_h_cm - r['inner_h'][0])
        h_values = []
        v_values = []

        # 按值大小降序排列 remaining indices
        remaining_indices = sorted(remaining_indices, key=lambda i: sorted_hits[i][0], reverse=True)

        h_first = h_gap >= v_gap  # 间隙大的方向优先分大值
        for idx in remaining_indices:
            if h_first:
                if len(h_values) < 2:
                    h_values.append(idx)
                elif len(v_values) < 2:
                    v_values.append(idx)
                else:
                    h_values.append(idx)
            else:
                if len(v_values) < 2:
                    v_values.append(idx)
                elif len(h_values) < 2:
                    h_values.append(idx)
                else:
                    v_values.append(idx)
            # 交换优先级，确保两边都有值
            h_first = not h_first

        # 如果水平/垂直边距都有值，按大小排序分配到具体边
        if len(h_values) == 2:
            # 较大的值给 right，较小给 left（或反之，后续 validate 会修正）
            vals = [(sorted_hits[i][0], i) for i in h_values]
            vals.sort()
            r['margin_left'] = (sorted_hits[vals[0][1]][0], sorted_hits[vals[0][1]][1])
            r['margin_right'] = (sorted_hits[vals[1][1]][0], sorted_hits[vals[1][1]][1])
        elif len(h_values) == 1:
            r['margin_right'] = (sorted_hits[h_values[0]][0], sorted_hits[h_values[0]][1])
        if len(v_values) == 2:
            vals = [(sorted_hits[i][0], i) for i in v_values]
            vals.sort()
            r['margin_top'] = (sorted_hits[vals[0][1]][0], sorted_hits[vals[0][1]][1])
            r['margin_bottom'] = (sorted_hits[vals[1][1]][0], sorted_hits[vals[1][1]][1])
        elif len(v_values) == 1:
            r['margin_bottom'] = (sorted_hits[v_values[0]][0], sorted_hits[v_values[0]][1])

        candidates.append(r)

        # 方案B2: 直接用最大 OCR 值作为 total_w（如果它接近 target）
        for idx, (v, c) in enumerate(sorted_hits):
            if abs(v - target_w_cm) <= target_w_cm * 0.15:
                r2 = dict(r)
                r2['total_w'] = (v, c)
                candidates.append(r2)
                break

    # 方案C：最大 → inner_w，其余全给边距（仅当最大值不接近 target 时）
    if n >= 1 and target_w_cm > 0 and target_h_cm > 0:
        max_val = sorted_hits[0][0]
        if abs(max_val - target_w_cm) > target_w_cm * 0.15 and abs(max_val - target_h_cm) > target_h_cm * 0.15:
            r = {
                'total_w': (target_w_cm, 5), 'total_h': (target_h_cm, 5),
                'inner_w': (sorted_hits[0][0], 8),
                'inner_h': (0.0, 0),
                'margin_top': (0.0, 0), 'margin_bottom': (0.0, 0),
                'margin_left': (0.0, 0), 'margin_right': (0.0, 0),
            }
            remaining = sorted_hits[1:]
            for i, (v, c) in enumerate(remaining):
                if i == 0: r['margin_bottom'] = (v, 7)
                elif i == 1: r['margin_right'] = (v, 7)
                elif i == 2: r['margin_left'] = (v, 6)
                elif i == 3: r['margin_top'] = (v, 6)
            candidates.append(r)

    # 方案D：无目标尺寸时的启发式
    if target_w_cm <= 0 or target_h_cm <= 0:
        if n >= 2:
            r = {
                'total_w': (sorted_hits[0][0], 8), 'total_h': (sorted_hits[1][0], 8),
                'inner_w': (0.0, 0), 'inner_h': (0.0, 0),
                'margin_top': (0.0, 0), 'margin_bottom': (0.0, 0),
                'margin_left': (0.0, 0), 'margin_right': (0.0, 0),
            }
            remaining = sorted_hits[2:]
            for i, (v, c) in enumerate(remaining):
                if i == 0: r['inner_w'] = (v, 7)
                elif i == 1: r['inner_h'] = (v, 7)
                elif i == 2: r['margin_bottom'] = (v, 6)
                elif i == 3: r['margin_right'] = (v, 6)
                elif i == 4: r['margin_left'] = (v, 6)
                elif i == 5: r['margin_top'] = (v, 6)
            candidates.append(r)

    return candidates if candidates else [{}]


def _validate_and_fix_margins(result):
    """验证分配结果的几何自洽性，并在需要时修正边距。

    核心策略：用外框-内框=边距之和的几何约束，
    在已知部分边距的情况下推算未知边距。
    """
    tw = result.get('total_w', (0, 0))[0]
    th = result.get('total_h', (0, 0))[0]
    iw = result.get('inner_w', (0, 0))[0]
    ih = result.get('inner_h', (0, 0))[0]
    mt = result.get('margin_top', (0, 0))[0]
    mb = result.get('margin_bottom', (0, 0))[0]
    ml = result.get('margin_left', (0, 0))[0]
    mr = result.get('margin_right', (0, 0))[0]

    # 用几何约束推算缺失的边距
    # 水平方向：如果知道一边距，可推算另一边距
    if tw > 0 and iw > 0:
        expected_horizontal = tw - iw
        if ml <= 0 and mr > 0:
            ml = max(0, expected_horizontal - mr)
            result['margin_left'] = (ml, result.get('margin_left', (0, 0))[1])
        elif mr <= 0 and ml > 0:
            mr = max(0, expected_horizontal - ml)
            result['margin_right'] = (mr, result.get('margin_right', (0, 0))[1])

    # 垂直方向：如果知道一边距，可推算另一边距
    if th > 0 and ih > 0:
        expected_vertical = th - ih
        if mt <= 0 and mb > 0:
            mt = max(0, expected_vertical - mb)
            result['margin_top'] = (mt, result.get('margin_top', (0, 0))[1])
        elif mb <= 0 and mt > 0:
            mb = max(0, expected_vertical - mt)
            result['margin_bottom'] = (mb, result.get('margin_bottom', (0, 0))[1])

    # 检查边距是否合理
    if tw > 0 and iw > 0 and ml > 0 and mr > 0:
        expected_lr = tw - iw
        actual_lr = ml + mr
        if expected_lr > 0 and actual_lr > 0:
            ratio = expected_lr / actual_lr
            if ratio > 2.0 or ratio < 0.5:
                total_margin = ml + mr
                if total_margin > 0 and expected_lr > 0:
                    scale = expected_lr / total_margin
                    result['margin_left'] = (ml * scale, result.get('margin_left', (0, 0))[1])
                    result['margin_right'] = (mr * scale, result.get('margin_right', (0, 0))[1])

    if th > 0 and ih > 0 and mt > 0 and mb > 0:
        expected_tb = th - ih
        actual_tb = mt + mb
        if expected_tb > 0 and actual_tb > 0:
            ratio = expected_tb / actual_tb
            if ratio > 2.0 or ratio < 0.5:
                total_margin = mt + mb
                if total_margin > 0 and expected_tb > 0:
                    scale = expected_tb / total_margin
                    result['margin_top'] = (mt * scale, result.get('margin_top', (0, 0))[1])
                    result['margin_bottom'] = (mb * scale, result.get('margin_bottom', (0, 0))[1])

    # 最终合理性检查
    for key in ['margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
        v = result.get(key, (0, 0))[0]
        if v < 0:
            result[key] = (0.0, result.get(key, (0, 0))[1])

    # === 新增：几何合理性硬约束裁剪 ===
    # 单/双边距如果超过外框对应边长的 40%，视为异常（通常是 OCR 误识或几何回退错乱），
    # 将其值按外框合理比例裁剪，避免错乱值传播到后续方向矫正阶段
    tw = result.get('total_w', (0, 0))[0]
    th = result.get('total_h', (0, 0))[0]
    iw = result.get('inner_w', (0, 0))[0]
    ih = result.get('inner_h', (0, 0))[0]

    def _clip_side(v, outer_side, inner_side):
        """将单侧边距裁剪到合理范围。"""
        if v <= 0 or outer_side <= 0:
            return v
        # 单侧边距不得超过外框边长的 60%（经验上限，放宽以容忍 OCR 偏差）
        upper = outer_side * 0.60
        # 也不得超过 (外框-内框) 的 90%（避免明显大于实际间隙）
        if inner_side > 0:
            gap_clip = max(0, (outer_side - inner_side) * 0.90)
            upper = min(upper, gap_clip)
        if v > upper:
            # 不再直接清零，而是裁剪到上限值
            return min(v, upper)
        return v

    if tw > 0:
        ml = result.get('margin_left', (0, 0))[0]
        mr = result.get('margin_right', (0, 0))[0]
        new_ml = _clip_side(ml, tw, iw)
        new_mr = _clip_side(mr, tw, iw)
        if new_ml != ml or new_mr != mr:
            logger.info(f"[sketch_parser] 水平边距合理性裁剪: ml {ml}→{new_ml}, mr {mr}→{new_mr}")
            result['margin_left'] = (new_ml, result.get('margin_left', (0, 0))[1])
            result['margin_right'] = (new_mr, result.get('margin_right', (0, 0))[1])
    if th > 0:
        mt = result.get('margin_top', (0, 0))[0]
        mb = result.get('margin_bottom', (0, 0))[0]
        new_mt = _clip_side(mt, th, ih)
        new_mb = _clip_side(mb, th, ih)
        if new_mt != mt or new_mb != mb:
            logger.info(f"[sketch_parser] 垂直边距合理性裁剪: mt {mt}→{new_mt}, mb {mb}→{new_mb}")
            result['margin_top'] = (new_mt, result.get('margin_top', (0, 0))[1])
            result['margin_bottom'] = (new_mb, result.get('margin_bottom', (0, 0))[1])

    return result


# ---------------------------------------------------------------------------
# 方向标签识别（上/下/左/右 + 数值 → 直接边距赋值）
# ---------------------------------------------------------------------------
# 当草图中标注了方向标签（如"上6"、"下9"、"左36"、"右112"）时，
# 通过检测方向字符并关联附近的数字，直接确定4个边距值。
# 这比纯空间位置分配更准确，尤其适用于非对称设计（左右/上下边距差异大）。

_DIR_CHAR_MAP = {
    '上': 'margin_top',
    '下': 'margin_bottom',
    '左': 'margin_left',
    '右': 'margin_right',
}

# Windows 中文字体路径候选（用于模板匹配渲染）
_CN_FONT_CANDIDATES = [
    r'C:\Windows\Fonts\simhei.ttf',   # 黑体
    r'C:\Windows\Fonts\msyh.ttc',     # 微软雅黑
    r'C:\Windows\Fonts\simsun.ttc',   # 宋体
    r'C:\Windows\Fonts\simfang.ttf',  # 仿宋
    r'C:\Windows\Fonts\Deng.ttf',     # 等线
]


def _detect_direction_labels_by_ocr(cv2, gray_img, tesseract, outer_rect):
    """使用 Tesseract（chi_sim+eng）检测方向标签 上/下/左/右 及其位置。

    Returns:
        list of (direction_char, margin_field, x_center, y_center, confidence, value_or_None)
    """
    from PIL import Image as PILImage

    results = []

    # 检查 chi_sim 语言包是否可用
    try:
        available_langs = tesseract.get_languages()
    except Exception:
        available_langs = ['eng']

    lang = 'chi_sim+eng' if 'chi_sim' in available_langs else 'eng'
    if 'chi_sim' not in available_langs:
        logger.warning(f"[sketch_parser] Tesseract 方向标签检测：chi_sim 未安装，"
                       f"仅用 eng 语言，中文方向字符识别率会降低。"
                       f"已安装语言: {available_langs}")

    ox, oy, ow, oh = outer_rect
    h_img, w_img = gray_img.shape[:2]

    # 裁剪 outer_rect 子图（与主 OCR 流程一致）
    _ox = max(0, int(ox)); _oy = max(0, int(oy))
    _ox2 = min(w_img, int(ox + ow)); _oy2 = min(h_img, int(oy + oh))
    sub = gray_img[_oy:_oy2, _ox:_ox2]
    if sub.size == 0 or sub.shape[0] < 5 or sub.shape[1] < 5:
        return results

    for scale in [2.0, 3.0]:
        scaled = cv2.resize(sub, None, fx=scale, fy=scale,
                            interpolation=cv2.INTER_CUBIC)

        # 预处理变体
        variants = [('orig', scaled)]
        try:
            _, otsu = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            variants.append(('otsu', otsu))
        except Exception:
            pass

        for _vname, variant in variants:
            pil_img = PILImage.fromarray(variant)
            for psm in [11, 6, 3]:  # sparse, block, auto
                config = f'--oem 3 --psm {psm}'
                try:
                    data = tesseract.image_to_data(
                        pil_img, config=config, lang=lang,
                        output_type=tesseract.Output.DICT,
                    )
                except Exception:
                    continue

                if not data or 'text' not in data:
                    continue

                n = len(data.get('text', []))
                for i in range(n):
                    text = str(data['text'][i]).strip()
                    if not text:
                        continue

                    # 检查文本中是否包含方向字符
                    for dchar, mfield in _DIR_CHAR_MAP.items():
                        if dchar not in text:
                            continue

                        try:
                            conf = int(data.get('conf', [50] * n)[i])
                        except Exception:
                            conf = 50
                        if conf < -1:
                            continue

                        try:
                            x_left = int(data.get('left', [0] * n)[i]) / scale
                            y_top = int(data.get('top', [0] * n)[i]) / scale
                            ww = int(data.get('width', [0] * n)[i]) / scale
                            hh = int(data.get('height', [0] * n)[i]) / scale
                        except Exception:
                            continue

                        x_c = x_left + ww / 2 + _ox  # 映射回原图坐标
                        y_c = y_top + hh / 2 + _oy

                        # 尝试从同一 token 中提取数字（如 "上6"、"上:6"）
                        after = text[text.index(dchar) + 1:]
                        num_match = re.search(r'(\d+\.?\d*)', after)
                        if num_match:
                            try:
                                val = float(num_match.group(1))
                                if 0.5 <= val <= 500:
                                    results.append((dchar, mfield, x_c, y_c, conf, val))
                            except ValueError:
                                pass
                        else:
                            # 方向字符单独出现，数字可能在相邻 token 中
                            results.append((dchar, mfield, x_c, y_c, conf, None))

    # 去重：同一方向字段只保留最高置信度且带数值的条目
    best = {}
    for dchar, mfield, xc, yc, conf, val in results:
        prev = best.get(mfield)
        if prev is None:
            best[mfield] = (dchar, mfield, xc, yc, conf, val)
        elif val is not None and (prev[5] is None or conf > prev[4]):
            best[mfield] = (dchar, mfield, xc, yc, conf, val)
        elif val is None and prev[5] is None and conf > prev[4]:
            best[mfield] = (dchar, mfield, xc, yc, conf, val)

    return list(best.values())


def _detect_direction_labels_by_template(cv2, gray_img, outer_rect, color_img=None, inner_rect=None):
    """使用模板匹配检测方向标签 上/下/左/右。

    支持黑色/红色/彩色文字：
    - 若提供 color_img，先尝试用红色 HSV mask 提取红色文字做匹配
    - 再回退到灰度图+黑模板匹配（处理黑色文字）
    - 对每个检测结果做空间合理性校验（位置必须在对应方向的边距间隙区域）

    Args:
        inner_rect: (ix, iy, iw, ih) 内框坐标，用于更精确地检查标签位置

    Returns:
        list of (direction_char, margin_field, x_center, y_center, confidence, value_or_None)
    """
    from PIL import Image as PILImage, ImageDraw, ImageFont

    results = []

    # 查找可用中文字体
    font_path = None
    for fp in _CN_FONT_CANDIDATES:
        if os.path.isfile(fp):
            font_path = fp
            break
    if font_path is None:
        return results

    ox, oy, ow, oh = outer_rect
    h_img, w_img = gray_img.shape[:2]
    
    # 解析内框坐标（如果提供）
    ix, iy, iw, ih = 0, 0, 0, 0
    if inner_rect is not None and len(inner_rect) == 4:
        ix, iy, iw, ih = inner_rect

    # 裁剪 outer_rect 子图
    _ox = max(0, int(ox)); _oy = max(0, int(oy))
    _ox2 = min(w_img, int(ox + ow)); _oy2 = min(h_img, int(oy + oh))
    sub = gray_img[_oy:_oy2, _ox:_ox2]
    if sub.size == 0 or sub.shape[0] < 5 or sub.shape[1] < 5:
        return results

    # ---- 构建多通道候选图（用于匹配不同颜色的文字）----
    candidate_maps = [("gray", sub)]

    # 如果有彩色图，尝试提取红色通道作为额外匹配候选
    if color_img is not None and len(color_img.shape) == 3:
        try:
            _sub_color = color_img[_oy:_oy2, _ox:_ox2]
            # 方法A：红色 HSV mask（将红色文字变白色，背景变黑色 → 反向用于 TM_CCOEFF_NORMED）
            hsv = cv2.cvtColor(_sub_color, cv2.COLOR_BGR2HSV)
            mask_r1 = cv2.inRange(hsv, np.array([0, 30, 30]), np.array([15, 255, 255]))
            mask_r2 = cv2.inRange(hsv, np.array([165, 30, 30]), np.array([180, 255, 255]))
            mask_red = cv2.bitwise_or(mask_r1, mask_r2)
            if cv2.countNonZero(mask_red) >= 10:
                # 膨胀 1 次，连接可能的断裂笔画
                _k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
                mask_red = cv2.dilate(mask_red, _k, iterations=1)
                # 反转：红色文字区域=255，其他=0，再反相为 黑底白字（模板渲染为白底黑字以匹配）
                candidate_maps.append(("red_mask", cv2.bitwise_not(mask_red)))
            # 方法B：R 通道直接作为匹配目标
            r_ch = _sub_color[:, :, 2]
            candidate_maps.append(("r_channel", r_ch))
        except Exception:
            pass

    # 估计文字大小范围（基于外框尺寸，大幅放宽范围）
    min_side = min(ow, oh)
    # 更宽的字号范围：0.015 ~ 0.15，覆盖从极小到极大的各种草图尺寸
    # 上限 0.15 可覆盖"文字占外框短边 15%"的大字号场景
    font_sizes_raw = [0.015, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.15]
    font_sizes = sorted(set(max(10, int(min_side * f)) for f in font_sizes_raw))

    for dchar, mfield in _DIR_CHAR_MAP.items():
        best_match = None  # (dchar, mfield, xc, yc, score, None)
        _max_score_for_char = 0.0

        for fsize in font_sizes:
            try:
                font = ImageFont.truetype(font_path, fsize)
            except Exception:
                continue

            # 渲染字符（白底黑字）
            pad = 4
            tmp_img = PILImage.new('L', (fsize + pad * 2, fsize + pad * 2), 255)
            draw = ImageDraw.Draw(tmp_img)
            draw.text((pad, pad), dchar, fill=0, font=font)
            template = np.array(tmp_img)

            if template.shape[0] > sub.shape[0] or template.shape[1] > sub.shape[1]:
                continue

            for map_name, map_img in candidate_maps:
                try:
                    res = cv2.matchTemplate(map_img, template, cv2.TM_CCOEFF_NORMED)
                except Exception:
                    continue

                # 放宽阈值：不同颜色通道匹配质量差异大
                # 降低阈值到0.30，提高召回率
                threshold = 0.30 if map_name != "gray" else 0.35
                locs = np.where(res >= threshold)

                for pt in zip(*locs):
                    score = float(res[pt[0], pt[1]])
                    tc_x = pt[1] + template.shape[1] / 2 + _ox
                    tc_y = pt[0] + template.shape[0] / 2 + _oy

                    # 记录最大分数用于调试
                    if score > _max_score_for_char:
                        _max_score_for_char = score

                    # 空间合理性作为加权因子（非硬性过滤）
                    spatial_ok = _is_label_position_reasonable(dchar, tc_x, tc_y, ox, oy, ow, oh, ix, iy, iw, ih)
                    # 位置合理时保留原分，不合理时大幅扣分（但仍保留候选）
                    _effective_score = score if spatial_ok else score * 0.5

                    if best_match is None or _effective_score > best_match[4]:
                        best_match = (dchar, mfield, tc_x, tc_y, score, None)

        # 调试日志：显示每个方向字符的最大匹配分数
        if _max_score_for_char < 0.35:
            logger.debug(f"[sketch_parser] 方向标签 {dchar}: 最大匹配分={_max_score_for_char:.3f} 低于阈值，可能未检测到")

        if best_match is not None:
            results.append(best_match)

    return results


def _detect_margins_by_geometry_ocr(cv2, gray_img, tesseract, outer_rect, 
                                     inner_rect=None,
                                     target_outer_w_cm=0, target_outer_h_cm=0):
    """基于几何位置的边距OCR检测。

    原理：草图中的边距数值通常标注在内外框之间的间隙区域。
    通过在间隙区域进行聚焦OCR，可以直接获取边距值。

    改进：使用内外框的间隙区域作为OCR搜索区域，更准确地覆盖边距值位置。
    使用目标外框尺寸（cm）进行过滤，排除外框总尺寸标注。

    Returns:
        dict: {margin_top/bottom/left/right: (value, confidence)}
    """
    from PIL import Image as PILImage
    import re

    ox, oy, ow, oh = outer_rect
    h_img, w_img = gray_img.shape[:2]
    
    margin_results = {}
    
    # 将像素尺寸转换为cm（如果有目标尺寸）
    _outer_w_cm = target_outer_w_cm if target_outer_w_cm > 0 else 0
    _outer_h_cm = target_outer_h_cm if target_outer_h_cm > 0 else 0
    
    # 计算间隙区域（使用inner_rect如果可用）
    if inner_rect is not None:
        ix, iy, iw, ih = inner_rect
        gap_top_region = (max(0, int(oy - 5)), min(h_img, int(iy + 5)))
        gap_bottom_region = (max(0, int(iy + ih - 5)), min(h_img, int(oy + oh + 5)))
        gap_left_region = (max(0, int(ox - 5)), min(w_img, int(ix + 5)))
        gap_right_region = (max(0, int(ix + iw - 5)), min(w_img, int(ox + ow + 5)))
        full_width = (max(0, int(ox - 10)), min(w_img, int(ox + ow + 10)))
        full_height = (max(0, int(oy - 10)), min(h_img, int(oy + oh + 10)))
    else:
        # 没有inner_rect时，使用旧的基于比例的方法
        gap_top_region = (max(0, int(oy - oh * 0.35)), min(h_img, int(oy + oh * 0.10)))
        gap_bottom_region = (max(0, int(oy + oh * 0.90)), min(h_img, int(oy + oh + oh * 0.35)))
        gap_left_region = (max(0, int(ox - ow * 0.35)), min(w_img, int(ox + ow * 0.10)))
        gap_right_region = (max(0, int(ox + ow * 0.90)), min(w_img, int(ox + ow + ow * 0.35)))
        full_width = (max(0, int(ox - ow * 0.05)), min(w_img, int(ox + ow + ow * 0.05)))
        full_height = (max(0, int(oy - oh * 0.05)), min(h_img, int(oy + oh + oh * 0.05)))
    
    # 定义边距搜索区域：间隙区域（垂直方向）× 外框范围（水平方向）
    edge_regions = {
        'margin_top': {
            'cx1': full_width[0], 'cx2': full_width[1],
            'cy1': gap_top_region[0], 'cy2': gap_top_region[1],
        },
        'margin_bottom': {
            'cx1': full_width[0], 'cx2': full_width[1],
            'cy1': gap_bottom_region[0], 'cy2': gap_bottom_region[1],
        },
        'margin_left': {
            'cx1': gap_left_region[0], 'cx2': gap_left_region[1],
            'cy1': full_height[0], 'cy2': full_height[1],
        },
        'margin_right': {
            'cx1': gap_right_region[0], 'cx2': gap_right_region[1],
            'cy1': full_height[0], 'cy2': full_height[1],
        },
    }
    
    for mfield, region in edge_regions.items():
        cx1, cx2 = region['cx1'], region['cx2']
        cy1, cy2 = region['cy1'], region['cy2']
        
        if cx2 - cx1 < 10 or cy2 - cy1 < 10:
            continue
        
        crop = gray_img[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            continue
        
        logger.debug(f"[sketch_parser] 几何OCR {mfield}: 区域=[{cx1}:{cx2},{cy1}:{cy2}], 尺寸={crop.shape}")
        
        all_values = []  # 收集所有识别到的值
        
        # 多尺度OCR
        for scale in [3.0, 4.5, 6.0]:
            scaled = cv2.resize(crop, None, fx=scale, fy=scale,
                                interpolation=cv2.INTER_CUBIC)
            
            # 预处理变体
            variants = [('orig', scaled)]
            try:
                _, otsu = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                variants.append(('otsu', otsu))
            except Exception:
                pass
            try:
                adaptive = cv2.adaptiveThreshold(scaled, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                cv2.THRESH_BINARY, 11, 3)
                variants.append(('adaptive', adaptive))
            except Exception:
                pass
            
            for _vname, variant in variants:
                pil_img = PILImage.fromarray(variant)
                
                configs = [
                    f'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.',
                    f'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.',
                    f'--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789.',
                    f'--oem 3 --psm 6',
                    f'--oem 3 --psm 11',
                    f'--oem 3 --psm 12',
                ]
                
                for config in configs:
                    try:
                        data = tesseract.image_to_data(
                            pil_img, config=config,
                            output_type=tesseract.Output.DICT,
                        )
                    except Exception:
                        continue
                    
                    if not data or 'text' not in data:
                        continue
                    
                    n = len(data.get('text', []))
                    for i in range(n):
                        text = str(data['text'][i]).strip()
                        if not text:
                            continue
                        try:
                            conf = int(data.get('conf', [0] * n)[i])
                        except Exception:
                            conf = 0
                        if conf < 10:
                            continue
                        
                        for m in re.finditer(r'(\d+\.?\d*)', text):
                            try:
                                val = float(m.group(1))
                                if not (0.5 <= val <= 500):
                                    continue
                                
                                # 过滤：不能等于外框总尺寸（cm）
                                if _outer_w_cm > 0 and abs(val - _outer_w_cm) <= 3.0:
                                    logger.debug(f"[sketch_parser] 几何OCR {mfield}: 丢弃值{val} (接近外框宽度{_outer_w_cm}cm)")
                                    continue
                                if _outer_h_cm > 0 and abs(val - _outer_h_cm) <= 3.0:
                                    logger.debug(f"[sketch_parser] 几何OCR {mfield}: 丢弃值{val} (接近外框高度{_outer_h_cm}cm)")
                                    continue
                                
                                all_values.append((val, conf))
                            except ValueError:
                                pass
        
        # 选择最佳值：综合置信度和值的合理性
        if all_values:
            # 去重并按值分组
            dedup = {}
            for val, conf in all_values:
                vround = round(val, 1)
                if vround not in dedup or conf > dedup[vround][1]:
                    dedup[vround] = (val, conf)
            
            candidates = list(dedup.values())
            if len(candidates) == 1:
                best_val, best_conf = candidates[0]
            else:
                # 按置信度排序
                candidates.sort(key=lambda x: -x[1])
                max_conf_val = candidates[0][0]
                max_conf = candidates[0][1]
                
                # 策略：
                # 1. 如果最大值的置信度明显更高(比所有其他值高30%)，使用最大值
                # 2. 否则，使用置信度最高的值（不偏向最小值）
                # 3. 只有当最小值的置信度接近最大值(>=80%)时才偏向最小值
                
                other_candidates = [c for c in candidates if c[1] < max_conf]
                if other_candidates:
                    # 检查最大值的置信度是否明显领先
                    second_best = max(other_candidates, key=lambda x: x[1])
                    if max_conf > second_best[1] * 1.3:
                        # 最大值置信度明显更高，使用最大值
                        best_val, best_conf = max_conf_val, max_conf
                    else:
                        # 置信度接近，使用置信度最高的值（可能为较小值或较大值）
                        best_val, best_conf = max_conf_val, max_conf
                else:
                    best_val, best_conf = max_conf_val, max_conf
            
            if best_val is not None:
                margin_results[mfield] = (best_val, best_conf)
                logger.info(f"[sketch_parser] 几何OCR {mfield}: 值={best_val}, conf={best_conf}, 所有结果={list(dedup.values())[:5]}")
    
    return margin_results


def _is_label_position_reasonable(dchar, x, y, ox, oy, ow, oh, ix=0, iy=0, iw=0, ih=0):
    """检查方向标签位置是否符合空间合理性。

    宽松检查：边距值应位于外框与内框之间的间隙区域。
    若提供内框坐标，则检查间隙区域；否则退化为外框半区检查。
    """
    has_inner = iw > 0 and ih > 0
    
    if dchar == '上':
        if has_inner and iy > oy:
            return oy - (iy - oy) * 0.2 <= y <= iy + (iy - oy) * 0.2
        return 0 <= y <= oy + oh * 0.6
    elif dchar == '下':
        if has_inner and iy + ih < oy + oh:
            gap_y2 = iy + ih
            gap_y3 = oy + oh
            return gap_y2 - (gap_y3 - gap_y2) * 0.2 <= y <= gap_y3 + (gap_y3 - gap_y2) * 0.2
        return oy + oh * 0.4 <= y <= oy + oh + oh * 0.3
    elif dchar == '左':
        if has_inner and ix > ox:
            return ox - (ix - ox) * 0.2 <= x <= ix + (ix - ox) * 0.2
        return 0 <= x <= ox + ow * 0.6
    elif dchar == '右':
        if has_inner and ix + iw < ox + ow:
            gap_x2 = ix + iw
            gap_x3 = ox + ow
            return gap_x2 - (gap_x3 - gap_x2) * 0.2 <= x <= gap_x3 + (gap_x3 - gap_x2) * 0.2
        return ox + ow * 0.4 <= x <= ox + ow + ow * 0.3
    return False


def _is_label_position_strict(dchar, x, y, ox, oy, ow, oh, ix=0, iy=0, iw=0, ih=0):
    """严格检查方向标签位置是否符合空间合理性。

    边距值应位于外框与内框之间的间隙区域：
    - 上标签：外框下方，内框上方 (oy ≤ y ≤ iy)
    - 下标签：内框下方，外框上方 (iy+ih ≤ y ≤ oy+oh)
    - 左标签：外框右侧，内框左侧 (ox ≤ x ≤ ix)
    - 右标签：内框右侧，外框左侧 (ix+iw ≤ x ≤ ox+ow)
    
    若提供了内框坐标，则精确检查间隙区域；否则退化为外框边缘检查。
    容差在间隙两侧对称扩展，最大5像素。
    """
    has_inner = iw > 0 and ih > 0
    
    if dchar == '上':
        x_ok = ox - ow * 0.15 <= x <= ox + ow * 1.15
        if has_inner and iy > oy:
            gap = iy - oy
            tol = min(gap * 0.1, 5)
            y_ok = (oy - tol) <= y <= (iy + tol)
        else:
            y_ok = oy - oh * 0.15 <= y <= oy + oh * 0.35
        return x_ok and y_ok
    elif dchar == '下':
        x_ok = ox - ow * 0.15 <= x <= ox + ow * 1.15
        if has_inner and iy + ih < oy + oh:
            gap = (oy + oh) - (iy + ih)
            tol = min(gap * 0.1, 5)
            y_ok = (iy + ih - tol) <= y <= (oy + oh + tol)
        else:
            y_ok = oy + oh * 0.65 <= y <= oy + oh + oh * 0.15
        return x_ok and y_ok
    elif dchar == '左':
        y_ok = oy - oh * 0.15 <= y <= oy + oh * 1.15
        if has_inner and ix > ox:
            gap = ix - ox
            tol = min(gap * 0.1, 5)
            x_ok = (ox - tol) <= x <= (ix + tol)
        else:
            x_ok = ox - ow * 0.15 <= x <= ox + ow * 0.35
        return x_ok and y_ok
    elif dchar == '右':
        y_ok = oy - oh * 0.15 <= y <= oy + oh * 1.15
        if has_inner and ix + iw < ox + ow:
            gap = (ox + ow) - (ix + iw)
            tol = min(gap * 0.1, 5)
            x_ok = (ix + iw - tol) <= x <= (ox + ow + tol)
        else:
            x_ok = ox + ow * 0.65 <= x <= ox + ow + ow * 0.15
        return x_ok and y_ok
    return False


def _focused_ocr_for_direction_label(cv2, gray_img, tesseract, dchar, mfield,
                                      lx, ly, outer_rect, target_outer_w_cm=0, target_outer_h_cm=0):
    """在方向标签位置进行聚焦OCR，获取更准确的边距数值。

    改进：尝试多种裁剪策略和PSM配置，提高数值识别率。
    使用目标外框尺寸（cm）进行过滤，排除外框总尺寸标注。

    Args:
        cv2, gray_img, tesseract: OCR相关依赖
        dchar: 方向字符 ('上'/'下'/'左'/'右')
        mfield: 对应字段名
        lx, ly: 方向标签中心位置（原图坐标）
        outer_rect: 外框 (ox, oy, ow, oh)
        target_outer_w_cm: 目标外框宽度(cm)
        target_outer_h_cm: 目标外框高度(cm)

    Returns:
        (value, confidence) or None
    """
    from PIL import Image as PILImage
    import re

    ox, oy, ow, oh = outer_rect
    h_img, w_img = gray_img.shape[:2]
    
    # 外框总尺寸（cm），用于排除外框尺寸标注
    _outer_w_cm = target_outer_w_cm if target_outer_w_cm > 0 else 0
    _outer_h_cm = target_outer_h_cm if target_outer_h_cm > 0 else 0

    # 生成多种裁剪策略
    crop_regions = []
    base_crop_w = max(25, ow * 0.12)
    base_crop_h = max(25, oh * 0.12)

    if dchar == '上':
        # 策略1：以标签为中心
        crop_regions.append(('center',
            max(0, int(lx - base_crop_w)), min(w_img, int(lx + base_crop_w)),
            max(0, int(ly - base_crop_h)), min(h_img, int(ly + base_crop_h))))
        # 策略2：向上扩展（数字可能在标签上方）
        crop_regions.append(('up_extended',
            max(0, int(lx - base_crop_w * 0.7)), min(w_img, int(lx + base_crop_w * 0.7)),
            max(0, int(ly - base_crop_h * 2.0)), min(h_img, int(ly + base_crop_h * 0.5))))
    elif dchar == '下':
        crop_regions.append(('center',
            max(0, int(lx - base_crop_w)), min(w_img, int(lx + base_crop_w)),
            max(0, int(ly - base_crop_h)), min(h_img, int(ly + base_crop_h))))
        crop_regions.append(('down_extended',
            max(0, int(lx - base_crop_w * 0.7)), min(w_img, int(lx + base_crop_w * 0.7)),
            max(0, int(ly - base_crop_h * 0.5)), min(h_img, int(ly + base_crop_h * 2.0))))
    elif dchar == '左':
        crop_regions.append(('center',
            max(0, int(lx - base_crop_w)), min(w_img, int(lx + base_crop_w)),
            max(0, int(ly - base_crop_h)), min(h_img, int(ly + base_crop_h))))
        crop_regions.append(('left_extended',
            max(0, int(lx - base_crop_w * 2.0)), min(w_img, int(lx + base_crop_h * 0.5)),
            max(0, int(ly - base_crop_h * 0.7)), min(h_img, int(ly + base_crop_h * 0.7))))
    elif dchar == '右':
        crop_regions.append(('center',
            max(0, int(lx - base_crop_w)), min(w_img, int(lx + base_crop_w)),
            max(0, int(ly - base_crop_h)), min(h_img, int(ly + base_crop_h))))
        crop_regions.append(('right_extended',
            max(0, int(lx - base_crop_w * 0.5)), min(w_img, int(lx + base_crop_w * 2.0)),
            max(0, int(ly - base_crop_h * 0.7)), min(h_img, int(ly + base_crop_h * 0.7))))
    else:
        return None

    best_result = None
    best_score = -1
    _all_ocr_results = []

    for region_name, cx1, cx2, cy1, cy2 in crop_regions:
        if cx2 - cx1 < 5 or cy2 - cy1 < 5:
            continue

        crop = gray_img[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            continue

        logger.debug(f"[sketch_parser] 聚焦OCR {dchar} ({region_name}): 区域=[{cx1}:{cx2},{cy1}:{cy2}], 尺寸={crop.shape}")

        # 多尺度 + 多PSM组合
        for scale in [3.0, 4.5, 6.0]:
            scaled = cv2.resize(crop, None, fx=scale, fy=scale,
                                interpolation=cv2.INTER_CUBIC)

            # 预处理：多种变体
            variants = [('orig', scaled)]
            try:
                _, otsu = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                variants.append(('otsu', otsu))
            except Exception:
                pass
            try:
                adaptive = cv2.adaptiveThreshold(scaled, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                cv2.THRESH_BINARY, 11, 3)
                variants.append(('adaptive', adaptive))
            except Exception:
                pass

            for _vname, variant in variants:
                pil_img = PILImage.fromarray(variant)

                configs = [
                    f'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.',
                    f'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.',
                    f'--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789.',
                    f'--oem 3 --psm 6',
                    f'--oem 3 --psm 11',
                    f'--oem 3 --psm 12',
                ]
                for config in configs:
                    try:
                        data = tesseract.image_to_data(
                            pil_img, config=config,
                            output_type=tesseract.Output.DICT,
                        )
                    except Exception:
                        continue

                    if not data or 'text' not in data:
                        continue

                    n = len(data.get('text', []))
                    for i in range(n):
                        text = str(data['text'][i]).strip()
                        if not text:
                            continue
                        try:
                            conf = int(data.get('conf', [0] * n)[i])
                        except Exception:
                            conf = 0
                        if conf < 10:
                            continue

                        for m in re.finditer(r'(\d+\.?\d*)', text):
                            try:
                                val = float(m.group(1))
                                if not (0.5 <= val <= 500):
                                    continue

                                _all_ocr_results.append((val, conf, _vname, region_name))

                                # 过滤：不能等于外框总尺寸（cm）
                                if _outer_w_cm > 0 and abs(val - _outer_w_cm) <= 3.0:
                                    continue
                                if _outer_h_cm > 0 and abs(val - _outer_h_cm) <= 3.0:
                                    continue

                                score = conf + 40 if val <= 80 else conf

                                if score > best_score:
                                    best_score = score
                                    best_result = (val, conf)
                            except ValueError:
                                pass

    # 调试输出
    if _all_ocr_results:
        logger.debug(f"[sketch_parser] 聚焦OCR {dchar}: 所有结果={_all_ocr_results[:10]}...")
    else:
        logger.debug(f"[sketch_parser] 聚焦OCR {dchar}: 无有效OCR结果")

    # 最终校验
    if best_result is not None:
        _bv, _bc = best_result
        if _outer_w_cm > 0 and abs(_bv - _outer_w_cm) <= 2.0:
            logger.warning(f"[sketch_parser] 聚焦OCR {dchar}: 值={_bv:.1f} 判定为外框宽度({_outer_w_cm}cm)，丢弃")
            best_result = None
        elif _outer_h_cm > 0 and abs(_bv - _outer_h_cm) <= 2.0:
            logger.warning(f"[sketch_parser] 聚焦OCR {dchar}: 值={_bv:.1f} 判定为外框高度({_outer_h_cm}cm)，丢弃")
            best_result = None

    if best_result is not None:
        logger.info(f"[sketch_parser] 聚焦OCR {dchar}边距: 识别到值={best_result[0]:.1f}, conf={best_result[1]}")
    return best_result


def _assign_margins_by_spatial_reasoning(ocr_hits, outer_rect):
    """基于空间推理将全图OCR结果分配到边距字段。

    原理：工程图中尺寸标注通常放在外框的外侧。
    通过分析OCR识别到的数值相对于外框的位置，
    可以准确判断每个数值对应哪个边距。

    分配规则：
    - margin_left: 外框左侧的数值 (x < o_left)
    - margin_right: 外框右侧的数值 (x > o_right)
    - margin_top: 外框上方的数值 (y < o_top)
    - margin_bottom: 外框下方的数值 (y > o_bottom)

    同时识别 total_w, total_h, inner_w, inner_h。

    Args:
        ocr_hits: [(value, x_center, y_center, conf), ...] 全图OCR结果
        outer_rect: 外框 (ox, oy, ow, oh)

    Returns:
        dict: {margin_top/bottom/left/right: (value, confidence)}
              以及可选的 total_w, total_h, inner_w, inner_h
    """
    ox, oy, ow, oh = outer_rect
    o_left, o_top = ox, oy
    o_right, o_bottom = ox + ow, oy + oh

    # 过滤有效数值
    valid_hits = [(v, x, y, c) for v, x, y, c in ocr_hits
                  if 0.5 <= v <= 500 and c >= 10]

    if not valid_hits:
        return {}

    # 外框中心
    o_cx = (o_left + o_right) / 2
    o_cy = (o_top + o_bottom) / 2

    result = {}

    # ---- 1. 分配边距值 ----
    # 边距值在对应方向上，且通常较小（<80）
    # 但也可能较大（如右边距53），需要综合考虑

    for mfield, direction_fn in [
        ('margin_left', lambda v, x, y: x < o_left),
        ('margin_right', lambda v, x, y: x > o_right),
        ('margin_top', lambda v, x, y: y < o_top),
        ('margin_bottom', lambda v, x, y: y > o_bottom),
    ]:
        candidates = []
        for v, x, y, c in valid_hits:
            if not direction_fn(v, x, y):
                continue
            # 评分：距离外框越近越好，值越小越像边距
            if mfield in ('margin_left', 'margin_right'):
                dist_to_frame = abs(x - (o_left if mfield == 'margin_left' else o_right))
                # 垂直方向偏差
                y_deviation = abs(y - o_cy) / max(oh, 1)
            else:
                dist_to_frame = abs(y - (o_top if mfield == 'margin_top' else o_bottom))
                # 水平方向偏差
                x_deviation = abs(x - o_cx) / max(ow, 1)

            # 合理性：边距通常 <80，但也可能更大
            plausibility = 0
            if v <= 30:
                plausibility = 50
            elif v <= 80:
                plausibility = 30
            elif v <= 120:
                plausibility = 10
            else:
                plausibility = -20

            score = -dist_to_frame * 0.5 + plausibility + c * 0.1

            candidates.append((score, v, x, y, c))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            best = candidates[0]
            result[mfield] = (best[1], best[4])
            logger.info(
                f"[sketch_parser] 空间推理{mfield}: "
                f"选中值={best[1]:.1f} at ({best[2]:.0f},{best[3]:.0f}) conf={best[4]} "
                f"(score={best[0]:.1f}, 候选数={len(candidates)})")

    # ---- 2. 识别 total_w 和 total_h ----
    # 从全图OCR中找到外框尺寸标注
    # 先排除已分配为边距的值
    margin_values = set()
    for mkey in ['margin_left', 'margin_right', 'margin_top', 'margin_bottom']:
        if mkey in result:
            margin_values.add(round(result[mkey][0], 1))

    # total_h: 外框左侧，垂直居中附近的较大值
    candidates_th = []
    for v, x, y, c in valid_hits:
        if round(v, 1) in margin_values:
            continue
        if x < o_left and abs(y - o_cy) < oh * 0.3:
            if v <= 300 and v >= 20:
                candidates_th.append((v, x, y, c, abs(y - o_cy)))

    if candidates_th:
        candidates_th.sort(key=lambda x: (x[4], -x[0]))
        result['total_h'] = (candidates_th[0][0], candidates_th[0][3])
        logger.info(f"[sketch_parser] 空间推理total_h: {candidates_th[0][0]:.1f} (候选数={len(candidates_th)})")

    # total_w: 外框下方，水平居中附近的较大值
    candidates_tw = []
    for v, x, y, c in valid_hits:
        if round(v, 1) in margin_values:
            continue
        if y > o_bottom and abs(x - o_cx) < ow * 0.3:
            if v <= 300:
                candidates_tw.append((v, x, y, c, abs(x - o_cx)))

    # 对 total_w 的特殊处理：
    # 如果已知 margin_left 和 margin_right，可以推算 inner_w 应为 total_w - ml - mr
    # 因此在候选中找到一个值，使 total_w - ml - mr 与某个 OCR 值匹配
    ml_val = result.get('margin_left', (0, 0))[0]
    mr_val = result.get('margin_right', (0, 0))[0]
    tw_val = result.get('total_w', (0, 0))[0]
    th_val = result.get('total_h', (0, 0))[0]
    mt_val = result.get('margin_top', (0, 0))[0]
    mb_val = result.get('margin_bottom', (0, 0))[0]

    # 如果 total_h 已知，且 mt+mb 已知，则 inner_h = total_h - mt - mb
    # 然后可以在 OCR 中找一个与 inner_h 匹配的值
    expected_ih = th_val - mt_val - mb_val if th_val > 0 and mt_val > 0 and mb_val > 0 else 0

    # 如果 inner_h 已知，从候选中排除该值，用于选择 total_w
    ih_values_to_exclude = set()
    if expected_ih > 0:
        ih_values_to_exclude.add(round(expected_ih, 1))
        # 也排除接近该值的
        for v, x, y, c in valid_hits:
            if abs(v - expected_ih) < 2 and x > o_left and x < o_right:
                ih_values_to_exclude.add(round(v, 1))

    filtered_tw = []
    for v, x, y, c, dev in candidates_tw:
        if round(v, 1) not in ih_values_to_exclude:
            filtered_tw.append((v, x, y, c, dev))

    if filtered_tw:
        # 优先选择与 margin_left + margin_right + inner_width 匹配的值
        # inner_width 应为一个合理值，通常在 5-200 之间
        best_tw = None
        best_score = -999
        for v, x, y, c, dev in filtered_tw:
            # 计算对应的 inner_width
            iw_candidate = v - ml_val - mr_val if ml_val > 0 and mr_val > 0 else 0
            # 评分：inner_width 合理 (5-200) 且与某个 OCR 值匹配
            score = c
            if 5 <= iw_candidate <= 200:
                score += 50
                # 检查是否有 OCR 值与 iw_candidate 匹配
                for ov, ox, oy, oc in valid_hits:
                    if abs(ov - iw_candidate) < 2 and ov not in margin_values:
                        score += 30
                        break
            elif iw_candidate > 200:
                score -= 30
            score -= dev * 0.1  # 惩罚偏离中心的值

            if score > best_score:
                best_score = score
                best_tw = (v, x, y, c, dev)

        if best_tw:
            result['total_w'] = (best_tw[0], best_tw[3])
            logger.info(f"[sketch_parser] 空间推理total_w: {best_tw[0]:.1f} (inner_w推算={best_tw[0]-ml_val-mr_val:.1f}, 候选数={len(filtered_tw)})")

    # ---- 3. 识别 inner_w 和 inner_h ----
    # 使用几何关系计算，然后用 OCR 验证
    tw_val = result.get('total_w', (0, 0))[0]
    th_val = result.get('total_h', (0, 0))[0]
    ml_val = result.get('margin_left', (0, 0))[0]
    mr_val = result.get('margin_right', (0, 0))[0]
    mt_val = result.get('margin_top', (0, 0))[0]
    mb_val = result.get('margin_bottom', (0, 0))[0]

    expected_iw = tw_val - ml_val - mr_val if tw_val > 0 and ml_val > 0 and mr_val > 0 else 0
    expected_ih = th_val - mt_val - mb_val if th_val > 0 and mt_val > 0 and mb_val > 0 else 0

    # 从 OCR 中找最匹配的值
    used_values = set(margin_values)
    for k in ['total_w', 'total_h']:
        if k in result:
            used_values.add(round(result[k][0], 1))

    # inner_w 候选：在外框内部区域，排除已用值
    iw_candidates = []
    ih_candidates = []
    for v, x, y, c in valid_hits:
        if round(v, 1) in used_values:
            continue
        if o_left < x < o_right and o_top < y < o_bottom:
            # 在内框内部的候选
            iw_candidates.append((v, x, y, c))
            ih_candidates.append((v, x, y, c))

    if iw_candidates and expected_iw > 0:
        best_match = min(iw_candidates, key=lambda t: abs(t[0] - expected_iw))
        result['inner_w'] = (best_match[0], best_match[2])
        logger.info(f"[sketch_parser] 空间推理inner_w: {best_match[0]:.1f} (期望={expected_iw:.1f})")
    elif iw_candidates:
        # 即使没有期望值，也尝试从OCR中找到inner_w
        # 选择最大的候选值（inner_w通常大于inner_h，且不会是边距值）
        best = max(iw_candidates, key=lambda t: t[0])
        # 验证：该值不应是边距值或total值
        if best[0] not in margin_values and best[0] != result.get('total_h', (0,0))[0]:
            result['inner_w'] = (best[0], best[2])
            logger.info(f"[sketch_parser] OCR直接识别inner_w: {best[0]:.1f}")
    elif expected_iw > 0:
        result['inner_w'] = (expected_iw, 5)
        logger.info(f"[sketch_parser] 几何计算inner_w: {expected_iw:.1f}")

    if ih_candidates and expected_ih > 0:
        best_match = min(ih_candidates, key=lambda t: abs(t[0] - expected_ih))
        result['inner_h'] = (best_match[0], best_match[2])
        logger.info(f"[sketch_parser] 空间推理inner_h: {best_match[0]:.1f} (期望={expected_ih:.1f})")
    elif ih_candidates:
        best = min(ih_candidates, key=lambda t: t[0])
        if best[0] not in margin_values and best[0] != result.get('total_h', (0,0))[0]:
            result['inner_h'] = (best[0], best[2])
            logger.info(f"[sketch_parser] OCR直接识别inner_h: {best[0]:.1f}")
    elif expected_ih > 0:
        result['inner_h'] = (expected_ih, 5)
        logger.info(f"[sketch_parser] 几何计算inner_h: {expected_ih:.1f}")

    return result


def _predictive_ocr_margins(cv2, gray_img, tesseract, outer_rect, inner_rect):
    """基于outer_rect和inner_rect预测边距数值位置，进行聚焦OCR。

    原理：外框和内框之间的间隙就是边距的标注位置。
    通过计算间隙中心坐标，裁剪小区域进行高倍率OCR，
    可以准确读取边距数值，不受方向标签位置误差影响。

    Args:
        cv2, gray_img, tesseract: OCR相关依赖
        outer_rect: 外框 (ox, oy, ow, oh)
        inner_rect: 内框 (ix, iy, iw, ih)

    Returns:
        dict: {margin_top/bottom/left/right: (value, confidence)}
    """
    from PIL import Image as PILImage
    import re

    ox, oy, ow, oh = outer_rect
    ix, iy, iw, ih = inner_rect
    h_img, w_img = gray_img.shape[:2]

    # 计算外框和内框的边界
    o_left, o_top = ox, oy
    o_right, o_bottom = ox + ow, oy + oh
    i_left, i_top = ix, iy
    i_right, i_bottom = ix + iw, iy + ih

    # 预测每个边距值的位置
    # 注意：在工程图中，尺寸标注通常放在外框的外侧，而不是内框和外框之间
    predictions = {
        'margin_left': {
            'cx': o_left - 25,  # 外框左侧25px处（标注在外侧）
            'cy': (o_top + o_bottom) / 2,  # 垂直方向：外框垂直中心
            'w': max(35, 60),  # 裁剪宽度（向左扩展）
            'h': max(30, oh * 0.3),  # 裁剪高度
        },
        'margin_right': {
            'cx': o_right + 25,  # 外框右侧25px处（标注在外侧）
            'cy': (o_top + o_bottom) / 2,
            'w': max(35, 60),
            'h': max(30, oh * 0.3),
        },
        'margin_top': {
            'cx': (o_left + o_right) / 2,
            'cy': o_top - 20,  # 外框顶侧20px处（标注在外侧）
            'w': max(30, ow * 0.3),
            'h': max(35, 50),
        },
        'margin_bottom': {
            'cx': (o_left + o_right) / 2,
            'cy': o_bottom + 25,  # 外框底侧25px处（标注在外侧）
            'w': max(30, ow * 0.3),
            'h': max(35, 55),
        },
    }

    results = {}

    logger.info(f"[sketch_parser] 预测性OCR: outer_rect={outer_rect}, inner_rect={inner_rect}")
    logger.info(f"[sketch_parser] 预测性OCR: o_left={o_left:.0f}, o_top={o_top:.0f}, o_right={o_right:.0f}, o_bottom={o_bottom:.0f}")
    logger.info(f"[sketch_parser] 预测性OCR: i_left={i_left:.0f}, i_top={i_top:.0f}, i_right={i_right:.0f}, i_bottom={i_bottom:.0f}")

    for mfield, pred in predictions.items():
        cx = pred['cx']
        cy = pred['cy']
        cw = pred['w']
        ch = pred['h']

        logger.info(f"[sketch_parser] 预测性OCR {mfield}: 预定位=({cx:.0f},{cy:.0f}), 区域={cw:.0f}x{ch:.0f}")

        # 计算裁剪区域（向外扩展更多）
        if mfield == 'margin_left':
            # 向左扩展更多
            cx1 = max(0, int(cx - cw * 1.5))
            cx2 = min(w_img, int(cx + cw * 0.5))
        elif mfield == 'margin_right':
            # 向右扩展更多
            cx1 = max(0, int(cx - cw * 0.5))
            cx2 = min(w_img, int(cx + cw * 1.5))
        elif mfield == 'margin_top':
            # 向上扩展更多
            cy1 = max(0, int(cy - ch * 1.5))
            cy2 = min(h_img, int(cy + ch * 0.5))
            cx1 = max(0, int(cx - cw / 2))
            cx2 = min(w_img, int(cx + cw / 2))
        elif mfield == 'margin_bottom':
            # 向下扩展更多
            cy1 = max(0, int(cy - ch * 0.5))
            cy2 = min(h_img, int(cy + ch * 1.5))
            cx1 = max(0, int(cx - cw / 2))
            cx2 = min(w_img, int(cx + cw / 2))
        else:
            cx1 = max(0, int(cx - cw / 2))
            cx2 = min(w_img, int(cx + cw / 2))
            cy1 = max(0, int(cy - ch / 2))
            cy2 = min(h_img, int(cy + ch / 2))

        if mfield in ('margin_left', 'margin_right'):
            cy1 = max(0, int(cy - ch / 2))
            cy2 = min(h_img, int(cy + ch / 2))

        if cx2 - cx1 < 5 or cy2 - cy1 < 5:
            continue

        crop = gray_img[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            continue

        # 多尺度+多预处理OCR（带高置信度早退出优化）
        best_result = None
        best_score = -1
        _EARLY_EXIT_SCORE = 75

        _scale = 4.0  # 单一最优尺度
        scaled = cv2.resize(crop, None, fx=_scale, fy=_scale,
                            interpolation=cv2.INTER_CUBIC)

        _variant_configs = [
            ('orig_whitelist', scaled,
             [f'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.',
              f'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.']),
        ]
        try:
            _, otsu = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            _variant_configs.append(
                ('otsu_whitelist', otsu,
                 [f'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.',
                  f'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.']))
        except Exception:
            pass
        try:
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            _variant_configs.append(
                ('clahe_flex', clahe.apply(scaled),
                 [f'--oem 3 --psm 6', f'--oem 3 --psm 11']))
        except Exception:
            pass

        for _vname, variant, configs in _variant_configs:
            pil_img = PILImage.fromarray(variant)
            for config in configs:
                try:
                    data = tesseract.image_to_data(
                        pil_img, config=config,
                        output_type=tesseract.Output.DICT,
                    )
                except Exception:
                    continue

                if not data or 'text' not in data:
                    continue

                n = len(data.get('text', []))
                for i in range(n):
                    text = str(data['text'][i]).strip()
                    if not text:
                        continue
                    try:
                        conf = int(data.get('conf', [0] * n)[i])
                    except Exception:
                        conf = 0
                    if conf < 10:
                        continue

                    for m in re.finditer(r'(\d+\.?\d*)', text):
                        try:
                            val = float(m.group(1))
                            if not (0.5 <= val <= 500):
                                continue

                            score = conf
                            if val <= 80:
                                score += 30
                            elif val <= 120:
                                score += 10
                            elif val > 150:
                                score -= 30

                            if score > best_score:
                                best_score = score
                                best_result = (val, conf)
                        except ValueError:
                            pass

                if best_score >= _EARLY_EXIT_SCORE:
                    break
            if best_score >= _EARLY_EXIT_SCORE:
                break

        if best_result is not None and best_result[0] > 0:
            logger.info(
                f"[sketch_parser] 预测性OCR {mfield}: "
                f"位置=({cx:.0f},{cy:.0f}), 裁剪={cx2-cx1}x{cy2-cy1}, "
                f"值={best_result[0]:.1f}, conf={best_result[1]}")
            results[mfield] = best_result
        else:
            logger.info(
                f"[sketch_parser] 预测性OCR {mfield}: "
                f"位置=({cx:.0f},{cy:.0f}), 裁剪={cx2-cx1}x{cy2-cy1}, "
                f"未识别到数值")

    return results


def _match_direction_labels_to_numbers(dir_labels, ocr_hits, outer_rect,
                                        cv2=None, gray_img=None, tesseract=None):
    """将方向标签与附近的OCR数字关联。

    对每个方向标签，在OCR hits中找最近的数字，赋值给对应的边距字段。
    如果方向标签本身已包含数字（OCR检测到"上6"），则直接使用。
    支持部分匹配：只要找到至少1个边距就返回结果。

    Returns:
        dict: {margin_top/bottom/left/right: (value, confidence)} 或 None（完全无匹配）
    """
    if not dir_labels:
        return None

    ox, oy, ow, oh = outer_rect
    # 距离阈值：方向字符到数字的最大距离（基于外框尺寸，放宽到25%以适配草图布局）
    max_dist = max(ow, oh) * 0.25

    margin_result = {}
    used_hit_idx = set()

    for dchar, mfield, lx, ly, lconf, lval in dir_labels:
        # ---- 第二遍聚焦OCR：在标签位置裁剪小区域，高倍率识别 ----
        focused_val = None
        if cv2 is not None and gray_img is not None and tesseract is not None:
            try:
                focused_result = _focused_ocr_for_direction_label(
                    cv2, gray_img, tesseract, dchar, mfield, lx, ly, outer_rect)
                if focused_result is not None and focused_result[0] > 0:
                    focused_val = focused_result[0]
                    logger.info(f"[sketch_parser] 聚焦OCR成功 {dchar}: 值={focused_val:.1f}, "
                                f"原标签值={lval}, 最近OCR值搜索将被跳过")
            except Exception as _e:
                logger.warning(f"[sketch_parser] 聚焦OCR异常: {_e}")

        # 优先使用聚焦OCR结果（比全图OCR更准确）
        if focused_val is not None:
            # —— [Fix-2026-08-17 合理性校验] 聚焦OCR返回的值不能等于外框尺寸，也不能过大
            # 方向标签附近常有外框尺寸(如58×120)的标注，聚焦OCR裁剪区域不够精准时会读到
            _margin_sanity_cap = min(ow, oh) * 0.55
            if (abs(focused_val - ow) <= 2.0 or abs(focused_val - oh) <= 2.0):
                logger.warning(
                    f"[sketch_parser] 聚焦OCR {dchar}: 值={focused_val:.1f}与外框尺寸"
                    f"({ow:.1f}x{oh:.1f})过近，判定为外框尺寸误读，跳过聚焦OCR结果"
                )
                focused_val = None
            elif focused_val > _margin_sanity_cap and focused_val > 15:
                logger.warning(
                    f"[sketch_parser] 聚焦OCR {dchar}: 值={focused_val:.1f}超过边距合理上限"
                    f"{_margin_sanity_cap:.1f}(外框短边的55%)，跳过聚焦OCR结果"
                )
                focused_val = None

        if focused_val is not None:
            margin_result[mfield] = (focused_val, 8)
            used_hit_idx.add(-1)
            continue

        # 如果方向标签已包含数字，直接使用
        if lval is not None and 0.5 <= lval <= 500:
            margin_result[mfield] = (lval, max(lconf, 5))
            used_hit_idx.add(-1)
            continue

        # ---- 方向感知搜索：在标签的特定方向（外框与内框之间的间隙）搜索数值 ----
        # 边距值通常标注在方向标签的间隙方向：
        #   左→标签左侧, 右→标签右侧, 上→标签上方, 下→标签下方
        # 同时结合距离和合理性评分
        dir_weights = {
            'margin_left': lambda hx, hy, lx, ly: lx - hx,  # 标签左侧的hx更小
            'margin_right': lambda hx, hy, lx, ly: hx - lx,  # 标签右侧的hx更大
            'margin_top': lambda hx, hy, lx, ly: ly - hy,    # 标签上方的hy更小
            'margin_bottom': lambda hx, hy, lx, ly: hy - ly, # 标签下方的hy更大
        }
        dir_weight_fn = dir_weights.get(mfield, lambda hx, hy, lx, ly: 0)

        candidates = []
        # —— [Fix-2026-08-17] 边距硬上限：绝不能等于/接近外框尺寸，也不能超过短边 55%
        _margin_abs_cap = min(ow, oh) * 0.55
        for idx, (val, hx, hy, hconf) in enumerate(ocr_hits):
            if idx in used_hit_idx:
                continue
            if not (0.5 <= val <= 500):
                continue
            # —— [Fix-2026-08-17 硬过滤1] 与外框尺寸过近的值直接跳过（这是外框总尺寸，不是边距）
            if abs(val - ow) <= 2.0 or abs(val - oh) <= 2.0:
                continue
            # —— [Fix-2026-08-17 硬过滤2] 超过外框短边 55% 的值不可能是边距
            if val > _margin_abs_cap and val > 15:
                continue

            dist = ((lx - hx) ** 2 + (ly - hy) ** 2) ** 0.5
            if dist > max_dist:
                continue

            # 方向权重：值在标签的预期方向时给正分，反方向给负分
            dir_score = dir_weight_fn(hx, hy, lx, ly)
            # 归一化到 [-1, 1] 范围（相对于max_dist）
            dir_norm = min(max(dir_score / max_dist, -1), 1)

            # —— [Fix-2026-08-17 合理性评分增强] 边距值越小越合理，超过一定比例强倒扣
            plausibility = 0
            if val <= min(60, _margin_abs_cap * 0.9):
                plausibility = 120
            elif val <= 80:
                plausibility = 70
            elif val <= _margin_abs_cap * 0.85:
                plausibility = 30
            elif val <= _margin_abs_cap:
                plausibility = -10
            else:
                plausibility = -80  # 理论上已被过滤，但再保护一次

            # 综合评分：距离 + 方向 + 合理性
            # 方向权重为正时表示在预期方向，应加分
            score = -dist + dir_norm * 80 + plausibility * 0.3

            candidates.append((score, idx, val, hconf, dist, dir_score))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            best = candidates[0]
            _, idx, val, hconf, dist, dir_score = best

            # 额外验证：如果最佳候选在反方向，检查是否有更合理的候选
            if dir_score < 0 and len(candidates) > 1:
                for c in candidates[1:]:
                    if c[5] > 0 and c[2] <= min(80, _margin_abs_cap * 0.9):
                        logger.info(
                            f"[sketch_parser] 边距方向修正: {dchar} 候选值从{val:.1f}(反方向)→{c[2]:.1f}(正方向)")
                        val = c[2]
                        idx = c[1]
                        hconf = c[3]
                        dist = c[4]
                        break

            # 额外验证：如果最佳候选值过大(>_margin_abs_cap*0.75)，检查是否有更合理的候选
            if val > _margin_abs_cap * 0.75 and len(candidates) > 1:
                for c in candidates[1:]:
                    if c[2] <= min(80, _margin_abs_cap * 0.8) and c[4] < max_dist * 1.5:
                        logger.info(
                            f"[sketch_parser] 边距值修正: {dchar} 候选值从{val:.1f}→{c[2]:.1f} (更合理)")
                        val = c[2]
                        idx = c[1]
                        hconf = c[3]
                        dist = c[4]
                        break

            # —— [Fix-2026-08-17 最终安全门] 赋值前最后一道校验：绝不能把外框尺寸作为边距返回
            if abs(val - ow) <= 1.5 or abs(val - oh) <= 1.5:
                logger.warning(
                    f"[sketch_parser] {dchar}边距安全门拦截: 值={val:.1f}与外框尺寸"
                    f"({ow:.1f}x{oh:.1f})过近，判定为外框尺寸误匹配，跳过该方向")
                continue  # 不赋值，宁可少一个方向也不给错误值
            if val > _margin_abs_cap and val > 15:
                logger.warning(
                    f"[sketch_parser] {dchar}边距安全门拦截: 值={val:.1f}超过边距上限"
                    f"{_margin_abs_cap:.1f}(外框短边55%)，不合理跳过")
                continue

            margin_result[mfield] = (val, max(hconf, 5))
            used_hit_idx.add(idx)
            logger.info(f"[sketch_parser] {dchar}边距: 选中值={val:.1f} (距标签{dist:.0f}px, "
                        f"方向分={dir_score:.0f}, conf={hconf})")

    # —— [Fix-2026-08-17 返回值最终校验] 确保返回的边距都是合理的
    # 如果某个边距值超过了外框的 50%，或者与外框尺寸接近，就剔除该值（避免错误污染）
    _sanitized_result = {}
    for _mf, (_mv, _mc) in margin_result.items():
        _bad = False
        if abs(_mv - ow) <= 2.0 or abs(_mv - oh) <= 2.0:
            logger.warning(f"[sketch_parser] 最终校验剔除{_mf}: 值={_mv:.1f}等于外框尺寸")
            _bad = True
        elif _mv > min(ow, oh) * 0.55 and _mv > 15:
            logger.warning(f"[sketch_parser] 最终校验剔除{_mf}: 值={_mv:.1f}超过边距上限")
            _bad = True
        if not _bad:
            _sanitized_result[_mf] = (_mv, _mc)
    margin_result = _sanitized_result

    # 只要找到至少1个边距就返回（支持部分匹配）
    if len(margin_result) >= 1:
        return margin_result
    return None


def _try_direction_label_fast_track(cv2, gray_img, tesseract, outer_rect, ocr_hits,
                                    target_w_hint=0.0, target_h_hint=0.0,
                                    color_img=None, inner_rect=None):
    """方向标签快速通道：检测方向标签并关联数字，直接返回8字段赋值。

    Args:
        inner_rect: (ix, iy, iw, ih) 内框坐标，用于更精确地检查标签位置

    Returns:
        dict with all 8 fields, or None if direction labels not detected.
    """
    logger.info("[sketch_parser] 方向标签快速通道：开始检测（outer_rect=%s, tesseract=%s）"
                % (outer_rect, "available" if tesseract is not None else "None"))

    # 策略1：Tesseract chi_sim OCR 检测方向字符
    dir_labels = []
    ocr_labels = []
    if tesseract is not None:
        try:
            ocr_labels = _detect_direction_labels_by_ocr(cv2, gray_img, tesseract, outer_rect)
            dir_labels.extend(ocr_labels)
            logger.info(f"[sketch_parser] 方向标签 OCR 检测到 {len(ocr_labels)} 个: "
                        f"{[(dl[0], dl[1]) for dl in ocr_labels]}")
        except Exception as _e:
            logger.warning(f"[sketch_parser] 方向标签 OCR 检测异常: {_e}")

    # 策略2：模板匹配兜底（补充未检测到的方向）
    detected_fields = {dl[1] for dl in dir_labels}
    missing = {'margin_top', 'margin_bottom', 'margin_left', 'margin_right'} - detected_fields
    tmpl_labels = []
    if missing:
        try:
            tmpl_labels = _detect_direction_labels_by_template(
                cv2, gray_img, outer_rect, color_img=color_img, inner_rect=inner_rect)
            for tl in tmpl_labels:
                if tl[1] in missing:
                    dir_labels.append(tl)
                    missing.discard(tl[1])
            if tmpl_labels:
                logger.info(f"[sketch_parser] 模板匹配检测到 {len(tmpl_labels)} 个方向标签: "
                            f"{[(dl[0], dl[1]) for dl in tmpl_labels]}")
        except Exception as _e:
            logger.warning(f"[sketch_parser] 模板匹配检测方向标签异常: {_e}")

    if not dir_labels:
        logger.info("[sketch_parser] 方向标签快速通道：未检测到任何方向标签")
        return None

    logger.info(f"[sketch_parser] 方向标签检测到 {len(dir_labels)} 个标签: "
                f"{[(dl[0], dl[1]) for dl in dir_labels]}")

    # 关联数字（传入cv2/gray/tesseract以启用聚焦OCR第二遍）
    margin_result = _match_direction_labels_to_numbers(
        dir_labels, ocr_hits, outer_rect,
        cv2=cv2, gray_img=gray_img, tesseract=tesseract)
    if margin_result is None:
        logger.info("[sketch_parser] 方向标签快速通道：未能关联全部4个边距数字（当前 %d 个: %s）"
                    % (len(dir_labels),
                       [(dl[0], dl[1]) for dl in dir_labels]))
        return None

    # 构造完整的8字段结果
    total_w = target_w_hint if target_w_hint > 0 else 0.0
    total_h = target_h_hint if target_h_hint > 0 else 0.0

    mt = margin_result['margin_top'][0]
    mb = margin_result['margin_bottom'][0]
    ml = margin_result['margin_left'][0]
    mr = margin_result['margin_right'][0]

    inner_w = max(0.0, total_w - ml - mr) if total_w > 0 else 0.0
    inner_h = max(0.0, total_h - mt - mb) if total_h > 0 else 0.0

    result = {
        'total_w': (total_w, 10),
        'total_h': (total_h, 10),
        'inner_w': (inner_w, 8),
        'inner_h': (inner_h, 8),
        'margin_top': margin_result['margin_top'],
        'margin_bottom': margin_result['margin_bottom'],
        'margin_left': margin_result['margin_left'],
        'margin_right': margin_result['margin_right'],
    }

    logger.warning(
        f"[sketch_parser] 方向标签快速通道命中："
        f"total={total_w}x{total_h} inner={inner_w:.1f}x{inner_h:.1f} "
        f"margin T/B/L/R={mt}/{mb}/{ml}/{mr}"
    )

    return result


def _robust_ocr_subimage(cv2, sub_img, tesseract, scale=3.0) -> list:
    """对子图进行健壮的OCR识别（不使用字符白名单，支持多位数）。

    核心改进：
    1. 不用 tessedit_char_whitelist —— 白名单会导致CAD绘制的数字被误读/漏读
    2. 使用 --psm 6(整块文字) / --psm 11(稀疏文字) 替代 --psm 8(单字)
    3. 多尺度(3x/5x) + 多预处理(原始/CLAHE/OTSU) + 多PSM组合投票
    4. 相邻字符智能合并为多位数
    5. 同时用 image_to_string(--psm 7) 作为多位数补充通道

    Args:
        sub_img: 灰度子图（outer_rect区域）
        tesseract: pytesseract 实例
        scale: 基础缩放倍数（子图建议3.0-4.0）

    Returns:
        list of (value, x_center, y_center, conf)
    """
    from PIL import Image as PILImage

    h, w = sub_img.shape[:2]
    if h < 5 or w < 5:
        return []

    # ---- 多尺度 ----
    scales = sorted(set([
        scale,
        max(1.5, scale * 0.7),
        min(5.0, scale * 1.4),
    ]))

    # 收集所有 (text, x, y, conf, w, h) 条目
    all_chars = []
    # 从 image_to_string 收集的 (value, x, y) 补充值
    string_hints = []

    for sc in scales:
        scaled = cv2.resize(sub_img, None, fx=sc, fy=sc,
                            interpolation=cv2.INTER_CUBIC)

        # ---- 多预处理 ----
        variants = [('orig', scaled)]
        try:
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            variants.append(('clahe', clahe.apply(scaled)))
        except Exception:
            pass
        try:
            blur = cv2.GaussianBlur(scaled, (3, 3), 0)
            variants.append(('blur', blur))
        except Exception:
            pass
        try:
            _, otsu = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            variants.append(('otsu', otsu))
        except Exception:
            pass

        for vname, variant in variants:
            pil_img = PILImage.fromarray(variant)

            # ---- image_to_data（逐字符），不用白名单 ----
            for psm in [6, 11]:
                config = f'--oem 3 --psm {psm}'
                try:
                    data = tesseract.image_to_data(
                        pil_img, config=config,
                        output_type=tesseract.Output.DICT,
                    )
                except Exception:
                    continue

                if not data or 'text' not in data:
                    continue

                n = len(data.get('text', []))
                for i in range(n):
                    text = str(data['text'][i]).strip()
                    if not text:
                        continue
                    try:
                        conf = int(data.get('conf', [0] * n)[i])
                    except Exception:
                        conf = 0
                    if conf < -1:
                        continue
                    try:
                        xl = int(data.get('left', [0] * n)[i])
                        yt = int(data.get('top', [0] * n)[i])
                        ww = int(data.get('width', [0] * n)[i])
                        hh = int(data.get('height', [0] * n)[i])
                    except Exception:
                        continue
                    all_chars.append((
                        text,
                        xl / sc + ww / (2 * sc),
                        yt / sc + hh / (2 * sc),
                        conf,
                        ww / sc,
                        hh / sc,
                    ))

            # ---- image_to_string（整行文字），不用白名单 ----
            for psm in [7, 6]:
                config = f'--oem 3 --psm {psm}'
                try:
                    text_out = tesseract.image_to_string(pil_img, config=config)
                    # 解析字符串中的所有数字
                    for match in re.finditer(r'\d+\.?\d*', text_out):
                        try:
                            val = float(match.group())
                        except ValueError:
                            continue
                        if 0.3 <= val <= 500:
                            # 用大致位置（子图中心偏向）作为hint
                            string_hints.append((val, w / 2, h / 2, 50))
                except Exception:
                    continue

    if not all_chars and not string_hints:
        return []

    # ---- 去重：同位置(25px容差)相似值合并 ----
    merged = []
    for txt, xc, yc, cf, cw, ch in all_chars:
        if not bool(re.fullmatch(r'[\d.]+', txt)):
            # 非纯数字文本仍保留，但降低优先级
            pass
        matched = False
        for j, (mtxt, mxc, myc, mcf, mcw, mch) in enumerate(merged):
            if abs(mxc - xc) > max(25, max(mcw, cw)):
                continue
            if abs(myc - yc) > max(25, max(mch, ch)):
                continue
            try:
                va = float(txt)
                vb = float(mtxt)
            except ValueError:
                continue
            if abs(va - vb) <= max(0.1, max(va, vb) * 0.05):
                if cf > mcf:
                    merged[j] = (txt, xc, yc, cf, cw, ch)
                matched = True
                break
        if not matched:
            merged.append((txt, xc, yc, cf, cw, ch))

    # ---- 按行分组，合并同行相邻字符为多位数 ----
    merged.sort(key=lambda c: c[2])
    lines = []
    for ch in merged:
        txt, xc, yc, cf, cw, chh = ch
        assigned = False
        for line in lines:
            max_h = max(c[5] for c in line['chars'])
            if abs(line['yc_avg'] - yc) < max(max_h * 1.6, 10):
                line['chars'].append(ch)
                line['yc_avg'] = sum(c[2] for c in line['chars']) / len(line['chars'])
                assigned = True
                break
        if not assigned:
            lines.append({'chars': [ch], 'yc_avg': yc})

    # ---- 生成最终数值列表 ----
    results = []
    seen = {}

    for line in lines:
        chars = sorted(line['chars'], key=lambda c: c[1])
        cur_text = ''
        cur_xs = []
        cur_ys = []
        cur_cfs = []
        prev_right = None
        prev_h = None

        for txt, xc, yc, cf, cw, chh in chars:
            x_left = xc - cw / 2
            x_right = xc + cw / 2

            if cur_text:
                gap = x_left - prev_right
                same_cluster = (gap < max(cw, 3) * 2.5 and
                                abs(yc - sum(cur_ys) / len(cur_ys)) < max(chh, prev_h) * 1.8)
                if not same_cluster:
                    # 输出当前token
                    if cur_text and re.search(r'\d', cur_text):
                        for m in re.finditer(r'\d+\.?\d*', cur_text):
                            try:
                                v = float(m.group())
                            except ValueError:
                                continue
                            if 0.3 <= v <= 500:
                                digit_len = len(m.group().replace('.', ''))
                                score = digit_len * 10 + sum(cur_cfs) / len(cur_cfs)
                                rk = round(v * 10)
                                if rk not in seen or score > seen[rk][1]:
                                    seen[rk] = (v, score)
                                    results.append((v,
                                                    sum(cur_xs) / len(cur_xs),
                                                    sum(cur_ys) / len(cur_ys),
                                                    sum(cur_cfs) / len(cur_cfs)))
                    cur_text = ''
                    cur_xs, cur_ys, cur_cfs = [], [], []

            if not cur_text:
                # 仅以数字开头
                if not re.match(r'[\d.]', txt):
                    continue

            cur_text += txt
            cur_xs.append(xc)
            cur_ys.append(yc)
            cur_cfs.append(cf)
            prev_right = x_right
            prev_h = chh

        # 输出最后一个token
        if cur_text and re.search(r'\d', cur_text):
            for m in re.finditer(r'\d+\.?\d*', cur_text):
                try:
                    v = float(m.group())
                except ValueError:
                    continue
                if 0.3 <= v <= 500:
                    digit_len = len(m.group().replace('.', ''))
                    score = digit_len * 10 + sum(cur_cfs) / len(cur_cfs)
                    rk = round(v * 10)
                    if rk not in seen or score > seen[rk][1]:
                        seen[rk] = (v, score)
                        results.append((v,
                                        sum(cur_xs) / len(cur_xs),
                                        sum(cur_ys) / len(cur_ys),
                                        sum(cur_cfs) / len(cur_cfs)))

    # ---- 从 string_hints 补充缺失的多位数 ----
    for sv, sx, sy, scf in string_hints:
        rk = round(sv * 10)
        if rk not in seen:
            # 尝试找到位置最接近的 all_chars 条目来获取正确位置
            best_pos = (sx, sy)
            min_dist = float('inf')
            for txt, xc, yc, cf, cw, chh in all_chars:
                try:
                    tv = float(txt)
                except ValueError:
                    continue
                if abs(tv - sv) * 10 < min_dist:
                    min_dist = abs(tv - sv) * 10
                    best_pos = (xc, yc)
            seen[rk] = (sv, scf)
            results.append((sv, best_pos[0], best_pos[1], scf))

    # ---- 按位置去重：同一位置只保留一个值 ----
    # 选择规则：位数多 > conf高 > 数值小（OCR更易多读数字而非少读）
    dedup = {}
    for v, xc, yc, cf in results:
        # 查找同位置的已有值
        existing_key = None
        for rk, (ev, exc, eyc, ecf) in dedup.items():
            if abs(exc - xc) < 30 and abs(eyc - yc) < 30:
                existing_key = rk
                break
        if existing_key is None:
            dedup[round(v * 10)] = (v, xc, yc, cf)
        else:
            ev, exc, eyc, ecf = dedup[existing_key]
            val_digits = len(str(int(v))) if v > 0 else 0
            ev_digits = len(str(int(ev))) if ev > 0 else 0
            if val_digits > ev_digits:
                dedup[existing_key] = (v, xc, yc, cf)
            elif val_digits == ev_digits and cf > ecf:
                dedup[existing_key] = (v, xc, yc, cf)
            elif val_digits == ev_digits and abs(cf - ecf) < 0.1 and v < ev:
                dedup[existing_key] = (v, xc, yc, cf)

    final = [(v, xc, yc, cf) for rk, (v, xc, yc, cf) in dedup.items()]

    # ---- 小数合并：检测是否有被分割的小数（如 42 和 4 被识别为两个值，但实际应为 42.4）----
    decimal_values = [(v, xc, yc, cf) for v, xc, yc, cf in final if '.' in str(v)]
    if decimal_values:
        for dv, dxc, dyc, dcf in decimal_values:
            # 提取整数部分和小数部分
            dv_str = str(dv)
            int_part = int(dv_str.split('.')[0])
            dec_part = int(dv_str.split('.')[1]) if len(dv_str.split('.')[1]) > 0 else 0
            
            # 查找是否有两个单独的值在相同位置
            found_int = False
            found_dec = False
            int_idx = -1
            dec_idx = -1
            
            for idx, (v, xc, yc, cf) in enumerate(final):
                if abs(v - int_part) < 0.5 and abs(xc - dxc) < 30 and abs(yc - dyc) < 30:
                    found_int = True
                    int_idx = idx
                if dec_part > 0 and abs(v - dec_part) < 0.5 and abs(xc - dxc) < 60 and abs(yc - dyc) < 30:
                    found_dec = True
                    dec_idx = idx
            
            # 如果找到整数部分和小数部分的单独值，移除它们
            if found_int and found_dec and int_idx >= 0 and dec_idx >= 0:
                logger.info(f"[sketch_parser] 小数合并: {int_part} + {dec_part} → {dv}")
                # 移除分量（从后向前删除以避免索引偏移）
                for idx in sorted([int_idx, dec_idx], reverse=True):
                    if 0 <= idx < len(final):
                        final.pop(idx)
                # 确保小数值被保留
                if not any(abs(v - dv) < 0.1 for v, xc, yc, cf in final):
                    final.append((dv, dxc, dyc, dcf))

    # 按置信度排序
    final.sort(key=lambda t: t[3], reverse=True)

    logger.info(f"[sketch_parser] _robust_ocr_subimage: 识别到 {len(final)} 个数值")
    for v, xc, yc, cf in final:
        logger.info(f"  值={v:.2f} 位置=({xc:.0f},{yc:.0f}) 置信度={cf:.0f}")

    return final


def _simple_ocr_full_image(cv2, gray_img, tesseract, outer_rect):
    """对全图做简单OCR，返回识别到的所有数值。

    与 _robust_ocr_subimage 不同，此函数：
    1. 不裁剪、不缩放，直接对全图做OCR
    2. 使用较低PSM阈值(conf>=10)，过滤噪声
    3. 只提取数字，返回 (value, x_center, y_center, conf) 列表

    这样可以作为子图OCR的补充，避免因预处理导致的文字失真。
    """
    from PIL import Image as PILImage
    import re

    ox, oy, ow, oh = outer_rect
    h_img, w_img = gray_img.shape[:2]

    # 裁剪outer_rect区域（带适当扩展），但不缩放
    pad = max(10, int(0.05 * max(ow, oh)))
    sx1 = max(0, ox - pad)
    sy1 = max(0, oy - pad)
    sx2 = min(w_img, ox + ow + pad)
    sy2 = min(h_img, oy + oh + pad)
    sub = gray_img[sy1:sy2, sx1:sx2]

    if sub.size == 0:
        return []

    pil_img = PILImage.fromarray(sub)

    hits = []
    # 使用 PSM 6 (block text) 和 11 (sparse text) 两种模式
    for psm in [6, 11]:
        config = f'--oem 3 --psm {psm}'
        try:
            data = tesseract.image_to_data(
                pil_img, config=config,
                output_type=tesseract.Output.DICT,
            )
        except Exception:
            continue

        if not data or 'text' not in data:
            continue

        n = len(data.get('text', []))
        for i in range(n):
            text = str(data['text'][i]).strip()
            if not text:
                continue
            try:
                conf = int(data.get('conf', [0] * n)[i])
            except Exception:
                conf = 0
            if conf < 10:
                continue

            # 提取数字
            for m in re.finditer(r'(\d+\.?\d*)', text):
                try:
                    val = float(m.group(1))
                    if not (0.5 <= val <= 500):
                        continue

                    try:
                        xl = int(data.get('left', [0] * n)[i])
                        yt = int(data.get('top', [0] * n)[i])
                        ww = int(data.get('width', [0] * n)[i])
                        hh = int(data.get('height', [0] * n)[i])
                    except Exception:
                        continue

                    x_c = xl + ww / 2 + sx1
                    y_c = yt + hh / 2 + sy1
                    hits.append((val, x_c, y_c, conf))
                except ValueError:
                    pass

    # 去重：相同位置+相同数值只保留最高conf
    if hits:
        merged = []
        for v, xc, yc, cf in hits:
            found = False
            for j, (mv, mx, my, mc) in enumerate(merged):
                if abs(mv - v) < 0.5 and abs(mx - xc) < 30 and abs(my - yc) < 30:
                    if cf > mc:
                        merged[j] = (v, xc, yc, cf)
                    found = True
                    break
            if not found:
                merged.append((v, xc, yc, cf))
        hits = merged

    return hits


def _enhance_colored_text_for_ocr(color_img, gray_img):
    """增强红色/彩色文字在灰度图中的对比度，提升OCR识别率。

    原理：草图常使用红色/彩色标注（数字和尺寸线）。
    在灰度转换后，红色的灰度值（0.299R≈76）与白色背景（255）
    对比不够强烈，导致OCR漏读。本函数通过以下步骤增强：
      1. HSV颜色空间检测红色区域
      2. 将红色区域在灰度图中置黑（0），大幅提升对比度
      3. 同时检测橙/棕色调，覆盖更多标注颜色

    Args:
        color_img: BGR彩色原图
        gray_img: 灰度图（可为None，则自动从color_img生成）

    Returns:
        增强后的灰度图
    """
    cv2 = _safe_import_cv2()
    if cv2 is None or color_img is None:
        return gray_img

    if gray_img is None:
        gray_img = cv2.cvtColor(color_img, cv2.COLOR_BGR2GRAY)

    hsv = cv2.cvtColor(color_img, cv2.COLOR_BGR2HSV)
    h_img, w_img = gray_img.shape[:2]

    # 红色 HSV 范围（两段：0-10 和 160-180）
    lower_r1 = np.array([0, 40, 60])
    upper_r1 = np.array([10, 255, 255])
    lower_r2 = np.array([160, 40, 60])
    upper_r2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv, lower_r1, upper_r1)
    mask2 = cv2.inRange(hsv, lower_r2, upper_r2)
    red_mask = cv2.bitwise_or(mask1, mask2)

    # 橙/棕色调（覆盖打印/扫描中偏色的红色标注）
    lower_orange = np.array([8, 30, 60])
    upper_orange = np.array([25, 255, 255])
    orange_mask = cv2.inRange(hsv, lower_orange, upper_orange)

    # 深紫色
    lower_purple = np.array([130, 30, 60])
    upper_purple = np.array([160, 255, 255])
    purple_mask = cv2.inRange(hsv, lower_purple, upper_purple)

    # 合并所有彩色文字掩膜
    color_mask = cv2.bitwise_or(cv2.bitwise_or(red_mask, orange_mask), purple_mask)

    # 形态学膨胀：确保文字笔画完整覆盖
    kernel = np.ones((2, 2), np.uint8)
    color_mask = cv2.dilate(color_mask, kernel, iterations=1)

    # 将彩色区域置黑（0），背景和其他颜色保持原样
    enhanced = gray_img.copy()
    enhanced[color_mask > 0] = 0

    # 额外：对灰度图做自适应直方图均衡化，提升整体对比度
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(enhanced)
    except Exception:
        pass

    logger.info(f"[sketch_parser] 彩色文字增强: 检测到 {cv2.countNonZero(color_mask)} 像素的彩色区域")
    return enhanced


def _focused_ocr_for_geometry(cv2, gray_img, tesseract, outer_rect, inner_rect,
                               color_img=None):
    """基于几何位置的聚焦OCR：在8个关键字段的预期位置进行高倍率OCR。

    原理：根据 outer_rect 和 inner_rect 的几何关系，
    推算出8个字段（total_w/h, inner_w/h, 4个margin）的预期位置，
    在每个位置裁剪小区域、4-5倍放大、多预处理组合OCR。

    Args:
        cv2, gray_img, tesseract: OCR依赖
        outer_rect: 外框 (ox, oy, ow, oh)
        inner_rect: 内框 (ix, iy, iw, ih)
        color_img: 彩色原图（可选，用于红色增强）

    Returns:
        dict: {field_name: (value, confidence)} 仅包含成功识别的字段
    """
    from PIL import Image as PILImage

    if tesseract is None:
        return {}

    ox, oy, ow, oh = outer_rect
    ix, iy, iw, ih = inner_rect
    h_img, w_img = gray_img.shape[:2]

    result = {}

    # 如果有彩色原图，先增强红色文字
    if color_img is not None:
        ocr_gray = _enhance_colored_text_for_ocr(color_img, gray_img)
    else:
        ocr_gray = gray_img

    def _ocr_roi(cx, cy, cw, ch, label, expected_value_range=None):
        """在指定区域进行高倍率OCR（带高置信度早退出优化）。"""
        cx1 = max(0, int(cx - cw / 2))
        cy1 = max(0, int(cy - ch / 2))
        cx2 = min(w_img, int(cx + cw / 2))
        cy2 = min(h_img, int(cy + ch / 2))

        if cx2 - cx1 < 5 or cy2 - cy1 < 5:
            return None

        roi = ocr_gray[cy1:cy2, cx1:cx2]
        if roi.size == 0:
            return None

        best_val = None
        best_score = -1000

        # 早退出阈值：当得分超过此值时提前终止（每字段节省~80%的OCR调用）
        _EARLY_EXIT_SCORE = 80

        # 优先使用最有效的 scale 和 variant 组合
        _scales = [4.5, 3.5]  # 2个尺度（原3个减为2个）
        _variant_configs = [
            ('orig_whitelist', [('orig', None)],
             [f'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.',
              f'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.']),
            ('clahe_whitelist', [('clahe', 'clahe')],
             [f'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.',
              f'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.']),
            ('otsu_flexible', [('otsu', 'otsu')],
             [f'--oem 3 --psm 6', f'--oem 3 --psm 11']),
        ]

        for scale in _scales:
            scaled = cv2.resize(roi, None, fx=scale, fy=scale,
                                interpolation=cv2.INTER_CUBIC)

            for vname, vtype, configs in _variant_configs:
                if vtype == 'orig':
                    variant = scaled
                elif vtype == 'clahe':
                    try:
                        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                        variant = clahe.apply(scaled)
                    except Exception:
                        continue
                elif vtype == 'otsu':
                    try:
                        _, variant = cv2.threshold(scaled, 0, 255,
                                                   cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    except Exception:
                        continue
                else:
                    continue

                pil_img = PILImage.fromarray(variant)

                for config in configs:
                    try:
                        data = tesseract.image_to_data(
                            pil_img, config=config,
                            output_type=tesseract.Output.DICT,
                        )
                    except Exception:
                        continue

                    if not data or 'text' not in data:
                        continue

                    n = len(data.get('text', []))
                    for i in range(n):
                        text = str(data['text'][i]).strip()
                        if not text:
                            continue
                        try:
                            conf = int(data.get('conf', [0] * n)[i])
                        except Exception:
                            conf = 0
                        if conf < 5:
                            continue

                        for m in re.finditer(r'(\d+\.?\d*)', text):
                            try:
                                val = float(m.group(1))
                            except ValueError:
                                continue
                            if not (0.5 <= val <= 500):
                                continue

                            score = conf
                            if expected_value_range:
                                vmin, vmax = expected_value_range
                                if vmin <= val <= vmax:
                                    score += 50
                                elif val < vmin * 0.5 or val > vmax * 2:
                                    score -= 30
                            else:
                                if val <= 80:
                                    score += 20

                            if score > best_score:
                                best_score = score
                                best_val = val

                    # 高置信度早退出：已找到足够好的结果，不再尝试其他组合
                    if best_score >= _EARLY_EXIT_SCORE:
                        break
                if best_score >= _EARLY_EXIT_SCORE:
                    break
            if best_score >= _EARLY_EXIT_SCORE:
                break

        if best_val is not None and best_val > 0:
            logger.info(f"[sketch_parser] 几何聚焦OCR {label}: 值={best_val:.1f}, score={best_score}, "
                        f"ROI=({cx1},{cy1})-({cx2},{cy2})")
            return (best_val, best_score)
        return None

    # ---- 定义8个字段的预期位置 ----

    # total_w: 外框上方，水平居中
    total_w_cx = ox + ow / 2
    total_w_cy = max(0, oy - ow * 0.06)  # 外框上方
    total_w_cw = ow * 0.35
    total_w_ch = oh * 0.08
    res = _ocr_roi(total_w_cx, total_w_cy, total_w_cw, total_w_ch,
                   "total_w", expected_value_range=(max(5, ow * 0.3), ow * 1.2))
    if res:
        result['total_w'] = res

    # total_h: 外框左侧，垂直居中
    total_h_cx = max(0, ox - ow * 0.06)
    total_h_cy = oy + oh / 2
    total_h_cw = ow * 0.08
    total_h_ch = oh * 0.35
    res = _ocr_roi(total_h_cx, total_h_cy, total_h_cw, total_h_ch,
                   "total_h", expected_value_range=(max(5, oh * 0.3), oh * 1.2))
    if res:
        result['total_h'] = res

    # inner_w: 内框下方，水平居中
    inner_w_cx = ix + iw / 2
    inner_w_cy = iy + ih + max(10, (oh - ih) * 0.25)  # 内框下方中间区域
    inner_w_cw = iw * 0.40
    inner_w_ch = max(15, (oh - ih) * 0.5)
    res = _ocr_roi(inner_w_cx, inner_w_cy, inner_w_cw, inner_w_ch,
                   "inner_w", expected_value_range=(max(5, iw * 0.3), iw * 1.2))
    if res:
        result['inner_w'] = res

    # inner_h: 内框右侧，垂直居中
    inner_h_cx = ix + iw + max(10, (ow - iw) * 0.25)
    inner_h_cy = iy + ih / 2
    inner_h_cw = max(15, (ow - iw) * 0.5)
    inner_h_ch = ih * 0.40
    res = _ocr_roi(inner_h_cx, inner_h_cy, inner_h_cw, inner_h_ch,
                   "inner_h", expected_value_range=(max(5, ih * 0.3), ih * 1.2))
    if res:
        result['inner_h'] = res

    # margin_top: 外框上方与内框上方之间
    mt_cx = ox + ow / 2
    mt_cy = max(0, oy - (oy - iy) * 0.3)
    mt_cw = ow * 0.20
    mt_ch = max(8, (iy - oy) * 0.7)
    res = _ocr_roi(mt_cx, mt_cy, mt_cw, mt_ch,
                   "margin_top", expected_value_range=(2, max(80, (iy - oy) * 2)))
    if res:
        result['margin_top'] = res

    # margin_bottom: 外框下方与内框下方之间
    mb_cx = ox + ow / 2
    mb_cy = iy + ih + max(5, (oy + oh - iy - ih) * 0.5)
    mb_cw = ow * 0.20
    mb_ch = max(8, (oy + oh - iy - ih) * 0.7)
    res = _ocr_roi(mb_cx, mb_cy, mb_cw, mb_ch,
                   "margin_bottom", expected_value_range=(2, max(80, (oy + oh - iy - ih) * 2)))
    if res:
        result['margin_bottom'] = res

    # margin_left: 外框左侧与内框左侧之间
    ml_cx = max(0, ox - (ox - ix) * 0.3)
    ml_cy = oy + oh / 2
    ml_cw = max(8, (ix - ox) * 0.7)
    ml_ch = oh * 0.20
    res = _ocr_roi(ml_cx, ml_cy, ml_cw, ml_ch,
                   "margin_left", expected_value_range=(2, max(80, (ix - ox) * 2)))
    if res:
        result['margin_left'] = res

    # margin_right: 外框右侧与内框右侧之间
    mr_cx = ix + iw + max(5, (ox + ow - ix - iw) * 0.5)
    mr_cy = oy + oh / 2
    mr_cw = max(8, (ox + ow - ix - iw) * 0.7)
    mr_ch = oh * 0.20
    res = _ocr_roi(mr_cx, mr_cy, mr_cw, mr_ch,
                   "margin_right", expected_value_range=(2, max(80, (ox + ow - ix - iw) * 2)))
    if res:
        result['margin_right'] = res

    if result:
        logger.info(f"[sketch_parser] 几何聚焦OCR 完成: 成功识别 {len(result)}/8 个字段")
    else:
        logger.info(f"[sketch_parser] 几何聚焦OCR 完成: 未识别到任何字段")

    return result


def _find_and_read_numbers(cv2, gray_img, outer_rect: tuple, inner_rect: tuple,
                           tesseract=None,
                           target_w_hint: float = 0.0,
                           target_h_hint: float = 0.0,
                           color_img=None) -> dict:
    """检测并读取草图上的标注数字（简化版）。

    核心策略：
      1. 提取 outer_rect 子图并做健壮 OCR
      2. 调用 _assign_ocr_values_to_fields 进行空间锚点匹配
      3. 返回结果
    """
    ox, oy, ow, oh = outer_rect
    ix, iy, iw, ih = inner_rect
    h_img, w_img = gray_img.shape[:2]

    empty = {
        'total_w': (0.0, 0),
        'total_h': (0.0, 0),
        'inner_w': (0.0, 0),
        'inner_h': (0.0, 0),
        'margin_top': (0.0, 0),
        'margin_bottom': (0.0, 0),
        'margin_left': (0.0, 0),
        'margin_right': (0.0, 0),
    }

    if tesseract is None:
        return empty

    enhanced_gray = None
    if color_img is not None:
        try:
            enhanced_gray = _enhance_colored_text_for_ocr(color_img, gray_img)
        except Exception:
            pass

    ocr_gray = enhanced_gray if enhanced_gray is not None else gray_img

    pad = max(5, int(0.03 * max(ow, oh)))
    sx1 = max(0, ox - pad)
    sy1 = max(0, oy - pad)
    sx2 = min(w_img, ox + ow + pad)
    sy2 = min(h_img, oy + oh + pad)
    sub_img = ocr_gray[sy1:sy2, sx1:sx2]

    if sub_img.size == 0:
        return dict(empty)

    sub_h, sub_w = sub_img.shape[:2]
    if sub_h > 150:
        base_scale = 2.5
    elif sub_h > 80:
        base_scale = 3.5
    else:
        base_scale = 4.5

    ocr_raw_hits = _robust_ocr_subimage(cv2, sub_img, tesseract, scale=base_scale)

    hits = []
    for v, xc, yc, cf in ocr_raw_hits:
        hits.append((v, xc + sx1, yc + sy1, cf))

    if not hits:
        return dict(empty)

    logger.info(f"[sketch_parser] OCR完成：{len(hits)} 个值")

    result = _assign_ocr_values_to_fields(
        hits, outer_rect, inner_rect, h_img, w_img,
        target_w_hint=target_w_hint, target_h_hint=target_h_hint)

    return result


def _geometry_fallback_values(outer_rect: tuple, inner_rect: tuple,
                               target_w_cm: float, target_h_cm: float) -> dict:
    """当 OCR 全部/部分失败时，用几何方法计算回退值。

    Returns:
        dict with same keys as _find_and_read_numbers
    """
    ox, oy, ow, oh = outer_rect
    ix, iy, iw, ih = inner_rect

    # 像素→cm 换算
    cm_per_px_w = cm_per_px_h = 0.0
    if target_w_cm > 0 and ow > 0:
        cm_per_px_w = target_w_cm / ow
    if target_h_cm > 0 and oh > 0:
        cm_per_px_h = target_h_cm / oh
    if cm_per_px_w and not cm_per_px_h:
        cm_per_px_h = cm_per_px_w
    elif cm_per_px_h and not cm_per_px_w:
        cm_per_px_w = cm_per_px_h

    fallback = {
        'total_w': (target_w_cm, 0.5 if target_w_cm > 0 else 0),
        'total_h': (target_h_cm, 0.5 if target_h_cm > 0 else 0),
        'inner_w': (iw * cm_per_px_w, 0.5) if cm_per_px_w else (0.0, 0),
        'inner_h': (ih * cm_per_px_h, 0.5) if cm_per_px_h else (0.0, 0),
    }

    # 边距：当像素比例可靠时（有目标尺寸参照），用几何方法计算
    # 当无 OCR 时这是唯一可用的边距来源
    if cm_per_px_w > 0 and cm_per_px_h > 0:
        mt_cm = max(0.0, (iy - oy) * cm_per_px_h)
        mb_cm = max(0.0, ((oy + oh) - (iy + ih)) * cm_per_px_h)
        ml_cm = max(0.0, (ix - ox) * cm_per_px_w)
        mr_cm = max(0.0, ((ox + ow) - (ix + iw)) * cm_per_px_w)
        fallback.update({
            'margin_top': (mt_cm, 0.4),
            'margin_bottom': (mb_cm, 0.4),
            'margin_left': (ml_cm, 0.4),
            'margin_right': (mr_cm, 0.4),
        })
    else:
        fallback.update({
            'margin_top': (0.0, 0),
            'margin_bottom': (0.0, 0),
            'margin_left': (0.0, 0),
            'margin_right': (0.0, 0),
        })
    return fallback


# ---------------------------------------------------------------------------
# 主解析流程
# ---------------------------------------------------------------------------

def parse_sketch(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
    progress_callback=None,
) -> SketchParseResult:
    """解析尺寸草图，返回 SketchParseResult（永不抛异常）。

    简化版：
      1. 加载图片 → 复杂度评估 → 几何检测
      2. OCR 识别标注数字
      3. 用 target 尺寸作为外框主源，OCR 值用于内框和边距
      4. 几何验证通过则信任 OCR，否则回退到几何
    """
    def _progress(pct: int, msg: str):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass

    result = SketchParseResult(method="hybrid")
    _progress(10, "加载图片...")
    cv2 = _safe_import_cv2()
    if cv2 is None:
        result.message = "未安装 OpenCV，无法解析草图。请手动输入边距。"
        return result

    img, err = _load_image(image_path)
    if err:
        result.message = err
        return result

    gray = _to_gray(img)
    h, w = gray.shape[:2]

    cached = _get_cached_result(image_path, target_outer_w_cm, target_outer_h_cm)
    if cached is not None:
        return cached

    consistent_cached = _get_consistent_cached_result(image_path)
    if consistent_cached is not None:
        _store_cached_result(image_path, target_outer_w_cm, target_outer_h_cm, consistent_cached)
        return consistent_cached

    is_complex, complex_reason = _assess_complexity(gray)
    if is_complex:
        result.message = complex_reason
        result.debug["complex_skipped"] = True
        return result

    _progress(25, "几何检测：查找嵌套矩形...")
    top2 = _find_two_nested_rectangles(cv2, gray, img)
    if len(top2) < 2:
        msg = f"只检测到 {len(top2)} 个矩形轮廓，无法确定内外框关系。"
        if len(top2) == 0:
            msg += "可能原因：草图线条过细、模糊或缺少清晰的矩形边框。"
            msg += "请手动输入边距或使用更清晰的草图。"
        else:
            msg += "请手动输入边距或使用更清晰的草图。"
        _progress(30, msg)
        result.message = msg
        result.debug["rects_found"] = len(top2)
        return result

    (ox, oy, ow, oh, os_score), (ix, iy, iw, ih, ins_score) = top2
    result.debug["outer_rect_px"] = (ox, oy, ow, oh)
    result.debug["inner_rect_px"] = (ix, iy, iw, ih)

    _progress(40, "OCR识别：读取标注数字...")
    tesseract = _safe_import_tesseract()

    ocr_result = _find_and_read_numbers(
        cv2, gray, (ox, oy, ow, oh), (ix, iy, iw, ih), tesseract,
        target_w_hint=target_outer_w_cm,
        target_h_hint=target_outer_h_cm,
        color_img=img)

    # === 方向标签检测：利用"上/下/左/右"标签+数值直接确定边距 ===
    _progress(50, "方向标签检测：利用方向+数值标注...")
    direction_margins = {}
    
    # 方法1：模板匹配检测方向标签（传入内框以精确定位间隙区域）
    dir_labels_template = _detect_direction_labels_by_template(
        cv2, gray, (ox, oy, ow, oh), img, inner_rect=(ix, iy, iw, ih))
    logger.info(f"[sketch_parser] 模板匹配检测到 {len(dir_labels_template)} 个方向标签")
    
    # 方法2：OCR方法检测方向标签（同时使用）
    dir_labels_ocr = []
    if tesseract is not None:
        dir_labels_ocr = _detect_direction_labels_by_ocr(cv2, gray, tesseract, (ox, oy, ow, oh))
        logger.info(f"[sketch_parser] OCR方法检测到 {len(dir_labels_ocr)} 个方向标签")
    
    # 方法3：基于几何位置的边距OCR检测（不依赖方向标签）
    geometry_margins = {}
    if tesseract is not None:
        geometry_margins = _detect_margins_by_geometry_ocr(
            cv2, gray, tesseract, (ox, oy, ow, oh),
            inner_rect=(ix, iy, iw, ih),
            target_outer_w_cm=target_outer_w_cm,
            target_outer_h_cm=target_outer_h_cm)
        if geometry_margins:
            logger.info(f"[sketch_parser] 几何OCR检测到 {len(geometry_margins)} 个边距值")
    
    # 合并结果：优先保留带有数值的OCR结果
    # 模板匹配仅提供位置信息，OCR检测提供位置+数值
    dir_labels = []
    _field_best = {}
    
    for item in dir_labels_template:
        dchar, mfield, lx, ly, conf, val = item
        if mfield not in _field_best or val is not None:
            _field_best[mfield] = item
    
    for item in dir_labels_ocr:
        dchar, mfield, lx, ly, conf, val = item
        if mfield not in _field_best:
            _field_best[mfield] = item
        elif val is not None and _field_best[mfield][5] is None:
            _field_best[mfield] = item
    
    dir_labels = list(_field_best.values())
    
    logger.info(f"[sketch_parser] 合并后共 {len(dir_labels)} 个方向标签")
    
    # 计算标签位置是否在内框内部（不合理，会导致聚焦OCR读到内框值）
    _label_inside_inner = {}
    for dchar, mfield, lx, ly, conf, existing_val in dir_labels:
        if ix <= lx <= ix + iw and iy <= ly <= iy + ih:
            _label_inside_inner[mfield] = True
            logger.warning(f"[sketch_parser] 方向标签 {dchar} 位置({lx:.0f},{ly:.0f})在内框内部，聚焦OCR可能读到错误值")
    
    # 对每个检测到的方向标签进行聚焦OCR，获取准确的边距数值
    for dchar, mfield, lx, ly, conf, existing_val in dir_labels:
        logger.info(f"[sketch_parser] 方向标签 {dchar}(位置={lx:.0f},{ly:.0f}, conf={conf})")
        
        # 空间合理性检查：如果标签位置严重不合理，跳过
        # 检查标签是否在外框与内框之间的间隙区域
        if not _is_label_position_strict(dchar, lx, ly, ox, oy, ow, oh, ix, iy, iw, ih):
            logger.warning(f"[sketch_parser] 方向标签 {dchar} 位置不合理 ({lx:.0f},{ly:.0f}), 跳过")
            continue
        
        # 如果标签在内框内部，跳过聚焦OCR（会读到内框值而非边距值）
        if mfield in _label_inside_inner:
            logger.warning(f"[sketch_parser] 方向标签 {dchar} 在内框内部，跳过聚焦OCR，使用几何OCR结果")
            continue
        
        # 如果方向标签本身带有数值（如"上6"），直接使用
        if existing_val is not None and 0.5 <= existing_val <= 500:
            direction_margins[mfield] = (existing_val, max(70, conf))
            logger.info(f"[sketch_parser] 方向标签 {dchar} 自带数值={existing_val}, 直接采用")
            continue
        
        # 否则，在标签位置进行聚焦OCR
        if tesseract is not None:
            focused_result = _focused_ocr_for_direction_label(
                cv2, gray, tesseract, dchar, mfield, lx, ly, (ox, oy, ow, oh),
                target_outer_w_cm=target_outer_w_cm,
                target_outer_h_cm=target_outer_h_cm)
            if focused_result is not None:
                fval, fconf = focused_result
                # 合理性检查：边距值不应过大（超过外框短边的40%）
                _outer_min_side = min(target_outer_w_cm or ow, target_outer_h_cm or oh)
                if fval > _outer_min_side * 0.40:
                    logger.warning(f"[sketch_parser] 聚焦OCR {dchar} 值={fval}过大(>外框短边40%)，可能是内框值，跳过")
                else:
                    direction_margins[mfield] = (fval, fconf)
                    logger.info(f"[sketch_parser] 聚焦OCR {dchar}边距: 值={fval}, conf={fconf}")
            else:
                # 兜底策略：使用全图OCR结果
                _field_key_map = {'margin_top': 'mt', 'margin_bottom': 'mb',
                                  'margin_left': 'ml', 'margin_right': 'mr'}
                _fkey = _field_key_map.get(mfield, '')
                _ocr_val = ocr_result.get(mfield, (0, 0))[0]
                if _ocr_val > 0 and _fkey:
                    direction_margins[mfield] = (_ocr_val, 30)
                    logger.info(f"[sketch_parser] 聚焦OCR失败，使用全图OCR兜底 {dchar}: 值={_ocr_val}")

    # 方法4：使用几何OCR结果补充/替代方向标签结果
    # 对于方向标签检测失败或结果不可靠的边距，使用几何OCR结果
    if geometry_margins:
        for mfield, (gval, gconf) in geometry_margins.items():
            if mfield not in direction_margins:
                # 方向标签没有检测到，使用几何OCR结果
                direction_margins[mfield] = (gval, gconf)
                logger.info(f"[sketch_parser] 几何OCR补充 {mfield}: 值={gval}, conf={gconf}")
            else:
                # 方向标签已检测到，比较两个结果
                _dir_val, _dir_conf = direction_margins[mfield]
                
                # 策略改进：
                # 1. 如果两个值很接近（差异<=1.5），取平均
                # 2. 如果几何OCR置信度更高，使用几何OCR
                # 3. 如果聚焦OCR识别的值可能不准确（如识别到7而几何OCR识别到6），优先使用较小的值
                # 4. 如果两个值差异较大，选择更合理的值
                
                _diff = abs(gval - _dir_val)
                
                if _diff <= 1.5:
                    # 两个值很接近，取平均
                    avg_val = round((gval + _dir_val) / 2, 1)
                    max_conf = max(gconf, _dir_conf)
                    direction_margins[mfield] = (avg_val, max_conf)
                    logger.info(f"[sketch_parser] 几何OCR+方向标签平均 {mfield}: ({_dir_val}+{gval})/2={avg_val}")
                elif gconf > _dir_conf + 20:
                    # 几何OCR置信度明显更高，使用几何OCR
                    direction_margins[mfield] = (gval, gconf)
                    logger.info(f"[sketch_parser] 几何OCR替代 {mfield}: {_dir_val}→{gval} (conf: {_dir_conf}→{gconf})")
                elif gval < _dir_val and gconf >= _dir_conf - 5:
                    # 几何OCR的值更小且置信度接近，优先使用较小的值（边距通常较小）
                    direction_margins[mfield] = (gval, gconf)
                    logger.info(f"[sketch_parser] 几何OCR取小值 {mfield}: {_dir_val}→{gval} (更小且conf接近)")
                else:
                    # 其他情况，保留方向标签结果
                    pass

    if direction_margins:
        logger.info(f"[sketch_parser] 方向标签+几何OCR识别结果: {direction_margins}")

    _progress(75, "几何回退计算...")
    geo_result = _geometry_fallback_values(
        (ox, oy, ow, oh), (ix, iy, iw, ih),
        target_outer_w_cm, target_outer_h_cm)

    outer_w = target_outer_w_cm if target_outer_w_cm > 0 else ocr_result.get('total_w', (0, 0))[0]
    outer_h = target_outer_h_cm if target_outer_h_cm > 0 else ocr_result.get('total_h', (0, 0))[0]

    if outer_w <= 0:
        outer_w = ocr_result.get('total_w', (0, 0))[0]
    if outer_h <= 0:
        outer_h = ocr_result.get('total_h', (0, 0))[0]

    inner_w = ocr_result.get('inner_w', (0, 0))[0]
    inner_h = ocr_result.get('inner_h', (0, 0))[0]
    mt = ocr_result.get('margin_top', (0, 0))[0]
    mb = ocr_result.get('margin_bottom', (0, 0))[0]
    ml = ocr_result.get('margin_left', (0, 0))[0]
    mr = ocr_result.get('margin_right', (0, 0))[0]

    # === 方向标签边距覆盖：如果方向标签识别到了边距值，优先使用 ===
    # 但需要检查方向标签值是否合理（不能与空间分配的OCR值差异过大）
    if direction_margins:
        _dir_override_count = 0
        for _field, _key in [('margin_top', 'mt'), ('margin_bottom', 'mb'),
                              ('margin_left', 'ml'), ('margin_right', 'mr')]:
            if _field in direction_margins:
                _dir_val, _dir_conf = direction_margins[_field]
                _ocr_val = locals()[_key]
                
                # 判断是否使用方向标签值：
                # 1. OCR值为0时，直接使用方向标签值（唯一来源）
                # 2. 方向标签值与OCR值接近（差异<=30%）时，取方向标签值（通常更准）
                # 3. 方向标签值比OCR值稍小（差异在30%-50%之间），可能更准
                # 4. 方向标签值与OCR值差异过大（>50%）时，保留OCR值
                
                _should_use_dir = False
                if _ocr_val <= 0:
                    _should_use_dir = True
                elif abs(_dir_val - _ocr_val) / max(0.1, _ocr_val) <= 0.3:
                    _should_use_dir = True
                elif _dir_val < _ocr_val and abs(_dir_val - _ocr_val) / max(0.1, _ocr_val) <= 0.5:
                    # 方向标签值比OCR值小但差异在50%以内，可能更准
                    _should_use_dir = True
                else:
                    # 差异过大或方向标签值更大，保留OCR值
                    logger.warning(f"[sketch_parser] {_key}: 方向标签值={_dir_val}与OCR值={_ocr_val}差异过大(>{abs(_dir_val - _ocr_val) / max(0.1, _ocr_val):.0%})，保留OCR值")
                    continue
                
                if _should_use_dir:
                    if _key == 'mt':
                        mt = _dir_val
                    elif _key == 'mb':
                        mb = _dir_val
                    elif _key == 'ml':
                        ml = _dir_val
                    elif _key == 'mr':
                        mr = _dir_val
                    _dir_override_count += 1
                    logger.info(f"[sketch_parser] 方向标签覆盖 {_key}: OCR={_ocr_val:.2f} → 方向标签={_dir_val:.2f}")
        if _dir_override_count > 0:
            logger.info(f"[sketch_parser] 方向标签共覆盖 {_dir_override_count} 个边距值")

    def _check_geometry(ow_, oh_, iw_, ih_, mt_, mb_, ml_, mr_):
        if ow_ <= 0 or oh_ <= 0:
            return False
        h_diff = abs(ow_ - iw_ - (ml_ + mr_))
        v_diff = abs(oh_ - ih_ - (mt_ + mb_))
        h_tol = max(2.0, ow_ * 0.10)
        v_tol = max(2.0, oh_ * 0.10)
        return h_diff <= h_tol and v_diff <= v_tol

    ocr_valid = (outer_w > 0 and outer_h > 0 and inner_w > 0 and inner_h > 0
                 and mt > 0 and mb > 0 and ml > 0 and mr > 0
                 and _check_geometry(outer_w, outer_h, inner_w, inner_h, mt, mb, ml, mr))

    if not ocr_valid:
        ocr_valid_alt = (outer_w > 0 and outer_h > 0 and inner_w > 0 and inner_h > 0
                         and mt > 0 and mb > 0 and ml > 0 and mr > 0
                         and _check_geometry(outer_h, outer_w, inner_h, inner_w, mt, mb, ml, mr))
        if ocr_valid_alt:
            outer_w, outer_h = outer_h, outer_w
            inner_w, inner_h = inner_h, inner_w
            ocr_valid = True

    if not ocr_valid:
        logger.info("[sketch_parser] OCR 几何验证未通过，使用几何回退值")
        geo_inner = _geometry_fallback_values(
            (ox, oy, ow, oh), (ix, iy, iw, ih),
            outer_w, outer_h)
        # 如果有方向标签识别的边距值，保留它们（不被几何回退覆盖）
        _has_dir_mt = 'margin_top' in direction_margins
        _has_dir_mb = 'margin_bottom' in direction_margins
        _has_dir_ml = 'margin_left' in direction_margins
        _has_dir_mr = 'margin_right' in direction_margins
        
        if inner_w <= 0:
            inner_w = geo_inner.get('inner_w', (0, 0))[0]
        if inner_h <= 0:
            inner_h = geo_inner.get('inner_h', (0, 0))[0]
        if mt <= 0 and not _has_dir_mt:
            mt = geo_inner.get('margin_top', (0, 0))[0]
        if mb <= 0 and not _has_dir_mb:
            mb = geo_inner.get('margin_bottom', (0, 0))[0]
        if ml <= 0 and not _has_dir_ml:
            ml = geo_inner.get('margin_left', (0, 0))[0]
        if mr <= 0 and not _has_dir_mr:
            mr = geo_inner.get('margin_right', (0, 0))[0]
        logger.info(f"[sketch_parser] 几何回退：保留方向标签边距 mt={_has_dir_mt}, mb={_has_dir_mb}, ml={_has_dir_ml}, mr={_has_dir_mr}")

    # 对每个 margin 值做合理性检查：如果 OCR 值与几何回退值差异过大，用几何回退值
    # 但如果值来自方向标签，则保留（方向标签值优先级更高）
    geo_check = _geometry_fallback_values(
        (ox, oy, ow, oh), (ix, iy, iw, ih),
        outer_w, outer_h)
    
    # 记录方向标签提供的边距字段
    _dir_margin_fields = set(direction_margins.keys())
    
    for _name, _ocr_val, _geo_val, _field_key in [
        ('mt', mt, geo_check.get('margin_top', (0, 0))[0], 'margin_top'),
        ('mb', mb, geo_check.get('margin_bottom', (0, 0))[0], 'margin_bottom'),
        ('ml', ml, geo_check.get('margin_left', (0, 0))[0], 'margin_left'),
        ('mr', mr, geo_check.get('margin_right', (0, 0))[0], 'margin_right'),
    ]:
        # 如果该值来自方向标签，跳过合理性检查（方向标签值更可靠）
        if _field_key in _dir_margin_fields and direction_margins.get(_field_key, (0, 0))[0] == _ocr_val:
            logger.info(f"[sketch_parser] margin {_name}: 值来自方向标签({_ocr_val:.2f})，跳过几何合理性检查")
            continue
        if _geo_val > 0 and _ocr_val > 0:
            diff_ratio = abs(_ocr_val - _geo_val) / max(1, _geo_val)
            if diff_ratio > 0.3:
                logger.info(
                    f"[sketch_parser] margin {_name}: OCR={_ocr_val:.2f} "
                    f"与几何回退值={_geo_val:.2f} 差异过大({diff_ratio:.0%})，用几何回退值"
                )
                if _name == 'mt':
                    mt = _geo_val
                elif _name == 'mb':
                    mb = _geo_val
                elif _name == 'ml':
                    ml = _geo_val
                elif _name == 'mr':
                    mr = _geo_val

    def _clip_side(v, outer_side, is_from_direction=False):
        """裁剪边距值。方向标签来源的值放宽上限到70%。"""
        if v <= 0 or outer_side <= 0:
            return v
        # 方向标签值更可靠，允许更大的边距（放宽到70%）
        max_ratio = 0.7 if is_from_direction else 0.5
        if v > outer_side * max_ratio:
            return outer_side * max_ratio
        if v < 0:
            return 0.0
        return v

    ml = _clip_side(ml, outer_w, 'margin_left' in _dir_margin_fields)
    mr = _clip_side(mr, outer_w, 'margin_right' in _dir_margin_fields)
    mt = _clip_side(mt, outer_h, 'margin_top' in _dir_margin_fields)
    mb = _clip_side(mb, outer_h, 'margin_bottom' in _dir_margin_fields)

    if inner_w > outer_w > 0:
        inner_w = max(0, outer_w - max(ml + mr, 0))
    if inner_h > outer_h > 0:
        inner_h = max(0, outer_h - max(mt + mb, 0))

    if outer_w > 0 and outer_h > 0 and mt >= 0 and mb >= 0 and ml >= 0 and mr >= 0:
        result.outer_w_cm = round(outer_w, 2)
        result.outer_h_cm = round(outer_h, 2)
        result.inner_w_cm = round(inner_w, 2)
        result.inner_h_cm = round(inner_h, 2)
        result.margin_top_cm = round(mt, 2)
        result.margin_bottom_cm = round(mb, 2)
        result.margin_left_cm = round(ml, 2)
        result.margin_right_cm = round(mr, 2)
        result.success = True

        ocr_read_keys = [k for k, (v, c) in ocr_result.items() if c > 0 and v > 0]
        
        # 构建消息，标注方向标签识别的边距
        _dir_info = ""
        if direction_margins:
            _dir_parts = []
            for _f, (_v, _c) in direction_margins.items():
                _short = {'margin_top': '上', 'margin_bottom': '下', 
                          'margin_left': '左', 'margin_right': '右'}.get(_f, _f)
                _dir_parts.append(f"{_short}{_v:.1f}")
            _dir_info = f"（方向标签识别：{', '.join(_dir_parts)}）"
        
        if ocr_read_keys or direction_margins:
            result.method = "ocr+direction+geometry" if direction_margins else "ocr+geometry"
            result.message = (
                f"✅ 自动识别成功（{len(ocr_read_keys)} 项数值通过 OCR 读取"
                f"{'，'+str(len(direction_margins))+' 项通过方向标签' if direction_margins else ''}）：\n"
                f"外框 {result.outer_w_cm}×{result.outer_h_cm} cm，"
                f"内挖 {result.inner_w_cm}×{result.inner_h_cm} cm\n"
                f"边距：上{result.margin_top_cm}/下{result.margin_bottom_cm}/"
                f"左{result.margin_left_cm}/右{result.margin_right_cm} cm\n"
                f"{_dir_info}"
                f"请检查数值后继续（可手动微调）。"
            )
        else:
            result.method = "geometry"
            result.message = (
                f"✅ 几何检测成功（未检测到标注数字，用像素比例推算）：\n"
                f"外框 {result.outer_w_cm}×{result.outer_h_cm} cm，"
                f"内挖 {result.inner_w_cm}×{result.inner_h_cm} cm\n"
                f"边距：上{result.margin_top_cm}/下{result.margin_bottom_cm}/"
                f"左{result.margin_left_cm}/右{result.margin_right_cm} cm\n"
                f"💡 建议：若数值不准，请手动输入或安装 Tesseract 以启用 OCR。"
            )
    else:
        result.message = "解析结果不可信，请手动输入边距。"
        result.debug["fusion_state"] = {
            "outer_w": outer_w, "outer_h": outer_h,
            "mt": mt, "mb": mb, "ml": ml, "mr": mr,
        }

    result.debug["ocr_values"] = {k: round(v, 2) for k, (v, c) in ocr_result.items() if c > 0}
    result.debug["geo_values"] = {k: round(v, 2) for k, (v, c) in geo_result.items() if c > 0}

    if result.success:
        _store_cached_result(image_path, target_outer_w_cm, target_outer_h_cm, result)

        _all_positive = all(v > 0 for v in [
            result.outer_w_cm, result.outer_h_cm,
            result.inner_w_cm, result.inner_h_cm,
            result.margin_top_cm, result.margin_bottom_cm,
            result.margin_left_cm, result.margin_right_cm,
        ])
        if _all_positive:
            _h_diff = abs(result.outer_w_cm - result.inner_w_cm
                          - (result.margin_left_cm + result.margin_right_cm))
            _v_diff = abs(result.outer_h_cm - result.inner_h_cm
                          - (result.margin_top_cm + result.margin_bottom_cm))
            if _h_diff < 1.0 and _v_diff < 1.0:
                _store_consistent_cached_result(image_path, result)

    _progress(100, "草图解析完成")
    return result
# ---------------------------------------------------------------------------
# 旧接口 parse_sketch_geometry 已删除（v2026-08-15 清理冗余）。
# 原实现仅为 parse_sketch() 的薄封装，实际无任何调用方。
# 如需几何-only 解析，直接调用 parse_sketch() 并使用返回的 debug 字段即可。
# ---------------------------------------------------------------------------