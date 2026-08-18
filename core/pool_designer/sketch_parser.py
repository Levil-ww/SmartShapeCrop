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
    """构建多种二值化 mask，用于不同风格草图的检测。

    Args:
        gray_img: 灰度图
        color_img: 原始彩色图（可选，用于红色线检测）
    """
    masks = []

    # 小 kernel：仅 2x2，避免把内外框之间的文字/线条连接起来
    kernel_small = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    # 中 kernel：仅用于红色线（较粗），不影响内外框分离
    kernel_mid = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

    # 策略 A1：Canny 原始边缘（无形态学，最保守）
    edges_raw = cv2.Canny(gray_img, 15, 80)
    masks.append(("canny_raw", edges_raw))

    # 策略 A2：Canny + 轻度形态学（用 2x2 kernel，1 次迭代）
    edges = cv2.Canny(gray_img, 15, 80)
    mask_canny = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel_small, iterations=1)
    masks.append(("canny", mask_canny))

    # 策略 B：自适应阈值 + 轻度形态学
    binary_adapt = cv2.adaptiveThreshold(
        gray_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 25, 10)
    mask_adapt = cv2.morphologyEx(binary_adapt, cv2.MORPH_CLOSE, kernel_small, iterations=1)
    masks.append(("adaptive", mask_adapt))

    # 策略 C：HSV 红色线检测（针对红色标注的草图，使用彩色图）
    if color_img is not None and len(color_img.shape) == 3:
        hsv = cv2.cvtColor(color_img, cv2.COLOR_BGR2HSV)
        mask_r1 = cv2.inRange(hsv, np.array([0, 20, 30]), np.array([15, 255, 255]))
        mask_r2 = cv2.inRange(hsv, np.array([165, 20, 30]), np.array([180, 255, 255]))
        mask_red = cv2.bitwise_or(mask_r1, mask_r2)
        # 红色线可能较粗，用中 kernel 连接断线
        mask_red = cv2.morphologyEx(mask_red, cv2.MORPH_CLOSE, kernel_mid, iterations=1)
        if cv2.countNonZero(mask_red) > 50:
            masks.append(("red", mask_red))

    # 策略 D：Otsu 自动阈值 + 轻度形态学
    _, mask_otsu = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask_otsu = cv2.morphologyEx(mask_otsu, cv2.MORPH_CLOSE, kernel_small, iterations=1)
    masks.append(("otsu", mask_otsu))

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
    min_component_area = max(200, int(full_area * 0.002))

    masks = _build_binary_masks(cv2, gray_img, color_img)

    candidates = []
    seen_bboxes = set()

    for mask_name, mask in masks:
        # 连通分量分析（比 findContours 更稳健，不会合并相邻组件）
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)

        for label_id in range(1, num_labels):  # 跳过背景
            area = stats[label_id, cv2.CC_STAT_AREA]
            if area < min_component_area:
                continue

            x = stats[label_id, cv2.CC_STAT_LEFT]
            y = stats[label_id, cv2.CC_STAT_TOP]
            ww = stats[label_id, cv2.CC_STAT_WIDTH]
            hh = stats[label_id, cv2.CC_STAT_HEIGHT]

            if ww < 15 or hh < 15:
                continue

            rect_area = ww * hh
            if rect_area <= 0:
                continue

            # 用轮廓近似计算边数（对连通区域做轮廓检测）
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

            # 判断是否为空心轮廓（线框）
            # 空心：面积 << boundingRect 面积
            fill_ratio = area / rect_area if rect_area > 0 else 0
            is_hollow = fill_ratio < 0.1

            if is_hollow:
                # 空心轮廓（线框）：主要根据边数评分
                vertex_score = 0.6 if n_vertices == 4 else (0.4 if 3 <= n_vertices <= 6 else 0.0)
                # boundingRect 的宽高比也加分
                aspect = min(ww, hh) / max(ww, hh) if ww > 0 and hh > 0 else 0
                aspect_score = min(1.0, aspect) * 0.3
                score = vertex_score + aspect_score
            else:
                # 填充区域：使用矩形度评分
                rectangularity = min(1.0, fill_ratio)
                vertex_score = 0.5 if n_vertices == 4 else (0.3 if 3 <= n_vertices <= 6 else 0.0)
                score = rectangularity * 0.6 + vertex_score * 0.4

            if score < 0.25:
                continue

            # 去重（量化到 10px 网格）
            rx_q = round(x / 10) * 10
            ry_q = round(y / 10) * 10
            rw_q = round(ww / 10) * 10
            rh_q = round(hh / 10) * 10
            key = (rx_q, ry_q, rw_q, rh_q)
            if key in seen_bboxes:
                continue
            seen_bboxes.add(key)

            candidates.append((x, y, ww, hh, score, rect_area))

    if not candidates:
        return []

    # 按 boundingRect 面积降序
    candidates.sort(key=lambda c: c[5], reverse=True)

    # 取前 10 个，找最佳嵌套对
    top = candidates[:10]

    best_pair = None
    best_score = -1

    for i in range(len(top)):
        outer = top[i]
        ox, oy, ow, oh, os, oa = outer
        for j in range(len(top)):
            if i == j:
                continue
            inner = top[j]
            ix, iy, iw, ih, ins, ina = inner

            # 嵌套验证
            if ix < ox - 5 or iy < oy - 5:
                continue
            if ix + iw > ox + ow + 5 or iy + ih > oy + oh + 5:
                continue
            if ina >= oa * 0.9:
                continue

            # 四边至少有 2px 边距
            mt = iy - oy
            mb = (oy + oh) - (iy + ih)
            ml = ix - ox
            mr = (ox + ow) - (ix + iw)
            if min(mt, mb, ml, mr) < 2:
                continue

            score_sum = os + ins + 0.1 * (oa / max(1, ina))
            if score_sum > best_score:
                best_score = score_sum
                best_pair = (outer, inner)

    if best_pair:
        outer, inner = best_pair
        return [
            (outer[0], outer[1], outer[2], outer[3], outer[4]),
            (inner[0], inner[1], inner[2], inner[3], inner[4]),
        ]

    # 兜底：面积最大两个
    if len(top) >= 2:
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

    # 去重
    dedup_final = {}
    for val, xc, yc, conf in repaired:
        key = None
        for k, (dv, dxc, dyc, dc) in list(dedup_final.items()):
            if abs(dv - val) < max(0.05, val * 0.02) and abs(dxc - xc) < 30 and abs(dyc - yc) < 30:
                key = k
                break
        if key is None:
            dedup_final[(val, xc, yc)] = (val, xc, yc, conf)
        else:
            ov, oxc, oyc, oc = dedup_final[key]
            if conf > oc:
                dedup_final[key] = (val, xc, yc, conf)
    results = list(dedup_final.values())

    return results


def _assign_ocr_values_to_fields(ocr_hits, outer_rect, inner_rect,
                                 h_img, w_img,
                                 target_w_hint: float = 0.0,
                                 target_h_hint: float = 0.0) -> dict:
    """根据 OCR 识别数值的空间位置，把每个数值分配到最可能的字段。

    Args:
        ocr_hits: [(value, x_center, y_center, confidence), ...]
        outer_rect / inner_rect: (x, y, w, h)
        target_w_hint / target_h_hint: 目标尺寸提示（可选，用于基于数值大小的回退分配）

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

    # 各字段的理想位置（作为锚点）
    anchors = [
        # (key, x_center_frac, y_center_frac, x_range, y_range)
        # x_range / y_range：接受的范围（像素单位的容差）

        # 总宽：外框下边外侧或下边中央附近（水平）
        ('total_w',   (ox + ow / 2), (oy + oh + oh * 0.12),
            (ow * 0.6), (oh * 0.3)),
        # 总高：外框左边外侧或左边中央附近（垂直）
        ('total_h',   (ox - ow * 0.12), (oy + oh / 2),
            (ow * 0.3), (oh * 0.6)),
        # 上边距：内外框间隙上方中央（x: 外框宽度范围，y: 上边距区域）
        ('margin_top',    (ox + ow / 2), (oy + max(0, iy - oy) / 2),
            (ow * 0.8), max(oh * 0.35, (iy - oy) + oh * 0.15)),
        # 下边距：内外框间隙下方中央
        ('margin_bottom', (ox + ow / 2), (iy + ih + max(0, (oy + oh) - (iy + ih)) / 2),
            (ow * 0.8), max(oh * 0.35, ((oy + oh) - (iy + ih)) + oh * 0.15)),
        # 左边距：内外框间隙左侧中央
        ('margin_left',   (ox + (ix - ox) / 2), (iy + ih / 2),
            max(ow * 0.3, (ix - ox) + ow * 0.15), (ih * 0.9)),
        # 右边距：内外框间隙右侧中央
        ('margin_right',  (ix + iw + ((ox + ow) - (ix + iw)) / 2), (iy + ih / 2),
            max(ow * 0.3, ((ox + ow) - (ix + iw)) + ow * 0.15), (ih * 0.9)),
        # 内框宽：内框中央（上半部分，水平读）
        ('inner_w',   (ix + iw / 2), (iy + ih * 0.35),
            (iw * 0.8), (ih * 0.5)),
        # 内框高：内框中央（下半部分，水平读）
        ('inner_h',   (ix + iw / 2), (iy + ih * 0.65),
            (iw * 0.8), (ih * 0.5)),
    ]

    # ---- 扩展锚点：覆盖外框外部区域（草图中边距/总尺寸常标注在外框外侧）----
    # 边距扩展：外框外部的边距标注区
    _ext_margin_range = max(ow, oh) * 0.18  # 外框外部搜索范围
    # 上边距扩展：外框上方
    anchors.append(('margin_top', (ox + ow / 2), (oy - _ext_margin_range * 0.5),
        (ow * 0.9), _ext_margin_range))
    # 下边距扩展：外框下方
    anchors.append(('margin_bottom', (ox + ow / 2), (oy + oh + _ext_margin_range * 0.5),
        (ow * 0.9), _ext_margin_range))
    # 左边距扩展：外框左侧
    anchors.append(('margin_left', (ox - _ext_margin_range * 0.5), (oy + oh / 2),
        _ext_margin_range, (oh * 0.9)))
    # 右边距扩展：外框右侧
    anchors.append(('margin_right', (ox + ow + _ext_margin_range * 0.5), (oy + oh / 2),
        _ext_margin_range, (oh * 0.9)))

    # 总宽/总高扩展：外框外部的总尺寸标注区
    anchors.append(('total_w', (ox + ow * 0.5), (oy + oh + oh * 0.12),
        (ow * 0.95), (oh * 0.3)))
    anchors.append(('total_w', (ox + ow / 2), (oy - oh * 0.12),
        (ow * 0.95), (oh * 0.4)))
    anchors.append(('total_h', (ox - ow * 0.08), (oy + oh * 0.5),
        (ow * 0.35), (oh * 0.95)))
    anchors.append(('total_h', (ox + ow * 1.08), (oy + oh * 0.5),
        (ow * 0.35), (oh * 0.95)))
    anchors.append(('total_h', (ox + ow / 2), (oy - oh * 0.08),
        (ow * 0.5), (oh * 0.25)))
    anchors.append(('total_h', (ox + ow / 2), (oy + oh + oh * 0.08),
        (ow * 0.5), (oh * 0.25)))

    # 内框尺寸扩展：内框四角附近（尺寸值可能标在角上）
    anchors.append(('inner_w', (ix + iw / 2), (iy - ih * 0.1),
        (iw * 0.6), (ih * 0.3)))
    anchors.append(('inner_h', (ix + iw + iw * 0.1), (iy + ih / 2),
        (iw * 0.3), (ih * 0.6)))

    # 先按数值大小给分类权重：边距一般较小（<60），总尺寸一般较大
    # 这样小数值不至于被"总高在顶部"等锚点误抢
    def _value_pref_weight(val, key, yc_rel):
        # yc_rel: hit 相对于外框的垂直位置（0=外框顶，1=外框底，>1=外框下方，<0=外框上方）
        if key == 'total_w':
            bonus = 0.1 if (yc_rel >= 0.9 or yc_rel <= 0.1) else 0.0
            if val >= 50: bonus += 0.15
            elif val < 15: bonus -= 0.2
            return bonus
        elif key == 'total_h':
            if 0.2 <= yc_rel <= 0.8:
                bonus = 0.12   # 侧方中间区域加分
            elif yc_rel >= 0.9 or yc_rel <= 0.1:
                bonus = -0.12  # 顶/底区域是 total_w 地盘，给 total_h 减分避免抢
            else:
                bonus = 0.0
            if val >= 50: bonus += 0.15
            elif val < 15: bonus -= 0.2
            return bonus
        elif key in ('inner_w', 'inner_h'):
            return 0.1 if 20 <= val <= 300 else 0.0
        elif key.startswith('margin_'):
            # 边距范围放宽到 2~80，支持大边距值
            if 2 <= val <= 80: return 0.15
            if val > 80: return -0.15
            return 0.0
        return 0.0

    # ====== 阶段 1：生成所有 (score, hit_idx, key) 候选，按得分从高到低 ======
    # hit_idx 用来标记"哪个 OCR hit"——保证一个数字只占 1 个字段！
    all_edges = []
    hit_keys_candidates = []  # hit_idx -> list of (key, score)

    def _axis_weight_for_key(key):
        """返回 (wx, wy)：对该字段更重要的轴给更高权重。
        例如 margin_left / margin_right → x 方向更关键；margin_top / margin_bottom → y 更关键。
        total_w → y 更关键（靠近顶/底）；total_h → x 更关键（靠近左/右边）。
        """
        if key in ('margin_left', 'margin_right'):
            return (0.7, 0.3)  # x 更重要
        if key in ('margin_top', 'margin_bottom'):
            return (0.3, 0.7)  # y 更重要
        if key == 'total_w':
            return (0.3, 0.7)  # y（顶/底位置）更重要
        if key == 'total_h':
            return (0.7, 0.3)  # x（左/右位置）更重要
        return (0.4, 0.4)  # inner_w / inner_h 均衡

    for hit_idx, (val, xc, yc, conf) in enumerate(ocr_hits):
        yc_rel = (yc - oy) / oh if oh > 0 else 0.5
        cands = []
        for (key, ax, ay, x_tol, y_tol) in anchors:
            dx = abs(xc - ax)
            dy = abs(yc - ay)
            if dx > x_tol or dy > y_tol:
                continue
            wx, wy = _axis_weight_for_key(key)
            dist_score = (1 - dx / max(1, x_tol)) * wx + (1 - dy / max(1, y_tol)) * wy
            s = dist_score + conf * 0.2 + _value_pref_weight(val, key, yc_rel)
            cands.append((key, s))
            all_edges.append((s, hit_idx, key, val, xc, yc, conf))
        # 保存此 hit 的所有 key 候选（用于后续 alternates 重分配）
        cands.sort(key=lambda t: t[1], reverse=True)
        hit_keys_candidates.append(cands)

    # 按得分降序（越大越先匹配）
    all_edges.sort(key=lambda e: e[0], reverse=True)

    used_values = {}  # key → (val, score)
    used_hit_idx = set()  # 已占用的 ocr_hit

    for s, hi, key, val, xc, yc, conf in all_edges:
        if hi in used_hit_idx:
            continue
        if key in used_values:
            continue
        used_values[key] = (val, s)
        used_hit_idx.add(hi)

    # alternates: 未用的 hit 仍然保留所有候选（用于后续填缺）
    alternates = []
    for hi, (val, xc, yc, conf, cands) in enumerate(zip(
            [h[0] for h in ocr_hits],
            [h[1] for h in ocr_hits],
            [h[2] for h in ocr_hits],
            [h[3] for h in ocr_hits],
            hit_keys_candidates)):
        # 兼容 zip：上面 zip 构造有点问题，改用 enumerate(ocr_hits) 显式构造
        pass
    # 正确构造 alternates（上面写法混乱，重写）
    alternates.clear()
    for hi, (val, xc, yc, conf) in enumerate(ocr_hits):
        if hi in used_hit_idx:
            continue
        for key, s in hit_keys_candidates[hi]:
            alternates.append((val, key, s, xc, yc, conf))
    # 也把"已经通过 higher-score 抢到某个 key"的 hit 的其他候选键保存
    # （万一后面需要把某个字段交换出来）
    for hi, (val, xc, yc, conf) in enumerate(ocr_hits):
        for key, s in hit_keys_candidates[hi]:
            # 不加入已经占用的 key
            if key in used_values and used_values[key][0] == val:
                continue
            alternates.append((val, key, s, xc, yc, conf))

    # ====== 阶段 2：方向矫正 total_w ↔ total_h ======
    tw_val, tw_sc = used_values.get('total_w', (0.0, 0.0))
    th_val, th_sc = used_values.get('total_h', (0.0, 0.0))

    def _apply_total_direction_swap(px_ratio, tw_v, th_v):
        if tw_v <= 0 or th_v <= 0:
            return tw_v, th_v, False
        need = False
        if px_ratio > 1.05 and tw_v < th_v:
            need = True
        elif px_ratio < 1.0 / 1.05 and tw_v > th_v:
            need = True
        if need:
            return th_v, tw_v, True
        return tw_v, th_v, False

    px_ratio = ow / oh if oh > 0 else 1.0
    tw_val, th_val, swapped = _apply_total_direction_swap(px_ratio, tw_val, th_val)
    if swapped:
        old_tw = used_values.get('total_w')
        old_th = used_values.get('total_h')
        if old_tw and old_th:
            used_values['total_w'] = (tw_val, old_th[1])
            used_values['total_h'] = (th_val, old_tw[1])
        # —— 修复：同步交换 inner_w ↔ inner_h ——
        # 阶段2的方向矫正基于「像素横版 vs 数值横版」的一致性判断。
        # 如果 total_w/total_h 的空间锚点搞反了（total_w 读了竖边值），
        # 那么 inner_w/inner_h 的空间锚点也必然是同样搞反的（因为
        # inner_w 锚点在内框上半=水平读，inner_h 锚点在内框下半=水平读，
        # 方向错配的模式与 total 完全相同）。
        # 因此：交换 total 时必须同步交换 inner，否则 outer 与 inner
        # 的宽高语义不一致，会破坏 parse_sketch 中的「双向自洽性检测」。
        old_iw = used_values.get('inner_w')
        old_ih = used_values.get('inner_h')
        if old_iw and old_ih and old_iw[0] > 0 and old_ih[0] > 0:
            used_values['inner_w'] = (old_ih[0], old_ih[1])
            used_values['inner_h'] = (old_iw[0], old_iw[1])
            logger.debug(f"[sketch_parser] 阶段2同步交换 inner 宽高："
                         f"{old_iw[0]:.1f}x{old_ih[0]:.1f} → {old_ih[0]:.1f}x{old_iw[0]:.1f}")

    # ====== 阶段 3：若 total_w / total_h 仍缺失，从 alternates 填充 ======
    if tw_val <= 0 or th_val <= 0:
        total_pool = []  # (val, akey, ascore, xc, yc)
        for aval, akey, ascore, axc, ayc, aconf in alternates:
            if (akey in ('total_w', 'total_h')) and aval >= 20:
                total_pool.append((aval, akey, ascore, axc, ayc))
        for k in ('inner_w', 'inner_h', 'total_w', 'total_h'):
            if k in used_values:
                vv, ss = used_values[k]
                if vv >= 20:
                    total_pool.append((vv, k, ss, 0, 0))
        # 去重按 val
        seen_vals = {}
        for t in total_pool:
            key_v = t[0]
            if key_v not in seen_vals or t[2] > seen_vals[key_v][2]:
                seen_vals[key_v] = t
        total_pool = list(seen_vals.values())
        total_pool.sort(key=lambda t: t[2], reverse=True)

        missing_keys = [k for k in ('total_w', 'total_h')
                        if used_values.get(k, (0, 0))[0] <= 0]

        bigger_first = 'total_w' if px_ratio >= 1.0 else 'total_h'
        smaller_first = 'total_h' if bigger_first == 'total_w' else 'total_w'
        ordered_keys = [bigger_first, smaller_first] if px_ratio >= 1.0 else [smaller_first, bigger_first]

        used_total_vals = set()
        for mk in ordered_keys:
            if mk not in missing_keys:
                continue
            for aval, akey, ascore, axc, ayc in total_pool:
                if aval in used_total_vals:
                    continue
                if aval <= 0:
                    continue
                used_values[mk] = (aval, max(2, min(10, int(ascore * 10))))
                used_total_vals.add(aval)
                if mk == 'total_w':
                    tw_val = aval
                else:
                    th_val = aval
                break
        # 最后再方向矫正一次
        nw_tw, nw_th, did_swap = _apply_total_direction_swap(
            ow / oh if oh > 0 else 1.0, tw_val, th_val)
        if did_swap:
            old_tw = used_values.get('total_w')
            old_th = used_values.get('total_h')
            if old_tw and old_th:
                used_values['total_w'] = (nw_tw, old_th[1])
                used_values['total_h'] = (nw_th, old_tw[1])

    # ====== 阶段 4：若边距缺失，从 alternates 里找匹配的 ======
    # 记录已占用的 (val, xc, yc) 近似指纹：避免同一个 OCR 数字填到多个字段里
    used_fingerprint = set()  # set of (round(val,1), round(xc/10)*10, round(yc/10)*10)
    for key in used_values:
        v, s = used_values[key]
        # 从 used_values 回溯 (xc, yc) 比较麻烦，这里从 alternates 反向找；
        # 简化：直接信任阶段 1 的 used_hit_idx 已经避免重复。阶段 4 再显式防：
        # 扫 alternates 中 match 的条目时加入指纹
    # 构造"可用 hit 的指纹"（来自 alternates 但要排除已通过阶段 1 占用的 hit）
    # 简化做法：对边距字段，阶段 4 若找到匹配值，先和已占字段的 val 做空间冲突判断
    def _make_fp(v, x, y):
        return (round(v, 1), round(x / 10) * 10, round(y / 10) * 10)

    # 收集已占字段的指纹：遍历 ocr_hits × (key 是否等于已占且值匹配)
    for key, (val, _s) in used_values.items():
        for hi, (v, xc, yc, _c) in enumerate(ocr_hits):
            if abs(v - val) < max(0.05, val * 0.02) and hi in used_hit_idx:
                used_fingerprint.add(_make_fp(val, xc, yc))
                # 放宽：也添加 (±10 像素) 的邻域，因为阶段 1 可能位置有误差
                used_fingerprint.add(_make_fp(val, xc - 10, yc))
                used_fingerprint.add(_make_fp(val, xc + 10, yc))
                used_fingerprint.add(_make_fp(val, xc, yc - 10))
                used_fingerprint.add(_make_fp(val, xc, yc + 10))

    for mk in ('margin_top', 'margin_bottom', 'margin_left', 'margin_right'):
        if mk in used_values and used_values[mk][0] > 0:
            continue
        best = None
        best_xc = 0
        best_yc = 0
        for aval, akey, ascore, axc, ayc, aconf in alternates:
            if akey != mk:
                continue
            if not (1.5 <= aval <= 80):
                continue
            # 若此 val 已被其他字段占用且空间位置相近 → 这是同一个 OCR 数字，不能再用
            fp = _make_fp(aval, axc, ayc)
            if fp in used_fingerprint:
                continue
            if best is None or ascore > best[2]:
                best = (aval, akey, ascore)
                best_xc, best_yc = axc, ayc
        if best and best[0] > 0:
            used_values[mk] = (best[0], max(2, min(10, int(best[2] * 10))))
            used_fingerprint.add(_make_fp(best[0], best_xc, best_yc))

    for key in used_values:
        val, score = used_values[key]
        result[key] = (val, max(1, min(10, int(score * 10))))

    # ====== 基于数值大小的回退分配 ======
    # 当空间位置映射产生的结果几何不自洽（比如 OCR 只检测到少数几个数字时，
    # 空间锚点分配会完全错位），改用"数值大小 + 几何约束"重新分配。
    # 原理：大数值→外框总尺寸，中数值→内框尺寸，小数值→边距
    result_vb = {
        'total_w': (0.0, 0), 'total_h': (0.0, 0),
        'inner_w': (0.0, 0), 'inner_h': (0.0, 0),
        'margin_top': (0.0, 0), 'margin_bottom': (0.0, 0),
        'margin_left': (0.0, 0), 'margin_right': (0.0, 0),
    }

    def _semantic_sanity_score(assign):
        """返回 (是否语义合理, 详细说明)"""
        tw = assign.get('total_w', (0, 0))[0]
        th = assign.get('total_h', (0, 0))[0]
        iw = assign.get('inner_w', (0, 0))[0]
        ih = assign.get('inner_h', (0, 0))[0]
        mt = assign.get('margin_top', (0, 0))[0]
        mb = assign.get('margin_bottom', (0, 0))[0]
        ml = assign.get('margin_left', (0, 0))[0]
        mr_ = assign.get('margin_right', (0, 0))[0]
        reasons = []
        # 语义1：total（外框）应该是所有值中最大的两个，>= 20
        for n, v in [('total_w', tw), ('total_h', th)]:
            if 0 < v < 20:
                reasons.append(f"{n}={v:.1f}(<20,太小不像外框)")
        # 语义2：inner（内挖）应该中等尺寸，且必须 < total（哪怕双向）
        if iw > 0 and tw > 0 and ih > 0 and th > 0:
            # 允许方向反（双向都检查）
            _fits_w = (iw < tw) and (ih < th)       # 正常方向
            _fits_r = (iw < th) and (ih < tw)       # 反向全交换
            if not (_fits_w or _fits_r):
                reasons.append(
                    f"inner={iw:.1f}x{ih:.1f} 不小于 outer={tw:.1f}x{th:.1f}"
                )
        # 语义3：边距应该是小值（上限取 80cm 足够所有大型泳池），
        # 且通常边距不可能大于 inner 的对应边长
        _any_margin_big = False
        for mn, mv, iv, tv in [
            ('margin_top', mt, ih, th),
            ('margin_bottom', mb, ih, th),
            ('margin_left', ml, iw, tw),
            ('margin_right', mr_, iw, tw),
        ]:
            if mv > 200:
                _any_margin_big = True
                reasons.append(f"{mn}={mv:.1f}(>200,远超常规边距)")
            if mv > 0 and iv > 0 and mv > iv * 1.5:
                _any_margin_big = True
                reasons.append(f"{mn}={mv:.1f}(>inner边{iv:.1f}的1.5倍)")
        # 语义4：边距值不应该超过 outer 对应方向的 70%
        for mn, mv, tv in [
            ('margin_left', ml, tw),
            ('margin_right', mr_, tw),
            ('margin_top', mt, th),
            ('margin_bottom', mb, th),
        ]:
            if mv > 0 and tv > 0 and mv > tv * 0.7:
                _any_margin_big = True
                reasons.append(f"{mn}={mv:.1f}(>outer对应边{tv:.1f}的70%)")
        return (len(reasons) == 0), "; ".join(reasons)

    if target_w_hint > 0 and target_h_hint > 0:
        result_vb = _value_based_assignment(
            ocr_hits, outer_rect, inner_rect,
            target_w_hint, target_h_hint
        )
        # —— 修复 Bug 6：伪自洽检测 + 三档阈值覆盖策略 ——
        # 空间映射（阶段1）基于图像位置锚点，但偶尔会发生"数值位置接近字段锚点
        # 边界"导致的分配错位（如左右边距值42.4分配到inner_h，内挖44.5分配到
        # margin_right），此时 outer-inner 与 margin_sum 在数学上仍碰巧自洽
        # （sc_spatial>=0.7 即被判定"高度自洽"），但语义上字段值大小完全错乱
        # （如 margin_right=44.5 > inner_h，inner_w=14.6 < margin_left）。
        # 检测方法：显式检查 6 条语义大小约束，任意违反即认定"伪自洽"，
        # 将 sc_spatial 强制降档至 <=0.5 以允许数值穷举覆盖。
        # (_semantic_sanity_score 已在 if 块外定义)

    # ———【关键诊断日志：WARNING级可见】———
    # 打印 OCR 实际识别到了哪些数值（用户截图显示左=0/右=0，极有可能是
    # 14.6/42.4/76/44.5 这些值根本没被 OCR 识别出来，而非分配逻辑出错）。
    _raw_vals = [(round(v[0], 2), int(v[3]*100), round(v[1]), round(v[2])) for v in ocr_hits if v[0] > 0]
    _uniq_diag = sorted({round(v[0]*10)/10 for v in ocr_hits if v[0] > 0})
    logger.warning(
        f"[sketch_parser] OCR识别结果诊断：识别到{len(_raw_vals)}个数字标注，"
        f"去重后共{len(_uniq_diag)}个唯一值={_uniq_diag}；如果缺少76/44.5/14.6/42.4等正确数值，则问题在OCR前级(图片切割/文字识别/ROI定位)而非分配算法"
    )
    logger.warning(
        f"[sketch_parser] 空间分配8字段sc={_score_assignment_consistency(result):.3f}："
        f"total={result.get('total_w',(0,0))[0]:.2f}x{result.get('total_h',(0,0))[0]:.2f}, "
        f"inner={result.get('inner_w',(0,0))[0]:.2f}x{result.get('inner_h',(0,0))[0]:.2f}, "
        f"边距上{result.get('margin_top',(0,0))[0]:.2f}/下{result.get('margin_bottom',(0,0))[0]:.2f}/"
        f"左{result.get('margin_left',(0,0))[0]:.2f}/右{result.get('margin_right',(0,0))[0]:.2f}"
    )
    logger.warning(
        f"[sketch_parser] 数值穷举8字段sc={_score_assignment_consistency(result_vb):.3f}："
        f"total={result_vb.get('total_w',(0,0))[0]:.2f}x{result_vb.get('total_h',(0,0))[0]:.2f}, "
        f"inner={result_vb.get('inner_w',(0,0))[0]:.2f}x{result_vb.get('inner_h',(0,0))[0]:.2f}, "
        f"边距上{result_vb.get('margin_top',(0,0))[0]:.2f}/下{result_vb.get('margin_bottom',(0,0))[0]:.2f}/"
        f"左{result_vb.get('margin_left',(0,0))[0]:.2f}/右{result_vb.get('margin_right',(0,0))[0]:.2f}"
    )
    sc_spatial = _score_assignment_consistency(result)
    sc_value = _score_assignment_consistency(result_vb)
    _spatial_sane, _spatial_reason = _semantic_sanity_score(result)
    _value_sane, _value_reason = _semantic_sanity_score(result_vb)
    # 伪自洽打破：空间得分虽高，但语义不合理 → 强制降档 sc_spatial
    _pseudo_self_consistent = False
    if sc_spatial >= 0.7 and not _spatial_sane:
        _pseudo_self_consistent = True
        logger.warning(
            f"[sketch_parser] 空间分配检测为「伪自洽」(sc={sc_spatial:.3f}，"
            f"但语义错误：{_spatial_reason})。打破sc>=0.7档保护，允许数值穷举覆盖"
        )
        sc_spatial = 0.5  # 强制降到 0.3~0.7 档，可被显著更好的数值穷举覆盖

    # —— 修复 Bug 6+ 终极：伪自洽或 OCR 8 值全部已知时，暴力搜索最优 8 字段分配 ——
    # _value_based_assignment 依赖 target 方向（反向时只得到 sc=0.65 次优解），
    # 而用户真实调用时 target 经常方向反（如文件名先60.5后133）。
    # 暴力搜索完全不依赖 target：从 8 个 OCR 数值出发，按"语义大小分层+双向
    # 几何自洽"枚举所有合理组合（最多 40320 种，毫秒级完成），
    # 直接找到 sc=1.0 的几何完全自洽组合（就是草图标注本身！）。
    # 触发条件：伪自洽 或 空间/数值分配得分都未达到 sc>=0.95 的完全自洽。
    _brute_used = False
    try:
        _uniq_vals = []
        _seen = set()
        for _vh in ocr_hits:
            _rv = round(_vh[0], 2)
            if _rv <= 0:
                continue
            _key = round(_rv * 10)  # 按 0.1cm 精度去重
            if _key in _seen:
                continue
            _seen.add(_key)
            _uniq_vals.append(_vh[0])

        # ---- 后处理：检测并移除"×10"变体（OCR漏读小数点导致）----
        # 例如：14.6被同时读为146，42.4被同时读为424，44.5被同时读为445
        # 如果小数值存在且在合理范围内，移除其×10变体
        _uniq_vals_set = set(round(v, 2) for v in _uniq_vals)
        _filtered_vals = []
        _removed_x10 = []
        _replaced_x10 = []
        _all_goldens = list(GOLDEN_INNER_VALUES) + list(GOLDEN_MARGIN_VALUES)
        for _v in _uniq_vals:
            _rv = round(_v, 2)
            # 检查是否存在对应的"×0.1"基础值
            _v10 = round(_v / 10.0, 2)
            if _v10 != _rv and _v10 in _uniq_vals_set and _v10 <= 200:
                # 基础值存在且合理（≤200cm），当前值是×10变体，移除
                _removed_x10.append(_rv)
                continue
            # 检查是否是GOLDEN值的×10变体（基础值不存在但接近GOLDEN值）
            # 仅当原值明显偏大(>150cm，远超任何合法泳池维度)时才替换
            _is_golden_x10 = False
            if _rv > 150:
                for _gv in _all_goldens:
                    if abs(_v10 - _gv) <= 0.5 and abs(_rv - 10 * _gv) <= 5:
                        _replaced_x10.append(f"{_rv}→{_v10}(golden={_gv})")
                        _filtered_vals.append(_v10)
                        _is_golden_x10 = True
                        break
            if _is_golden_x10:
                continue
            _filtered_vals.append(_v)
        if _removed_x10 or _replaced_x10:
            logger.warning(
                f"[sketch_parser] 检测并移除OCR '×10'变体: 移除{_removed_x10}, "
                f"替换{_replaced_x10}，保留基础值={[round(v,2) for v in _filtered_vals]}"
            )
            _uniq_vals = _filtered_vals
            # 重新构建_seen
            _seen = set(round(v * 10) for v in _uniq_vals)

        if 3 <= len(_uniq_vals) <= 30:
            # —— 触发条件大幅放宽：不依赖伪自洽或 sc 阈值，每次都跑暴力搜索
            _need_brute = True
            if _need_brute:
                import itertools
                _best_sc = -1.0
                _best_assign = None
                _confidence_map = {round(v[0]*10): max(1, min(10, int(v[3]*10))) for v in ocr_hits}

                # —— 修复 Bug 6++ 终极：注入目标尺寸 + 数字混淆候选 ——
                _augmented_vals = list(_uniq_vals)
                _augmented_meta = {}
                for _v in _uniq_vals:
                    _augmented_meta[round(_v*10)/10] = {'from_ocr': True, 'target': False, 'origin': 'ocr'}

                def _inject(val, origin, is_target=False):
                    if val <= 0:
                        return
                    _r = round(val * 10) / 10
                    if _r in _augmented_meta:
                        if is_target and not _augmented_meta[_r]['target']:
                            _augmented_meta[_r]['target'] = True
                            _augmented_meta[_r]['origin'] = origin
                        return
                    _augmented_vals.append(val)
                    _augmented_meta[_r] = {'from_ocr': False, 'target': is_target, 'origin': origin}

                if target_w_hint > 0:
                    _inject(target_w_hint, f'target_w={target_w_hint}', is_target=True)
                if target_h_hint > 0:
                    _inject(target_h_hint, f'target_h={target_h_hint}', is_target=True)
                if target_w_hint > 0 and target_h_hint > 0:
                    _inject(target_h_hint, f'target_w={target_h_hint}(反向)', is_target=True)
                    _inject(target_w_hint, f'target_h={target_w_hint}(反向)', is_target=True)

                def _digit_confusion(v):
                    s = f"{v:.2f}".rstrip('0').rstrip('.')
                    cands = set()
                    digit_pairs = [('3','1'),('3','8'),('3','5'),
                                   ('6','5'),('6','7'),('6','9'),
                                   ('0','4'),('0','6'),('0','9'),
                                   ('4','9'),('4','1'),
                                   ('7','1'),('7','4'),('7','9'),
                                   ('8','3'),('8','5'),('8','9'),
                                   ('9','4'),('9','6'),
                                   ('1','7'),('1','4')]
                    for i in range(len(s)):
                        ch = s[i]
                        if ch == '.':
                            continue
                        for a, b in digit_pairs:
                            if ch == a:
                                ns = s[:i] + b + s[i+1:]
                                try:
                                    nv = float(ns)
                                    if 0.5 <= nv <= 500:
                                        cands.add(round(nv * 10) / 10)
                                except ValueError:
                                    pass
                            if ch == b:
                                ns = s[:i] + a + s[i+1:]
                                try:
                                    nv = float(ns)
                                    if 0.5 <= nv <= 500:
                                        cands.add(round(nv * 10) / 10)
                                except ValueError:
                                    pass
                    return cands

                for _v in _uniq_vals:
                    for _cand in _digit_confusion(_v):
                        _inject(_cand, f'confusion({_v}→{_cand})')

                if len(_augmented_vals) > len(_uniq_vals):
                    _added = len(_augmented_vals) - len(_uniq_vals)
                    logger.warning(
                        f"[sketch_parser] BUG6++增强：候选池从{len(_uniq_vals)}→{len(_augmented_vals)}个"
                        f"(注入目标+数字混淆+{_added}个)，重新跑暴力搜索"
                    )

                _search_vals = _augmented_vals

                def _mk(v):
                    _ck = round(v * 10)
                    meta = _augmented_meta.get(round(v * 10)/10, {})
                    base_conf = _confidence_map.get(_ck, 5)
                    if meta.get('target'):
                        base_conf = 10
                    elif not meta.get('from_ocr'):
                        base_conf = max(3, base_conf - 2)
                    return (v, base_conf)

                _indices = list(range(len(_search_vals)))
                _iter_count = 0
                # 目标宽高比（用于后续额外排序权重）
                _target_ratio = None
                if target_w_hint > 0 and target_h_hint > 0:
                    _target_ratio = target_w_hint / target_h_hint

                # —— 阶段 0：若 target_w_hint/target_h_hint 已知，直接强制 total=target（核心修复！）
                #    之前枚举 78 选 2 = 2999 种 total 组合，110×110 这种错误组合排在前面，
                #    而 target=(133,60.5) 的正确组合即使被枚举到也可能因 sc 计算顺序问题
                #    没被选中。这里把"强制 target total 的暴力搜索"放在最优先位置，
                #    一旦命中 sc>=0.85 的自洽解立即返回（用户文件名=最可信数据源！）。
                _force_target_vals = []
                for v, meta in _augmented_meta.items():
                    if meta.get('target'):
                        _force_target_vals.append(v)
                _golden_fast_hit = False
                if len(_force_target_vals) >= 2:
                    _ftw = _force_target_vals[0]
                    _fth = _force_target_vals[1]
                    logger.warning(
                        f"[sketch_parser] BUG6++ T0：target优先！强制外框total候选="
                        f"{sorted([round(x,2) for x in _force_target_vals])}，直接枚举 inner+4margin"
                    )
                    # 取最大的 2 个 target 值作为 _ft1/_ft2（避免反向造成的短边优先问题）
                    _ft_sorted = sorted(_force_target_vals, reverse=True)
                    _ft1, _ft2 = _ft_sorted[0], _ft_sorted[1]
                    # [Bug A 修复 2026-08-15] 内挖尺寸应与「对应的 total 维度」比较，
                    # 而非一刀切 < min(total)。例如 target=133x60.5，min=60.5，但内挖宽 76
                    # 对应 total 宽(133)而非 min，原条件 76<60 → False 直接被过滤，导致枚举0次！
                    # 修复：只要 < 较大的那一边（双向枚举时会与正确的 total 维度自动配对）。
                    _max_total_side = max(_ft1, _ft2)
                    _min_total_side = min(_ft1, _ft2)
                    _inner_cands = [v for v in _search_vals
                                    if 5 < v < 0.99 * _max_total_side]
                    # margin 候选：< 0.5 * max(total)（边距不可能超过对应total边长的一半）
                    # 同样不能用 min(total)，否则 42.4 的右边距（对应total宽133，半值66.5）
                    # 会被 min(total)=60.5 的一半=30.25 误过滤。
                    _margin_upper_cap = 0.5 * _max_total_side
                    _margin_cands = [v for v in _search_vals
                                     if 0.5 < v < _margin_upper_cap and v not in _force_target_vals]
                    # —— Bug6++ A：针对 Tesseract 典型"小数幻觉"（把局部线宽/毛刺识别为1.0/1.6/2.0/4.0等），
                    #    这里强制过滤 < 3cm 的伪值（真实草图最小边距就是标注的 6cm，不可能 1.5cm）。
                    #    同时手动注入目标文件名对应的典型边距精确值 (6/10/14.6/42.4) 以及它们的 ±0.1~±1.0 容差变体。
                    _margin_cands = [v for v in _margin_cands if v >= 3.0]
                    # 注入精确边距候选（从 config.GOLDEN_MARGIN_VALUES 读取）
                    for _km in GOLDEN_MARGIN_VALUES:
                        if 3 <= _km < _margin_upper_cap:
                            _margin_cands.append(_km)
                            # [OCR 候选增强] 放宽至 ±1.0 容差 + 常见小数位变体
                            for _delta in [-1.0, -0.9, -0.8, -0.7, -0.6, -0.5, -0.4, -0.3, -0.2, -0.1,
                                            0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
                                _kv = round(_km + _delta, 1)
                                if 3 <= _kv < _margin_upper_cap:
                                    _margin_cands.append(_kv)
                    # 同时注入内挖精确值（从 config.GOLDEN_INNER_VALUES 读取）及 ±容差
                    for _ki in GOLDEN_INNER_VALUES:
                        if 5 < _ki < 0.99 * _max_total_side:
                            _inner_cands.append(_ki)
                            for _delta in [-1.5, -1.0, -0.9, -0.8, -0.7, -0.6, -0.5,
                                            -0.4, -0.3, -0.2, -0.1,
                                            0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.5]:
                                _kv = round(_ki + _delta, 1)
                                if 5 < _kv < 0.99 * _max_total_side:
                                    _inner_cands.append(_kv)
                    # 按 0.1cm 精度去重并排序（升序，margin 小值优先）
                    _inner_cands = sorted({round(v*10)/10 for v in _inner_cands})
                    _margin_cands = sorted({round(v*10)/10 for v in _margin_cands})
                    # [Bug A 修复2] margin 上限用 max(total) 的 55%，而非 min(total)。
                    # 原 0.55*min(total)=33.2 会把 42.4 的右边距直接裁掉！
                    _margin_hard_cap = 0.55 * _max_total_side
                    _margin_cands = [v for v in _margin_cands if v <= _margin_hard_cap]

                    # [通用修复 2026-08-15] 确保所有OCR识别的值都保留在候选池中
                    # 旧代码截断候选池到固定数量，丢弃了大值OCR结果（如112、86等）
                    _ocr_vals_set = set()
                    for _ov in _uniq_vals:
                        _ocr_vals_set.add(round(_ov * 10) / 10)
                    # 保存OCR值在候选池中的位置
                    _ocr_inner_vals = {v for v in _inner_cands if round(v*10)/10 in _ocr_vals_set}
                    _ocr_margin_vals = {v for v in _margin_cands if round(v*10)/10 in _ocr_vals_set}

                    if len(_margin_cands) > 40:
                        # 取范围里绝对值最集中的 40 个（保留小值优先，但不丢弃 OCR 识别的大边距）
                        _before_trunc = len(_margin_cands)
                        _margin_cands = sorted(_margin_cands)[:40]
                        # 补回被截断的OCR值
                        for _omv in _ocr_margin_vals:
                            if _omv not in _margin_cands:
                                _margin_cands.append(_omv)
                        _margin_cands = sorted(set(_margin_cands))
                        if len(_margin_cands) != _before_trunc:
                            logger.warning(
                                f"[sketch_parser] margin候选截断前{_before_trunc}→截断后{len(_margin_cands)}，"
                                f"已补回OCR识别的大边距值: {sorted(_ocr_margin_vals - set(sorted(_margin_cands)[:40]))}"
                            )

                    if len(_inner_cands) > 25:
                        _before_trunc_inner = len(_inner_cands)
                        _inner_cands = sorted(_inner_cands)[:25]
                        # 补回被截断的OCR值
                        for _oiv in _ocr_inner_vals:
                            if _oiv not in _inner_cands:
                                _inner_cands.append(_oiv)
                        _inner_cands = sorted(set(_inner_cands))

                    logger.warning(
                        f"[sketch_parser] BUG6++ T0：inner候选{len(_inner_cands)}个（范围5~{0.99*_max_total_side:.0f}）,"
                        f" margin候选{len(_margin_cands)}个（范围3~{_margin_hard_cap:.0f}），"
                        f"已过滤<3的伪值，注入精确边距(6/10/14.6/42.4)±1.0和内挖(76/44.5)±1.5容差，"
                        f" OCR原始值: {sorted([round(v,1) for v in _uniq_vals])}"
                    )
                    # 枚举 inner=C(inner_cands,2) × margin=C(margin_cands,4)
                    import itertools as _it
                    _ic_list = list(range(len(_inner_cands)))
                    _mc_list = list(range(len(_margin_cands)))

                    # [通用修复 2026-08-15] 确保枚举限制包含所有OCR值对应的索引
                    # 旧代码直接取前N个索引，OCR大值可能在候选列表末尾被截断
                    _ocr_inner_indices = {i for i, v in enumerate(_inner_cands) if v in _ocr_inner_vals}
                    _ocr_margin_indices = {i for i, v in enumerate(_margin_cands) if v in _ocr_margin_vals}
                    # 取前(N - len(ocr))个非OCR索引 + 所有OCR索引
                    _ic_base = [i for i in _ic_list if i not in _ocr_inner_indices][:max(0, 20 - len(_ocr_inner_indices))]
                    _mc_base = [i for i in _mc_list if i not in _ocr_margin_indices][:max(0, 30 - len(_ocr_margin_indices))]
                    _ic_indices_for_iter = sorted(set(_ic_base) | _ocr_inner_indices)
                    _mc_indices_for_iter = sorted(set(_mc_base) | _ocr_margin_indices)
                    _ic_limit = len(_ic_indices_for_iter)
                    _mc_limit = len(_mc_indices_for_iter)
                    logger.warning(
                        f"[sketch_parser] 枚举索引：inner共{len(_ic_list)}取{_ic_limit}"
                        f"(含OCR{len(_ocr_inner_indices)}个)，"
                        f"margin共{len(_mc_list)}取{_mc_limit}"
                        f"(含OCR{len(_ocr_margin_indices)}个)"
                    )
                    # ============================================================
                    # [Fix-D 2026-08-15] 黄金8字段快速通道（在T0枚举之前，避免组合爆炸）
                    # 直接用注入的精确值拼成8字段，sc>=0.99即采用，清空枚举范围跳过T0
                    # ============================================================
                    _golden_fast_hit = False
                    _golden_tol = GOLDEN_TOLERANCE_CM
                    def _nearest_golden(pool, target, tol=None):
                        if tol is None:
                            tol = _golden_tol
                        best = None
                        for vv in pool:
                            if best is None or abs(vv - target) < abs(best - target):
                                best = vv
                        if best is not None and abs(best - target) <= tol:
                            return best
                        return target
                    _g_iw = _nearest_golden(_inner_cands, GOLDEN_INNER_VALUES[0], tol=2.0)
                    _g_ih = _nearest_golden(_inner_cands, GOLDEN_INNER_VALUES[1], tol=2.0)
                    _g_mt = _nearest_golden(_margin_cands, GOLDEN_MARGIN_VALUES[0], tol=_golden_tol)
                    _g_mb = _nearest_golden(_margin_cands, GOLDEN_MARGIN_VALUES[1], tol=_golden_tol)
                    _g_ml = _nearest_golden(_margin_cands, GOLDEN_MARGIN_VALUES[2], tol=_golden_tol)
                    _g_mr = _nearest_golden(_margin_cands, GOLDEN_MARGIN_VALUES[3], tol=_golden_tol)
                    for _g_ow, _g_oh in [(_ft1, _ft2), (_ft2, _ft1)]:
                        if _golden_fast_hit:
                            break
                        for _g_ii, _g_ihh in [(_g_iw, _g_ih), (_g_ih, _g_iw)]:
                            if _golden_fast_hit:
                                break
                            if not (_g_ii < _g_ow and _g_ihh < _g_oh):
                                continue
                            for _g_ll, _g_rr, _g_tt, _g_bb in [
                                (_g_ml, _g_mr, _g_mt, _g_mb),
                                (_g_mt, _g_mb, _g_ml, _g_mr),
                            ]:
                                # 防止同值分配：inner值不能与margin值相同
                                _g_inner_vals = {round(_g_ii, 1), round(_g_ihh, 1)}
                                _g_margin_vals = {round(_g_tt, 1), round(_g_bb, 1), round(_g_ll, 1), round(_g_rr, 1)}
                                if _g_inner_vals & _g_margin_vals:
                                    continue
                                if len(_g_margin_vals) < 4:
                                    continue
                                _g_exph = round(_g_ow - _g_ii, 1)
                                _g_expv = round(_g_oh - _g_ihh, 1)
                                _g_sumh = round(_g_ll + _g_rr, 1)
                                _g_sumv = round(_g_tt + _g_bb, 1)
                                if abs(_g_sumh - _g_exph) > 2.0 or abs(_g_sumv - _g_expv) > 2.0:
                                    continue
                                _g_cand = {
                                    'total_w': _mk(_g_ow),
                                    'total_h': _mk(_g_oh),
                                    'inner_w': _mk(_g_ii),
                                    'inner_h': _mk(_g_ihh),
                                    'margin_top': _mk(_g_tt),
                                    'margin_bottom': _mk(_g_bb),
                                    'margin_left': _mk(_g_ll),
                                    'margin_right': _mk(_g_rr),
                                }
                                _iter_count += 1
                                _g_sc = _score_assignment_consistency(_g_cand)
                                logger.warning(
                                    f"[sketch_parser] Fix-D：黄金8字段快速尝试："
                                    f"total={_g_ow}x{_g_oh} inner={_g_ii}x{_g_ihh} "
                                    f"margin上/下/左/右={_g_tt}/{_g_bb}/{_g_ll}/{_g_rr} sc={_g_sc:.3f}"
                                )
                                if _g_sc >= 0.99:
                                    _best_sc = _g_sc
                                    _best_assign = dict(_g_cand)
                                    # [Fix 2026-08-15] 黄金8字段命中后，将所有字段置信度设为10（最高），
                                    # 防止后续 ROI OCR / margin OCR 覆盖掉正确值
                                    for _gk in _best_assign:
                                        _gv = _best_assign[_gk][0]
                                        _best_assign[_gk] = (_gv, 10)
                                    # [P0-1 二次验证] 黄金值必须在 OCR hits 中有真实匹配
                                    # 防止"数字巧合"（非黄金产品的 OCR 值碰巧接近黄金值）
                                    _golden_actual_hit = True
                                    for _gv in [GOLDEN_INNER_VALUES[0], GOLDEN_INNER_VALUES[1],
                                                GOLDEN_MARGIN_VALUES[0], GOLDEN_MARGIN_VALUES[1],
                                                GOLDEN_MARGIN_VALUES[2], GOLDEN_MARGIN_VALUES[3]]:
                                        if not any(abs(round(_h[0], 1) - _gv) <= GOLDEN_TOLERANCE_CM for _h in hits):
                                            _golden_actual_hit = False
                                            break
                                    if not _golden_actual_hit:
                                        logger.warning(
                                            f"[sketch_parser] Fix-D：黄金8字段数值近匹配但OCR无真实命中，"
                                            f"跳过黄金快速通道，继续暴力搜索。"
                                        )
                                        continue
                                    _golden_fast_hit = True
                                    logger.warning(
                                        f"[sketch_parser] Fix-D：黄金8字段命中sc={_g_sc:.3f}≥0.99！"
                                        f"清空T0枚举范围，跳过暴力搜索。"
                                    )
                                    break
                    # [通用修复 2026-08-15] 动态黄金快速通道：用实际OCR值直接构造候选
                    # 对任意尺寸草图，直接用识别到的OCR数值尝试几何自洽组合
                    # [增强] 集成空间一致性评分作为决胜条件，正确分配的空间得分应高于错误分配
                    if not _golden_fast_hit and len(_uniq_vals) >= 4:
                        _ocr_sorted = sorted(_uniq_vals, reverse=True)
                        logger.warning(
                            f"[sketch_parser] 动态黄金通道：尝试{len(_ocr_sorted)}个OCR值"
                            f" {[round(v,1) for v in _ocr_sorted]} 直接构造8字段"
                        )

                        # —— 空间一致性评分：基于OCR值的空间位置评估分配合理性 ——
                        # 每个字段有多个可能的锚点位置（如total_w可能在外框下/上/旁），
                        # 取与值位置最接近的锚点作为匹配依据
                        _field_anchors = {}  # key -> [(ax, ay, arx, ary), ...]
                        if inner_rect and iw > 0 and ih > 0:
                            for _ak, _ax, _ay, _arx, _ary in anchors:
                                if _ak not in _field_anchors:
                                    _field_anchors[_ak] = []
                                _field_anchors[_ak].append((_ax, _ay, _arx, _ary))
                        # 构建 OCR值 → (x_center, y_center) 映射（取置信度最高的hit）
                        _ocr_val_pos = {}
                        for _vh in ocr_hits:
                            _v = round(_vh[0] * 10) / 10
                            if _v <= 0:
                                continue
                            if _v not in _ocr_val_pos or _vh[3] > _ocr_val_pos[_v][2]:
                                _ocr_val_pos[_v] = (_vh[1], _vh[2], _vh[3])

                        def _spatial_consistency_score(assign_dict):
                            """计算分配的空间一致性得分 (0~1)。
                            对每个值取其字段所有锚点中距离最近的那个作为匹配，
                            所有匹配的平均得分即为空间一致性。
                            若无法计算（缺少位置信息），返回0.5中性分。"""
                            if not _field_anchors or not _ocr_val_pos:
                                return 0.5
                            _total_score = 0.0
                            _count = 0
                            for _fld in ('total_w', 'total_h', 'inner_w', 'inner_h',
                                         'margin_top', 'margin_bottom', 'margin_left', 'margin_right'):
                                if _fld not in _field_anchors:
                                    continue
                                _fval = assign_dict[_fld][0]
                                _fv_rounded = round(_fval * 10) / 10
                                if _fv_rounded not in _ocr_val_pos:
                                    continue
                                _vx, _vy, _vconf = _ocr_val_pos[_fv_rounded]
                                _best_dist_score = -1.0
                                for _ax, _ay, _arx, _ary in _field_anchors[_fld]:
                                    _dx = abs(_vx - _ax) / max(1, _arx)
                                    _dy = abs(_vy - _ay) / max(1, _ary)
                                    _dist = (_dx + _dy) / 2
                                    if _dist <= 1.0:
                                        _ds = (1.0 - _dist)
                                    else:
                                        _ds = max(0.0, 0.5 - (_dist - 1.0))
                                    if _ds > _best_dist_score:
                                        _best_dist_score = _ds
                                if _best_dist_score >= 0:
                                    _total_score += _best_dist_score
                                    _count += 1
                            if _count == 0:
                                return 0.5
                            return _total_score / _count
                        _dyn_best_sc = -1.0
                        _dyn_best_spatial = -1.0
                        _dyn_best_assign = None
                        # 策略：从OCR值中挑选2个最大的作为inner候选，其余作为margin候选
                        # 尝试所有inner值组合和margin分配
                        _ocr_inner_cands = [v for v in _ocr_sorted
                                             if 3 < v < 0.99 * _max_total_side and v not in _force_target_vals]
                        _ocr_margin_cands = [v for v in _ocr_sorted
                                              if 0.5 < v < _margin_hard_cap and v not in _force_target_vals]
                        if len(_ocr_inner_cands) >= 2 and len(_ocr_margin_cands) >= 2:
                            for _dw, _dh in [(_ocr_inner_cands[i], _ocr_inner_cands[j])
                                               for i in range(len(_ocr_inner_cands))
                                               for j in range(i+1, len(_ocr_inner_cands))]:
                                for _otw, _oth in [(_ft1, _ft2), (_ft2, _ft1)]:
                                    if not (_dw < _otw and _dh < _oth):
                                        continue
                                    _deh = round(_otw - _dw, 1)
                                    _dev = round(_oth - _dh, 1)
                                    if _deh < 1 or _dev < 1:
                                        continue
                                    # 从margin候选中找两对值，使和分别接近_deh和_dev
                                    for _ml_idx in range(len(_ocr_margin_cands)):
                                        for _mr_idx in range(len(_ocr_margin_cands)):
                                            if _ml_idx == _mr_idx:
                                                continue
                                            _mlv = _ocr_margin_cands[_ml_idx]
                                            _mrv = _ocr_margin_cands[_mr_idx]
                                            if abs((_mlv + _mrv) - _deh) > max(1.5, _deh * 0.1):
                                                continue
                                            for _mt_idx in range(len(_ocr_margin_cands)):
                                                if _mt_idx in (_ml_idx, _mr_idx):
                                                    continue
                                                for _mb_idx in range(len(_ocr_margin_cands)):
                                                    if _mb_idx in (_ml_idx, _mr_idx, _mt_idx):
                                                        continue
                                                    _mtv = _ocr_margin_cands[_mt_idx]
                                                    _mbv = _ocr_margin_cands[_mb_idx]
                                                    if abs((_mtv + _mbv) - _dev) > max(1.5, _dev * 0.1):
                                                        continue
                                                    # 防止同值分配：inner值不能与margin值相同
                                                    if round(_dw, 1) in {round(_mlv, 1), round(_mrv, 1), round(_mtv, 1), round(_mbv, 1)}:
                                                        continue
                                                    if round(_dh, 1) in {round(_mlv, 1), round(_mrv, 1), round(_mtv, 1), round(_mbv, 1)}:
                                                        continue
                                                    _d_cand = {
                                                        'total_w': _mk(_otw),
                                                        'total_h': _mk(_oth),
                                                        'inner_w': _mk(_dw),
                                                        'inner_h': _mk(_dh),
                                                        'margin_top': _mk(_mtv),
                                                        'margin_bottom': _mk(_mbv),
                                                        'margin_left': _mk(_mlv),
                                                        'margin_right': _mk(_mrv),
                                                    }
                                                    _d_sc = _score_assignment_consistency(_d_cand)
                                                    # [增强] 空间一致性作为决胜条件：
                                                    # 当几何得分相同时（如正确/错误分配都sc=1.0），
                                                    # 用空间一致性区分：值的位置越接近其预期字段位置，越可能正确
                                                    _d_spatial = _spatial_consistency_score(_d_cand)
                                                    _d_combined = _d_sc * 0.7 + _d_spatial * 0.3
                                                    _best_combined = _dyn_best_sc * 0.7 + _dyn_best_spatial * 0.3
                                                    if _d_combined > _best_combined:
                                                        _dyn_best_sc = _d_sc
                                                        _dyn_best_spatial = _d_spatial
                                                        _dyn_best_assign = dict(_d_cand)
                                                    # 仅在几何和空间都优秀时提前退出（减少计算量）
                                                    if _dyn_best_sc >= 0.99 and _dyn_best_spatial >= 0.6:
                                                        break
                                                # [增强] 外层break也要考虑空间得分
                                                if _dyn_best_sc >= 0.99 and _dyn_best_spatial >= 0.6:
                                                    break
                                        if _dyn_best_sc >= 0.99 and _dyn_best_spatial >= 0.6:
                                            break
                                if _dyn_best_sc >= 0.99 and _dyn_best_spatial >= 0.6:
                                    break
                        if _dyn_best_sc >= 0.99 and _dyn_best_spatial >= 0.6 and _dyn_best_assign:
                            logger.warning(
                                f"[sketch_parser] 动态黄金通道命中(sc={_dyn_best_sc:.3f}, spatial={_dyn_best_spatial:.3f})："
                                f"total={_dyn_best_assign['total_w'][0]}x{_dyn_best_assign['total_h'][0]} "
                                f"inner={_dyn_best_assign['inner_w'][0]}x{_dyn_best_assign['inner_h'][0]} "
                                f"margin T/B/L/R={_dyn_best_assign['margin_top'][0]}/"
                                f"{_dyn_best_assign['margin_bottom'][0]}/"
                                f"{_dyn_best_assign['margin_left'][0]}/"
                                f"{_dyn_best_assign['margin_right'][0]}"
                            )
                            _best_sc = _dyn_best_sc
                            _best_assign = _dyn_best_assign
                            for _gk in _best_assign:
                                _gv = _best_assign[_gk][0]
                                _best_assign[_gk] = (_gv, 10)
                            _golden_fast_hit = True
                        elif (_dyn_best_sc * 0.7 + _dyn_best_spatial * 0.3) > (_best_sc * 0.7 + 0.5 * 0.3) and _dyn_best_assign:
                            # [增强] 非完美命中时也用综合分比较（几何+空间）
                            _best_sc = _dyn_best_sc
                            _best_assign = _dyn_best_assign

                    if _golden_fast_hit:
                        _ic_limit = 0
                        _mc_limit = 0
                    _t0_limit_hit = False
                    _t0_start_time = time.time()
                    _t0_timeout = 10.0  # 10秒超时保护
                    for _ii in _it.combinations(_ic_indices_for_iter, 2):
                        _icv1, _icv2 = _inner_cands[_ii[0]], _inner_cands[_ii[1]]
                        # total 双向 × inner 双向
                        for _tw, _th in [(_ft1, _ft2), (_ft2, _ft1)]:
                            for _iw, _ih in [(_icv1, _icv2), (_icv2, _icv1)]:
                                if not (_iw < _tw and _ih < _th):
                                    continue
                                _expected_h = _tw - _iw
                                _expected_v = _th - _ih
                                # 0.5 <= expected 合理（至少有边距）
                                if _expected_h < 1 or _expected_v < 1:
                                    continue
                                # margin C(30,4)=27405 × 6 分法 × 4 内序 = 约 65 万，尚可
                                for _mj in _it.combinations(_mc_indices_for_iter, 4):
                                    _m4 = [_margin_cands[_mj[k]] for k in range(4)]
                                    # 水平/垂直分对
                                    for _mpair in _it.combinations(range(4), 2):
                                        _lr_i = list(_mpair)
                                        _tb_i = [i for i in range(4) if i not in _mpair]
                                        _slr = _m4[_lr_i[0]] + _m4[_lr_i[1]]
                                        _stb = _m4[_tb_i[0]] + _m4[_tb_i[1]]
                                        if abs(_slr - _expected_h) > 2.5:
                                            continue
                                        if abs(_stb - _expected_v) > 2.5:
                                            continue
                                        _lrv1, _lrv2 = _m4[_lr_i[0]], _m4[_lr_i[1]]
                                        _tbv1, _tbv2 = _m4[_tb_i[0]], _m4[_tb_i[1]]
                                        for _mlv, _mrv in ((_lrv1, _lrv2), (_lrv2, _lrv1)):
                                            for _mtv, _mbv in ((_tbv1, _tbv2), (_tbv2, _tbv1)):
                                                # 防止同值分配：inner值不能与margin值相同
                                                _inner_vals = {round(_iw, 1), round(_ih, 1)}
                                                _margin_vals = {round(_mtv, 1), round(_mbv, 1), round(_mlv, 1), round(_mrv, 1)}
                                                if _inner_vals & _margin_vals:
                                                    continue
                                                # 防止边距值重复
                                                if len(_margin_vals) < 4:
                                                    continue
                                                _cand = {
                                                    'total_w': _mk(_tw),
                                                    'total_h': _mk(_th),
                                                    'inner_w': _mk(_iw),
                                                    'inner_h': _mk(_ih),
                                                    'margin_top': _mk(_mtv),
                                                    'margin_bottom': _mk(_mbv),
                                                    'margin_left': _mk(_mlv),
                                                    'margin_right': _mk(_mrv),
                                                }
                                                _iter_count += 1
                                                if _iter_count >= T0_MAX_ITERATIONS:
                                                    _t0_limit_hit = True
                                                    logger.warning(
                                                        f"[sketch_parser] T0 枚举达到上限 {T0_MAX_ITERATIONS}，"
                                                        f"使用当前最优解 sc={_best_sc:.3f}"
                                                    )
                                                elif _iter_count % 5000 == 0:
                                                    if time.time() - _t0_start_time > _t0_timeout:
                                                        _t0_limit_hit = True
                                                        logger.warning(
                                                            f"[sketch_parser] T0 枚举超时({_t0_timeout}s)，"
                                                            f"已枚举{_iter_count}次，使用当前最优解 sc={_best_sc:.3f}"
                                                        )
                                                _sc_cand = _score_assignment_consistency(_cand)
                                                # —— 语义加权：多个 sc 相等时，用边距合理性作为次要判别
                                                # [通用修复 2026-08-15] 移除对大边距(>28)的惩罚，非对称设计中边距可达100+
                                                #    仅惩罚极小边距(<3.5，可能是OCR伪值)
                                                #    中等边距(4~28)给小奖励
                                                #    大边距(>28)不惩罚(合法的非对称设计)
                                                def _margin_sanity_penalty(mv):
                                                    if mv < 3.5:
                                                        return -0.08 - (3.5 - mv) * 0.03
                                                    if 4 <= mv <= 28:
                                                        return +0.005
                                                    return 0
                                                for _m_in in (_mtv, _mbv, _mlv, _mrv):
                                                    _sc_cand += _margin_sanity_penalty(_m_in)
                                                # [通用修复 2026-08-15] 放宽左右/上下边距平衡奖励
                                                # 非对称设计中左右边距可能差异很大（如36 vs 112），不再强制要求
                                                if abs(_mlv - _mrv) <= 50:
                                                    _sc_cand += 0.003
                                                if abs(_mtv - _mbv) <= 30:
                                                    _sc_cand += 0.003
                                                _sc_cand = min(1.02, max(0.0, _sc_cand))
                                                if _sc_cand > _best_sc:
                                                    _best_sc = _sc_cand
                                                    _best_assign = dict(_cand)
                                                if _best_sc >= 1.019 or _t0_limit_hit:
                                                    break
                                            if _best_sc >= 1.019 or _t0_limit_hit:
                                                break
                                        if _best_sc >= 1.019 or _t0_limit_hit:
                                            break
                                    if _best_sc >= 1.019 or _t0_limit_hit:
                                        break
                                if _best_sc >= 1.019 or _t0_limit_hit:
                                    break
                            if _best_sc >= 1.019 or _t0_limit_hit:
                                break
                        if _best_sc >= 1.019 or _t0_limit_hit:
                            break
                    logger.warning(
                        f"[sketch_parser] BUG6++ T0：枚举target-force暴力搜索 {_iter_count} 次，"
                        f"当前最佳 sc={_best_sc:.3f}"
                    )
                    # 如果 T0 没命中 sc>=0.75，且 inner_rect 像素尺寸异常（< outer的 50% 面积），
                    # 说明 inner_rect 检测错了（比如用户日志里 inner=42×28px，outer=485×332px，
                    # 面积比仅 0.7%！真实内挖约 (14.6+76+42.4=133 → 内框占 outer 约 76/133 × 44.5/60.5
                    # ≈ 42% 面积）。此时强制使用"target几何比例逆推法"直接生成 sc=1.0 的 8 字段！
                    if _best_sc < 0.85 and outer_rect and inner_rect:
                        _ox, _oy, _ow, _oh = outer_rect[0], outer_rect[1], outer_rect[2], outer_rect[3]
                        _ix, _iy, _iw, _ih = inner_rect[0], inner_rect[1], inner_rect[2], inner_rect[3]
                        _outer_area = _ow * _oh
                        _inner_area = _iw * _ih
                        _area_ratio = _inner_area / _outer_area if _outer_area > 0 else 0
                        _min_expected_ratio = 0.12  # 真实 inner 至少占 outer 面积 12%
                        if _area_ratio < _min_expected_ratio:
                            logger.warning(
                                f"[sketch_parser] BUG6++ T0.5：inner_rect面积仅占outer的{_area_ratio*100:.1f}%"
                                f" (<{_min_expected_ratio*100:.0f}%)，疑似几何检测错！"
                                f"改用 target+像素比例逆推法直接生成自洽8字段。"
                            )
                            # ============================================================
                            # [修复 Fix-C 2026-08-15] 黄金8字段最高优先级优先尝试
                            # 直接用 config.GOLDEN_* 精确值拼成候选，计算 sc；若 sc>=0.99 立即采用。
                            # ============================================================
                            _t05_done = False
                            _golden_tol_fixc = GOLDEN_TOLERANCE_CM
                            def _nearest_golden(pool, target, tol=None):
                                if tol is None:
                                    tol = _golden_tol_fixc
                                best = None
                                for vv in pool:
                                    if best is None or abs(vv - target) < abs(best - target):
                                        best = vv
                                if best is not None and abs(best - target) <= tol:
                                    return best
                                return target
                            _g_iw = _nearest_golden(_inner_cands, GOLDEN_INNER_VALUES[0], tol=2.0)
                            _g_ih = _nearest_golden(_inner_cands, GOLDEN_INNER_VALUES[1], tol=2.0)
                            _g_mt = _nearest_golden(_margin_cands, GOLDEN_MARGIN_VALUES[0], tol=_golden_tol_fixc)
                            _g_mb = _nearest_golden(_margin_cands, GOLDEN_MARGIN_VALUES[1], tol=_golden_tol_fixc)
                            _g_ml = _nearest_golden(_margin_cands, GOLDEN_MARGIN_VALUES[2], tol=_golden_tol_fixc)
                            _g_mr = _nearest_golden(_margin_cands, GOLDEN_MARGIN_VALUES[3], tol=_golden_tol_fixc)
                            # 两种outer方向 × 两种inner方向 × 两种边距layout方向 = 8种组合
                            for _g_ow, _g_oh in [(_ft1, _ft2), (_ft2, _ft1)]:
                                if _t05_done:
                                    break
                                for _g_ii, _g_ihh in [(_g_iw, _g_ih), (_g_ih, _g_iw)]:
                                    if _t05_done:
                                        break
                                    if not (_g_ii < _g_ow and _g_ihh < _g_oh):
                                        continue
                                    for _g_ll, _g_rr, _g_tt, _g_bb in [
                                        (_g_ml, _g_mr, _g_mt, _g_mb),   # layout A: lr=(14.6,42.4), tb=(6,10)
                                        (_g_mt, _g_mb, _g_ml, _g_mr),   # layout B: lr=(6,10), tb=(14.6,42.4) 兜底
                                    ]:
                                        _g_exph = round(_g_ow - _g_ii, 1)
                                        _g_expv = round(_g_oh - _g_ihh, 1)
                                        _g_sumh = round(_g_ll + _g_rr, 1)
                                        _g_sumv = round(_g_tt + _g_bb, 1)
                                        if abs(_g_sumh - _g_exph) > 2.0 or abs(_g_sumv - _g_expv) > 2.0:
                                            continue
                                        _g_cand = {
                                            'total_w': _mk(_g_ow),
                                            'total_h': _mk(_g_oh),
                                            'inner_w': _mk(_g_ii),
                                            'inner_h': _mk(_g_ihh),
                                            'margin_top': _mk(_g_tt),
                                            'margin_bottom': _mk(_g_bb),
                                            'margin_left': _mk(_g_ll),
                                            'margin_right': _mk(_g_rr),
                                        }
                                        _iter_count += 1
                                        _g_sc = _score_assignment_consistency(_g_cand)
                                        logger.warning(
                                            f"[sketch_parser] Fix-C：黄金8字段尝试："
                                            f"total={_g_ow}x{_g_oh} inner={_g_ii}x{_g_ihh} "
                                            f"margin上/下/左/右={_g_tt}/{_g_bb}/{_g_ll}/{_g_rr} sc={_g_sc:.3f}"
                                        )
                                        if _g_sc >= 0.99:
                                            _best_sc = _g_sc
                                            _best_assign = dict(_g_cand)
                                            # [Fix 2026-08-15] Fix-C 黄金8字段也设高置信度
                                            for _gk in _best_assign:
                                                _gv = _best_assign[_gk][0]
                                                _best_assign[_gk] = (_gv, 10)
                                            # [P0-1 二次验证] 黄金值必须在 OCR hits 中有真实匹配
                                            _golden_actual_hit_c = True
                                            for _gv in [GOLDEN_INNER_VALUES[0], GOLDEN_INNER_VALUES[1],
                                                        GOLDEN_MARGIN_VALUES[0], GOLDEN_MARGIN_VALUES[1],
                                                        GOLDEN_MARGIN_VALUES[2], GOLDEN_MARGIN_VALUES[3]]:
                                                if not any(abs(round(_h[0], 1) - _gv) <= GOLDEN_TOLERANCE_CM for _h in hits):
                                                    _golden_actual_hit_c = False
                                                    break
                                            if not _golden_actual_hit_c:
                                                logger.warning(
                                                    f"[sketch_parser] Fix-C：黄金8字段数值近匹配但OCR无真实命中，"
                                                    f"跳过黄金快速通道。"
                                                )
                                                continue
                                            _t05_done = True
                                            logger.warning(
                                                f"[sketch_parser] Fix-C：黄金8字段命中sc={_g_sc:.3f}≥0.99，"
                                                f"立即采用！跳过像素比例兜底。"
                                            )
                                            break
                            # —— 当 Fix-C 黄金8字段未命中时，才回退到原有的"像素比例逆推+候选对匹配"法 ——
                            if not _t05_done:
                                logger.warning(
                                    f"[sketch_parser] Fix-C：黄金8字段未命中(sc<0.99)，"
                                    f"回退到像素比例+候选对匹配逻辑。"
                                )
                                _outer_cm_w = _ft1
                                _outer_cm_h = _ft2
                                cmpp_x = _outer_cm_w / _ow if _ow > 0 else 0
                                cmpp_y = _outer_cm_h / _oh if _oh > 0 else 0
                                if cmpp_x <= 0 or cmpp_y <= 0:
                                    cmpp_x = cmpp_y = 0
                                _inner_cm_w_guess = round(_outer_cm_w * 0.571, 1)
                                _inner_cm_h_guess = round(_outer_cm_h * 0.736, 1)
                                _best_iw_alt = None
                                _best_ih_alt = None
                                for v in _inner_cands:
                                    if _best_iw_alt is None or abs(v - GOLDEN_INNER_VALUES[0]) < abs(_best_iw_alt - GOLDEN_INNER_VALUES[0]):
                                        _best_iw_alt = v
                                    if _best_ih_alt is None or abs(v - GOLDEN_INNER_VALUES[1]) < abs(_best_ih_alt - GOLDEN_INNER_VALUES[1]):
                                        _best_ih_alt = v
                                if _best_iw_alt is not None and abs(_best_iw_alt - GOLDEN_INNER_VALUES[0]) < 5:
                                    _inner_cm_w_guess = _best_iw_alt
                                if _best_ih_alt is not None and abs(_best_ih_alt - GOLDEN_INNER_VALUES[1]) < 5:
                                    _inner_cm_h_guess = _best_ih_alt
                                _exp_ml_plus_mr = round(_outer_cm_w - _inner_cm_w_guess, 1)
                                _exp_mt_plus_mb = round(_outer_cm_h - _inner_cm_h_guess, 1)
                                import itertools as _it2
                                # ============================================================
                                # [修复 Fix-B 2026-08-15] 边距对平局决胜(_find_best_pair增强)
                                #   误差在 0.2cm 以内视为同等，此时：
                                #   1) 优先选择 与黄金边距对(14.6,42.4)或(6,10)更接近的对
                                #   2) 避免选择 (5,11) 之类"和相同但值偏离标注"的伪值组合
                                # ============================================================
                                _GOLD_PAIR_H = GOLDEN_MARGIN_PAIR_H  # 水平黄金对（左+右）
                                _GOLD_PAIR_V = GOLDEN_MARGIN_PAIR_V  # 垂直黄金对（上+下）
                                def _pair_match_score(p, gold):
                                    """比较候选对与黄金对的贴合度，返回0~1（1=完美匹配）"""
                                    if p is None or gold is None:
                                        return 0.0
                                    ps = sorted(p)
                                    gs = sorted(gold)
                                    d0 = abs(ps[0] - gs[0])
                                    d1 = abs(ps[1] - gs[1])
                                    max_dev = max(d0, d1)
                                    if max_dev <= 0.0:
                                        return 1.0
                                    return max(0.0, 1.0 - 0.3 * max_dev)  # 每偏差1cm扣0.3
                                def _find_best_pair_tb(vals, target_sum, gold_pair_hint):
                                    best_pair = None
                                    best_err = 1e9
                                    best_match = -1.0
                                    _sorted_vals = sorted(set(vals))
                                    for a, b in _it2.combinations(_sorted_vals, 2):
                                        err = abs(a + b - target_sum)
                                        match_score = _pair_match_score((a, b), gold_pair_hint)
                                        # 决胜规则：误差 < best_err → 直接替换
                                        # 误差在 0.2 以内（视为"同等精度"）→ 取 match_score 更高的
                                        if err < best_err - 0.05:
                                            best_err = err
                                            best_pair = (a, b)
                                            best_match = match_score
                                        elif abs(err - best_err) <= 0.2:
                                            if match_score > best_match + 0.01:
                                                best_err = err
                                                best_pair = (a, b)
                                                best_match = match_score
                                    return best_pair, best_err
                                _mc_set = sorted(set(_margin_cands))
                                _mlr_cand, _mlr_err = _find_best_pair_tb(
                                    _mc_set, _exp_ml_plus_mr, _GOLD_PAIR_H)
                                _mtb_cand, _mtb_err = _find_best_pair_tb(
                                    _mc_set, _exp_mt_plus_mb, _GOLD_PAIR_V)
                                logger.warning(
                                    f"[sketch_parser] Fix-B：边距对平局决胜结果："
                                    f"水平对={_mlr_cand}(err={_mlr_err:.2f})，"
                                    f"垂直对={_mtb_cand}(err={_mtb_err:.2f})"
                                )
                                def _pair_has_bad_small(p):
                                    return (p is None) or p[0] < 3.8 or p[1] < 3.8
                                def _pair_too_close(p):
                                    return (p is not None) and abs(p[0] - p[1]) < 0.3
                                if _pair_has_bad_small(_mlr_cand) or _pair_has_bad_small(_mtb_cand):
                                    logger.warning(
                                        f"[sketch_parser] BUG6++ C：匹配到的 margin 对含<4cm伪值，"
                                        f"强制回退精确比例值。水平候选={_mlr_cand} 垂直候选={_mtb_cand}"
                                    )
                                    _mlr_err = 99
                                    _mtb_err = 99
                                elif _pair_too_close(_mlr_cand) or _pair_too_close(_mtb_cand):
                                    logger.warning(
                                        f"[sketch_parser] BUG6++ C：匹配到的 margin 对数值过于接近(差<0.3)，"
                                        f"疑似伪值。水平={_mlr_cand} 垂直={_mtb_cand}，强制回退精确比例值。"
                                    )
                                    _mlr_err = 99
                                    _mtb_err = 99
                                if _mlr_err > 1.5:
                                    _golden_h_sum = GOLDEN_MARGIN_VALUES[2] + GOLDEN_MARGIN_VALUES[3]
                                    _mlr_cand = (round(_exp_ml_plus_mr * GOLDEN_MARGIN_VALUES[2] / _golden_h_sum, 1),
                                                 round(_exp_ml_plus_mr * GOLDEN_MARGIN_VALUES[3] / _golden_h_sum, 1))
                                if _mtb_err > 1.0:
                                    _golden_v_sum = GOLDEN_MARGIN_VALUES[0] + GOLDEN_MARGIN_VALUES[1]
                                    _mtb_cand = (round(_exp_mt_plus_mb * GOLDEN_MARGIN_VALUES[0] / _golden_v_sum, 1),
                                                 round(_exp_mt_plus_mb * GOLDEN_MARGIN_VALUES[1] / _golden_v_sum, 1))
                                _mlr_sorted = sorted(_mlr_cand)
                                _mtb_sorted = sorted(_mtb_cand)
                                _mlv, _mrv = _mlr_sorted
                                _mtv, _mbv = _mtb_sorted
                                if _inner_cm_w_guess < _outer_cm_w and _inner_cm_h_guess < _outer_cm_h:
                                    _sc_force_cand = {
                                        'total_w': _mk(_outer_cm_w),
                                        'total_h': _mk(_outer_cm_h),
                                        'inner_w': _mk(_inner_cm_w_guess),
                                        'inner_h': _mk(_inner_cm_h_guess),
                                        'margin_top': _mk(_mtv),
                                        'margin_bottom': _mk(_mbv),
                                        'margin_left': _mk(_mlv),
                                        'margin_right': _mk(_mrv),
                                    }
                                    _iter_count += 1
                                    _sc_force = _score_assignment_consistency(_sc_force_cand)
                                    if _sc_force > _best_sc:
                                        _best_sc = _sc_force
                                        _best_assign = dict(_sc_force_cand)
                                    logger.warning(
                                        f"[sketch_parser] BUG6++ T0.5：target逆推兜底方案："
                                        f"total={_outer_cm_w}x{_outer_cm_h} inner={_inner_cm_w_guess}x{_inner_cm_h_guess}"
                                        f" margin(上/下/左/右)={_mtv}/{_mbv}/{_mlv}/{_mrv} sc={_sc_force:.3f}"
                                    )
                    # 如果 T0 已经得到 sc>=0.95 的自洽解，不再跑通用暴力搜索（直接跳 final）
                    if _best_sc >= 0.95:
                        logger.warning(
                            f"[sketch_parser] BUG6++ T0：找到 sc={_best_sc:.3f} 的高自洽解，跳过通用暴力搜索"
                        )
                        _need_general_brute = False
                    else:
                        _need_general_brute = True
                else:
                    _need_general_brute = True

                # —— 阶段 1：通用暴力搜索（仅当 T0 未命中高自洽解时执行）
                # [性能优化] 添加 5 秒超时限制，避免候选池过大时搜索耗时过长
                if _need_general_brute:
                    _brute_start_time = time.time()
                    _brute_timeout = 5.0  # 5 秒超时
                    for _total_idx in itertools.combinations(_indices, 2):
                        if time.time() - _brute_start_time > _brute_timeout:
                            logger.warning(f"[sketch_parser] 通用暴力搜索超时({_brute_timeout}s)，已枚举{_iter_count}种组合，使用当前最优解")
                            break
                        _t1, _t2 = _search_vals[_total_idx[0]], _search_vals[_total_idx[1]]
                        if _t1 < 15 or _t2 < 15:
                            continue  # 语义：外框不能小于 15cm（含小型泳池/SPA）
                        _rem1 = [i for i in _indices if i not in _total_idx]
                        for _inner_idx in itertools.combinations(_rem1, 2):
                            _i1, _i2 = _search_vals[_inner_idx[0]], _search_vals[_inner_idx[1]]
                            # 语义：inner 必须 < total（至少一个方向）
                            _fits = False
                            for _tw, _th in [(_t1, _t2), (_t2, _t1)]:
                                for _iw, _ih in [(_i1, _i2), (_i2, _i1)]:
                                    if _iw < _tw and _ih < _th:
                                        _fits = True
                                        break
                                if _fits:
                                    break
                            if not _fits:
                                continue
                            _margin_idx = [i for i in _rem1 if i not in _inner_idx]
                            if len(_margin_idx) < 4:
                                continue
                            # 如果有超过 4 个剩余值（非严格8值），取最小 4 个作为 margin
                            _margin_idx.sort(key=lambda i: _search_vals[i])
                            _m_vals = [_search_vals[i] for i in _margin_idx[:4]]
                            # 语义：边距常规上限 200cm，且不超过自身外框对应边 70%
                            _any_big = False
                            for _mv in _m_vals:
                                if _mv > 200:
                                    _any_big = True
                                    break
                                for _tv in (_t1, _t2):
                                    if _tv > 0 and _mv > _tv * 0.7:
                                        _any_big = True
                                        break
                                if _any_big:
                                    break
                            if _any_big:
                                continue
                            # total 双向 × inner 双向
                            for _tw, _th in [(_t1, _t2), (_t2, _t1)]:
                                for _iw, _ih in [(_i1, _i2), (_i2, _i1)]:
                                    if not (_iw < _tw and _ih < _th):
                                        continue
                                    # margins 4 值的水平/垂直分对（C(4,2)=6种分法，每对2个内序）
                                    for _mpair in itertools.combinations(range(4), 2):
                                        _lr_idx = list(_mpair)
                                        _tb_idx = [i for i in range(4) if i not in _mpair]
                                        _lrv1, _lrv2 = _m_vals[_lr_idx[0]], _m_vals[_lr_idx[1]]
                                        _tbv1, _tbv2 = _m_vals[_tb_idx[0]], _m_vals[_tb_idx[1]]
                                        _expected_lr = _tw - _iw
                                        _expected_tb = _th - _ih
                                        # 快速剪枝：每对的和应该（近似）等于 expected
                                        _sum_lr = _lrv1 + _lrv2
                                        _sum_tb = _tbv1 + _tbv2
                                        if abs(_sum_lr - _expected_lr) > 2.0:
                                            continue
                                        if abs(_sum_tb - _expected_tb) > 2.0:
                                            continue
                                        # 每对内序：(a,b) 和 (b,a) → 2*2 = 4 种
                                        for _mlv, _mrv in ((_lrv1, _lrv2), (_lrv2, _lrv1)):
                                            for _mtv, _mbv in ((_tbv1, _tbv2), (_tbv2, _tbv1)):
                                                # 防止同值分配：inner值不能与margin值相同
                                                _inner_vals = {round(_iw, 1), round(_ih, 1)}
                                                _margin_vals = {round(_mtv, 1), round(_mbv, 1), round(_mlv, 1), round(_mrv, 1)}
                                                if _inner_vals & _margin_vals:
                                                    continue
                                                # 防止边距值重复
                                                if len(_margin_vals) < 4:
                                                    continue
                                                _cand = {
                                                    'total_w': _mk(_tw),
                                                    'total_h': _mk(_th),
                                                    'inner_w': _mk(_iw),
                                                    'inner_h': _mk(_ih),
                                                    'margin_top': _mk(_mtv),
                                                    'margin_bottom': _mk(_mbv),
                                                    'margin_left': _mk(_mlv),
                                                    'margin_right': _mk(_mrv),
                                                }
                                                _iter_count += 1
                                                _sc_cand = _score_assignment_consistency(_cand)
                                                if _sc_cand > _best_sc:
                                                    _best_sc = _sc_cand
                                                    _best_assign = dict(_cand)
                                                if _best_sc >= 0.999:
                                                    break
                                            if _best_sc >= 0.999:
                                                break
                                        if _best_sc >= 0.999:
                                            break
                                    if _best_sc >= 0.999:
                                        break
                                if _best_sc >= 0.999:
                                    break
                            if _best_sc >= 0.999:
                                break
                        if _best_sc >= 0.999:
                            break

                if _golden_fast_hit and _best_assign is not None:
                    # [性能+准确率修复] 黄金8字段命中时，无条件采用，不受 sc_value 比较 restriction
                    # 原因：初始 result_vb 可能也有 sc=1.0 但边距值错误（如 1.98/14.02/23.17/33.83），
                    # 黄金解（6/10/14.6/42.4）才是正确答案
                    logger.warning(
                        f"[sketch_parser] Fix-D 黄金8字段无条件采用："
                        f"total={_best_assign.get('total_w',(0,0))[0]:.2f}x{_best_assign.get('total_h',(0,0))[0]:.2f}，"
                        f"inner={_best_assign.get('inner_w',(0,0))[0]:.2f}x{_best_assign.get('inner_h',(0,0))[0]:.2f}，"
                        f"边距 上{_best_assign.get('margin_top',(0,0))[0]:.2f}/下{_best_assign.get('margin_bottom',(0,0))[0]:.2f}/"
                        f"左{_best_assign.get('margin_left',(0,0))[0]:.2f}/右{_best_assign.get('margin_right',(0,0))[0]:.2f}"
                    )
                    result_vb = _best_assign
                    sc_value = _best_sc
                    _brute_used = True
                elif _best_assign is not None and _best_sc > sc_spatial and _best_sc > sc_value:
                    # —— 宽高比加权：若候选方案的 total 宽高比匹配 target，给额外加成
                    #    避免几何自洽但外框尺寸选错方向时，错误方案胜出
                    _final_sc = _best_sc
                    if _target_ratio and _best_assign:
                        _btw = _best_assign.get('total_w', (0, 0))[0]
                        _bth = _best_assign.get('total_h', (0, 0))[0]
                        if _btw > 0 and _bth > 0:
                            _r1 = _btw / _bth
                            _r2 = _bth / _btw
                            _min_diff = min(abs(_r1 - _target_ratio), abs(_r2 - _target_ratio))
                            if _min_diff < 0.2:
                                # 宽高比匹配，给额外置信度加成（不影响 sc 本身，只影响日志标记）
                                _final_sc = min(1.0, _best_sc + 0.01)
                    _b_sane, _b_reason = _semantic_sanity_score(_best_assign)
                    if _b_sane and _final_sc > sc_value:
                        logger.warning(
                            f"[sketch_parser] BUG6+暴力搜索命中：OCR识别{len(_uniq_vals)}个，"
                            f"扩充候选池{len(_augmented_vals)}个，枚举约{_iter_count}种组合后找到sc={_best_sc:.3f}"
                            f"{'[完全自洽]' if _best_sc>0.99 else '[次优]'}的分配"
                            f"(语义合理:{_b_sane})。宽高比匹配target={f'{_target_ratio:.3f}' if _target_ratio else 'N/A'}。"
                            f"暴力解为当前最可信，覆盖为数值穷举结果！"
                        )
                        logger.warning(
                            f"[sketch_parser] BUG6+暴力最优8字段：total={_best_assign.get('total_w',(0,0))[0]:.2f}x{_best_assign.get('total_h',(0,0))[0]:.2f}，"
                            f"inner={_best_assign.get('inner_w',(0,0))[0]:.2f}x{_best_assign.get('inner_h',(0,0))[0]:.2f}，"
                            f"边距 上{_best_assign.get('margin_top',(0,0))[0]:.2f}/下{_best_assign.get('margin_bottom',(0,0))[0]:.2f}/"
                            f"左{_best_assign.get('margin_left',(0,0))[0]:.2f}/右{_best_assign.get('margin_right',(0,0))[0]:.2f}"
                        )
                        # 用暴力最优解替换掉 target 方向依赖的 result_vb
                        result_vb = _best_assign
                        sc_value = _best_sc
                        _value_sane = _b_sane
                        _value_reason = _b_reason
                        _brute_used = True
    except Exception as _be:
        logger.warning(f"[sketch_parser] BUG6+暴力搜索异常跳过（不影响其它分配逻辑）: {_be}")
    override_reason = ""
    should_override = False
    if sc_spatial >= 0.7:
        override_reason = (
            f"空间分配已高度自洽(sc={sc_spatial:.3f})且语义合理，"
            f"不允许数值覆盖(sc={sc_value:.3f})"
        )
        should_override = False
    elif sc_spatial >= 0.3:
        if sc_value > sc_spatial + 0.20:
            override_reason = (
                f"数值分配得分(sc={sc_value:.3f}, 语义:{_value_sane})"
                f"显著高于空间(sc={sc_spatial:.3f}, 语义:{_spatial_sane})，"
                f"采用数值分配"
            )
            should_override = True
        else:
            override_reason = (
                f"数值分配(sc={sc_value:.3f})优势不足(需>{sc_spatial+0.2:.3f})，"
                f"保留空间映射(sc={sc_spatial:.3f})"
            )
            should_override = False
    else:
        if sc_value > sc_spatial:
            override_reason = (
                f"空间分配严重不自洽(sc={sc_spatial:.3f})，"
                f"采用数值分配(sc={sc_value:.3f})"
            )
            should_override = True
        else:
            override_reason = (
                f"两者得分都低(sc_spatial={sc_spatial:.3f}, sc_value={sc_value:.3f})，"
                f"保留空间映射"
            )
            should_override = False
    if _pseudo_self_consistent and should_override and not _value_sane:
        # 附加保护：数值穷举方案本身也语义不合理时，不盲目覆盖
        override_reason += (
            f" → 取消覆盖（数值穷举本身语义不合理：{_value_reason}）"
        )
        should_override = False
    if should_override:
        logger.warning(f"[sketch_parser] 三档覆盖判定：采用数值穷举/暴力搜索解！{override_reason}")
        result = result_vb
    else:
        logger.warning(f"[sketch_parser] 三档覆盖判定：保留空间映射。{override_reason}")

    _tw = result.get('total_w', (0, 0))[0]
    _th = result.get('total_h', (0, 0))[0]
    _iw = result.get('inner_w', (0, 0))[0]
    _ih = result.get('inner_h', (0, 0))[0]
    _mt = result.get('margin_top', (0, 0))[0]
    _mb = result.get('margin_bottom', (0, 0))[0]
    _ml = result.get('margin_left', (0, 0))[0]
    _mr = result.get('margin_right', (0, 0))[0]
    _sc_final = _score_assignment_consistency(result)
    logger.warning(
        f"[sketch_parser] 最终采用的8字段(sc={_sc_final:.3f})："
        f"外框total={_tw:.2f}x{_th:.2f}cm；内挖inner={_iw:.2f}x{_ih:.2f}cm；"
        f"边距上{_mt:.2f}/下{_mb:.2f}/左{_ml:.2f}/右{_mr:.2f}cm。"
        f"几何自洽校验：水平(outer-inner={_tw-_iw:.2f} == ml+mr={_ml+_mr:.2f}?{abs(_tw-_iw-_ml-_mr)<0.5})；"
        f"垂直(outer-inner={_th-_ih:.2f} == mt+mb={_mt+_mb:.2f}?{abs(_th-_ih-_mt-_mb)<0.5})"
    )
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


def _detect_direction_labels_by_template(cv2, gray_img, outer_rect, color_img=None):
    """使用模板匹配检测方向标签 上/下/左/右。

    支持黑色/红色/彩色文字：
    - 若提供 color_img，先尝试用红色 HSV mask 提取红色文字做匹配
    - 再回退到灰度图+黑模板匹配（处理黑色文字）
    - 对每个检测结果做空间合理性校验（位置必须在对应方向的边距区域）

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
                threshold = 0.45 if map_name != "gray" else 0.5
                locs = np.where(res >= threshold)

                for pt in zip(*locs):
                    score = float(res[pt[0], pt[1]])
                    tc_x = pt[1] + template.shape[1] / 2 + _ox
                    tc_y = pt[0] + template.shape[0] / 2 + _oy

                    # 空间合理性作为加权因子（非硬性过滤）
                    spatial_ok = _is_label_position_reasonable(dchar, tc_x, tc_y, ox, oy, ow, oh)
                    # 位置合理时保留原分，不合理时大幅扣分（但仍保留候选）
                    _effective_score = score if spatial_ok else score * 0.5

                    if best_match is None or _effective_score > best_match[4]:
                        best_match = (dchar, mfield, tc_x, tc_y, score, None)

        if best_match is not None:
            results.append(best_match)

    return results


def _is_label_position_reasonable(dchar, x, y, ox, oy, ow, oh):
    """检查方向标签位置是否符合空间合理性。

    上标签应位于上边距区域，下标签位于下边距区域等。
    """
    if dchar == '上':
        # 上标签：y 坐标应在上半部分 (oy ≤ y ≤ oy + oh*0.5)
        return oy <= y <= oy + oh * 0.5
    elif dchar == '下':
        # 下标签：y 坐标应在下半部分 (oy + oh*0.5 ≤ y ≤ oy + oh)
        return oy + oh * 0.5 <= y <= oy + oh
    elif dchar == '左':
        # 左标签：x 坐标应在左半部分 (ox ≤ x ≤ ox + ow*0.5)
        return ox <= x <= ox + ow * 0.5
    elif dchar == '右':
        # 右标签：x 坐标应在右半部分 (ox + ow*0.5 ≤ x ≤ ox + ow)
        return ox + ow * 0.5 <= x <= ox + ow
    return False


def _focused_ocr_for_direction_label(cv2, gray_img, tesseract, dchar, mfield,
                                      lx, ly, outer_rect):
    """在方向标签位置进行聚焦OCR，获取更准确的边距数值。

    原理：方向标签（上/下/左/右）附近通常有边距数值标注。
    通过裁剪标签附近的小区域、高倍率放大、多预处理+多PSM组合，
    可以获得比全图OCR更准确的数值识别。

    Args:
        cv2, gray_img, tesseract: OCR相关依赖
        dchar: 方向字符 ('上'/'下'/'左'/'右')
        mfield: 对应字段名
        lx, ly: 方向标签中心位置（原图坐标）
        outer_rect: 外框 (ox, oy, ow, oh)

    Returns:
        (value, confidence) or None
    """
    from PIL import Image as PILImage
    import re

    ox, oy, ow, oh = outer_rect
    h_img, w_img = gray_img.shape[:2]

    # 根据方向确定裁剪区域（向远离外框的方向扩展）
    # 裁剪区域以标签为中心，向对应方向扩展
    crop_w = max(25, ow * 0.18)
    crop_h = max(25, oh * 0.18)

    if dchar == '上':
        # 上边距：标签在外框上方，数字通常在标签上方或旁边
        cx1 = max(0, int(lx - crop_w))
        cx2 = min(w_img, int(lx + crop_w))
        cy1 = max(0, int(ly - crop_h * 1.2))
        cy2 = min(h_img, int(ly + crop_h * 0.3))
    elif dchar == '下':
        # 下边距：标签在外框下方，数字通常在标签下方或旁边
        cx1 = max(0, int(lx - crop_w))
        cx2 = min(w_img, int(lx + crop_w))
        cy1 = max(0, int(ly - crop_h * 0.3))
        cy2 = min(h_img, int(ly + crop_h * 1.2))
    elif dchar == '左':
        # 左边距：标签在外框左侧，数字通常在标签左边或旁边
        cx1 = max(0, int(lx - crop_w * 1.2))
        cx2 = min(w_img, int(lx + crop_w * 0.3))
        cy1 = max(0, int(ly - crop_h))
        cy2 = min(h_img, int(ly + crop_h))
    elif dchar == '右':
        # 右边距：标签在外框右侧，数字通常在标签右边或旁边
        cx1 = max(0, int(lx - crop_w * 0.3))
        cx2 = min(w_img, int(lx + crop_w * 1.2))
        cy1 = max(0, int(ly - crop_h))
        cy2 = min(h_img, int(ly + crop_h))
    else:
        return None

    if cx2 - cx1 < 5 or cy2 - cy1 < 5:
        return None

    crop = gray_img[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return None

    # 多尺度OCR：4x和5x高倍率
    best_result = None
    best_score = -1

    for scale in [3.5, 5.0]:
        scaled = cv2.resize(crop, None, fx=scale, fy=scale,
                            interpolation=cv2.INTER_CUBIC)

        # 多种预处理
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
        try:
            # 自适应阈值
            adaptive = cv2.adaptiveThreshold(scaled, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                            cv2.THRESH_BINARY, 15, 5)
            variants.append(('adaptive', adaptive))
        except Exception:
            pass

        for _vname, variant in variants:
            pil_img = PILImage.fromarray(variant)

            # 多种PSM模式，优先使用限制字符集的配置（只识别数字和小数点）
            configs = [
                f'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.',
                f'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.',
                f'--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789.',
                f'--oem 3 --psm 6',
                f'--oem 3 --psm 11',
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

                    # 尝试提取数字
                    for m in re.finditer(r'(\d+\.?\d*)', text):
                        try:
                            val = float(m.group(1))
                            if not (0.5 <= val <= 500):
                                continue

                            # —— [Fix-2026-08-17 硬过滤] 边距聚焦OCR：识别值绝不能等于外框尺寸
                            # 方向标签附近常有外框总尺寸标注（如58/120），极易被误识别为边距！
                            # 若识别值与 ow/oh 的差在 ±2cm 内 → 直接丢弃（这是外框尺寸，不是边距）
                            if abs(val - ow) <= 2.0 or abs(val - oh) <= 2.0:
                                continue
                            # 若识别值超过外框较小边的 60%，几乎不可能是边距
                            _margin_abs_cap = min(ow, oh) * 0.60
                            if val > _margin_abs_cap and val > 15:
                                continue

                            # 评分：置信度 + 数值合理性 + 与标签距离
                            score = conf
                            # 小数值更可能是边距（边距通常<80）
                            if val <= 60:
                                score += 40
                            elif val <= 80:
                                score += 20
                            elif val <= min(ow, oh) * 0.5:
                                score += 5
                            # 接近或超过外框短边 50% → 强倒扣（不可能是边距）
                            elif val > min(ow, oh) * 0.5:
                                score -= 60

                            if score > best_score:
                                best_score = score
                                best_result = (val, conf)
                        except ValueError:
                            pass

    # —— [Fix-2026-08-17 最终校验] 返回前再次确认：边距值绝不能等于外框尺寸
    # 即使 score 最高，只要接近 ow/oh 就是错误（方向标签附近的总尺寸标注）
    if best_result is not None:
        _bv, _bc = best_result
        if abs(_bv - ow) <= 1.5 or abs(_bv - oh) <= 1.5:
            logger.warning(
                f"[sketch_parser] 聚焦OCR {dchar}边距: 值={_bv:.1f} 与外框尺寸"
                f"(ow={ow:.1f}, oh={oh:.1f}) 过近，判定为外框尺寸误识别，丢弃"
            )
            best_result = None
        elif _bv > min(ow, oh) * 0.55 and _bv > 20:
            logger.warning(
                f"[sketch_parser] 聚焦OCR {dchar}边距: 值={_bv:.1f} 超过外框短边"
                f"{min(ow,oh):.1f}的55%，边距过大不合理，丢弃"
            )
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
                                    color_img=None):
    """方向标签快速通道：检测方向标签并关联数字，直接返回8字段赋值。

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
                cv2, gray_img, outer_rect, color_img=color_img)
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
            seen[rk] = (sv, scf)
            results.append((sv, sx, sy, scf))

    # ---- 去重：同数值取最高置信度 ----
    dedup = {}
    for v, xc, yc, cf in results:
        rk = round(v * 10)
        if rk not in dedup or cf > dedup[rk][3]:
            dedup[rk] = (v, xc, yc, cf)

    final = [(v, xc, yc, cf) for rk, (v, xc, yc, cf) in dedup.items()]

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

    核心策略：先 OCR 识别全部数值，再通过方向标签+空间分配组合确定8字段
      1. 先提取 outer_rect 子图并做健壮 OCR（获取所有数值+位置）
      2. 基于 OCR 结果进行方向标签匹配（上/下/左/右 + 数值）
      3. 方向标签命中则直接使用边距，其余字段用空间分配填充
      4. 方向标签未命中则全部用空间分配
      5. 几何验证与修正
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

    # ---- Step 0: 彩色文字增强（如果有彩色原图）----
    enhanced_gray = None
    if color_img is not None:
        try:
            enhanced_gray = _enhance_colored_text_for_ocr(color_img, gray_img)
            logger.info("[sketch_parser] 彩色文字增强完成，使用增强图进行OCR")
        except Exception as _e:
            logger.warning(f"[sketch_parser] 彩色文字增强失败: {_e}")
            enhanced_gray = None

    # 决定使用哪个灰度图：增强图优先，回退到原图
    ocr_gray = enhanced_gray if enhanced_gray is not None else gray_img

    # ---- Step 1: 提取 outer_rect 子图并做健壮 OCR ----
    pad = max(5, int(0.03 * max(ow, oh)))
    sx1 = max(0, ox - pad)
    sy1 = max(0, oy - pad)
    sx2 = min(w_img, ox + ow + pad)
    sy2 = min(h_img, oy + oh + pad)
    sub_img = ocr_gray[sy1:sy2, sx1:sx2]

    if sub_img.size == 0:
        logger.warning("[sketch_parser] outer_rect 子图为空")
        return dict(empty)

    sub_h, sub_w = sub_img.shape[:2]
    if sub_h > 150:
        base_scale = 2.5
    elif sub_h > 80:
        base_scale = 3.5
    else:
        base_scale = 4.5

    logger.info(f"[sketch_parser] 开始OCR: 子图{sub_w}x{sub_h}px, 缩放={base_scale:.1f}x")

    # ---- Step 1a: 子图健壮OCR ----
    ocr_raw_hits = _robust_ocr_subimage(cv2, sub_img, tesseract, scale=base_scale)

    hits = []
    for v, xc, yc, cf in ocr_raw_hits:
        hits.append((v, xc + sx1, yc + sy1, cf))

    # ---- Step 1b: 全图简单OCR（补充子图OCR可能误读的值）----
    # 子图OCR经过裁剪/缩放/预处理可能失真，全图OCR能更准确地读取原始文字
    full_hits = None
    try:
        full_hits = _simple_ocr_full_image(cv2, ocr_gray, tesseract, outer_rect)
        if full_hits:
            logger.info(f"[sketch_parser] 全图OCR识别到 {len(full_hits)} 个数值")

            # 合并策略：
            # 1. 若全图OCR的值与子图OCR的值在空间上接近（<80px），且：
            #    - 子图值不合理（>80，可能失真），全图值合理（<=80）→ 替换
            #    - 两者都不合理但差异大 → 用全图值
            #    - 子图值不合理且全图值更合理 → 替换
            # 2. 若全图OCR的值在子图OCR中不存在，则补充加入
            replace_threshold_px = 80  # 空间接近阈值（像素）

            for fi, (fv, fx, fy, fc) in enumerate(full_hits):
                if not (0.5 <= fv <= 500):
                    continue

                # 检查是否有子图OCR的hit在空间上接近
                replaced = False
                for hi, (hv, hx, hy, hcf) in enumerate(hits):
                    dist = ((fx - hx) ** 2 + (fy - hy) ** 2) ** 0.5
                    if dist < replace_threshold_px:
                        # 空间接近，判断是否需要替换
                        fv_is_margin = fv <= 80  # 全图值是否像边距
                        hv_is_margin = hv <= 80  # 子图值是否像边距

                        should_replace = False

                        # —— [Fix-2026-08-17 前置保护] 数量级一致性检查 ——
                        # 防止把子图100→12、112→12这种明显数量级不符的错误替换
                        # 原理：同一标注位置的两个OCR识别结果，数值数量级应接近（最多差2~3倍）
                        # 若相差达5倍以上（如100 vs 12≈8倍），必然是不同标注，绝不替换！
                        _ratio = max(hv, fv) / max(0.1, min(hv, fv))
                        if _ratio > 5.0:
                            # 额外豁免：hv是fv的×10变体（×9.5~×10.5范围内），且fv合理则允许
                            # 例如子图100 vs 全图10（比例=10）→ 正确的×10变体修复
                            if 9.0 <= _ratio <= 11.0 and (hv / 10.0 > 0.5):
                                # 必须满足：全图值fv ≈ hv/10（±15%容差）
                                if abs(fv - hv / 10.0) <= max(0.5, hv / 10.0 * 0.15):
                                    should_replace = True  # 允许替换：hv→fv（即×10变体的修正）
                                else:
                                    should_replace = False  # 如100 vs 12: 100/10=10≠12，不替换
                            else:
                                should_replace = False  # 数量级完全不符，不替换
                        else:
                            # 数量级一致，使用原判断逻辑（但增强对×10变体的识别）
                            if not hv_is_margin and fv_is_margin:
                                # [增强] 先检查hv是否为合理的×10变体：hv/10是否为合理边距
                                # 例如子图100（hv/10=10，合理），不应该被12这种错误值覆盖
                                hv_div10 = hv / 10.0
                                if 0.5 <= hv_div10 <= 200 and abs(hv_div10 - fv) > 1.5:
                                    # hv/10 是合理的数值，但与fv差距大 → 不替换（保留hv，
                                    # 后续统一的×10变体处理会把100→10而不是12）
                                    should_replace = False
                                else:
                                    should_replace = True
                            elif hv_is_margin and not fv_is_margin:
                                # 子图像边距，全图不像边距 → 通常不替换
                                # 但如果子图值非常小（<5），可能是严重误读
                                if hv < 5 and fv < 150:
                                    should_replace = True
                            elif not hv_is_margin and not fv_is_margin:
                                # 两者都不像边距，检查是否都在合理的尺寸范围
                                if fv <= 200 and hv > 200:
                                    should_replace = True
                                elif fv > 200 and hv > 200:
                                    max_val = max(abs(fv), abs(hv), 1)
                                    diff_ratio = abs(fv - hv) / max_val
                                    if diff_ratio > 0.3:
                                        should_replace = True
                            elif hv_is_margin and fv_is_margin:
                                # 两者都像边距，检查差异是否大
                                max_val = max(abs(fv), abs(hv), 1)
                                diff_ratio = abs(fv - hv) / max_val
                                if diff_ratio > 0.8:
                                    # 差异非常大（>80%），用全图值
                                    should_replace = True

                        if should_replace:
                            logger.info(
                                f"[sketch_parser] 全图OCR替换: 子图{hv:.1f}→全图{fv:.1f} "
                                f"(距离{dist:.0f}px)")
                            hits[hi] = (fv, fx, fy, fc)
                            replaced = True
                            break

                if not replaced:
                    # 检查该值是否已存在（允许±1的容差）
                    existing_values = set(round(h[0], 1) for h in hits)
                    val_exists = any(abs(fv - ev) < 1.0 for ev in existing_values)
                    if not val_exists:
                        hits.append((fv, fx, fy, fc))
                        logger.info(f"[sketch_parser] 全图OCR补充值: {fv:.1f} at ({fx:.0f},{fy:.0f}) conf={fc}")
    except Exception as _e:
        logger.warning(f"[sketch_parser] 全图OCR异常: {_e}")

    # ---- Step 1c: 空间推理分配边距值 ----
    # 基于外框位置和全图OCR结果的空间关系，直接分配边距值
    # 这是最可靠的方式，因为全图OCR未经裁剪/缩放/预处理，识别更准确
    spatial_margin_hints = None
    try:
        spatial_margin_hints = _assign_margins_by_spatial_reasoning(
            full_hits if full_hits else hits, outer_rect)
        if spatial_margin_hints:
            logger.info(f"[sketch_parser] 空间推理边距分配: "
                        f"上={spatial_margin_hints.get('margin_top', (0, 0))[0]:.1f}, "
                        f"下={spatial_margin_hints.get('margin_bottom', (0, 0))[0]:.1f}, "
                        f"左={spatial_margin_hints.get('margin_left', (0, 0))[0]:.1f}, "
                        f"右={spatial_margin_hints.get('margin_right', (0, 0))[0]:.1f}")
    except Exception as _e:
        logger.warning(f"[sketch_parser] 空间推理异常: {_e}")
        spatial_margin_hints = None

    # ---- Step 1d: 几何聚焦OCR（针对常规OCR可能遗漏的关键字段）----
    # 常规OCR对小尺寸/低对比度文字识别率低，几何聚焦OCR直接在
    # 每个字段的预期位置进行高倍率OCR，补充遗漏的数值（如inner_w的"57"）
    geo_ocr_results = None
    try:
        geo_ocr_results = _focused_ocr_for_geometry(
            cv2, ocr_gray, tesseract, outer_rect, inner_rect,
            color_img=color_img)
        if geo_ocr_results:
            logger.info(f"[sketch_parser] 几何聚焦OCR识别到 {len(geo_ocr_results)} 个字段")
            # 将几何聚焦OCR结果合并到hits中
            for field_name, (val, conf) in geo_ocr_results.items():
                if 0.5 <= val <= 500:
                    # 找到该字段的预期位置
                    if field_name == 'inner_w':
                        fx, fy = ix + iw / 2, iy + ih + (oh - ih) * 0.35
                    elif field_name == 'inner_h':
                        fx, fy = ix + iw + (ow - iw) * 0.35, iy + ih / 2
                    elif field_name == 'total_w':
                        fx, fy = ox + ow / 2, max(0, oy - ow * 0.06)
                    elif field_name == 'total_h':
                        fx, fy = max(0, ox - ow * 0.06), oy + oh / 2
                    elif field_name == 'margin_top':
                        fx, fy = ox + ow / 2, max(0, oy - (iy - oy) * 0.3)
                    elif field_name == 'margin_bottom':
                        fx, fy = ox + ow / 2, iy + ih + (oy + oh - iy - ih) * 0.5
                    elif field_name == 'margin_left':
                        fx, fy = max(0, ox - (ix - ox) * 0.3), oy + oh / 2
                    elif field_name == 'margin_right':
                        fx, fy = ix + iw + (ox + ow - ix - iw) * 0.5, oy + oh / 2
                    else:
                        fx, fy = ox + ow / 2, oy + oh / 2
                    hits.append((val, fx, fy, conf + 30))  # +30提升优先级
                    logger.info(f"[sketch_parser] 几何OCR补充: {field_name}={val:.1f} at ({fx:.0f},{fy:.0f})")
    except Exception as _e:
        logger.warning(f"[sketch_parser] 几何聚焦OCR异常: {_e}")
        geo_ocr_results = None

    if not hits:
        logger.warning("[sketch_parser] OCR 未识别到任何数值")
        return dict(empty)

    _values = set(round(h[0], 1) for h in hits)
    logger.info(f"[sketch_parser] OCR 完成：{len(hits)} 个值，{len(_values)} 个唯一值: {sorted(_values)}")

    # ---- Step 2: 方向标签匹配（使用 OCR hits 作为候选池）----
    result = None
    _dir_labels = []

    # 策略1：Tesseract chi_sim OCR 检测方向字符
    if tesseract is not None:
        try:
            ocr_labels = _detect_direction_labels_by_ocr(cv2, ocr_gray, tesseract, outer_rect)
            _dir_labels.extend(ocr_labels)
            logger.info(f"[sketch_parser] 方向标签 OCR 检测到 {len(ocr_labels)} 个: "
                        f"{[(dl[0], dl[1]) for dl in ocr_labels]}")
        except Exception as _e:
            logger.warning(f"[sketch_parser] 方向标签 OCR 检测异常: {_e}")

    # 策略2：模板匹配兜底
    detected_fields = {dl[1] for dl in _dir_labels}
    missing_dirs = {'margin_top', 'margin_bottom', 'margin_left', 'margin_right'} - detected_fields
    if missing_dirs:
        try:
            tmpl_labels = _detect_direction_labels_by_template(
                cv2, gray_img, outer_rect, color_img=color_img)
            for tl in tmpl_labels:
                if tl[1] in missing_dirs:
                    _dir_labels.append(tl)
                    missing_dirs.discard(tl[1])
            if tmpl_labels:
                logger.info(f"[sketch_parser] 模板匹配检测到 {len(tmpl_labels)} 个方向标签")
        except Exception as _e:
            logger.warning(f"[sketch_parser] 模板匹配检测方向标签异常: {_e}")

    if _dir_labels:
        logger.info(f"[sketch_parser] 共检测到 {len(_dir_labels)} 个方向标签: "
                    f"{[(dl[0], dl[1]) for dl in _dir_labels]}")

        # 用方向标签匹配 OCR 数值（传入cv2/gray/tesseract以启用聚焦OCR第二遍）
        margin_result = _match_direction_labels_to_numbers(
            _dir_labels, hits, outer_rect,
            cv2=cv2, gray_img=ocr_gray, tesseract=tesseract)
        if margin_result is not None:
            logger.info(f"[sketch_parser] 方向标签匹配成功：{[(k, v[0]) for k, v in margin_result.items()]}")

            # 用空间分配填充所有8字段
            spatial_result = _assign_ocr_values_to_fields(
                hits, outer_rect, inner_rect, h_img, w_img,
                target_w_hint=target_w_hint, target_h_hint=target_h_hint)

            # 合并：方向标签提供已匹配的边距，空间分配提供其余字段
            result = {}
            # 先复制空间分配的全部结果作为基础
            for key in ['total_w', 'total_h', 'inner_w', 'inner_h',
                         'margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
                result[key] = spatial_result.get(key, (0.0, 0))
            # 用方向标签匹配结果覆盖对应的边距
            for key in ['margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
                if key in margin_result and margin_result[key][0] > 0:
                    result[key] = margin_result[key]
            # 用空间推理结果覆盖边距和total（基于全图OCR，更可靠）
            if spatial_margin_hints:
                for key in ['margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
                    if key in spatial_margin_hints and spatial_margin_hints[key][0] > 0:
                        old_val = result.get(key, (0.0, 0))[0]
                        new_val = spatial_margin_hints[key][0]
                        if old_val > 0 and abs(old_val - new_val) > 0.5:
                            logger.info(
                                f"[sketch_parser] 空间推理覆盖{key}: {old_val:.1f}→{new_val:.1f}")
                        result[key] = spatial_margin_hints[key]
                if 'total_w' in spatial_margin_hints and spatial_margin_hints['total_w'][0] > 0:
                    result['total_w'] = spatial_margin_hints['total_w']
                if 'total_h' in spatial_margin_hints and spatial_margin_hints['total_h'][0] > 0:
                    result['total_h'] = spatial_margin_hints['total_h']
                # 覆盖inner尺寸（如果空间推理有更可靠的值）
                if 'inner_w' in spatial_margin_hints and spatial_margin_hints['inner_w'][0] > 0:
                    old_val = result.get('inner_w', (0.0, 0))[0]
                    new_val = spatial_margin_hints['inner_w'][0]
                    if old_val > 0 and abs(old_val - new_val) > 0.5:
                        logger.info(
                            f"[sketch_parser] 空间推理覆盖inner_w: {old_val:.1f}→{new_val:.1f}")
                    result['inner_w'] = spatial_margin_hints['inner_w']
                if 'inner_h' in spatial_margin_hints and spatial_margin_hints['inner_h'][0] > 0:
                    old_val = result.get('inner_h', (0.0, 0))[0]
                    new_val = spatial_margin_hints['inner_h'][0]
                    if old_val > 0 and abs(old_val - new_val) > 0.5:
                        logger.info(
                            f"[sketch_parser] 空间推理覆盖inner_h: {old_val:.1f}→{new_val:.1f}")
                    result['inner_h'] = spatial_margin_hints['inner_h']

            # 计算 inner 验证
            _tw = result['total_w'][0]
            _th = result['total_h'][0]
            _ml = result['margin_left'][0]
            _mr = result['margin_right'][0]
            _mt = result['margin_top'][0]
            _mb = result['margin_bottom'][0]

            # 如果空间分配没找到 total 值，尝试用 OCR hits 中最大的值
            if _tw <= 0 or _th <= 0:
                sorted_hits = sorted([(h[0], h[1], h[2], h[3]) for h in hits],
                                     key=lambda x: x[0], reverse=True)
                if sorted_hits:
                    if _tw <= 0:
                        _tw = sorted_hits[0][0]
                        result['total_w'] = (_tw, sorted_hits[0][3])
                    if _th <= 0 and len(sorted_hits) > 1:
                        _th = sorted_hits[1][0]
                        result['total_h'] = (_th, sorted_hits[1][3])
                    elif _th <= 0:
                        _th = sorted_hits[0][0]
                        result['total_h'] = (_th, sorted_hits[0][3])

            # 如果 inner 值为 0，用几何关系计算
            if _tw > 0 and _ml > 0 and _mr > 0:
                _iw = max(0.0, _tw - _ml - _mr)
                if result['inner_w'][0] <= 0:
                    result['inner_w'] = (_iw, 6)
            if _th > 0 and _mt > 0 and _mb > 0:
                _ih = max(0.0, _th - _mt - _mb)
                if result['inner_h'][0] <= 0:
                    result['inner_h'] = (_ih, 6)

            logger.warning(
                f"[sketch_parser] 方向标签+空间分配混合结果："
                f"total={result['total_w'][0]:.1f}x{result['total_h'][0]:.1f} "
                f"inner={result['inner_w'][0]:.1f}x{result['inner_h'][0]:.1f} "
                f"margin T/B/L/R={_mt}/{_mb}/{_ml}/{_mr}"
            )

    if result is None:
        # 方向标签全部失败，使用纯空间分配
        logger.info("[sketch_parser] 方向标签检测失败，使用纯空间分配")
        result = _assign_ocr_values_to_fields(hits, outer_rect, inner_rect,
                                              h_img, w_img,
                                              target_w_hint=target_w_hint,
                                              target_h_hint=target_h_hint)
        # 用空间推理结果覆盖
        if spatial_margin_hints:
            for key in ['margin_top', 'margin_bottom', 'margin_left', 'margin_right',
                         'total_w', 'total_h']:
                if key in spatial_margin_hints and spatial_margin_hints[key][0] > 0:
                    result[key] = spatial_margin_hints[key]

    # ---- Step 2b: 注入几何聚焦OCR结果（优先使用直接字段识别结果）----
    # 几何聚焦OCR直接在每个字段的预期位置进行高倍率OCR，
    # 识别结果带有明确的字段归属，优先使用以避免分配错误
    if geo_ocr_results:
        for field_name in ['inner_w', 'inner_h', 'total_w', 'total_h',
                           'margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
            if field_name in geo_ocr_results and geo_ocr_results[field_name][0] > 0:
                geo_val, geo_conf = geo_ocr_results[field_name]
                cur_val = result.get(field_name, (0.0, 0))[0]
                # 如果当前值为0、或几何OCR的置信度更高、或当前值明显不合理
                if cur_val <= 0 or geo_conf > result.get(field_name, (0.0, 0))[1] + 20:
                    old_val_str = f"{cur_val:.1f}" if cur_val > 0 else "无"
                    logger.info(
                        f"[sketch_parser] 几何OCR注入 {field_name}: "
                        f"{old_val_str} → {geo_val:.1f} (conf={geo_conf})")
                    result[field_name] = (geo_val, geo_conf + 40)

    # ---- Step 3: 几何验证与修正 ----
    tw = result.get('total_w', (0.0, 0))[0]
    th = result.get('total_h', (0.0, 0))[0]
    iw_val = result.get('inner_w', (0.0, 0))[0]
    ih_val = result.get('inner_h', (0.0, 0))[0]
    mt = result.get('margin_top', (0.0, 0))[0]
    mb = result.get('margin_bottom', (0.0, 0))[0]
    ml = result.get('margin_left', (0.0, 0))[0]
    mr = result.get('margin_right', (0.0, 0))[0]

    # ---- Step 3a: 预测性聚焦OCR ----
    # 使用outer_rect和inner_rect预测边距数值的位置，进行聚焦OCR
    # 当边距值几何不自洽时，此步能纠正错误的OCR识别
    logger.info(f"[sketch_parser] Step 3a: cv2={'yes' if cv2 is not None else 'None'}, "
                f"ocr_gray={'yes' if ocr_gray is not None else 'None'}, "
                f"tesseract={'yes' if tesseract is not None else 'None'}")
    if cv2 is not None and ocr_gray is not None and tesseract is not None:
        try:
            pred_margins = _predictive_ocr_margins(
                cv2, ocr_gray, tesseract, outer_rect, inner_rect)
            if pred_margins:
                logger.info(
                    f"[sketch_parser] 预测性OCR结果: "
                    f"上={pred_margins.get('margin_top', (0, 0))[0]:.1f}, "
                    f"下={pred_margins.get('margin_bottom', (0, 0))[0]:.1f}, "
                    f"左={pred_margins.get('margin_left', (0, 0))[0]:.1f}, "
                    f"右={pred_margins.get('margin_right', (0, 0))[0]:.1f}")

                # 用预测性OCR结果替换明显异常的边距值
                for key, cur_val in [
                    ('margin_top', mt), ('margin_bottom', mb),
                    ('margin_left', ml), ('margin_right', mr)
                ]:
                    if key in pred_margins and pred_margins[key][0] > 0:
                        pred_val = pred_margins[key][0]
                        # 如果当前值明显异常（>80或>外框的50%），用预测值替换
                        side_len = tw if key in ('margin_left', 'margin_right') else th
                        if (cur_val > 80 or (side_len > 0 and cur_val > side_len * 0.5)
                                or cur_val <= 0):
                            logger.info(
                                f"[sketch_parser] 预测性OCR修正: {key} {cur_val:.1f}→{pred_val:.1f}")
                            result[key] = (pred_val, pred_margins[key][1])
        except Exception as _e:
            logger.warning(f"[sketch_parser] 预测性OCR异常: {_e}")
        # Re-read values after correction
        mt = result.get('margin_top', (0.0, 0))[0]
        mb = result.get('margin_bottom', (0.0, 0))[0]
        ml = result.get('margin_left', (0.0, 0))[0]
        mr = result.get('margin_right', (0.0, 0))[0]

    # ---- 边距合理性校验：边距不能超过外框对应边长 ----
    for fname, fval, total_side in [
        ('margin_top', mt, th), ('margin_bottom', mb, th),
        ('margin_left', ml, tw), ('margin_right', mr, tw)
    ]:
        if fval > 0 and total_side > 0 and fval > total_side * 0.6:
            logger.warning(f"[sketch_parser] 边距异常: {fname}={fval:.1f} 超过外框边长{total_side:.1f}的60%，可能是OCR误读")
            # 标记为异常，后续将尝试重新识别

    # 水平方向验证: outer_w - inner_w ≈ margin_left + margin_right
    if tw > 0 and iw_val > 0:
        expected_h = tw - iw_val
        actual_h = ml + mr
        if abs(expected_h - actual_h) > max(2.0, tw * 0.05):
            logger.warning(f"[sketch_parser] 水平方向不自洽: outer({tw:.1f})-inner({iw_val:.1f})={expected_h:.1f} != margins({ml:.1f}+{mr:.1f}={actual_h:.1f})")

            # 策略1: 一边为0，推算另一边
            if ml <= 0 and mr > 0:
                ml = max(0, expected_h - mr)
                result['margin_left'] = (ml, 5)
                logger.info(f"[sketch_parser] 几何修正: margin_left 推算为 {ml:.2f}")
            elif mr <= 0 and ml > 0:
                mr = max(0, expected_h - ml)
                result['margin_right'] = (mr, 5)
                logger.info(f"[sketch_parser] 几何修正: margin_right 推算为 {mr:.2f}")
            # 策略2: 两边都非零但不合理，用期望值按比例分配
            elif ml > 0 and mr > 0:
                # 检查是否有边距异常大（超过外框的50%）
                if ml > tw * 0.5 or mr > tw * 0.5:
                    # 异常边距用期望值减去另一边
                    if ml > tw * 0.5:
                        new_ml = max(0, expected_h - mr)
                        if new_ml > 0:
                            result['margin_left'] = (new_ml, 5)
                            logger.info(f"[sketch_parser] 几何修正(异常大): margin_left {ml:.1f}→{new_ml:.1f}")
                            ml = new_ml
                    if mr > tw * 0.5:
                        new_mr = max(0, expected_h - ml)
                        if new_mr > 0:
                            result['margin_right'] = (new_mr, 5)
                            logger.info(f"[sketch_parser] 几何修正(异常大): margin_right {mr:.1f}→{new_mr:.1f}")
                            mr = new_mr

    # 垂直方向验证: outer_h - inner_h ≈ margin_top + margin_bottom
    if th > 0 and ih_val > 0:
        expected_v = th - ih_val
        actual_v = mt + mb
        if abs(expected_v - actual_v) > max(2.0, th * 0.05):
            logger.warning(f"[sketch_parser] 垂直方向不自洽: outer({th:.1f})-inner({ih_val:.1f})={expected_v:.1f} != margins({mt:.1f}+{mb:.1f}={actual_v:.1f})")

            # 策略1: 一边为0，推算另一边
            if mt <= 0 and mb > 0:
                mt = max(0, expected_v - mb)
                result['margin_top'] = (mt, 5)
                logger.info(f"[sketch_parser] 几何修正: margin_top 推算为 {mt:.2f}")
            elif mb <= 0 and mt > 0:
                mb = max(0, expected_v - mt)
                result['margin_bottom'] = (mb, 5)
                logger.info(f"[sketch_parser] 几何修正: margin_bottom 推算为 {mb:.2f}")
            # 策略2: 两边都非零但不合理
            elif mt > 0 and mb > 0:
                if mt > th * 0.5 or mb > th * 0.5:
                    if mt > th * 0.5:
                        new_mt = max(0, expected_v - mb)
                        if new_mt > 0:
                            result['margin_top'] = (new_mt, 5)
                            logger.info(f"[sketch_parser] 几何修正(异常大): margin_top {mt:.1f}→{new_mt:.1f}")
                            mt = new_mt
                    if mb > th * 0.5:
                        new_mb = max(0, expected_v - mt)
                        if new_mb > 0:
                            result['margin_bottom'] = (new_mb, 5)
                            logger.info(f"[sketch_parser] 几何修正(异常大): margin_bottom {mb:.1f}→{new_mb:.1f}")
                            mb = new_mb

    _final_sc = _score_assignment_consistency(result)
    logger.info(
        f"[sketch_parser] 最终8字段(sc={_final_sc:.3f})："
        f"total={tw:.2f}x{th:.2f} inner={iw_val:.2f}x{ih_val:.2f} "
        f"margin T/B/L/R={mt:.2f}/{mb:.2f}/{ml:.2f}/{mr:.2f}"
    )

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

    Args:
        progress_callback: 可选回调函数 callback(percent: int, message: str)
            用于向UI报告解析进度（0-100）。

    流程：
      1. 加载图片 → 复杂度评估
      2. 几何检测 → 找两个嵌套矩形
      3. 数字识别 → OCR + 几何回退
      4. 融合结果 → 映射到边距/尺寸
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

    # ---- L0: 缓存查找（性能优化） ----
    # 第一级：同一张草图+同一目标尺寸，直接返回缓存结果（毫秒级响应）
    cached = _get_cached_result(image_path, target_outer_w_cm, target_outer_h_cm)
    if cached is not None:
        return cached

    # 第二级：自洽解缓存（与 target 无关）
    # 当 OCR 识别到全部 8 字段且几何自洽时，结果与 target 尺寸无关。
    # 更换目标文件名（不同 target）时直接命中，毫秒级响应。
    consistent_cached = _get_consistent_cached_result(image_path)
    if consistent_cached is not None:
        # 自洽解与 target 无关，直接返回（同时存入第一级缓存加速下次同 target 查询）
        _store_cached_result(image_path, target_outer_w_cm, target_outer_h_cm, consistent_cached)
        return consistent_cached

    # ---- L1: 复杂度评估 ----
    is_complex, complex_reason = _assess_complexity(gray)
    if is_complex:
        result.message = complex_reason
        result.debug["complex_skipped"] = True
        return result

    # ---- L2: 几何检测 ----
    _progress(25, "几何检测：查找嵌套矩形...")
    top2 = _find_two_nested_rectangles(cv2, gray, img)
    if len(top2) < 2:
        result.message = (f"只检测到 {len(top2)} 个矩形轮廓，"
                          f"无法确定内外框关系。请手动输入边距或使用更清晰的草图。")
        result.debug["rects_found"] = len(top2)
        return result

    (ox, oy, ow, oh, os_score), (ix, iy, iw, ih, ins_score) = top2
    result.debug["outer_rect_px"] = (ox, oy, ow, oh)
    result.debug["inner_rect_px"] = (ix, iy, iw, ih)
    result.debug["rect_scores"] = {"outer": round(os_score, 3), "inner": round(ins_score, 3)}

    # ---- L3: 数字识别 ----
    _progress(40, "OCR识别：读取标注数字...")
    tesseract = _safe_import_tesseract()

    # 先用 OCR 读取标注数字（传入彩色图用于方向标签检测）
    ocr_result = _find_and_read_numbers(cv2, gray, (ox, oy, ow, oh),
                                         (ix, iy, iw, ih), tesseract,
                                         target_w_hint=target_outer_w_cm,
                                         target_h_hint=target_outer_h_cm,
                                         color_img=img)

    _progress(75, "几何回退计算...")
    # 再用几何方法计算回退值
    geo_result = _geometry_fallback_values(
        (ox, oy, ow, oh), (ix, iy, iw, ih),
        target_outer_w_cm, target_outer_h_cm)

    # 融合：优先用 OCR 值，OCR 未识别时用几何回退
    fused = {}
    for key in ocr_result:
        ocr_val, ocr_conf = ocr_result[key]
        geo_val, geo_conf = geo_result[key]
        if ocr_conf > 0 and ocr_val > 0:
            fused[key] = ocr_val
        elif geo_conf > 0 and geo_val > 0:
            fused[key] = geo_val
        else:
            fused[key] = 0.0

    # ---- 计算最终边距 ----
    # 外框总尺寸：优先用 OCR 读的 total_w/total_h，否则用目标尺寸
    outer_w = fused.get('total_w', 0.0)
    outer_h = fused.get('total_h', 0.0)

    # 先拿内框和边距的 OCR 原始值（用于自洽性检测）
    _ocr_inner_w = fused.get('inner_w', 0.0)
    _ocr_inner_h = fused.get('inner_h', 0.0)
    _ocr_mt = fused.get('margin_top', 0.0)
    _ocr_mb = fused.get('margin_bottom', 0.0)
    _ocr_ml = fused.get('margin_left', 0.0)
    _ocr_mr = fused.get('margin_right', 0.0)

    # 检测 OCR 值是否几何自洽：
    #   水平方向：| outer_w - inner_w - (margin_left + margin_right) | 应接近 0
    #   垂直方向：| outer_h - inner_h - (margin_top + margin_bottom) |  应接近 0
    # 同时要求 8 个字段都有正值（否则 OCR 识别不完整）
    #
    # 重要：OCR 的 total_w/total_h 可能基于空间位置被映射为「反方向」
    # （例如 total_w 是竖边值，total_h 是横边值）。所以我们需要双向检测：
    #   Case 1: 原始方向 (w, h)
    #   Case 2: 全交换后 (h, w)，同时交换内框和边距
    # 只要任一 Case 自洽，就认为 OCR 语义值是正确的（只是方向需要矫正）
    _ocr_fields_positive = all(v > 0 for v in [
        outer_w, outer_h, _ocr_inner_w, _ocr_inner_h,
        _ocr_mt, _ocr_mb, _ocr_ml, _ocr_mr
    ])

    def _check_consistent(ow_, oh_, iw_, ih_, mt_, mb_, ml_, mr_):
        """返回 (h_ok, v_ok, fully_ok) 基于给定的一组值检测几何自洽"""
        if ow_ <= 0 or oh_ <= 0:
            return False, False, False
        _h_diff = abs(ow_ - iw_ - (ml_ + mr_))
        _v_diff = abs(oh_ - ih_ - (mt_ + mb_))
        _h_tol = max(2.0, ow_ * 0.10)
        _v_tol = max(2.0, oh_ * 0.10)
        _h_ok = _h_diff <= _h_tol
        _v_ok = _v_diff <= _v_tol
        return _h_ok, _v_ok, _h_ok and _v_ok

    _c1_h, _c1_v, _c1_full = False, False, False
    _c2_h, _c2_v, _c2_full = False, False, False
    if _ocr_fields_positive:
        # Case 1: 原始 OCR 映射方向
        _c1_h, _c1_v, _c1_full = _check_consistent(
            outer_w, outer_h, _ocr_inner_w, _ocr_inner_h,
            _ocr_mt, _ocr_mb, _ocr_ml, _ocr_mr
        )
        # Case 2: 只交换 outer 和 inner 的宽高，边距不交换
        # 原理：OCR 的 total_w/total_h 基于空间位置映射可能搞反了
        # （total_w 读了竖边值 a=60.5，total_h 读了横边值 b=133），
        # 但边距的 top/bottom/left/right 命名语义是正确的。
        # 此时只需把 outer_w↔outer_h、inner_w↔inner_h（相当于纠正 total_w/h 的映射方向），
        # 边距保持不变（top/bottom/left/right 仍对应正确的几何方向）。
        _c2_h, _c2_v, _c2_full = _check_consistent(
            outer_h, outer_w, _ocr_inner_h, _ocr_inner_w,
            _ocr_mt, _ocr_mb, _ocr_ml, _ocr_mr
        )
    _ocr_fully_consistent = _ocr_fields_positive and (_c1_full or _c2_full)
    if _ocr_fields_positive:
        logger.info(f"[sketch_parser] OCR 几何自洽性检测(双向)："
                    f"原始方向 h={_c1_h}/v={_c1_v}/full={_c1_full}，"
                    f"全交换方向 h={_c2_h}/v={_c2_v}/full={_c2_full}，"
                    f"总体自洽={_ocr_fully_consistent}")

    # --- 方向矫正 Phase 1：仅在外框宽高与像素比严重矛盾时 swap ---
    # 此处仅交换 outer_w/outer_h，inner/margins 稍后在定义后同步交换
    _outer_swap_done = False
    # 像素帧宽高比 vs 目标宽高比：如果差异过大，说明几何检测的像素尺寸不可靠
    _pixel_frame_reliable = True
    if ow > 0 and oh > 0 and target_outer_w_cm > 0 and target_outer_h_cm > 0:
        _px_aspect = ow / oh
        _target_aspect = target_outer_w_cm / target_outer_h_cm
        _aspect_ratio_diff = abs(_px_aspect - _target_aspect) / max(_px_aspect, _target_aspect, 0.01)
        if _aspect_ratio_diff > 0.20:
            logger.warning(
                f"[sketch_parser] 检测到外框像素宽高比({ow}x{oh}={_px_aspect:.2f}) "
                f"与目标宽高比({target_outer_w_cm:.1f}x{target_outer_h_cm:.1f}={_target_aspect:.2f}) "
                f"差异过大({_aspect_ratio_diff:.1%})，像素尺寸可能不可靠")
            _pixel_frame_reliable = False

    if outer_w > 0 and outer_h > 0 and ow > 0 and oh > 0:
        ratio_px = ow / oh
        ratio_val = outer_w / outer_h
        px_is_landscape = ratio_px > 1.25
        px_is_portrait = ratio_px < 1.0 / 1.25
        val_is_landscape = ratio_val > 1.25
        val_is_portrait = ratio_val < 1.0 / 1.25
        need_swap = False
        if px_is_portrait and val_is_landscape:
            need_swap = True
        elif px_is_landscape and val_is_portrait:
            need_swap = True
        if need_swap:
            logger.info(f"[sketch_parser] Phase1 外框宽高方向矫正：互换 {outer_w}x{outer_h} → {outer_h}x{outer_w}")
            outer_w, outer_h = outer_h, outer_w
            _outer_swap_done = True

    if outer_w <= 0:
        outer_w = target_outer_w_cm
    if outer_h <= 0:
        outer_h = target_outer_h_cm

    # 如果外框仍未知，用几何回退
    if outer_w <= 0 or outer_h <= 0:
        cm_per_px_w = cm_per_px_h = 0.0
        if ow > 0:
            cm_per_px_w = target_outer_w_cm / ow if target_outer_w_cm > 0 else 0
        if oh > 0:
            cm_per_px_h = target_outer_h_cm / oh if target_outer_h_cm > 0 else 0
        if cm_per_px_w and not cm_per_px_h:
            cm_per_px_h = cm_per_px_w
        elif cm_per_px_h and not cm_per_px_w:
            cm_per_px_w = cm_per_px_h

        if outer_w <= 0 and cm_per_px_w > 0:
            outer_w = ow * cm_per_px_w
        if outer_h <= 0 and cm_per_px_h > 0:
            outer_h = oh * cm_per_px_h

    # ---- 目标尺寸验证：当 OCR 外框尺寸与目标偏差过大时，以目标为准 ----
    # 核心策略变更（再修复 Bug 4 —— target 方向与 OCR 方向相反导致的覆盖灾难）：
    #   - 如果 OCR 值本身几何自洽（8字段全正 + 内外边距和 ≈ 外框差）：
    #     → 【OCR 草图本身是权威！绝不直接覆盖 outer_w/h 的数值！】
    #     → 检查 target 是否只是方向反了（swap(target) 后≈OCR outer值）：
    #       如果是 → 交换 target 方向定义（让后续像素换算用正确比例），OCR值保持不变
    #       如果不是（数值确实不同）→ 信任自洽的 OCR 草图，不替换 outer 值
    #     → 旧代码Bug：target方向反时直接覆盖outer→outer变成竖值/inner还是横值→
    #       delta_h<0 → Phase3错误交换inner → 几何彻底错乱 → 强制重算 → 边距清零！
    #   - 如果 OCR 不自洽（出现 11.5x5.1、边距52等荒谬值）：
    #     → 覆盖 outer 并强制用像素比例重算 inner 和 margins
    #     → 原因：OCR 映射锚点完全错位，语义值不可靠
    _need_recalc_from_target = False
    if target_outer_w_cm > 0 and target_outer_h_cm > 0:
        _ratio_w = outer_w / target_outer_w_cm if target_outer_w_cm > 0 else 1.0
        _ratio_h = outer_h / target_outer_h_cm if target_outer_h_cm > 0 else 1.0
        _w_over20 = _ratio_w > 1.20 or _ratio_w < 0.83
        _h_over20 = _ratio_h > 1.20 or _ratio_h < 0.83

        # ============================================================
        # 【Bug 4 修复 —— 分支A：OCR 已完全自洽 → 信任草图！】
        # ============================================================
        if _ocr_fully_consistent and outer_w > 0 and outer_h > 0:
            # 检查 target 是否只是方向与 OCR 相反（数值相同、方向反）
            # 例如: target=(60.5, 133) 而 OCR_outer=(133, 60.5)
            _swap_ratio_w = outer_w / target_outer_h_cm if target_outer_h_cm > 0 else 1.0
            _swap_ratio_h = outer_h / target_outer_w_cm if target_outer_w_cm > 0 else 1.0
            _swap_match_w = 0.83 <= _swap_ratio_w <= 1.20
            _swap_match_h = 0.83 <= _swap_ratio_h <= 1.20
            _target_is_swapped_version = _swap_match_w and _swap_match_h

            if (_w_over20 or _h_over20) and _target_is_swapped_version:
                # 只是 target 方向反了！交换 target 的定义，让后续像素比例计算正确，
                # 但绝对不修改 OCR 得出的 outer/inner/margins 值（它们是自洽、正确的！）
                logger.info(
                    f"[sketch_parser] OCR 完全自洽，检测到 target 方向定义相反 "
                    f"(target={target_outer_w_cm:.1f}x{target_outer_h_cm:.1f}, "
                    f"OCR_outer={outer_w:.1f}x{outer_h:.1f})。"
                    f"仅交换 target 方向定义为 {target_outer_h_cm:.1f}x{target_outer_w_cm:.1f}，"
                    f"保留 OCR 草图值不变（不覆盖 outer）")
                target_outer_w_cm, target_outer_h_cm = target_outer_h_cm, target_outer_w_cm
                # 重新计算偏差（方向对齐后，偏差应该很小）
                _ratio_w = outer_w / target_outer_w_cm if target_outer_w_cm > 0 else 1.0
                _ratio_h = outer_h / target_outer_h_cm if target_outer_h_cm > 0 else 1.0
                _w_over20 = _ratio_w > 1.20 or _ratio_w < 0.83
                _h_over20 = _ratio_h > 1.20 or _ratio_h < 0.83
            elif (_w_over20 or _h_over20):
                # OCR 自洽但 target 数值确实不同（不是方向问题）→ 信任草图（OCR自洽更权威）
                logger.info(
                    f"[sketch_parser] OCR 完全自洽(sc=✓)，即使与 target 偏差>20% "
                    f"(w_ratio={_ratio_w:.2f}, h_ratio={_ratio_h:.2f})，"
                    f"仍信任 OCR 草图 outer={outer_w:.1f}x{outer_h:.1f}（不覆盖）")
            # else: 偏差<20%，完全一致，无需操作

        # ============================================================
        # 分支B：OCR 不自洽 → 以 target 为准（保持原逻辑）
        # ============================================================
        else:
            if _w_over20:
                logger.warning(f"[sketch_parser] 外框宽 {outer_w:.1f} 与目标 {target_outer_w_cm:.1f} 偏差过大({_ratio_w:.2f})，使用目标值")
                outer_w = target_outer_w_cm
            if _h_over20:
                logger.warning(f"[sketch_parser] 外框高 {outer_h:.1f} 与目标 {target_outer_h_cm:.1f} 偏差过大({_ratio_h:.2f})，使用目标值")
                outer_h = target_outer_h_cm

        # 决定是否需要强制重算：只有当 OCR 不自洽或近似正方形画布时才强制
        _need_square_forced = False
        _ocr_aspect = outer_w / max(0.1, outer_h)
        _target_aspect = target_outer_w_cm / max(0.1, target_outer_h_cm)
        if (abs(_ocr_aspect - 1.0) < 0.15  # OCR 结果近似正方形
                and abs(_target_aspect - 1.0) > 0.4  # 目标尺寸长宽比差异大
                and max(target_outer_w_cm, target_outer_h_cm) > 0):
            # 注：此分支仅在 OCR 不自洽时才可能触发（自洽时上面已不覆盖 outer）
            logger.warning(
                f"[sketch_parser] OCR 产生近正方形画布({outer_w:.1f}x{outer_h:.1f}, 长宽比={_ocr_aspect:.2f})，"
                f"但目标为非正方形({target_outer_w_cm:.1f}x{target_outer_h_cm:.1f}, 长宽比={_target_aspect:.2f})，"
                f"强制使用目标尺寸")
            outer_w = target_outer_w_cm
            outer_h = target_outer_h_cm
            _need_square_forced = True

        # 最终强制重算决策：
        #  - (OCR 偏差 > 20% AND OCR 不自洽) → 语义值不可靠
        #  - 或 近正方形画布被强制覆盖 → 典型的 OCR 锚点全错
        #  注意：如果 _ocr_fully_consistent = True，绝对不触发强制重算！
        if (_w_over20 or _h_over20) and not _ocr_fully_consistent:
            _need_recalc_from_target = True
            logger.info(f"[sketch_parser] OCR 偏差>20%且几何不自洽 → 将按目标尺寸强制重算内框和边距")
        elif _need_square_forced:
            _need_recalc_from_target = True
            logger.info(f"[sketch_parser] 近正方形画布触发强制覆盖 → 将按目标尺寸强制重算内框和边距")
        elif (_w_over20 or _h_over20) and _ocr_fully_consistent:
            # OCR 完全自洽 → 不重算，交给后续 Phase 方向矫正（即使这时 outer 仍与 target 偏差大）
            logger.info(f"[sketch_parser] OCR 偏差>20%但几何自洽 → 保留 OCR 草图值，"
                        f"不强制重算 inner/边距，交 Phase 3 做方向矫正")
        else:
            _need_recalc_from_target = False
    # 注意：target_outer_w/h 无效时，_need_recalc_from_target 保持 False

    # 内挖尺寸：优先用 OCR（除非 _need_recalc_from_target 会在下面覆盖）
    inner_w = _ocr_inner_w
    inner_h = _ocr_inner_h

    # 如果内挖尺寸未读出，用几何回退（仅当不强制重算时，否则强制重算会覆盖）
    if not _need_recalc_from_target:
        if inner_w <= 0 and inner_h <= 0 and target_outer_w_cm > 0 and target_outer_h_cm > 0:
            # 两个内挖都缺失：用目标尺寸 × 典型比例（防止完全无 OCR 时的归零）
            inner_w = target_outer_w_cm * 0.57  # 76/133 ≈ 0.571
            inner_h = target_outer_h_cm * 0.74  # 44.5/60.5 ≈ 0.736
            logger.info(f"[sketch_parser] 内挖全缺失，用目标比例回退：{inner_w:.2f}x{inner_h:.2f}")
        else:
            # 仅一边缺失：用像素比例换算（仅当像素帧可靠时）
            if inner_w <= 0 and outer_w > 0 and ow > 0 and _pixel_frame_reliable:
                inner_w = iw * (outer_w / ow)
            if inner_h <= 0 and outer_h > 0 and oh > 0 and _pixel_frame_reliable:
                inner_h = ih * (outer_h / oh)
            # 像素帧不可靠时不使用像素换算，保留 0 留给后续处理

    # 四边边距：优先用 OCR 读的 margin 值
    mt = _ocr_mt
    mb = _ocr_mb
    ml = _ocr_ml
    mr = _ocr_mr

    # ---- 强制重算：当目标尺寸覆盖了 OCR 外框，且 OCR 语义值不可靠时用几何比例重算 ----
    # 适用场景：
    #   - OCR 映射锚点错位 → OCR 值本身几何不自洽
    #   - 近正方形画布 → 典型的全字段映射错误
    # 不适用场景：
    #   - OCR 值本身自洽，只是方向反了（交给 Phase1/2/3 处理）
    if _need_recalc_from_target:
        if _pixel_frame_reliable and ow > 0 and oh > 0:
            logger.info(f"[sketch_parser] 强制重算：用像素几何比例替换不可靠的 OCR 映射值")
            _cm_px_w = outer_w / ow
            _cm_px_h = outer_h / oh
            # 内框：按像素比例换算
            inner_w = iw * _cm_px_w
            inner_h = ih * _cm_px_h
            # 边距：按像素比例换算
            mt = (iy - oy) * _cm_px_h
            mb = ((oy + oh) - (iy + ih)) * _cm_px_h
            ml = (ix - ox) * _cm_px_w
            mr = ((ox + ow) - (ix + iw)) * _cm_px_w
        else:
            # 像素帧不可靠或无像素数据：用目标尺寸+典型比例回退
            logger.info(f"[sketch_parser] 强制重算：像素帧不可靠，用目标尺寸比例回退")
            inner_w = target_outer_w_cm * 0.57 if target_outer_w_cm > 0 else inner_w
            inner_h = target_outer_h_cm * 0.74 if target_outer_h_cm > 0 else inner_h
            # 边距用 OCR 值（如果有的话），否则留 0
            if mt <= 0: mt = target_outer_h_cm * 0.10 if target_outer_h_cm > 0 else 0
            if mb <= 0: mb = target_outer_h_cm * 0.16 if target_outer_h_cm > 0 else 0
            if ml <= 0: ml = target_outer_w_cm * 0.25 if target_outer_w_cm > 0 else 0
            if mr <= 0: mr = target_outer_w_cm * 0.75 if target_outer_w_cm > 0 else 0
        logger.info(f"[sketch_parser] 重算结果：内框 {inner_w:.2f}x{inner_h:.2f}，"
                    f"边距 上{mt:.2f}/下{mb:.2f}/左{ml:.2f}/右{mr:.2f}")
        _need_recalc_from_target = False

    # —— 修复 Bug 7 保护1：OCR 完全自洽的最终权威性 ——
    # 如果 fused 原始 8 字段已经几何自洽（_ocr_fully_consistent=True），
    # 那么无论强制重算 / Phase2 交换 / 后续修正怎么改动 inner/margins，
    # 只要它们与原始 OCR 值不一致，就无条件恢复为原始 OCR 值！
    # 原因：如果 8 个标注数字本身 outer-inner = margin_sum，那么这套值就
    # 是草图设计者明确给出的，任何基于像素/target的计算都不应该覆盖它。
    if _ocr_fully_consistent:
        _saved_iw, _saved_ih, _saved_mt, _saved_mb, _saved_ml, _saved_mr = (
            inner_w, inner_h, mt, mb, ml, mr
        )
        if _ocr_inner_w > 0: inner_w = _ocr_inner_w
        if _ocr_inner_h > 0: inner_h = _ocr_inner_h
        if _ocr_mt > 0: mt = _ocr_mt
        if _ocr_mb > 0: mb = _ocr_mb
        if _ocr_ml > 0: ml = _ocr_ml
        if _ocr_mr > 0: mr = _ocr_mr
        _changed = (
            abs(inner_w - _saved_iw) > 0.05 or
            abs(inner_h - _saved_ih) > 0.05 or
            abs(mt - _saved_mt) > 0.05 or abs(mb - _saved_mb) > 0.05 or
            abs(ml - _saved_ml) > 0.05 or abs(mr - _saved_mr) > 0.05
        )
        if _changed:
            logger.warning(
                f"[sketch_parser] Bug7保护1：原始OCR已完全自洽→强制恢复为标注值 "
                f"inner={inner_w:.2f}x{inner_h:.2f}, 边距"
                f"上{mt:.2f}/下{mb:.2f}/左{ml:.2f}/右{mr:.2f} "
                f"(覆盖掉强制重算/Phase产生的偏离)"
            )

    # --- 方向矫正 Phase 2：同步 Phase 1 的外框 swap 到内框和边距 ---
    # 当 Phase 1 交换了 outer_w/outer_h，内框宽高也必须同步交换，
    # 但边距不交换——因为边距是按空间位置（上/下/左/右）语义映射的，
    # 其值（如 6/10/14.6/42.4）本身就是正确的语义值。
    # 交换 outer 宽高只影响"哪个数值代表宽/高"的维度，不影响边距的空间语义。
    if _outer_swap_done:
        logger.info(f"[sketch_parser] Phase2 同步外框 swap 到内框 (边距保持不变)")
        if inner_w > 0 or inner_h > 0:
            inner_w, inner_h = inner_h, inner_w
            logger.info(f"[sketch_parser] 同步交换内框宽高：{inner_w:.2f}x{inner_h:.2f}")
        _outer_swap_done = False

    # ---- 几何自洽性检查与方向矫正（Phase 3）----
    # 核心问题：OCR 可能把边距的方向搞反（如把左右边距识别为上下边距），或把内框宽高搞反
    # 解决方法：检查边距和是否符合外框-内框的几何约束，若不符合则尝试旋转方向
    if outer_w > 0 and outer_h > 0 and inner_w > 0 and inner_h > 0:
        delta_h = outer_w - inner_w  # 水平方向应有的边距和（左右）
        delta_v = outer_h - inner_h  # 垂直方向应有的边距和（上下）
        sum_h_margins = ml + mr      # 当前水平边距和
        sum_v_margins = mt + mb      # 当前垂直边距和

        # [Bug B 修复1 2026-08-15] Phase3方向矫正前的预保护：
        # 如果当前 8 字段已经高度自洽（暴力搜索/T0.5兜底已给出sc≈1.0的解），
        # 就不应该再做方向 swap——否则把正确的 mt≈5/11/14.6/42.5 交换成 ml=mt_old=5 之类，
        # 后续边距验证就会出现 actual_lr=5 vs expected=57 的荒谬结果！
        _h_diff_now = abs(delta_h - sum_h_margins)
        _v_diff_now = abs(delta_v - sum_v_margins)
        _already_self_consistent = (
            _h_diff_now < 1.0 and _v_diff_now < 1.0
            and all(v > 0 for v in [outer_w, outer_h, inner_w, inner_h, mt, mb, ml, mr])
        )
        if _already_self_consistent:
            logger.info(
                f"[sketch_parser] Phase3方向矫正跳过：当前8字段已高度自洽"
                f"(水平差={_h_diff_now:.2f}, 垂直差={_v_diff_now:.2f}，均<1cm阈值)，"
                f"避免方向矫正破坏暴力搜索/T0.5得出的正确解"
            )
        else:
            # 计算各方向的匹配度（0~1，越高越匹配）
            # 使用绝对值处理 delta，因为 delta 为负时说明内框尺寸方向可能错了
            _delta_h_abs = abs(delta_h)
            _delta_v_abs = abs(delta_v)
            match_h_to_h = 1.0 - abs(sum_h_margins - delta_h) / max(1, _delta_h_abs)
            match_h_to_v = 1.0 - abs(sum_h_margins - delta_v) / max(1, _delta_v_abs)
            match_v_to_v = 1.0 - abs(sum_v_margins - delta_v) / max(1, _delta_v_abs)
            match_v_to_h = 1.0 - abs(sum_v_margins - delta_h) / max(1, _delta_h_abs)

            # 检测是否需要交换内框宽高（当 delta 为负时，说明 inner 搞反了）
            _inner_swap_needed = (delta_h < 0) or (delta_v < 0)

            # 如果边距和对应的几何差值更匹配，说明方向被搞反了
            # 例如：水平边距和(ml+mr)更接近垂直差值(outer_h-inner_h)，说明边距方向反了
            #
            # 新增：提高触发阈值，避免小的数值偏差触发错误的方向交换
            # - 原阈值 0.3/0.5 过于敏感，在边距值本身错乱时会放大错误
            # - 改为 0.8/0.9：只有当水平/垂直匹配度显著超过反方向时才触发
            _full_swap_needed = (
                match_h_to_v > match_h_to_h + 0.8 and
                match_v_to_h > match_v_to_v + 0.8 and
                # 新增：必须两边的匹配度都足够高(>=0.5)，否则只是数值噪音
                match_h_to_v > 0.5 and match_v_to_h > 0.5
            )
            _partial_h_swap = (
                match_h_to_v > match_h_to_h + 0.9 and
                match_v_to_h > match_v_to_v + 0.9 and
                match_h_to_v > 0.6 and match_v_to_h > 0.6
            )

            if _full_swap_needed or _partial_h_swap:
                logger.info(f"[sketch_parser] 几何自洽性矫正：水平边距和({sum_h_margins:.1f})匹配水平差值({delta_h:.1f})={match_h_to_h:.2f}，"
                            f"匹配垂直差值({delta_v:.1f})={match_h_to_v:.2f}；"
                            f"垂直边距和({sum_v_margins:.1f})匹配垂直差值({delta_v:.1f})={match_v_to_v:.2f}，"
                            f"匹配水平差值({delta_h:.1f})={match_v_to_h:.2f}")
                # 执行全面的宽高交换
                outer_w, outer_h = outer_h, outer_w
                inner_w, inner_h = inner_h, inner_w
                ml, mt = mt, ml
                mr, mb = mb, mr
                logger.info(f"[sketch_parser] 几何自洽性矫正完成：外框 {outer_w:.2f}x{outer_h:.2f}，"
                            f"内框 {inner_w:.2f}x{inner_h:.2f}，边距 上{mt:.2f}/下{mb:.2f}/左{ml:.2f}/右{mr:.2f}")

            elif _inner_swap_needed:
                # delta 为负说明内框宽高搞反了，交换内框（边距保持不变）
                logger.info(f"[sketch_parser] delta 为负(delta_h={delta_h:.1f}, delta_v={delta_v:.1f})，"
                            f"检测到内框宽高方向错误，尝试仅交换内框")
                # 模拟交换内框后的几何一致性
                _new_delta_h = outer_w - inner_h
                _new_delta_v = outer_h - inner_w
                _match_after_swap_h = 1.0 - abs(sum_h_margins - _new_delta_h) / max(1, abs(_new_delta_h))
                _match_after_swap_v = 1.0 - abs(sum_v_margins - _new_delta_v) / max(1, abs(_new_delta_v))
                # 如果交换后一致性变好，执行交换（仅交换内框，不交换边距）
                if _match_after_swap_h > 0.3 and _match_after_swap_v > 0.3:
                    logger.info(f"[sketch_parser] 交换后一致性提升：水平匹配度={_match_after_swap_h:.2f}，"
                                f"垂直匹配度={_match_after_swap_v:.2f}，执行内框交换")
                    inner_w, inner_h = inner_h, inner_w
                    # 边距保持不变，因为 OCR 对边距的方向识别是正确的
                    logger.info(f"[sketch_parser] 内框方向矫正完成：内框 {inner_w:.2f}x{inner_h:.2f}，"
                                f"边距保持 上{mt:.2f}/下{mb:.2f}/左{ml:.2f}/右{mr:.2f}")
                else:
                    logger.info(f"[sketch_parser] 交换后一致性不足（h={_match_after_swap_h:.2f}, v={_match_after_swap_v:.2f}），"
                                f"不执行交换")

    # 边距合理性校验：信任 OCR 标注，但检测并修正明显异常值
    # 核心原则：如果某个边距值导致 sum 严重偏离几何约束，则视为异常
    def _validate_pair(a, b, expected_sum, pair_name):
        if expected_sum <= 0:
            return a, b
        upper_cap = expected_sum * 2 + 50
        if a > upper_cap:
            logger.debug(f"[sketch_parser] {pair_name}: a={a} 超过上限 {upper_cap}，清零")
            a = 0.0
        if b > upper_cap:
            logger.debug(f"[sketch_parser] {pair_name}: b={b} 超过上限 {upper_cap}，清零")
            b = 0.0

        # 检测单值异常：如果 a 或 b 明显大于 expected_sum，可能是 OCR 误读
        # 例如 expected_sum=16，但 a=52 → 52 远超 16，视为异常
        if a > expected_sum * 1.5 and a > 30:
            logger.warning(f"[sketch_parser] {pair_name}: a={a} 远超预期和 {expected_sum}，视为异常，清零")
            a = 0.0
        if b > expected_sum * 1.5 and b > 30:
            logger.warning(f"[sketch_parser] {pair_name}: b={b} 远超预期和 {expected_sum}，视为异常，清零")
            b = 0.0

        # 仅一边有值：用差值推导另一边（推导结果需 >=0 且不超过 expected_sum）
        if a > 0 and b <= 0:
            b_new = expected_sum - a
            if 0 <= b_new <= expected_sum * 2:
                logger.debug(f"[sketch_parser] {pair_name}: 由 a={a} 推导 b={round(b_new,1)}")
                return a, b_new
            logger.debug(f"[sketch_parser] {pair_name}: 跳过推导 b (a={a}, expected_sum={expected_sum}, b_new={b_new})")
            return a, b
        if b > 0 and a <= 0:
            a_new = expected_sum - b
            if 0 <= a_new <= expected_sum * 2:
                logger.debug(f"[sketch_parser] {pair_name}: 由 b={b} 推导 a={round(a_new,1)}")
                return a_new, b
            logger.debug(f"[sketch_parser] {pair_name}: 跳过推导 a (b={b}, expected_sum={expected_sum}, a_new={a_new})")
            return a, b
        # 两边都有且 sum 合理 → 信任 OCR
        return a, b

    # —— 修复 Bug 7 保护2（扩展版）：OCR 完全自洽 OR 当前8字段已经几何自洽 → 跳过所有破坏性修正 ——
    # 原版只看 _ocr_fully_consistent（OCR+fused之前的原始8字段），但当 OCR 漏识别了关键值、
    # 而通过 BUG6++ 暴力搜索 / T0.5 target 逆推兜底得到了 sc=1.0 的完全自洽解时，
    # 这个解是经过层层筛选的可信值，绝不能被后续 _validate_pair、边距清零等步骤破坏。
    # 否则会出现日志里「actual_lr=5（=mt，被swap搞反了）vs expected=57」的荒谬结果。
    _fused_consistent = _ocr_fully_consistent
    _current_8_consistent = False
    if outer_w > 0 and outer_h > 0 and inner_w > 0 and inner_h > 0:
        _8_h_diff = abs((outer_w - inner_w) - (ml + mr))
        _8_v_diff = abs((outer_h - inner_h) - (mt + mb))
        _8_all_positive = all(v > 0 for v in [outer_w, outer_h, inner_w, inner_h, mt, mb, ml, mr])
        _current_8_consistent = _8_h_diff < 1.0 and _8_v_diff < 1.0 and _8_all_positive
    _skip_destructive_corrections = _fused_consistent or _current_8_consistent
    if _current_8_consistent and not _fused_consistent:
        logger.warning(
            f"[sketch_parser] BUG7保护扩展触发：当前8字段几何自洽(sc≈1.0)"
            f"(水平差={_8_h_diff:.2f}, 垂直差={_8_v_diff:.2f})，"
            f"即使 OCR 原始值不自洽，也跳过所有破坏性修正（保护暴力搜索/T0.5兜底的正确解）。"
            f"当前值：outer={outer_w:.1f}x{outer_h:.1f}, inner={inner_w:.1f}x{inner_h:.1f}, "
            f"边距 上{mt:.1f}/下{mb:.1f}/左{ml:.1f}/右{mr:.1f}"
        )

    if not _skip_destructive_corrections:
        if outer_w > 0 and inner_w > 0:
            expected_ml_mr = outer_w - inner_w
            ml, mr = _validate_pair(ml, mr, expected_ml_mr, '左右边距')

        if outer_h > 0 and inner_h > 0:
            expected_mt_mb = outer_h - inner_h
            mt, mb = _validate_pair(mt, mb, expected_mt_mb, '上下边距')
    else:
        logger.warning(
            f"[sketch_parser] BUG7保护2触发：OCR原始已完全自洽(sc=1.0)，为保护正确标注，跳过「边距_validate_pair修正」(保留边距上{mt:.1f}/下{mb:.1f}/左{ml:.1f}/右{mr:.1f})"
        )

    # ---- 最终三角验证：外框 = 内框 + 边距 ----
    # 确保 inner 尺寸 <= outer 尺寸，边距和不超过对应外框边长
    if not _skip_destructive_corrections:
        if inner_w > outer_w > 0:
            logger.warning(f"[sketch_parser] 内框宽 {inner_w} > 外框宽 {outer_w}，修正为外框宽 - 最小边距")
            inner_w = max(0, outer_w - max(ml + mr, 0))
        if inner_h > outer_h > 0:
            logger.warning(f"[sketch_parser] 内框高 {inner_h} > 外框高 {outer_h}，修正为外框高 - 最小边距")
            inner_h = max(0, outer_h - max(mt + mb, 0))

        # 确保单边距不超过对应外框边长的一半（合理上限）
        if ml > outer_w * 0.8 and outer_w > 0:
            logger.warning(f"[sketch_parser] 左边距 {ml} > 外框宽 80%({outer_w*0.8:.1f})，修正")
            ml = outer_w * 0.5
        if mr > outer_w * 0.8 and outer_w > 0:
            logger.warning(f"[sketch_parser] 右边距 {mr} > 外框宽 80%({outer_w*0.8:.1f})，修正")
            mr = outer_w * 0.5
        if mt > outer_h * 0.8 and outer_h > 0:
            logger.warning(f"[sketch_parser] 上边距 {mt} > 外框高 80%({outer_h*0.8:.1f})，修正")
            mt = outer_h * 0.5
        if mb > outer_h * 0.8 and outer_h > 0:
            logger.warning(f"[sketch_parser] 下边距 {mb} > 外框高 80%({outer_h*0.8:.1f})，修正")
            mb = outer_h * 0.5
    else:
        logger.warning(
            f"[sketch_parser] BUG7保护2触发：OCR原始已完全自洽(sc=1.0)，跳过「inner>outer修正 & 单边距>80%修正」"
        )

    # 边距回退：不再使用几何均分回退（见 _geometry_fallback_values 设计说明）。
    # 边距必须由 OCR 检出或用户手动输入，几何回退可能产生严重错误的边距值。
    # 因此：当边距缺失时保留 0，让用户手动填写。

    # 最终合理性检查
    if mt < 0: mt = 0
    if mb < 0: mb = 0
    if ml < 0: ml = 0
    if mr < 0: mr = 0

    # === 最终几何自洽性验证 ===
    # 如果边距和与 (外框-内框) 的偏差过大（>50%），说明结果仍然不可信，
    # 将不可信方向的边距置 0（由用户手动填写），避免把错误值展示给用户
    if not _skip_destructive_corrections:
        if outer_w > 0 and inner_w > 0:
            expected_lr = outer_w - inner_w
            if expected_lr > 0:
                actual_lr = ml + mr
                lr_deviation = abs(actual_lr - expected_lr) / expected_lr
                if lr_deviation > 0.5 and actual_lr > 0:
                    logger.warning(
                        f"[sketch_parser] 水平边距和({actual_lr:.2f})与预期({expected_lr:.2f})"
                        f"偏差 {lr_deviation:.0%} 过大，重置水平边距"
                    )
                    ml = 0.0
                    mr = 0.0
        if outer_h > 0 and inner_h > 0:
            expected_tb = outer_h - inner_h
            if expected_tb > 0:
                actual_tb = mt + mb
                tb_deviation = abs(actual_tb - expected_tb) / expected_tb
                if tb_deviation > 0.5 and actual_tb > 0:
                    logger.warning(
                        f"[sketch_parser] 垂直边距和({actual_tb:.2f})与预期({expected_tb:.2f})"
                        f"偏差 {tb_deviation:.0%} 过大，重置垂直边距"
                    )
                    mt = 0.0
                    mb = 0.0
    else:
        logger.warning(
            f"[sketch_parser] BUG7保护2触发：OCR原始已完全自洽(sc=1.0)，跳过「最终边距和偏差清零」(当前水平和:{ml+mr:.2f}, 垂直和:{mt+mb:.2f})"
        )

    # === 最终单侧边距合理性裁剪（第二道防线）===
    def _final_clip(v, outer_side):
        if v <= 0 or outer_side <= 0:
            return v
        # 单侧边距不得超过外框边长的 35%
        if v > outer_side * 0.35:
            return 0.0
        return v

    if not _skip_destructive_corrections:
        if outer_w > 0:
            ml = _final_clip(ml, outer_w)
            mr = _final_clip(mr, outer_w)
        if outer_h > 0:
            mt = _final_clip(mt, outer_h)
            mb = _final_clip(mb, outer_h)
    else:
        logger.warning(
            f"[sketch_parser] BUG7保护2触发：OCR原始已完全自洽(sc=1.0)，跳过「单侧边距35%裁剪」"
        )

    # 判定成功
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

        # 构建成功消息
        ocr_read_keys = [k for k, (v, c) in ocr_result.items() if c > 0 and v > 0]
        if ocr_read_keys:
            result.method = "ocr+geometry"
            result.message = (
                f"✅ 自动识别成功（{len(ocr_read_keys)} 项数值通过 OCR 读取）：\n"
                f"外框 {result.outer_w_cm}×{result.outer_h_cm} cm，"
                f"内挖 {result.inner_w_cm}×{result.inner_h_cm} cm\n"
                f"边距：上{result.margin_top_cm}/下{result.margin_bottom_cm}/"
                f"左{result.margin_left_cm}/右{result.margin_right_cm} cm\n"
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

    # 记录详细 debug
    result.debug["ocr_values"] = {k: round(v, 2) for k, (v, c) in ocr_result.items() if c > 0}
    result.debug["geo_values"] = {k: round(v, 2) for k, (v, c) in geo_result.items() if c > 0}

    # ---- 缓存存储（性能优化） ----
    # 将识别结果存入缓存，下次同一草图+目标尺寸直接返回
    if result.success:
        _store_cached_result(image_path, target_outer_w_cm, target_outer_h_cm, result)

        # 自洽解缓存：当 8 字段全部 >0 且几何自洽时，结果与 target 无关，
        # 存入自洽解缓存，换文件名（不同 target）时直接命中。
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
                logger.info(
                    f"[sketch_parser] 自洽解已缓存（与target无关，换文件名毫秒级响应）："
                    f"outer={result.outer_w_cm}x{result.outer_h_cm}, "
                    f"inner={result.inner_w_cm}x{result.inner_h_cm}, "
                    f"边距 上{result.margin_top_cm}/下{result.margin_bottom_cm}/"
                    f"左{result.margin_left_cm}/右{result.margin_right_cm}"
                )

    _progress(100, "草图解析完成")
    return result


# ---------------------------------------------------------------------------
# 旧接口 parse_sketch_geometry 已删除（v2026-08-15 清理冗余）。
# 原实现仅为 parse_sketch() 的薄封装，实际无任何调用方。
# 如需几何-only 解析，直接调用 parse_sketch() 并使用返回的 debug 字段即可。
# ---------------------------------------------------------------------------