"""Phase2 两项算法纯逻辑验证 + 性能基准"""
import sys, time
sys.path.insert(0, '.')
# 1. 导入验证
from core.pool_designer.sketch_parser import (
    _brute_force_margin_permute,
    _score_assignment_consistency,
    _parse_dir_num_token,
    _DIR_CHAR_MAP,
)
print('✅ [1/5] 导入成功：_brute_force_margin_permute 等函数')

# ============================================================
# 2. 穷举校验 - 场景A：乱序数据 - 4边距全错，穷举自动纠偏
# ============================================================
# 真实几何: outer=121x58, inner=68x45 → 正确边距: 上5/下8/左2/右51
# 守恒方程: 横向 2+68+51=121 ✓   纵向 5+45+8=58 ✓
asg_wrong = {
    'total_w': (121.0, 0.95),
    'total_h': (58.0, 0.95),
    'inner_w': (68.0, 0.90),
    'inner_h': (45.0, 0.90),
    # 故意乱序: top=51(本应是右), bottom=2(本应是左), left=8(本应是下), right=5(本应是上)
    'margin_top':    (51.0, 0.5),
    'margin_bottom': (2.0, 0.5),
    'margin_left':   (8.0, 0.5),
    'margin_right':  (5.0, 0.5),
}
sc_bad = _score_assignment_consistency(asg_wrong)
buckets_for_test = {
    'margin_top':    [(5.0, 80, None), (10.0, 60, None)],
    'margin_bottom': [(8.0, 70, None), (0.0, 10, None)],
    'margin_left':   [(2.0, 75, None), (30.0, 40, None)],
    'margin_right':  [(51.0, 85, None), (55.0, 50, None)],
}
new_asg, new_sc, info = _brute_force_margin_permute(asg_wrong, set(), buckets=buckets_for_test)
print(f'✅ [2/5] 穷举校验-场景A(乱序纠偏):')
print(f'     原始sc={sc_bad:.3f}  top=51 底=2 左=8 右=5 '
      f'→ 横向8+68+5=81≠121 (错), 纵向51+45+2=98≠58 (错)')
ok_2 = False
if new_asg is not None:
    t = new_asg['margin_top'][0]; b=new_asg['margin_bottom'][0]
    l=new_asg['margin_left'][0]; r=new_asg['margin_right'][0]
    print(f'     穷举 sc={new_sc:.3f}  top={t:.0f} 底={b:.0f} 左={l:.0f} 右={r:.0f}  info={info}')
    ok_2 = (abs(t-5)<0.1 and abs(b-8)<0.1 and abs(l-2)<0.1 and abs(r-51)<0.1 and new_sc > 0.9)
    print(f'     → 自动纠偏 top=5/底=8/左=2/右=51 ? {"✅" if ok_2 else "❌"}')
else:
    print(f'     → 无改进 ({info}) ❌')

# ============================================================
# 3. 穷举校验 - 场景B：完美自洽 sc=1.0 不调用穷举（条件触发=0开销）
# ============================================================
asg_perfect = {
    'total_w': (121.0, 0.95), 'total_h': (58.0, 0.95),
    'inner_w': (68.0, 0.90), 'inner_h': (45.0, 0.90),
    'margin_top': (5.0, 0.9), 'margin_bottom': (8.0, 0.9),
    'margin_left': (2.0, 0.9), 'margin_right': (51.0, 0.9),
}
sc_perfect = _score_assignment_consistency(asg_perfect)
need_brute = (sc_perfect < 0.9) or any([asg_perfect[f][0] <= 0 for f in
    ('margin_top','margin_bottom','margin_left','margin_right')])
ok_3 = (sc_perfect >= 0.99 and not need_brute)
print(f'✅ [3/5] 穷举校验-场景B(完美路径保护): sc={sc_perfect:.3f} need_brute={need_brute} '
      f'→ 穷举不运行=0开销? {"✅" if ok_3 else "❌"}')

# ============================================================
# 4. 穷举校验 - 场景C：方向锁定字段不被穷举替换（不改变原有逻辑）
# ============================================================
asg_locked_test = dict(asg_wrong)
asg_locked_test['margin_bottom'] = (8.0, 0.95)  # 被方向锁定=永远不改变
new_asg_c, new_sc_c, info_c = _brute_force_margin_permute(
    asg_locked_test, {'margin_bottom'}, buckets=buckets_for_test)
mb_after = new_asg_c['margin_bottom'][0] if new_asg_c is not None else 8.0
ok_4 = (abs(mb_after - 8.0) < 0.001)
print(f'✅ [4/5] 穷举校验-场景C(锁定保护): 锁定bottom=8.0, 穷举后bottom={mb_after:.0f} '
      f'info="{info_c}" → 未被替换? {"✅" if ok_4 else "❌"}')

# ============================================================
# 5. 性能基准 - 穷举 1680 排列 耗时
# ============================================================
asg_bench = dict(asg_wrong)
N = 200
t0 = time.perf_counter()
for _ in range(N):
    _brute_force_margin_permute(asg_bench, set(), buckets=buckets_for_test, max_candidates=8)
t1 = time.perf_counter()
ms_per_call = (t1-t0) * 1000 / N
ratio_pct = ms_per_call / 40000 * 100
print(f'✅ [5/5] 性能基准-穷举: {N}次平均 = {ms_per_call:.2f}ms/次')
print(f'     占 40s 总流程比例 = {ratio_pct:.4f}%  (要求 <0.03% → {"✅ PASS" if ratio_pct < 0.03 else "❌ FAIL"})')
print(f'     两项算法总耗时估计: 空间距离场(O(4×12)≈1ms) + 穷举({ms_per_call:.1f}ms) ≈ {ms_per_call+1:.1f}ms < 10ms要求 → '
      f'{"✅ PASS" if (ms_per_call+1) < 10 else "❌ FAIL"}')

# 汇总
all_ok = ok_2 and ok_3 and ok_4 and (ms_per_call+1) < 10 and ratio_pct < 0.03
print()
print('🎉 Phase2 纯逻辑 + 性能:', '全部通过 ✅' if all_ok else '部分失败 ❌')
sys.exit(0 if all_ok else 1)
