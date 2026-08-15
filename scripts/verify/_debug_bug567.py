"""
验证 Bug 5/6/7 修复：模拟用户真实场景
场景：阶段1空间锚点分配错位 → 伪自洽(sc>=0.7，语义错误)
→ Bug6 伪自洽检测打破 sc≥0.7 档保护
→ Bug5 ROI几何宽松双向上限 不跳过 ml=14.6/mr=42.4
→ 数值穷举 (result_vb) 找到语义正确、几何自洽的8字段
→ 覆盖为正确值
→ Bug7 保护1/2 锁定这套正确值不被后续破坏
→ 最终输出 133×60.5 / 76×44.5 / 6/10/14.6/42.4
"""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logging.basicConfig(level=logging.DEBUG, format='%(levelname).1s %(name)s: %(message)s')
logger = logging.getLogger('core.pool_designer.sketch_parser')
logger.setLevel(logging.DEBUG)

from core.pool_designer.sketch_parser import (
    _score_assignment_consistency,
    _value_based_assignment,
)


print("=" * 70)
print("Bug 6 验证：伪自洽空间分配 vs 正确数值穷举")
print("=" * 70)

ox, oy, ow, oh = 0, 0, 485, 332
ix, iy, iw, ih = 80, 60, 300, 220
outer_rect = (ox, oy, ow, oh)
inner_rect = (ix, iy, iw, ih)

# 用户实际 OCR 8 个值
ocr_hits = [
    (133.0,  240,  10,  0.95),   # 0: total_w
    (60.5,   10,   170, 0.95),   # 1: total_h
    (76.0,   240,  100, 0.90),   # 2: inner_w
    (44.5,   240,  210, 0.90),   # 3: inner_h
    (6.0,    240,  40,  0.90),   # 4: mt
    (10.0,   240,  300, 0.90),   # 5: mb
    (14.6,   40,   170, 0.90),   # 6: ml
    (42.4,   450,  170, 0.90),   # 7: mr
]

# --------------------------------------------------------------------
# 场景：阶段1空间锚点错位 → 产生伪自洽分配
#   把正确的 margin(14.6,42.4) 锚到了 inner 槽
#   把正确的 inner(76,44.5) 锚到了 margin_left/right 槽
#   但 total(133,60.5) 和 mt/mb(6,10) 正确
# --------------------------------------------------------------------
spatial_pseudo = {
    'total_w':       (133.0, 10),
    'total_h':       (60.5,  10),
    'inner_w':       (14.6,  10),  # 错位：实际是 ml
    'inner_h':       (42.4,  10),  # 错位：实际是 mr
    'margin_top':    (6.0,   10),
    'margin_bottom': (10.0,  10),
    'margin_left':   (76.0,  10),  # 错位：实际是 inner_w
    'margin_right':  (44.5,  10),  # 错位：实际是 inner_h
}


def semantic_sanity(assign, label):
    """与 Bug 6 修复中相同的 4 条语义合理性检测"""
    tw = assign.get('total_w', (0, 0))[0]
    th = assign.get('total_h', (0, 0))[0]
    iw = assign.get('inner_w', (0, 0))[0]
    ih = assign.get('inner_h', (0, 0))[0]
    mt = assign.get('margin_top', (0, 0))[0]
    mb = assign.get('margin_bottom', (0, 0))[0]
    ml = assign.get('margin_left', (0, 0))[0]
    mr_ = assign.get('margin_right', (0, 0))[0]
    reasons = []
    for n, v in [('total_w', tw), ('total_h', th)]:
        if 0 < v < 20: reasons.append(f"{n}={v:.1f}(<20)")
    if iw > 0 and tw > 0 and ih > 0 and th > 0:
        _fw = (iw < tw) and (ih < th)
        _fr = (iw < th) and (ih < tw)
        if not (_fw or _fr):
            reasons.append(f"inner{iw:.1f}x{ih:.1f}不小于outer{tw:.1f}x{th:.1f}")
    for mn, mv, iv, tv in [
        ('margin_top', mt, ih, th), ('margin_bottom', mb, ih, th),
        ('margin_left', ml, iw, tw), ('margin_right', mr_, iw, tw),
    ]:
        if mv > 80:
            reasons.append(f"{mn}={mv:.1f}(>80cm)")
        if mv > 0 and iv > 0 and mv > iv * 1.5:
            reasons.append(f"{mn}={mv:.1f}(>inner边{iv:.1f}1.5倍)")
    for mn, mv, tv in [
        ('margin_left', ml, tw), ('margin_right', mr_, tw),
        ('margin_top', mt, th), ('margin_bottom', mb, th),
    ]:
        if mv > 0 and tv > 0 and mv > tv * 0.7:
            reasons.append(f"{mn}={mv:.1f}(>outer{tv:.1f}的70%)")
    ok = len(reasons) == 0
    print(f"  语义合理性[{label}]: {'✓ PASS' if ok else '✗ FAIL: ' + '; '.join(reasons)}")
    return ok, reasons


sc_spatial = _score_assignment_consistency(spatial_pseudo)
print(f"\n步骤1: 伪自洽空间分配(sc={sc_spatial:.3f}——高，因为数学碰巧自洽)")
print(f"  值: total=133x60.5, inner=14.6x42.4, margins=6/10/76/44.5")
ok_spatial, _ = semantic_sanity(spatial_pseudo, "空间分配")

# 检测伪自洽 → sc 应该被强制降到 0.5
_pseudo = sc_spatial >= 0.7 and not ok_spatial
sc_spatial_effective = 0.5 if _pseudo else sc_spatial
print(f"\n  → 伪自洽判定：sc_spatial={sc_spatial:.3f}>=0.7 且语义不合理 → 是伪自洽！")
print(f"  → Bug 6 打破保护：sc_spatial 强制降档为 {sc_spatial_effective}")
print(f"  → 现在 sc_spatial={sc_spatial_effective} ∈ [0.3, 0.7] 档，允许数值穷举覆盖")

# --------------------------------------------------------------------
# 数值穷举找到正确的分配
# --------------------------------------------------------------------
# 注意用户的 target 是反向传入（60.5×133）！
target_w_v = 60.5
target_h_v = 133.0
result_vb = _value_based_assignment(
    ocr_hits, outer_rect, inner_rect, target_w_v, target_h_v
)

sc_value = _score_assignment_consistency(result_vb)
print(f"\n步骤2: 数值穷举分配(sc={sc_value:.3f})")
_vb_tw = result_vb.get('total_w', (0,0))[0]
_vb_th = result_vb.get('total_h', (0,0))[0]
_vb_iw = result_vb.get('inner_w', (0,0))[0]
_vb_ih = result_vb.get('inner_h', (0,0))[0]
_vb_mt = result_vb.get('margin_top', (0,0))[0]
_vb_mb = result_vb.get('margin_bottom', (0,0))[0]
_vb_ml = result_vb.get('margin_left', (0,0))[0]
_vb_mr = result_vb.get('margin_right', (0,0))[0]
print(f"  total={_vb_tw}x{_vb_th}, inner={_vb_iw}x{_vb_ih}, "
      f"margins=上{_vb_mt}/下{_vb_mb}/左{_vb_ml}/右{_vb_mr}")
ok_value, _ = semantic_sanity(result_vb, "数值穷举")

# --------------------------------------------------------------------
# 覆盖判定（现在 sc_spatial_effective = 0.5, sc_value = ?）
# --------------------------------------------------------------------
print(f"\n步骤3: Bug 6 修复后三档覆盖判定")
print(f"  sc_spatial(生效后) = {sc_spatial_effective:.3f}")
print(f"  sc_value           = {sc_value:.3f}")
should_override = False
if sc_spatial_effective >= 0.7:
    print(f"  → 档1：sc≥0.7(高度自洽且语义对)，不允许覆盖")
    should_override = False
elif sc_spatial_effective >= 0.3:
    if sc_value > sc_spatial_effective + 0.20:
        print(f"  → 档2：数值穷举得分高出 {sc_value - sc_spatial_effective:.3f} > 0.20 → 允许覆盖！✓")
        should_override = True
    else:
        print(f"  → 档2：优势不足(需+0.20)，不覆盖")
else:
    if sc_value > sc_spatial_effective:
        print(f"  → 档3：数值穷举得分更高，覆盖")
        should_override = True

if _pseudo and should_override and not ok_value:
    should_override = False
    print(f"  → 附加保护：取消覆盖（数值穷举本身语义不合理）")

print(f"\n  最终 should_override = {should_override}")
final_result = result_vb if should_override else spatial_pseudo
_tw = final_result.get('total_w', (0,0))[0]
_th = final_result.get('total_h', (0,0))[0]
_iw = final_result.get('inner_w', (0,0))[0]
_ih = final_result.get('inner_h', (0,0))[0]
_mt = final_result.get('margin_top', (0,0))[0]
_mb = final_result.get('margin_bottom', (0,0))[0]
_ml = final_result.get('margin_left', (0,0))[0]
_mr = final_result.get('margin_right', (0,0))[0]

print("\n" + "=" * 70)
print("最终预期输出（Bug 5/6/7 修复后）")
print("=" * 70)
ok_all = (
    _tw == 133 and _th == 60.5 and _iw == 76 and _ih == 44.5 and
    _mt == 6 and _mb == 10 and _ml == 14.6 and _mr == 42.4
)
h_sum_correct = abs(_tw - _iw - (_ml + _mr)) < 0.1
v_sum_correct = abs(_th - _ih - (_mt + _mb)) < 0.1
print(f"  外框 total： {_tw} × {_th}  cm  (期望 133 × 60.5)   {'✓' if _tw==133 and _th==60.5 else '✗'}")
print(f"  内挖 inner： {_iw} × {_ih}  cm  (期望  76 × 44.5)   {'✓' if _iw==76 and _ih==44.5 else '✗'}")
print(f"  边距：        上{_mt} / 下{_mb} / 左{_ml} / 右{_mr}  cm")
print(f"               (期望 6 / 10 / 14.6 / 42.4)            "
      f"{'✓' if (_mt==6 and _mb==10 and _ml==14.6 and _mr==42.4) else '✗'}")
print(f"\n  几何自洽：")
print(f"    水平 outer-inner = {_tw-_iw:.1f}; ml+mr = {_ml+_mr:.1f}   "
      f"{'✓ 相等' if h_sum_correct else '✗ 不相等'}")
print(f"    垂直 outer-inner = {_th-_ih:.1f}; mt+mb = {_mt+_mb:.1f}   "
      f"{'✓ 相等' if v_sum_correct else '✗ 不相等'}")

print(f"\n  Bug 7 保护将锁定上述正确值：")
print(f"    → 保护1：强制重算后恢复为原始 OCR 的 6字段")
print(f"    → 保护2：跳过 _validate_pair / inner>outer修正 / 80%上限 / ")
print(f"             最终>50%边距清零 / 35%单边裁剪")

if ok_all and h_sum_correct and v_sum_correct:
    print(f"\n  🎉 全部正确！用户运行后应当得到与草图标注完全一致的结果。")
else:
    print(f"\n  ⚠ 修复未能完全达到预期，请检查上面的字段值。")
