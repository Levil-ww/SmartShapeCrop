"""尺寸草图解析器 —— 边距校验与赋值打分层（由 sketch_parser.py 拆分而来，facade 模式）。

原文件 core/pool_designer/sketch_parser.py 为编排层 facade，
本模块只包含 边距校验与赋值打分层 相关的实现，逻辑与原文件完全一致。
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
            # --- [不变量 S6/S7 守护] 内框候选异常时，优先用边距推导内框，而非按异常内框缩放边距 ---
            # 触发条件：
            #   (a) iw < max(ml, mr)                  ← 几何不可能：内宽比边距还小
            #   (b) 两侧边距都被方向标签锁定            ← 两侧方向锁都在，outer-margins 更可信
            #   (c) gap/margin_sum >2 或 <0.5          ← 马上会触发比例缩放(说明当前iw极不兼容)
            # 命中任意一条 + 推导 iw=tw-ml-mr 在合理范围内 → 直接重写 iw，避免后续病态比例放大
            _ml_lock = 'margin_left' in dir_locked_fields
            _mr_lock = 'margin_right' in dir_locked_fields
            _derived_iw = tw - ml - mr
            _trigger = (
                (iw < max(ml, mr, 0.01))
                or (_ml_lock and _mr_lock)
                or (gap > 0 and margin_sum > 0 and (gap / margin_sum > 2.0 or gap / margin_sum < 0.5))
            )
            if _trigger and 0 < _derived_iw < tw * 0.95:
                _tag_bits = (
                    ('IW<' if iw < max(ml, mr, 0.01) else '')
                    + ('L+R_dir_locked' if (_ml_lock and _mr_lock) else '')
                    + ('ratio_abnormal' if (gap > 0 and margin_sum > 0 and (gap / margin_sum > 2.0 or gap / margin_sum < 0.5)) else '')
                )
                logger.info(
                    f"[Step6] 横向inner异常用outer-margins重写: inner_w {iw:.1f}→{_derived_iw:.1f} "
                    f"(原因={_tag_bits}; outer={tw:.1f}  lhs_old={lhs:.1f})"
                )
                put('inner_w', _derived_iw, 0.5)
                iw = _derived_iw
                lhs = iw + ml + mr
                gap = tw - iw
            # --- 比例缩放：当边距和与预期差距 >2x 时按比例缩放两侧（经过上面inner修正后，正常场景通常不再触发）---
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
                        # [不变量S8] 方向锁的边距不能被比例缩放覆盖！只缩放非锁侧
                        side_changed = False
                        if not _ml_lock:
                            put('margin_left', new_ml, 0.5)
                            side_changed = True
                        if not _mr_lock:
                            put('margin_right', new_mr, 0.5)
                            side_changed = True
                        if side_changed:
                            ml = get('margin_left')
                            mr = get('margin_right')
                            logger.info(f"[Step6] 横向比例缩放: ml {ml:.1f}(锁{'是' if _ml_lock else '否'}) mr {mr:.1f}(锁{'是' if _mr_lock else '否'}) (ratio={ratio:.2f})")
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
            # --- [不变量 S6/S7 守护] 内框候选异常时，优先用边距推导内框（花漾之约 ih=7→35.5 修复关键）---
            _mt_lock = 'margin_top' in dir_locked_fields
            _mb_lock = 'margin_bottom' in dir_locked_fields
            _derived_ih = th - mt - mb
            _trigger_v = (
                (ih < max(mt, mb, 0.01))
                or (_mt_lock and _mb_lock)
                or (gap > 0 and margin_sum > 0 and (gap / margin_sum > 2.0 or gap / margin_sum < 0.5))
            )
            if _trigger_v and 0 < _derived_ih < th * 0.95:
                _tag_v = (
                    ('IH<' if ih < max(mt, mb, 0.01) else '')
                    + ('T+B_dir_locked' if (_mt_lock and _mb_lock) else '')
                    + ('ratio_abnormal' if (gap > 0 and margin_sum > 0 and (gap / margin_sum > 2.0 or gap / margin_sum < 0.5)) else '')
                )
                logger.info(
                    f"[Step6] 纵向inner异常用outer-margins重写: inner_h {ih:.1f}→{_derived_ih:.1f} "
                    f"(原因={_tag_v}; outer={th:.1f}  lhs_old={lhs:.1f})"
                )
                put('inner_h', _derived_ih, 0.5)
                ih = _derived_ih
                lhs = mt + ih + mb
                gap = th - ih
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
                        # [不变量S8] 方向锁的边距不被比例缩放覆盖 → 只缩放非锁侧
                        changed = False
                        if not _mt_lock:
                            put('margin_top', new_mt, 0.5)
                            changed = True
                        if not _mb_lock:
                            put('margin_bottom', new_mb, 0.5)
                            changed = True
                        if changed:
                            mt = get('margin_top')
                            mb = get('margin_bottom')
                            logger.info(
                                f"[Step6] 纵向比例缩放: mt {mt:.1f}(锁{'是' if _mt_lock else '否'}) "
                                f"mb {mb:.1f}(锁{'是' if _mb_lock else '否'}) (ratio={ratio:.2f})"
                            )
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

