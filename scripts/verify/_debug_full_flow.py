"""
完整复现用户场景的调试脚本：
模拟 8 个 OCR 识别值全部正确 [133, 76, 60.5, 44.5, 42.4, 14.6, 10, 6]
目标尺寸 target: 133 x 60.5 cm (横版，经过 PoolRenderWorker 修正后的文件名尺寸)
像素外框 ow=485, oh=332 → px_ratio=1.46 (竖版！因为草图是竖拍的图片)
像素内框 iw, ih (假设)
"""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logging.basicConfig(
    level=logging.DEBUG,
    format='%(levelname).1s %(name)s: %(message)s'
)
logger = logging.getLogger('core.pool_designer.sketch_parser')
logger.setLevel(logging.DEBUG)

from core.pool_designer.sketch_parser import (
    _assign_ocr_values_to_fields,
    _score_assignment_consistency,
    _value_based_assignment,
    _enumerate_assignments,
    _validate_and_fix_margins,
)


# ====================================================================
# 用户场景
# ====================================================================
TARGET_W = 133.0
TARGET_H = 60.5
# 8 个 OCR 识别值（都正确识别），按从大到小排序
# 实际空间位置（草图中的）：
#   total_w = 133 (上标注，横边)
#   total_h = 60.5 (左标注，竖边)
#   inner_w = 76 (内挖，横边)
#   inner_h = 44.5 (内挖，竖边)
#   mr = 42.4 (右边距)
#   ml = 14.6 (左边距)
#   mb = 10 (下边距)
#   mt = 6 (上边距)

# 模拟 OCR hits 格式: (value, center_x, center_y, confidence)
# 假设图像尺寸: ox, oy, ow, oh = 0, 0, 485, 332
ox, oy, ow, oh = 0, 0, 485, 332
# 内框像素坐标（假设）
ix, iy, iw, ih = 80, 60, 300, 220
outer_rect = (ox, oy, ow, oh)
inner_rect = (ix, iy, iw, ih)
print(f"=== 用户场景 ===")
print(f"目标尺寸 target_outer: {TARGET_W} × {TARGET_H} cm (横版 2.20:1)")
print(f"像素外框: {ow}×{oh} → 比例 {ow/oh:.2f} (实际图片是 1.46:1，横版但比例不足)")
print(f"8个OCR值: [133, 76, 60.5, 44.5, 42.4, 14.6, 10, 6]")
print()

# 构造模拟 OCR hits（使用合理的空间位置）
ocr_hits = [
    # (val, xc, yc, conf)
    (133.0,  240,  10,  0.95),  # total_w: 上边缘中心
    (60.5,   10,   170, 0.95),  # total_h: 左边缘中心
    (76.0,   240,  100, 0.90),  # inner_w: 内框上半
    (44.5,   240,  210, 0.90),  # inner_h: 内框下半
    (6.0,    240,  40,  0.90),  # mt: 上边
    (10.0,   240,  300, 0.90),  # mb: 下边
    (14.6,   40,   170, 0.90),  # ml: 左边
    (42.4,   450,  170, 0.90),  # mr: 右边
]

# ====================================================================
# 步骤1：调用 _assign_ocr_values_to_fields
# ====================================================================
print("=" * 70)
print("步骤1: 调用 _assign_ocr_values_to_fields (内部含阶段2同步交换修复)")
print("=" * 70)
result_assign = _assign_ocr_values_to_fields(
    ocr_hits, outer_rect, inner_rect,
    oh, ow,  # h_img=oh(332), w_img=ow(485)
    target_w_hint=TARGET_W, target_h_hint=TARGET_H,
)
print("\n最终分配结果:")
for field in ['total_w', 'total_h', 'inner_w', 'inner_h',
              'margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
    val, score = result_assign.get(field, (0, 0))
    expected = {
        'total_w': 133.0, 'total_h': 60.5,
        'inner_w': 76.0, 'inner_h': 44.5,
        'margin_top': 6.0, 'margin_bottom': 10.0,
        'margin_left': 14.6, 'margin_right': 42.4,
    }.get(field, 0)
    status = "✓" if abs(val - expected) < 0.01 else ("✗ 应为 {}".format(expected))
    print(f"  {field:15s} = {val:6.2f}  (得分={score}) {status}")

tw = result_assign.get('total_w', (0, 0))[0]
th = result_assign.get('total_h', (0, 0))[0]
iw_v = result_assign.get('inner_w', (0, 0))[0]
ih_v = result_assign.get('inner_h', (0, 0))[0]
mt = result_assign.get('margin_top', (0, 0))[0]
mb = result_assign.get('margin_bottom', (0, 0))[0]
ml = result_assign.get('margin_left', (0, 0))[0]
mr = result_assign.get('margin_right', (0, 0))[0]

print()
print("几何约束检查:")
print(f"  水平: total_w - inner_w = {tw - iw_v:.2f}; ml + mr = {ml + mr:.2f}")
print(f"  垂直: total_h - inner_h = {th - ih_v:.2f}; mt + mb = {mt + mb:.2f}")
sc = _score_assignment_consistency(result_assign)
print(f"  一致性得分 sc_spatial = {sc:.3f}")

# ====================================================================
# 步骤2：单独调用 _value_based_assignment，看它返回什么方案
# ====================================================================
print()
print("=" * 70)
print("步骤2: 单独调用 _value_based_assignment，分析穷举方案")
print("=" * 70)
result_vb = _value_based_assignment(
    ocr_hits, outer_rect, inner_rect,
    TARGET_W, TARGET_H
)
sc_vb = _score_assignment_consistency(result_vb)
print(f"\n数值穷举得分 sc_value = {sc_vb:.3f}")
print("数值穷举分配结果:")
for field in ['total_w', 'total_h', 'inner_w', 'inner_h',
              'margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
    val, score = result_vb.get(field, (0, 0))
    expected = {
        'total_w': 133.0, 'total_h': 60.5,
        'inner_w': 76.0, 'inner_h': 44.5,
        'margin_top': 6.0, 'margin_bottom': 10.0,
        'margin_left': 14.6, 'margin_right': 42.4,
    }.get(field, 0)
    status = "✓" if abs(val - expected) < 0.01 else ("✗ 应为 {}".format(expected))
    print(f"  {field:15s} = {val:6.2f}  (得分={score}) {status}")

# ====================================================================
# 步骤3：_enumerate_assignments 的所有候选方案打分
# ====================================================================
print()
print("=" * 70)
print("步骤3: 穷举方案 _enumerate_assignments 的 Top 候选")
print("=" * 70)
ocr_with_pos = [(v, x, y, min(10, max(1, int(c*10)))) for v, x, y, c in ocr_hits if v > 0]
sorted_hits_value = sorted([(v, c) for v, x, y, c in ocr_with_pos if v > 0],
                           key=lambda x: x[0], reverse=True)
print(f"sorted_hits (按数值降序): {[v for v, c in sorted_hits_value]}")
candidates = _enumerate_assignments(sorted_hits_value, TARGET_W, TARGET_H)
print(f"\n候选方案数量: {len(candidates)}")
# 为每个候选打分并排序
scored = []
for idx, cand in enumerate(candidates):
    s = _score_assignment_consistency(cand)
    scored.append((s, idx, cand))
scored.sort(key=lambda t: t[0], reverse=True)

for rank, (s, idx, cand) in enumerate(scored[:5], 1):
    tw = cand.get('total_w', (0, 0))[0]
    th = cand.get('total_h', (0, 0))[0]
    iw = cand.get('inner_w', (0, 0))[0]
    ih = cand.get('inner_h', (0, 0))[0]
    mt = cand.get('margin_top', (0, 0))[0]
    mb = cand.get('margin_bottom', (0, 0))[0]
    ml = cand.get('margin_left', (0, 0))[0]
    mr = cand.get('margin_right', (0, 0))[0]
    print(f"\nTop{rank} (候选#{idx}, 得分={s:.3f}):")
    print(f"  total: {tw} × {th}")
    print(f"  inner: {iw} × {ih}")
    print(f"  margins: 上{mt}/下{mb}/左{ml}/右{mr}")
    print(f"  水平校验: total-inner={tw-iw:.1f}, ml+mr={ml+mr:.1f}  {'✓' if abs(tw-iw-(ml+mr))<0.5 else '✗'}")
    print(f"  垂直校验: total-inner={th-ih:.1f}, mt+mb={mt+mb:.1f}  {'✓' if abs(th-ih-(mt+mb))<0.5 else '✗'}")

# ====================================================================
# 步骤4：模拟 parse_sketch 中的双向自洽检测 (_ocr_fully_consistent)
# ====================================================================
print()
print("=" * 70)
print("步骤4: 模拟 parse_sketch 中的双向自洽检测 _ocr_fully_consistent")
print("=" * 70)

def _check_consistent(ow_, oh_, iw_, ih_, mt_, mb_, ml_, mr_):
    c1_h = abs(ml_ + mr_ - (ow_ - iw_)) < 1.0
    c1_v = abs(mt_ + mb_ - (oh_ - ih_)) < 1.0
    c1_full = c1_h and c1_v and all(x >= 0 for x in [ml_, mr_, mt_, mb_]) and (iw_ < ow_ and ih_ < oh_)
    c2_h = abs(ml_ + mr_ - (oh_ - ih_)) < 1.0
    c2_v = abs(mt_ + mb_ - (ow_ - iw_)) < 1.0
    c2_full = c2_h and c2_v and all(x >= 0 for x in [ml_, mr_, mt_, mb_]) and (ih_ < oh_ and iw_ < ow_)
    return c1_h, c1_v, c1_full, c2_h, c2_v, c2_full

# 使用 result_assign（空间映射+同步交换修复）的输出
# 但要注意 parse_sketch 使用的 outer_w/h 是来自 result_assign 的
outer_w = result_assign.get('total_w', (0, 0))[0]
outer_h = result_assign.get('total_h', (0, 0))[0]
ocr_iw = result_assign.get('inner_w', (0, 0))[0]
ocr_ih = result_assign.get('inner_h', (0, 0))[0]
ocr_mt = result_assign.get('margin_top', (0, 0))[0]
ocr_mb = result_assign.get('margin_bottom', (0, 0))[0]
ocr_ml = result_assign.get('margin_left', (0, 0))[0]
ocr_mr = result_assign.get('margin_right', (0, 0))[0]

print(f"分配后的值: total={outer_w}×{outer_h}, inner={ocr_iw}×{ocr_ih}, margins 上{ocr_mt}/下{ocr_mb}/左{ocr_ml}/右{ocr_mr}")

c1h, c1v, c1f, c2h, c2v, c2f = _check_consistent(
    outer_w, outer_h, ocr_iw, ocr_ih, ocr_mt, ocr_mb, ocr_ml, ocr_mr
)
_ocr_fields_positive = all(x > 0 for x in [outer_w, outer_h, ocr_iw, ocr_ih, ocr_mt, ocr_mb, ocr_ml, ocr_mr])
_ocr_fully_consistent = _ocr_fields_positive and (c1f or c2f)

print(f"\n_ocr_fields_positive（8字段全正）: {_ocr_fields_positive}")
print(f"Case1（原始方向）: h={c1h}, v={c1v}, full={c1f}")
print(f"Case2（互换方向）: h={c2h}, v={c2v}, full={c2f}")
print(f"★ _ocr_fully_consistent = {_ocr_fully_consistent}")

# ====================================================================
# 步骤5：强制重算决策
# ====================================================================
print()
print("=" * 70)
print("步骤5: 模拟强制重算决策 (_need_recalc_from_target)")
print("=" * 70)

_ratio_w = outer_w / TARGET_W if TARGET_W > 0 else 1.0
_ratio_h = outer_h / TARGET_H if TARGET_H > 0 else 1.0
_w_over20 = _ratio_w > 1.20 or _ratio_w < 0.83
_h_over20 = _ratio_h > 1.20 or _ratio_h < 0.83
print(f"外框 w 与目标偏差: {outer_w:.1f} vs {TARGET_W:.1f} → 比率={_ratio_w:.2f} → 偏差>20%? {_w_over20}")
print(f"外框 h 与目标偏差: {outer_h:.1f} vs {TARGET_H:.1f} → 比率={_ratio_h:.2f} → 偏差>20%? {_h_over20}")

if (_w_over20 or _h_over20) and not _ocr_fully_consistent:
    print("★ 强制重算触发: OCR偏差>20% AND 不自洽 → 将按目标尺寸强制重算内框和边距")
    print("  → 这会导致内挖尺寸变成 69.56×42.44 (由像素比/典型比例逆推)，边距归零!")
    print("  → 这正是用户日志中看到的现象!")
elif (_w_over20 or _h_over20) and _ocr_fully_consistent:
    print("★ 不触发强制重算: OCR偏差>20%但几何自洽 → 仅覆盖外框，不强制重算 (正确路径)")
else:
    print("★ 不触发强制重算: OCR与目标一致或偏差<20% → 信任OCR值")
    if not _ocr_fully_consistent:
        print("  警告：OCR 不自洽，但由于偏差<20%，仍不强制重算（可能边距需要手动调整）")
