"""尺寸草图解析器 —— 方向标签与数值提取层（由 sketch_parser.py 拆分而来，facade 模式）。

原文件 core/pool_designer/sketch_parser.py 为编排层 facade，
本模块只包含 方向标签与数值提取层 相关的实现，逻辑与原文件完全一致。
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


_DIR_CHAR_MAP = {'上': 'margin_top', '下': 'margin_bottom', '左': 'margin_left', '右': 'margin_right'}



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
            # 但如果两个bbox高度重叠且值不同，说明是OCR把小数拆成两个数字（如"7.4"→"7"+"4"），需要尝试合并
            overlap_x = max(0, min(a_bb[0]+a_bb[2], b_bb[0]+b_bb[2]) - max(a_bb[0], b_bb[0]))
            overlap_y = max(0, min(a_bb[1]+a_bb[3], b_bb[1]+b_bb[3]) - max(a_bb[1], b_bb[1]))
            overlap_area = overlap_x * overlap_y
            min_area = min(a_bb[2]*a_bb[3], b_bb[2]*b_bb[3])
            is_high_overlap = overlap_area > min_area * 0.5
            if is_high_overlap:
                # 高重叠：如果两个值相同（重复检测）则跳过；如果值不同则继续尝试小数合并
                if abs(a_val - b_val) < 0.01:
                    continue  # 同一数字的重复检测，跳过
                # 值不同 → 可能是小数被拆成两个数字
                # [Fix] 但如果是 两位数+个位数（a>=10, b<10）的高重叠，说明a本身就是完整独立值，b是别处碎片
                # 如 74 和 4：74是"右=74cm"，4是别处"6.5"被误读成的碎片。小数拆分为 7+4，此时 a=7<10
                if a_val >= 10 and b_val < 10:
                    continue
                if b_val >= 10 and a_val < 10:
                    continue
            # b值应为1-9的小数部分（或a为1-9，b为整数）
            small_first = a_val < 10 and a_val >= 1
            small_second = b_val < 10 and b_val >= 1
            # 拼接尝试：a.b（要求b是小数部分，即b<10）
            forward_concat = None
            reverse_concat = None
            if b_val < 10 and b_val >= 1 and small_second:
                try:
                    concat = float(f"{int(a_val)}.{int(b_val)}")
                    # [Fix] 合并后的值必须 > 小数前整数部分，但同时不能超出合理范围：
                    # 如果是高重叠的拆分合并（a<10, b<10）：结果<=99.9即可
                    # 如果是相邻的非高重叠（如43+5=43.5）：结果需<=999且 > 前整数
                    if a_val >= 10 and is_high_overlap:
                        pass  # 已在前面排除，这里不执行
                    if 0.5 <= concat <= 500 and concat > a_val:
                        # [防误合并] 两位数或更大整数 + 个位数 的拼接：
                        # 非高重叠（相邻排列）场景下，74+4=74.4 > 50，基本不可能是边距小数，只允许当 合并值 <= 99.9 且 合并后的值 看起来像"内框尺寸"才允许
                        # 更简单：若 a >= 10 且 拼接后的整数部分(a_val) >= 10 且 结果值 > 50 → 跳过
                        # （边距一般 < 50；内框尺寸 > 50 不需要这种小数合并）
                        if a_val >= 10 and concat > 50.0:
                            pass  # 不允许 74+4→74.4 这类误合并
                        else:
                            forward_concat = concat
                except ValueError:
                    logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                    pass
            # 反向拼接：b.a（a是小数部分，a<10）
            if a_val < 10 and a_val >= 1 and small_first:
                try:
                    concat = float(f"{int(b_val)}.{int(a_val)}")
                    if 0.5 <= concat <= 500 and concat > b_val:
                        # [防误合并] 两位数或更大整数 + 个位数 的反向拼接 >50 跳过
                        if b_val >= 10 and concat > 50.0:
                            pass
                        else:
                            reverse_concat = concat
                except ValueError:
                    logger.debug("[_merge_split_decimals] 忽略异常", exc_info=True)
                    pass
            # 消歧：当正反拼接都有效时，选整数部分较大的（更自然的小数写法）
            # 例如 7+4: 7.4(整数7) vs 4.7(整数4) → 选 7.4
            if forward_concat is not None and reverse_concat is not None:
                f_int = int(forward_concat)
                r_int = int(reverse_concat)
                if f_int >= r_int:
                    reverse_concat = None
                else:
                    forward_concat = None
            # 应用选择结果
            for concat, label in [(forward_concat, 'forward'), (reverse_concat, 'reverse')]:
                if concat is None:
                    continue
                nbb = (min(a_bb[0], b_bb[0]), min(a_bb[1], b_bb[1]),
                       max(a_bb[0]+a_bb[2], b_bb[0]+b_bb[2]) - min(a_bb[0], b_bb[0]),
                       max(a_bb[1]+a_bb[3], b_bb[1]+b_bb[3]) - min(a_bb[1], b_bb[1]))
                new_ones.append((concat, max(a_conf, b_conf) * 0.85, nbb))
                if label == 'forward':
                    logger.info(f"[OCR小数合并] {int(a_val)}+{int(b_val)} → {concat}")
                else:
                    logger.info(f"[OCR小数合并] {int(b_val)}+{int(a_val)} → {concat}")
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
    # [智能覆盖保护] 追踪恢复值的元数据：防止后续OCR误读覆盖已正确恢复的值
    _recovered_meta = {}  # field → (source_integer, recovered_value, raw_conf)
    # [全局源整数追踪] 即使恢复元数据被清除（被直接OCR覆盖），仍记录已使用的源整数
    _used_source_ints = {}  # source_integer → (field, recovered_value)
    if tesseract is None:
        return result

    # ---- [防OCR噪声] 预计算边距合理性上限 ----
    # 边距通常是外框短边的 2%~25%，或绝对不超过 25cm
    # 此阈值用于剔除 OCR 把装饰文字/尺寸标注误识别为"边距"的情况
    _target_is_authoritative = (target_outer_w_cm > 0 and target_outer_h_cm > 0)
    _ref_long = max(target_outer_w_cm, target_outer_h_cm, 0.0)
    _ref_short = min(target_outer_w_cm, target_outer_h_cm) if (target_outer_w_cm > 0 and target_outer_h_cm > 0) else max(target_outer_w_cm, target_outer_h_cm, 0.0)
    if _target_is_authoritative:
        # [权威模式] 当 target 外框完整可用时，按**轴向**分别设置边距上限（而非统一用短边）
        # 因为非对称边框的某一边可能非常大（例如内框偏左侧：mr=target_w-iw-ml 可能=74cm > 短边60cm）
        # 这种情况是数学上正确的，不应被当作 OCR 噪声拒绝
        # 规则：
        #   - margin_left / margin_right：上限 = outer_w * 0.9（预留10%给内框），或统一 upper cap
        #   - margin_top / margin_bottom：上限 = outer_h * 0.9
        #   - 全局统一 fallback cap（用于 _is_reasonable_margin 无字段上下文时）：max(轴向上限)
        _cap_horizontal = target_outer_w_cm * 0.90  # 横向边距上限 (left/right)
        _cap_vertical = target_outer_h_cm * 0.90     # 纵向边距上限 (top/bottom)
        _margin_hard_cap = max(_cap_horizontal, _cap_vertical)   # 无字段上下文的 fallback（保守上限取最大）
        # 轴向合理判断：需要知道是哪个字段 → 提供专用函数
        def _is_reasonable_margin_for_field(v, field_hint=None):
            """判断边距值是否合理（按轴向区分上限）。
            field_hint: 'margin_left'/'margin_right'（横向）'margin_top'/'margin_bottom'（纵向）或 None（fallback）
            """
            if v is None or v <= 0:
                return False
            if v < 0.3:
                return False
            if field_hint in ('margin_left', 'margin_right'):
                if v > _cap_horizontal:
                    return False
            elif field_hint in ('margin_top', 'margin_bottom'):
                if v > _cap_vertical:
                    return False
            else:
                # 不知道字段：使用全局上限（更宽松以防误伤）
                if _margin_hard_cap > 0 and v > _margin_hard_cap:
                    return False
            return True
    else:
        # 无 target 模式：保持原有严格上限
        _margin_hard_cap = min(_ref_long * 0.30, 25.0) if _ref_long > 0 else 25.0
        def _is_reasonable_margin_for_field(v, field_hint=None):
            """判断边距值是否合理（避免把外框尺寸/装饰文字误识别为边距）。"""
            if v is None or v <= 0:
                return False
            if v < 0.3:
                return False
            if _margin_hard_cap > 0 and v > _margin_hard_cap:
                return False
            if v > 50.0:
                return False
            return True

    # 保留向后兼容的 _is_reasonable_margin 别名
    def _is_reasonable_margin(v):
        return _is_reasonable_margin_for_field(v, None)

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

        # ---- [智能覆盖保护] 判断是否允许覆盖已有值 ----
        def _can_overwrite(existing_field, new_conf, is_recovered=False):
            """判断新值是否允许覆盖已有值。
            规则：
            1. 空字段 → 允许
            2. 新值是直接OCR（非恢复）→ 用原始conf比较存储conf
            3. 新值是恢复值 → 仅当原始conf比已有值的原始conf高10%以上才允许
            4. 同源于检测：恢复值的源整数若已被其他字段使用 → 拒绝
            """
            if existing_field not in result:
                return True
            old_val, old_conf, _ = result[existing_field]
            # 已有值也是恢复值：比较原始conf
            if existing_field in _recovered_meta:
                _, _, old_raw_conf = _recovered_meta[existing_field]
                if is_recovered:
                    # 新值也是恢复值：需要比已有恢复值高10%以上的原始conf
                    return new_conf > old_raw_conf * 1.10
                else:
                    # 新值是直接值：直接值可以覆盖恢复值（直接OCR更可信）
                    return conf > old_conf
            else:
                # 已有值是直接OCR值
                if is_recovered:
                    # 恢复值不能轻易覆盖直接值
                    return new_conf > old_conf * 1.15
                else:
                    # 都是直接值：标准比较
                    return new_conf > old_conf

        # [防OCR噪声] 边距值合理性检查：拒绝明显是外框尺寸/装饰文字的超大值
        # [Bug5 Fix] 使用已知字段的轴向合理性 cap（margin_left/right 按 outer_w*0.9；top/bottom 按 outer_h*0.9）
        if not _is_reasonable_margin_for_field(val, field_hint=field):
            # ----- 小数点恢复尝试：OCR把"7.4"读成"74"的补漏 -----
            # 仅对整数值尝试（非整数已经是正确的小数格式）
            if abs(val - round(val)) <= 0.01 and 10 <= val <= 99:
                s = str(int(val))
                src_int = int(val)  # 记录源整数用于同源检测
                candidates = []
                # 2位数: 74 → 7.4
                if len(s) == 2 and s[1] != '0':
                    try:
                        candidates.append(float(f"{s[0]}.{s[1]}"))
                    except ValueError as e:
                        logger.debug(f"[Step4] 小数候选解析 ValueError 跳过: {e}")
                # 3位数: 736 → 73.6（十位后加小数点）或 7.36（百位后）
                if len(s) == 3:
                    if s[2] != '0':
                        try:
                            candidates.append(float(f"{s[:2]}.{s[2]}"))
                        except ValueError as e:
                            logger.debug(f"[Step4] 小数候选解析 ValueError 跳过: {e}")
                    try:
                        candidates.append(float(f"{s[0]}.{s[1:]}"))
                    except ValueError as e:
                        logger.debug(f"[Step4] 小数候选解析 ValueError 跳过: {e}")
                # 尝试每个小数候选
                for dec_val in candidates:
                    if 0.3 <= dec_val <= 50.0 and _is_reasonable_margin_for_field(dec_val, field_hint=field):
                        # [同源检测1] 从_recovered_meta检查（当前仍为恢复值）
                        for other_field, (o_src_int, o_dec_val, _) in _recovered_meta.items():
                            if other_field != field and o_src_int == src_int:
                                logger.info(
                                    f"[Step4] 同源冲突拒绝: {dir_char}={src_int}→{dec_val:.1f}cm → {field} "
                                    f"(源整数{src_int}已被字段{other_field}恢复为{o_dec_val:.1f}，拒绝重复绑定)"
                                )
                                return
                        # [同源检测2] 从全局追踪检查（即使恢复元数据被清除，仍记录源整数使用历史）
                        if src_int in _used_source_ints:
                            prev_field, prev_val = _used_source_ints[src_int]
                            if prev_field != field:
                                logger.info(
                                    f"[Step4] 同源冲突拒绝: {dir_char}={src_int}→{dec_val:.1f}cm → {field} "
                                    f"(源整数{src_int}历史上已被字段{prev_field}使用为{prev_val:.1f}，全局拒绝重复绑定)"
                                )
                                return
                        # [同值检测] 恢复值与已有其他字段值相同 → 拒绝（左右/上下重复识别）
                        for other_field, (o_val, _, _) in result.items():
                            if other_field != field and abs(o_val - dec_val) < 0.01:
                                if other_field in _recovered_meta:
                                    logger.info(
                                        f"[Step4] 同值冲突拒绝: {dir_char}={src_int}→{dec_val:.1f}cm → {field} "
                                        f"(值{dec_val:.1f}已被字段{other_field}恢复锁定，拒绝重复绑定)"
                                    )
                                    return
                        # [几何一致性] 若已有对侧字段且新值与对侧值过于接近 → 检查是否合理
                        opp_field_map = {'margin_left': 'margin_right', 'margin_right': 'margin_left',
                                         'margin_top': 'margin_bottom', 'margin_bottom': 'margin_top'}
                        opp_field = opp_field_map.get(field)
                        if opp_field and opp_field in result and field not in _recovered_meta:
                            opp_val = result[opp_field][0]
                            if abs(dec_val - opp_val) < 0.5 and conf < 85:
                                # 与对侧值非常接近且置信度不高 → 可能是OCR误读
                                logger.info(
                                    f"[Step4] 几何冲突拒绝: {dir_char}={src_int}→{dec_val:.1f}cm → {field} "
                                    f"(与对侧{opp_field}={opp_val:.1f}过于接近，可能为OCR复制，拒绝)"
                                )
                                return
                        logger.info(
                            f"[Step4] 小数点恢复: {dir_char}={val:.0f}→{dec_val:.1f}cm → {field} "
                            f"(原整数超出上限，小数解释通过)"
                        )
                        # [智能覆盖] 使用保护逻辑判断是否允许覆盖
                        if _can_overwrite(field, conf, is_recovered=True):
                            result[field] = (dec_val, conf * 0.85, (bx, by, bw, bh))
                            _recovered_meta[field] = (src_int, dec_val, conf)
                            _used_source_ints[src_int] = (field, dec_val)  # 全局追踪
                            logger.info(f"[Step4] {tag}: {dir_char}={dec_val:.1f}(恢复) conf={conf*0.85:.0f} → {field}")
                        else:
                            logger.info(
                                f"[Step4] 覆盖保护: {dir_char}={dec_val:.1f}(恢复) conf={conf} → {field} "
                                f"(被已有值保护，拒绝覆盖)"
                            )
                        return  # 恢复成功，不再继续
            # 所有恢复尝试均失败，维持原拒绝逻辑
            logger.info(
                f"[Step4] OCR噪声拒绝: {dir_char}={val}cm → {field} "
                f"(超过边距合理上限 cap={_margin_hard_cap:.1f}cm，可能是装饰文字/尺寸标注)"
            )
            return
        # [直接OCR值] 使用智能覆盖逻辑
        if _can_overwrite(field, conf, is_recovered=False):
            result[field] = (val, conf, (bx, by, bw, bh))
            # 直接OCR值清除恢复元数据（不再是恢复值）
            if field in _recovered_meta:
                del _recovered_meta[field]
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
                        # [Fix] 接受带"厘米"/"cm"等单位后缀的 token：OCR常读成 "74cm"/"46厘米"（一个token）
                        # 如果没有方向字字符，且开头能提取出数值，则接受
                        m_pure = re.match(r'^(\d+\.?\d*|\.\d+)', txt)  # 前缀匹配，允许后缀
                        if m_pure:
                            # 检查不能包含方向字汉字（防止 '上46' 这种被当作数值token）
                            has_dir = any(dc in txt for dc in _DIR_CHAR_MAP)
                            if has_dir:
                                pass  # 含方向字，交给 _parse_dir_num_token 的单token/双token逻辑处理
                            else:
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
                        # [Fix] 必须走 _try_bind 执行合理性校验+覆盖保护，不能直接赋值
                        # 直接赋值曾导致 7.4（合成小数）被无保护地写入 margin_right，覆盖正确的 74
                        _before_has = dfield in result
                        _before_val = result[dfield][0] if _before_has else None
                        _try_bind(dchar, nv, nconf, bx0, by0, bw0, bh0,
                                  "Phase3空间距离场(enh)")
                        # 只有真的绑定成功才记一次计数
                        if (not _before_has and dfield in result) or (_before_has and result[dfield][0] != _before_val):
                            bind_count += 1
                            logger.info(f"[Step4] Phase3空间绑定({dchar}↔{nv}): dist={best_dist:.0f}px "
                                        f"(Rmax={R_MAX:.0f}) conf={nconf} → {dfield}")
                        elif dfield not in result:
                            # _try_bind 拒绝了此值（不合理或被保护）
                            # 尝试次近距离的备选数值，直到耗尽或成功
                            # 这里实现简单版：找下一个距离最近的备选
                            for retry in range(3):
                                next_i = -1
                                next_d = float('inf')
                                for j, (nv2, ncx2, ncy2, nh2, nconf2) in enumerate(num_tokens_s3):
                                    if j in used_num_idx or j == best_i:
                                        continue
                                    dx2 = dcx - ncx2
                                    dy2 = dcy - ncy2
                                    d2 = _math_s3.sqrt(dx2*dx2 + dy2*dy2)
                                    if d2 <= R_MAX and d2 < next_d:
                                        next_d = d2
                                        next_i = j
                                if next_i < 0:
                                    break
                                nv2, _, _, _, nconf2 = num_tokens_s3[next_i]
                                used_num_idx.add(next_i)
                                bx2 = min(dcx - 10, num_tokens_s3[next_i][1] - 10)
                                by2 = min(dcy - dh//2, num_tokens_s3[next_i][2] - num_tokens_s3[next_i][3]//2)
                                bw2 = max(20, abs(dcx - num_tokens_s3[next_i][1]) + 30)
                                bh2 = max(20, dh + num_tokens_s3[next_i][3])
                                _try_bind(dchar, nv2, nconf2, bx2, by2, bw2, bh2,
                                          f"Phase3空间距离场(备选{retry+1})")
                                if dfield in result:
                                    bind_count += 1
                                    logger.info(f"[Step4] Phase3空间绑定(备选)({dchar}↔{nv2}): dist={next_d:.0f}px "
                                                f"(Rmax={R_MAX:.0f}) conf={nconf2} → {dfield}")
                                    break
                logger.info(f"[Step4] Phase3空间距离场完成：新绑定 {bind_count} 个字段")

    # ========== Phase 4：小数点恢复（OCR把"7.4"读成"74"的补漏）==========
    # 原理：Tesseract 有时丢失小数点，将 "7.4" 识别为 "74"。
    # 对每个方向标签值尝试小数恢复：2位整数ab → a.b
    # 仅当小数解释通过合理性校验且原整数不通过时才替换
    # [智能保护] Phase4 只在字段当前是整数值且未被恢复锁定时才操作
    recovered_fields = {}
    for field_name, (val, conf, bbox) in result.items():
        # 跳过已经是恢复值的字段（由_try_bind锁定，不重复处理）
        if field_name in _recovered_meta:
            continue
        if abs(val - round(val)) <= 0.01 and 10 <= val <= 99:
            s = str(int(val))
            src_int = int(val)
            # 2位数恢复：74 → 7.4，85 → 8.5
            if len(s) == 2 and s[1] != '0':
                try:
                    dec_val = float(f"{s[0]}.{s[1]}")
                    if 0.3 <= dec_val <= 50.0:
                        # 只有当原整数不合理或小数更合理时才替换
                        # [Bug5 Fix] 使用已知字段轴向合理性 cap
                        orig_ok = _is_reasonable_margin_for_field(val, field_hint=field_name)
                        dec_ok = _is_reasonable_margin_for_field(dec_val, field_hint=field_name)
                        if dec_ok and not orig_ok:
                            recovered_fields[field_name] = (dec_val, conf * 0.8, bbox, src_int)
                            logger.info(
                                f"[Step4] 小数点恢复: {field_name} {val:.0f}→{dec_val:.1f} "
                                f"(原整数超出边距上限，小数解释合理)"
                            )
                        # [Bug Fix] 移除旧的 short_cap=min(w,h)*0.5 硬阈值强制改写分支：
                        # 当 orig_ok（经轴向cap判断，如 74 ≤ 172*0.9=154.8）且 dec_ok 同时合理时，
                        # 不能再用 30cm=60*0.5 这种固定短边阈值去强行把 74→7.4。
                        # 庄园秘境等需要小数恢复的场景本来就是 "74/85" 作为整数值不合理
                        # （短边 44.5 或 57.8 → cap≈21 或 26，74/85 都超）→ 走 dec_ok and not orig_ok 分支。
                except ValueError:
                    logger.debug("[_extract_direction_label_numbers] 忽略异常", exc_info=True)
                    pass
    # [智能保护] Phase4 应用恢复时使用与_try_bind一致的保护逻辑
    for fn, item in recovered_fields.items():
        rv, rc, rb, src_int = item
        # 同源检测1：从_recovered_meta检查
        skip = False
        for other_field, (o_src_int, o_dec_val, _) in _recovered_meta.items():
            if other_field != fn and o_src_int == src_int:
                logger.info(
                    f"[Step4] Phase4同源冲突拒绝: {fn}={src_int}→{rv:.1f} "
                    f"(源整数{src_int}已被字段{other_field}恢复为{o_dec_val:.1f})"
                )
                skip = True
                break
        if skip:
            continue
        # 同源检测2：从全局追踪检查
        if src_int in _used_source_ints:
            prev_field, prev_val = _used_source_ints[src_int]
            if prev_field != fn:
                logger.info(
                    f"[Step4] Phase4同源冲突拒绝: {fn}={src_int}→{rv:.1f} "
                    f"(源整数{src_int}历史上已被字段{prev_field}使用为{prev_val:.1f}，全局拒绝)"
                )
                continue
        # 同值检测
        for other_field, (o_val, _, _) in result.items():
            if other_field != fn and abs(o_val - rv) < 0.01:
                if other_field in _recovered_meta:
                    logger.info(
                        f"[Step4] Phase4同值冲突拒绝: {fn}={src_int}→{rv:.1f} "
                        f"(值{rv:.1f}已被字段{other_field}恢复锁定)"
                    )
                    skip = True
                    break
        if skip:
            continue
        result[fn] = (rv, rc, rb)
        _recovered_meta[fn] = (src_int, rv, max(rc / 0.85, rc / 0.8))
        _used_source_ints[src_int] = (fn, rv)  # 全局追踪

    return result

