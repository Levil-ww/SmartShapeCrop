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

logger = logging.getLogger(__name__)


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

    # 二值化处理：文字是暗色在白底上
    _, binary = cv2.threshold(roi_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 形态学：去除噪点
    kernel_small = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel_small, iterations=1)

    # 找连通区域
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

    components = []
    roi_area = w * h
    for i in range(1, num_labels):
        rx = stats[i, cv2.CC_STAT_LEFT]
        ry = stats[i, cv2.CC_STAT_TOP]
        rw = stats[i, cv2.CC_STAT_WIDTH]
        rh = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]

        # 过滤：太小的噪点、太大的非文字区域（矩形边框等）
        if area < 8:
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
    all_nums.sort(key=lambda t: t[1])
    return all_nums[0][0]


def _ocr_full_image(cv2, gray_img, tesseract) -> list[tuple]:
    """对整张图像做 OCR，返回识别到的所有 (value, x_center, y_center, conf)。

    方案：
      1. 多尺度放大图像，提高小字/大字识别率（1.0x, 1.5x, 2.5x 合并）
      2. 用 pytesseract.image_to_data 获取每个字/词的精确位置
      3. 把同一行中相邻的数字合并为完整的数值（支持小数点）
      4. 多尺度结果去重（按数值+坐标距离）
    """
    from PIL import Image as PILImage
    import re

    h_img, w_img = gray_img.shape[:2]

    # 选择多个 scale：小图用大倍率放大，大图用适度倍率
    max_side = max(h_img, w_img)
    if max_side < 600:
        scales = [2.0, 3.0, 1.5]
    elif max_side < 1000:
        scales = [1.5, 2.5, 1.0]
    elif max_side < 1600:
        scales = [1.2, 2.0, 1.0]
    else:
        scales = [1.0, 1.5]

    all_raw_chars = []  # (text, x_center, y_center, conf, w, h)

    for scale in scales:
        if abs(scale - 1.0) < 1e-6:
            gray_scaled = gray_img
        else:
            gray_scaled = cv2.resize(gray_img, None, fx=scale, fy=scale,
                                     interpolation=cv2.INTER_CUBIC)
        # 做多种预处理变体，提高小字识别率
        variants = [gray_scaled]  # 原始
        # 自适应二值化（帮助弱像素线框图里的深黑字）
        try:
            bin1 = cv2.adaptiveThreshold(gray_scaled, 255,
                                          cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY, 25, 8)
            variants.append(bin1)
        except Exception:
            pass
        # CLAHE 对比度增强
        try:
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            variants.append(clahe.apply(gray_scaled))
        except Exception:
            pass

        for variant in variants:
            pil_img = PILImage.fromarray(variant)
            # 用 PSM 11 (sparse text) + image_to_data 获取位置
            try:
                config_data = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789.'
                data = tesseract.image_to_data(
                    pil_img, config=config_data,
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
                # 降低置信度门槛，让小字符数字（6、10 等）也能进候选
                if conf < 10:
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

    # 如果多尺度 PSM 11 没结果，尝试 PSM 6（假设统一块）
    if not all_raw_chars:
        try:
            pil_img = PILImage.fromarray(gray_img)
            config_data = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789.'
            data = tesseract.image_to_data(
                pil_img, config=config_data,
                output_type=tesseract.Output.DICT,
            )
            if data and 'text' in data:
                n = len(data['text'])
                for i in range(n):
                    text = str(data['text'][i]).strip()
                    if not text:
                        continue
                    try:
                        conf = int(data.get('conf', [50] * n)[i])
                    except Exception:
                        conf = 50
                    if conf < 10:
                        continue
                    try:
                        x_left = int(data.get('left', [0] * n)[i])
                        y_top = int(data.get('top', [0] * n)[i])
                        ww = int(data.get('width', [0] * n)[i])
                        hh = int(data.get('height', [0] * n)[i])
                    except Exception:
                        continue
                    x_c = x_left + ww / 2
                    y_c = y_top + hh / 2
                    if re.fullmatch(r'[.\s]+', text):
                        continue
                    all_raw_chars.append((text, x_c, y_c, conf, ww, hh))
        except Exception:
            pass

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

    # 总宽可能在外框下边的整个底部（x 范围更宽）
    anchors.append(('total_w', (ox + ow * 0.5), (oy + oh + oh * 0.12),
        (ow * 0.95), (oh * 0.3)))
    # 总宽也可能在顶部或顶部上方
    anchors.append(('total_w', (ox + ow / 2), (oy - oh * 0.12),
        (ow * 0.95), (oh * 0.4)))
    # 总高也可能在外框左侧（x更小的范围），或整个外框左侧
    anchors.append(('total_h', (ox - ow * 0.08), (oy + oh * 0.5),
        (ow * 0.35), (oh * 0.95)))
    # 总高也可能在外框右侧（右边缘标注总高）
    anchors.append(('total_h', (ox + ow * 1.08), (oy + oh * 0.5),
        (ow * 0.35), (oh * 0.95)))
    # 顶/底区域的总高（水平标注总高）
    anchors.append(('total_h', (ox + ow / 2), (oy - oh * 0.08),
        (ow * 0.5), (oh * 0.25)))
    anchors.append(('total_h', (ox + ow / 2), (oy + oh + oh * 0.08),
        (ow * 0.5), (oh * 0.25)))

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
            if 2 <= val <= 40: return 0.15
            if val > 80: return -0.3
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
    if target_w_hint > 0 and target_h_hint > 0:
        result_vb = _value_based_assignment(
            ocr_hits, outer_rect, inner_rect,
            target_w_hint, target_h_hint
        )
        # 比较两种分配的几何自洽性，使用更好的那个
        sc_spatial = _score_assignment_consistency(result)
        sc_value = _score_assignment_consistency(result_vb)
        if sc_value > sc_spatial:
            logger.info(f"[sketch_parser] 数值大小分配比空间位置分配更自洽 "
                        f"(score {sc_value:.3f} vs {sc_spatial:.3f})，采用数值分配")
            result = result_vb

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

    # 奖励：边距 < 内框（合理范围，边距不应大于内框）
    if iw > 0 and ml > iw * 0.9:
        score -= 0.1
    if iw > 0 and mr > iw * 0.9:
        score -= 0.1
    if ih > 0 and mt > ih * 0.9:
        score -= 0.1
    if ih > 0 and mb > ih * 0.9:
        score -= 0.1

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
            # 剩余分配给 inner/margin
            remaining = sorted_hits[2:]
            for i, (v, c) in enumerate(remaining):
                if i == 0: r['inner_w'] = (v, 7)
                elif i == 1: r['inner_h'] = (v, 7)
                elif i == 2: r['margin_top'] = (v, 6)
                elif i == 3: r['margin_bottom'] = (v, 6)
                elif i == 4: r['margin_left'] = (v, 6)
                elif i == 5: r['margin_right'] = (v, 6)
            candidates.append(r)

    # 方案B：最大 → inner_w, 次大 → inner_h, 其余 → margins（外框用目标值）
    if n >= 2 and target_w_cm > 0 and target_h_cm > 0:
        r = {
            'total_w': (target_w_cm, 5), 'total_h': (target_h_cm, 5),
            'inner_w': (sorted_hits[0][0], 8),
            'inner_h': (sorted_hits[1][0], 8),
            'margin_top': (0.0, 0), 'margin_bottom': (0.0, 0),
            'margin_left': (0.0, 0), 'margin_right': (0.0, 0),
        }
        remaining = sorted_hits[2:]
        for i, (v, c) in enumerate(remaining):
            if i == 0: r['margin_bottom'] = (v, 7)
            elif i == 1: r['margin_right'] = (v, 7)
            elif i == 2: r['margin_left'] = (v, 6)
            elif i == 3: r['margin_top'] = (v, 6)
        candidates.append(r)

    # 方案C：最大 → inner_w, 其余全给边距
    if n >= 1 and target_w_cm > 0 and target_h_cm > 0:
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
        # 单侧边距不得超过外框边长的 40%（经验上限）
        upper = outer_side * 0.40
        # 也不得超过 (外框-内框) 的 80%（避免明显大于实际间隙）
        if inner_side > 0:
            gap_clip = max(0, (outer_side - inner_side) * 0.80)
            upper = min(upper, gap_clip)
        if v > upper:
            return 0.0  # 异常值直接清零，让上层几何回退或用户手动填写
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


def _find_and_read_numbers(cv2, gray_img, outer_rect: tuple, inner_rect: tuple,
                           tesseract=None,
                           target_w_hint: float = 0.0,
                           target_h_hint: float = 0.0) -> dict:
    """检测并读取草图上的标注数字。

    两种策略并用：
      A) 有 Tesseract：全图 OCR → 得到所有 (val, x, y) → 按位置分配到字段
      B) 无 Tesseract：直接返回空，由后续几何回退处理
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

    # 策略 A：全图 OCR + 位置映射
    try:
        hits = _ocr_full_image(cv2, gray_img, tesseract)
    except Exception as e:
        logger.warning(f"[sketch_parser] 全图 OCR 失败: {e}")
        hits = []

    if hits:
        result = _assign_ocr_values_to_fields(hits, outer_rect, inner_rect, h_img, w_img,
                                               target_w_hint=target_w_hint,
                                               target_h_hint=target_h_hint)
    else:
        result = dict(empty)

    # 策略 B（兜底）：对 ROI 做区域 OCR（仅在 A 策略遗漏字段时填补）
    # 注意：ROI OCR 的识别率较低，容易误读相邻数字，所以置信度给低一些（1-4），
    # 只有当 A 策略的该字段为空或置信度 <= 2 时才覆盖。
    rois_to_check = [
        ('total_w',   (ox,                    max(0, oy - int(oh * 0.3)),  ow,                 max(6, int(oh * 0.35)))),
        ('total_w',   (ox,                    min(h_img - 1, oy + oh),    ow,                 max(6, int(oh * 0.35)))),
        ('total_h',   (max(0, ox - int(ow * 0.35)), oy,                   max(6, int(ow * 0.35)), oh)),
        ('total_h',   (min(w_img - 1, ox + ow),     oy,                   max(6, int(ow * 0.35)), oh)),
        ('margin_top',    (ox,                  oy,                      ow,  max(6, iy - oy + int(oh * 0.05)))),
        ('margin_bottom', (ox,                  iy + ih,                ow,  max(6, (oy + oh) - (iy + ih) + int(oh * 0.05)))),
        ('margin_left',   (ox,                  oy,                      max(6, ix - ox + int(ow * 0.05)), oh)),
        ('margin_right',  (ix + iw,             oy,                      max(6, (ox + ow) - (ix + iw) + int(ow * 0.05)), oh)),
        ('inner_w',       (ix,                  iy,                      iw,  max(6, int(ih * 0.7)))),
        ('inner_h',       (ix,                  iy + int(ih * 0.3),     iw,  max(6, int(ih * 0.7)))),
    ]

    for key, roi in rois_to_check:
        regions = _find_number_regions(cv2, gray_img, roi, max_regions=3)
        for reg in regions:
            val = _ocr_region(cv2, gray_img, reg, tesseract)
            if val is None:
                continue
            old_val, old_conf = result[key]
            # ROI OCR 的置信度 = 2 或 3（较低，避免抢夺 A 策略）
            new_conf = 3 if reg[4] > 300 else 2
            # 仅在 A 策略空或置信度很低（<= 2 且 val=0）时填
            if old_val > 0 and (old_conf > new_conf or old_conf >= 4):
                continue
            # 一值多槽防御：若 val 已被其他字段占用则跳过（误差 < 0.15）
            already_used = False
            for other_key, (other_val, _) in result.items():
                if other_key == key:
                    continue
                if other_val > 0 and abs(other_val - val) < 0.15:
                    already_used = True
                    break
            if already_used:
                continue
            result[key] = (val, new_conf)

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
        # 注意：边距字段一律不使用几何回退（返回 0）。
        # 原因：几何回退基于像素坐标比例，而草图的几何检测常把外框/内框识别错，
        # 导致边距回退值严重偏离标注值。边距必须由 OCR 检出或用户手动输入。
        'margin_top': (0.0, 0),
        'margin_bottom': (0.0, 0),
        'margin_left': (0.0, 0),
        'margin_right': (0.0, 0),
    }
    return fallback


# ---------------------------------------------------------------------------
# 主解析流程
# ---------------------------------------------------------------------------

def parse_sketch(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
) -> SketchParseResult:
    """解析尺寸草图，返回 SketchParseResult（永不抛异常）。

    流程：
      1. 加载图片 → 复杂度评估
      2. 几何检测 → 找两个嵌套矩形
      3. 数字识别 → OCR + 几何回退
      4. 融合结果 → 映射到边距/尺寸
    """
    result = SketchParseResult(method="hybrid")
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

    # ---- L1: 复杂度评估 ----
    is_complex, complex_reason = _assess_complexity(gray)
    if is_complex:
        result.message = complex_reason
        result.debug["complex_skipped"] = True
        return result

    # ---- L2: 几何检测 ----
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
    tesseract = _safe_import_tesseract()

    # 先用 OCR 读取标注数字
    ocr_result = _find_and_read_numbers(cv2, gray, (ox, oy, ow, oh),
                                         (ix, iy, iw, ih), tesseract,
                                         target_w_hint=target_outer_w_cm,
                                         target_h_hint=target_outer_h_cm)

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
    # 核心策略变更（修复 Bug 4）：
    #   - 如果 OCR 值本身几何自洽（8字段全正 + 内外边距和 ≈ 外框差）：
    #     → 仅覆盖 outer，不强制重算 inner/margins，由 Phase 3 做方向矫正
    #     → 原因：OCR 正确识别了数值，只是 total_w/h 映射方向反了。强制重算会
    #       依赖像素比例，而几何检测的矩形坐标可能有误差
    #   - 如果 OCR 不自洽（出现 11.5x5.1、边距52等荒谬值）：
    #     → 覆盖 outer 并强制用像素比例重算 inner 和 margins
    #     → 原因：OCR 映射锚点完全错位，语义值不可靠
    _need_recalc_from_target = False
    if target_outer_w_cm > 0 and target_outer_h_cm > 0:
        _ratio_w = outer_w / target_outer_w_cm if target_outer_w_cm > 0 else 1.0
        _ratio_h = outer_h / target_outer_h_cm if target_outer_h_cm > 0 else 1.0
        _w_over20 = _ratio_w > 1.20 or _ratio_w < 0.83
        _h_over20 = _ratio_h > 1.20 or _ratio_h < 0.83

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
        if (_w_over20 or _h_over20) and not _ocr_fully_consistent:
            _need_recalc_from_target = True
            logger.info(f"[sketch_parser] OCR 偏差>20%且几何不自洽 → 将按目标尺寸强制重算内框和边距")
        elif _need_square_forced:
            _need_recalc_from_target = True
            logger.info(f"[sketch_parser] 近正方形画布触发强制覆盖 → 将按目标尺寸强制重算内框和边距")
        elif (_w_over20 or _h_over20) and _ocr_fully_consistent:
            # 关键路径：OCR 值自洽但尺寸偏差大 → 只是方向反了，不强制重算
            # Phase 3 会做方向矫正（当 delta 为负时交换内框）
            logger.info(f"[sketch_parser] OCR 偏差>20%但几何自洽 → 仅覆盖外框尺寸，"
                        f"不强制重算，交 Phase 3 做方向矫正")
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

    if outer_w > 0 and inner_w > 0:
        expected_ml_mr = outer_w - inner_w
        ml, mr = _validate_pair(ml, mr, expected_ml_mr, '左右边距')

    if outer_h > 0 and inner_h > 0:
        expected_mt_mb = outer_h - inner_h
        mt, mb = _validate_pair(mt, mb, expected_mt_mb, '上下边距')

    # ---- 最终三角验证：外框 = 内框 + 边距 ----
    # 确保 inner 尺寸 <= outer 尺寸，边距和不超过对应外框边长
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
    if outer_w > 0 and inner_w > 0:
        expected_lr = outer_w - inner_w
        if expected_lr > 0:
            actual_lr = ml + mr
            lr_deviation = abs(actual_lr - expected_lr) / expected_lr
            if lr_deviation > 0.5 and actual_lr > 0:
                logger.warning(
                    f"[sketch_parser] 水平边距和({actual_lr:.2f})与预期({expected_lr:.2f})"
                    f"偏差 {lr_deviation:.0%} 过大，重置水平边距")
                # 保留单边较小的那个（可能是正确的），清零另一边
                # 简单策略：两边都清 0，让用户手动输入
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
                    f"偏差 {tb_deviation:.0%} 过大，重置垂直边距")
                mt = 0.0
                mb = 0.0

    # === 最终单侧边距合理性裁剪（第二道防线）===
    def _final_clip(v, outer_side):
        if v <= 0 or outer_side <= 0:
            return v
        # 单侧边距不得超过外框边长的 35%
        if v > outer_side * 0.35:
            return 0.0
        return v

    if outer_w > 0:
        ml = _final_clip(ml, outer_w)
        mr = _final_clip(mr, outer_w)
    if outer_h > 0:
        mt = _final_clip(mt, outer_h)
        mb = _final_clip(mb, outer_h)

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

    return result


# ---------------------------------------------------------------------------
# 向后兼容：parse_sketch_geometry (旧接口，已被 parse_sketch 替代)
# ---------------------------------------------------------------------------

def parse_sketch_geometry(
    image_path: str,
    *,
    target_outer_w_cm: float = 0.0,
    target_outer_h_cm: float = 0.0,
) -> SketchParseResult:
    """[已弃用] 旧版几何检测入口，请使用 parse_sketch()。

    保留此函数仅为向后兼容，内部直接调用 parse_sketch()。
    """
    return parse_sketch(
        image_path,
        target_outer_w_cm=target_outer_w_cm,
        target_outer_h_cm=target_outer_h_cm,
    )