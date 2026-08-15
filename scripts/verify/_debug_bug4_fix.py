"""
验证 Bug 4 修复：target 传入时方向相反（竖版 60.5×133），
模拟 _pool_auto_parse_sketch 首次调用 parse_sketch 时的场景。
这个场景是用户实际遇到的灾难路径！
"""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname).1s %(name)s: %(message)s'
)
logger = logging.getLogger('core.pool_designer.sketch_parser')
logger.setLevel(logging.DEBUG)

# 模拟 parse_sketch 内部的关键决策逻辑（直接复制核心决策）
# 我们不直接调用 parse_sketch（需要实际图像），但可模拟它的决策流程
from core.pool_designer.sketch_parser import (
    _assign_ocr_values_to_fields,
    _score_assignment_consistency,
)


# ====================================================================
# 用户实际场景：第一次调用（_pool_auto_parse_sketch）
# 文件名是 60.5x133CM，name_parser 解析成 width=60.5, height=133
# PropertyPanel 中 _pool_set_target_size 可能还没做方向交换
# 所以 target 是竖版的：target_w=60.5, target_h=133
# ====================================================================
TARGET_W_V = 60.5   # 竖版（方向反！）
TARGET_H_V = 133.0  # 竖版（方向反！）
TARGET_W_H = 133.0  # 横版（PoolRenderWorker 修正后）
TARGET_H_H = 60.5   # 横版（PoolRenderWorker 修正后）

ox, oy, ow, oh = 0, 0, 485, 332
ix, iy, iw, ih = 80, 60, 300, 220
outer_rect = (ox, oy, ow, oh)
inner_rect = (ix, iy, iw, ih)

print(f"=== 用户场景：Bug 4 验证（target 方向传入反了） ===")
print(f"首次调用 _pool_auto_parse_sketch 传入 target: {TARGET_W_V} × {TARGET_H_V} (竖版，方向反)")
print(f"修正后的 PoolRenderWorker 传入 target: {TARGET_W_H} × {TARGET_H_H} (横版，正确)")
print(f"像素外框: {ow}×{oh} → 比例 {ow/oh:.2f}")
print()

ocr_hits = [
    (133.0,  240,  10,  0.95),  # total_w
    (60.5,   10,   170, 0.95),  # total_h
    (76.0,   240,  100, 0.90),  # inner_w
    (44.5,   240,  210, 0.90),  # inner_h
    (6.0,    240,  40,  0.90),  # mt
    (10.0,   240,  300, 0.90),  # mb
    (14.6,   40,   170, 0.90),  # ml
    (42.4,   450,  170, 0.90),  # mr
]

# ====================================================================
# 步骤1: _assign_ocr_values_to_fields 返回 8 个字段（空间映射，方向正确）
# ====================================================================
result_assign = _assign_ocr_values_to_fields(
    ocr_hits, outer_rect, inner_rect,
    oh, ow,
    target_w_hint=TARGET_W_V, target_h_hint=TARGET_H_V,
)
print("步骤1: _assign_ocr_values_to_fields 输出（空间锚点映射）")
outer_w = result_assign.get('total_w', (0, 0))[0]
outer_h = result_assign.get('total_h', (0, 0))[0]
ocr_iw = result_assign.get('inner_w', (0, 0))[0]
ocr_ih = result_assign.get('inner_h', (0, 0))[0]
ocr_mt = result_assign.get('margin_top', (0, 0))[0]
ocr_mb = result_assign.get('margin_bottom', (0, 0))[0]
ocr_ml = result_assign.get('margin_left', (0, 0))[0]
ocr_mr = result_assign.get('margin_right', (0, 0))[0]
print(f"  total = {outer_w} × {outer_h}")
print(f"  inner = {ocr_iw} × {ocr_ih}")
print(f"  margins: 上{ocr_mt}/下{ocr_mb}/左{ocr_ml}/右{ocr_mr}")

# ====================================================================
# 步骤2: parse_sketch 中的双向自洽检测
# ====================================================================
def _check_consistent(ow_, oh_, iw_, ih_, mt_, mb_, ml_, mr_):
    c1_h = abs(ml_ + mr_ - (ow_ - iw_)) < 1.0
    c1_v = abs(mt_ + mb_ - (oh_ - ih_)) < 1.0
    c1_full = c1_h and c1_v and all(x >= 0 for x in [ml_, mr_, mt_, mb_]) and (iw_ < ow_ and ih_ < oh_)
    c2_h = abs(ml_ + mr_ - (oh_ - ih_)) < 1.0
    c2_v = abs(mt_ + mb_ - (ow_ - iw_)) < 1.0
    c2_full = c2_h and c2_v and all(x >= 0 for x in [ml_, mr_, mt_, mb_]) and (ih_ < oh_ and iw_ < ow_)
    return c1_full, c2_full

c1, c2 = _check_consistent(outer_w, outer_h, ocr_iw, ocr_ih, ocr_mt, ocr_mb, ocr_ml, ocr_mr)
_fields_positive = all(x > 0 for x in [outer_w, outer_h, ocr_iw, ocr_ih, ocr_mt, ocr_mb, ocr_ml, ocr_mr])
_ocr_fully_consistent = _fields_positive and (c1 or c2)
print(f"\n步骤2: 双向自洽检测 = {_ocr_fully_consistent}  (Case1={c1}, Case2={c2})")
print(f"  (注：这是 OCR 草图本身8字段的几何自洽性，与 target 无关)")

# ====================================================================
# 步骤3: Bug 4 修复的逻辑 —— target 传入方向反时的处理
# ====================================================================
print(f"\n步骤3: 传入反向 target（{TARGET_W_V}×{TARGET_H_V} 竖版）的决策流程")
# 现在复制我们修复后的决策逻辑
target_outer_w_cm = TARGET_W_V
target_outer_h_cm = TARGET_H_V

_ratio_w = outer_w / target_outer_w_cm if target_outer_w_cm > 0 else 1.0
_ratio_h = outer_h / target_outer_h_cm if target_outer_h_cm > 0 else 1.0
_w_over20 = _ratio_w > 1.20 or _ratio_w < 0.83
_h_over20 = _ratio_h > 1.20 or _ratio_h < 0.83
print(f"  偏差检测：w_ratio={_ratio_w:.2f} → 偏差>20%? {_w_over20}")
print(f"           h_ratio={_ratio_h:.2f} → 偏差>20%? {_h_over20}")

# Bug 4 修复后的分支A
if _ocr_fully_consistent and outer_w > 0 and outer_h > 0:
    _swap_ratio_w = outer_w / target_outer_h_cm if target_outer_h_cm > 0 else 1.0
    _swap_ratio_h = outer_h / target_outer_w_cm if target_outer_w_cm > 0 else 1.0
    _swap_match_w = 0.83 <= _swap_ratio_w <= 1.20
    _swap_match_h = 0.83 <= _swap_ratio_h <= 1.20
    _target_is_swapped_version = _swap_match_w and _swap_match_h
    print(f"\n  [修复后分支A：OCR已自洽]")
    print(f"    检查：swap(target)后匹配度：")
    print(f"      swap_w_ratio={_swap_ratio_w:.2f} (0.83~1.20)? {_swap_match_w}")
    print(f"      swap_h_ratio={_swap_ratio_h:.2f} (0.83~1.20)? {_swap_match_h}")
    print(f"    → target 只是方向反了吗？ {_target_is_swapped_version}")

    if (_w_over20 or _h_over20) and _target_is_swapped_version:
        print(f"    ✓ 修复生效：仅交换 target 方向定义，保持 OCR outer={outer_w:.1f}×{outer_h:.1f} 不变！")
        target_outer_w_cm, target_outer_h_cm = target_outer_h_cm, target_outer_w_cm
        print(f"      交换后 target: {target_outer_w_cm:.1f} × {target_outer_h_cm:.1f}")
        # 重新计算偏差
        _ratio_w = outer_w / target_outer_w_cm if target_outer_w_cm > 0 else 1.0
        _ratio_h = outer_h / target_outer_h_cm if target_outer_h_cm > 0 else 1.0
        _w_over20 = _ratio_w > 1.20 or _ratio_w < 0.83
        _h_over20 = _ratio_h > 1.20 or _ratio_h < 0.83
        print(f"      重新计算偏差：w={_ratio_w:.2f}, h={_ratio_h:.2f} → 偏差>20%? w={_w_over20}, h={_h_over20}")

# 对比：旧代码（Bug）的做法
print(f"\n  [对比旧代码 Bug 做法]：直接覆盖 outer = target:")
_old_outer_w = TARGET_W_V if _w_over20 else outer_w
_old_outer_h = TARGET_H_V if _h_over20 else outer_h
print(f"      旧 outer = {_old_outer_w:.1f} × {_old_outer_h:.1f} (从 OCR 的 {outer_w}×{outer_h} 被覆盖!)")
print(f"      inner 不变 = {ocr_iw} × {ocr_ih}")
_delta_h_old = _old_outer_w - ocr_iw
_delta_v_old = _old_outer_h - ocr_ih
print(f"      delta_h = {_delta_h_old:.1f} (<0? {_delta_h_old<0} → 触发 Phase3 错误交换 inner!)")
print(f"      这就是导致后续一切灾难的根源！")

# ====================================================================
# 步骤4: 强制重算决策（修复后 vs 旧代码）
# ====================================================================
print(f"\n步骤4: 强制重算决策（修复后）")
_need_recalc = False
if (_w_over20 or _h_over20) and not _ocr_fully_consistent:
    _need_recalc = True
    print(f"  ❌ 触发：OCR 偏差>20%且不自洽 → 强制重算")
elif _ocr_fully_consistent:
    print(f"  ✓ OCR 已完全自洽 → 不强制重算！(这是正确路径)")
    _need_recalc = False
else:
    print(f"  ✓ 不触发：OCR 与 target 偏差<20% → 信任 OCR")

print(f"\n  对比旧代码：")
_old_w_ratio = outer_w / TARGET_W_V if TARGET_W_V else 1
_old_h_ratio = outer_h / TARGET_H_V if TARGET_H_V else 1
_old_w_over20 = _old_w_ratio > 1.20 or _old_w_ratio < 0.83
_old_h_over20 = _old_h_ratio > 1.20 or _old_h_ratio < 0.83
_old_fully = False  # 旧代码在覆盖 outer 后又会重算双向自洽，但 outer 已被覆盖为反向！
if (_old_w_over20 or _old_h_over20) and not _old_fully:
    print(f"  ❌ 旧代码：强制重算 = True (因为偏差>20%，且outer被反向覆盖后自洽已破坏!)")
    print(f"      → inner 将被按像素比例逆推为 ~69.56×42.44，边距清零 (就是用户看到的错误!)")

# ====================================================================
# 步骤5: 最终预期结果（修复后）
# ====================================================================
print(f"\n" + "=" * 70)
print("修复后最终预期输出")
print("=" * 70)
# 修复后：outer, inner, margins 都直接来自 OCR 自洽值，不被覆盖不被重算
final_outer_w = outer_w
final_outer_h = outer_h
final_inner_w = ocr_iw
final_inner_h = ocr_ih
final_mt = ocr_mt
final_mb = ocr_mb
final_ml = ocr_ml
final_mr = ocr_mr

print(f"  外框 total:  {final_outer_w} × {final_outer_h} cm  (期望 133 × 60.5) {'✓' if final_outer_w==133 and final_outer_h==60.5 else '✗'}")
print(f"  内挖 inner:  {final_inner_w} × {final_inner_h} cm  (期望 76 × 44.5)  {'✓' if final_inner_w==76 and final_inner_h==44.5 else '✗'}")
print(f"  边距 margins: 上{final_mt} / 下{final_mb} / 左{final_ml} / 右{final_mr} cm")
print(f"               (期望  6 / 10 / 14.6 / 42.4)  {'✓' if (final_mt==6 and final_mb==10 and final_ml==14.6 and final_mr==42.4) else '✗'}")
print(f"  几何校验：")
h_sum_ok = abs(final_outer_w - final_inner_w - (final_ml + final_mr)) < 0.1
v_sum_ok = abs(final_outer_h - final_inner_h - (final_mt + final_mb)) < 0.1
print(f"    水平: outer-inner = {final_outer_w-final_inner_w:.1f}; ml+mr = {final_ml+final_mr:.1f}  {'✓' if h_sum_ok else '✗'}")
print(f"    垂直: outer-inner = {final_outer_h-final_inner_h:.1f}; mt+mb = {final_mt+final_mb:.1f}  {'✓' if v_sum_ok else '✗'}")
