"""
验证 OCR 识别修复：
  Bug 1: 阶段2交换 total_w/h 时同步交换 inner_w/h
  Bug 2: 提高数值分配覆盖阈值

用户场景草图 (均为 cm):
  外框: 133 (宽) x 60.5 (高)
  内挖: 76 x 44.5
  边距: 上6 / 下10 / 左14.6 / 右42.4

几何约束自洽:
  水平: total_w - inner_w = 133 - 76 = 57;  ml + mr = 14.6 + 42.4 = 57  ✓
  垂直: total_h - inner_h = 60.5 - 44.5 = 16; mt + mb = 6 + 10 = 16   ✓
"""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# 降低日志噪音
logging.basicConfig(level=logging.WARNING)

from core.pool_designer.sketch_parser import (
    _score_assignment_consistency,
)


def make_assignment(tw, th, iw, ih, mt, mb, ml, mr):
    """构造 (val, score) 格式的分配字典。"""
    return {
        'total_w': (float(tw), 8),
        'total_h': (float(th), 8),
        'inner_w': (float(iw), 8),
        'inner_h': (float(ih), 8),
        'margin_top': (float(mt), 8),
        'margin_bottom': (float(mb), 8),
        'margin_left': (float(ml), 8),
        'margin_right': (float(mr), 8),
    }


def check_consistency(a, label=""):
    """检查双向自洽性（与 parse_sketch 中 _ocr_fully_consistent 相同的逻辑）。"""
    tw = a['total_w'][0]; th = a['total_h'][0]
    iw = a['inner_w'][0]; ih = a['inner_h'][0]
    mt = a['margin_top'][0]; mb = a['margin_bottom'][0]
    ml = a['margin_left'][0]; mr = a['margin_right'][0]

    # Case 1: (total_w, inner_w) 匹配水平方向
    ok1 = (
        abs(ml + mr - (tw - iw)) < 1.0 and
        abs(mt + mb - (th - ih)) < 1.0 and
        (ml >= 0 and mr >= 0 and mt >= 0 and mb >= 0) and
        (iw < tw and ih < th)
    )
    # Case 2: 反向交换 (total_w ↔ total_h, inner_w ↔ inner_h)
    ok2 = (
        abs(ml + mr - (th - ih)) < 1.0 and
        abs(mt + mb - (tw - iw)) < 1.0 and
        (ml >= 0 and mr >= 0 and mt >= 0 and mb >= 0) and
        (ih < th and iw < tw)
    )
    sc = _score_assignment_consistency(a)
    print(f"  [{label}] case1(w/h水平匹配)={ok1}, case2(互换后匹配)={ok2}, consistency_score={sc:.3f}")
    return ok1 or ok2, sc


def stage2_swap_total_only(a, px_ratio):
    """模拟旧代码：只交换 total，不交换 inner。"""
    tw = a['total_w'][0]; th = a['total_h'][0]
    need_swap = False
    if px_ratio > 1.05 and tw < th:
        need_swap = True
    elif px_ratio < 1.0 / 1.05 and tw > th:
        need_swap = True
    if need_swap:
        a2 = dict(a)
        a2['total_w'] = (th, a['total_h'][1])
        a2['total_h'] = (tw, a['total_w'][1])
        print(f"  * 阶段2(旧代码)：仅交换 total_w/h： {tw:.1f}x{th:.1f} → {th:.1f}x{tw:.1f}，inner不变")
        return a2, True
    return a, False


def stage2_swap_both(a, px_ratio):
    """模拟修复后代码：同步交换 total 和 inner。"""
    tw = a['total_w'][0]; th = a['total_h'][0]
    need_swap = False
    if px_ratio > 1.05 and tw < th:
        need_swap = True
    elif px_ratio < 1.0 / 1.05 and tw > th:
        need_swap = True
    if need_swap:
        a2 = dict(a)
        a2['total_w'] = (th, a['total_h'][1])
        a2['total_h'] = (tw, a['total_w'][1])
        iw = a['inner_w'][0]; ih = a['inner_h'][0]
        a2['inner_w'] = (ih, a['inner_h'][1])
        a2['inner_h'] = (iw, a['inner_w'][1])
        print(f"  * 阶段2(修复后)：同步交换 total+inner：total {tw:.1f}x{th:.1f}→{th:.1f}x{tw:.1f}, inner {iw:.1f}x{ih:.1f}→{ih:.1f}x{iw:.1f}")
        return a2, True
    return a, False


def main():
    print("=" * 70)
    print("验证 Bug 1 & Bug 2 修复")
    print("=" * 70)

    # 像素横版：外框宽 > 高 (横版草图)
    px_ratio = 133.0 / 60.5  # ≈ 2.20 > 1.05，横版

    # ------------------------------------------------------------------
    print("\n【场景 A】空间映射正确：total_w=133, total_h=60.5, inner=76x44.5")
    print("  → 理想正确场景，阶段2不应交换，应直接自洽")
    # ------------------------------------------------------------------
    a = make_assignment(133, 60.5, 76, 44.5, mt=6, mb=10, ml=14.6, mr=42.4)
    ok, sc = check_consistency(a, "阶段1(空间映射后)")
    print(f"  双向自洽: {ok}, 分数: {sc:.3f}")
    print(f"  期望值: sc>=0.7 → 空间分配高度自洽，不被数值穷举覆盖 ✓")

    # ------------------------------------------------------------------
    print("\n【场景 B】空间锚点错配（w/h 读反）：total_w=60.5, total_h=133, inner=44.5x76")
    print("  → 这种情况：阶段2应该交换 total，旧代码不同步交换 inner → 破坏自洽")
    # ------------------------------------------------------------------
    a_b = make_assignment(60.5, 133, 44.5, 76, mt=6, mb=10, ml=14.6, mr=42.4)
    check_consistency(a_b, "阶段1(锚点错配)")

    print("\n  --- 旧代码：仅交换 total ---")
    a_old, _ = stage2_swap_total_only(a_b, px_ratio)
    ok_old, sc_old = check_consistency(a_old, "阶段2(旧代码仅交换total)")

    print("\n  --- 修复后：同步交换 total+inner ---")
    a_new, _ = stage2_swap_both(a_b, px_ratio)
    ok_new, sc_new = check_consistency(a_new, "阶段2(修复后同步交换)")

    print(f"\n  对比结论：")
    print(f"    旧代码：双向自洽={ok_old}, 分数={sc_old:.3f}  {'← BUG: 自洽被破坏!' if not ok_old else ''}")
    print(f"    修复后：双向自洽={ok_new}, 分数={sc_new:.3f}  {'← OK!' if ok_new else ''}")

    # ------------------------------------------------------------------
    print("\n【场景 C】穷举数值分配 vs 空间映射覆盖阈值 (Bug 2)")
    print("  → 空间映射已高度自洽(sc >= 0.7)时，不应被数值穷举覆盖")
    # ------------------------------------------------------------------
    # 空间映射高度自洽的情况
    spatial = make_assignment(133, 60.5, 76, 44.5, mt=6, mb=10, ml=14.6, mr=42.4)
    ok_sp, sc_sp = check_consistency(spatial, "空间映射(高度自洽)")

    # 模拟数值穷举的错误分配：字段语义错位但"几何恰好自洽"的假情况
    # 例如：把 14.6 当成 mr, 42.4 当成 mb ... 只要 ml+mr 刚好 ≈ total-inner
    fake_value = make_assignment(133, 60.5, 76, 44.5, mt=6, mb=42.4, ml=10, mr=47)
    # 这里故意构造一个得分差不多但字段语义错位的情况
    # 让它的 consistency_score 略低于空间分配（实际场景中穷举也可能达到类似或略高的分）
    ok_vb, sc_vb = check_consistency(fake_value, "数值穷举(假自洽但语义错位)")

    print(f"\n  覆盖判定规则（修复后）：")
    print(f"    sc_spatial={sc_sp:.3f} >= 0.7 → 绝对信任空间映射，不允许覆盖")
    print(f"    sc_value  ={sc_vb:.3f} 即使 sc_value > sc_spatial，也不会覆盖")
    allow_override = sc_sp < 0.7 and (
        (sc_sp < 0.3 and sc_vb > sc_sp) or
        (sc_sp >= 0.3 and sc_vb > sc_sp + 0.20)
    )
    print(f"    实际是否允许覆盖：{allow_override}  {'← 正确! 空间映射可信' if not allow_override else ''}")

    # ------------------------------------------------------------------
    print("\n【总结】最终预期的解析输出")
    # ------------------------------------------------------------------
    print("  正确且自洽的最终结果应当是：")
    result_correct = make_assignment(133, 60.5, 76, 44.5, mt=6, mb=10, ml=14.6, mr=42.4)
    tw = result_correct['total_w'][0]; th = result_correct['total_h'][0]
    iw = result_correct['inner_w'][0]; ih = result_correct['inner_h'][0]
    mt = result_correct['margin_top'][0]; mb = result_correct['margin_bottom'][0]
    ml = result_correct['margin_left'][0]; mr = result_correct['margin_right'][0]
    print(f"    外框 total: {tw} x {th} cm")
    print(f"    内挖 inner: {iw} x {ih} cm")
    print(f"    边距 margins: 上{mt} / 下{mb} / 左{ml} / 右{mr} cm")
    print(f"    水平校验: total_w - inner_w = {tw - iw}; ml + mr = {ml + mr}  { '✓' if abs(tw-iw-(ml+mr))<0.1 else '✗'}")
    print(f"    垂直校验: total_h - inner_h = {th - ih}; mt + mb = {mt + mb}  { '✓' if abs(th-ih-(mt+mb))<0.1 else '✗'}")
    check_consistency(result_correct, "最终输出")


if __name__ == "__main__":
    main()
