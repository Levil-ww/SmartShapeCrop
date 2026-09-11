"""验证草图解析修复效果（对比期望值与识别值，开发诊断脚本，非 pytest 用例）。

历史说明：
  本文件原位于 tests/sketch/test_sketch_fix.py。因文件名匹配 pytest 的 test_*.py
  规则会被收集，而其顶层代码在 import 阶段即调用 parse_sketch 执行真实 OCR 解析
  （并依赖可能缺失的 _test_sketch1.png），既污染测试环境，又存在"未来若改为抛异常
  会导致整个测试套件无法收集"的风险（2026-08-26 起由 tests/conftest.py 的
  collect_ignore 屏蔽）。2026-09-11 移出 tests/ 并改为 _diag_ 前缀。

运行方式（项目根目录）：
  python scripts/diagnose/_diag_sketch_fix.py
"""
import sys, os, time
from pathlib import Path

# 以文件位置定位项目根，避免依赖当前工作目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

import core.pool_designer.sketch_parser as sp
sp._SKETCH_CACHE.clear()
sp._SKETCH_CONSISTENT_CACHE.clear()

from core.pool_designer.sketch_parser import parse_sketch

all_ok = True

# Test sketch 1
print('='*60)
print('测试草图1: _test_sketch1.png')
print('期望: 外框120x58, 内挖57x42, 上6/下10/左10/右53')
result1 = parse_sketch('scripts/diagnose/_test_sketch1.png', target_outer_w_cm=120.0, target_outer_h_cm=58.0)
print()
print(f'最终结果:')
print(f'识别: 外框{result1.outer_w_cm}x{result1.outer_h_cm}, 内挖{result1.inner_w_cm}x{result1.inner_h_cm}')
print(f'边距: 上{result1.margin_top_cm}/下{result1.margin_bottom_cm}/左{result1.margin_left_cm}/右{result1.margin_right_cm}')
print(f'成功: {result1.success}')
print()
debug = result1.debug
print(f'DEBUG ocr_values={debug.get("ocr_values", {})}')
print(f'DEBUG geo_values={debug.get("geo_values", {})}')

print()
print('误差分析:')
checks1 = [
    ('外框宽', result1.outer_w_cm, 120.0, 2),
    ('外框高', result1.outer_h_cm, 58.0, 2),
    ('内挖宽', result1.inner_w_cm, 57.0, 3),
    ('内挖高', result1.inner_h_cm, 42.0, 3),
    ('上边距', result1.margin_top_cm, 6.0, 2),
    ('下边距', result1.margin_bottom_cm, 10.0, 2),
    ('左边距', result1.margin_left_cm, 10.0, 3),
    ('右边距', result1.margin_right_cm, 53.0, 3),
]
for name, actual, expected, tol in checks1:
    ok = abs(actual - expected) <= tol
    if not ok:
        all_ok = False
    print(f'  {name}: {actual:.1f} vs {expected:.1f} (tol={tol}) {"OK" if ok else "FAIL"}')

print()

# Test sketch 2
print('='*60)
print('测试草图2: _test_sketch2.png')
print('期望: 外框234x60, 内挖86x45, 上6/下9/左36/右112')
result2 = parse_sketch('scripts/diagnose/_test_sketch2.png', target_outer_w_cm=234.0, target_outer_h_cm=60.0)
print()
print(f'最终结果:')
print(f'识别: 外框{result2.outer_w_cm}x{result2.outer_h_cm}, 内挖{result2.inner_w_cm}x{result2.inner_h_cm}')
print(f'边距: 上{result2.margin_top_cm}/下{result2.margin_bottom_cm}/左{result2.margin_left_cm}/右{result2.margin_right_cm}')
print(f'成功: {result2.success}')
print()
debug = result2.debug
print(f'DEBUG ocr_values={debug.get("ocr_values", {})}')
print(f'DEBUG geo_values={debug.get("geo_values", {})}')

print()
print('误差分析:')
checks2 = [
    ('外框宽', result2.outer_w_cm, 234.0, 2),
    ('外框高', result2.outer_h_cm, 60.0, 2),
    ('内挖宽', result2.inner_w_cm, 86.0, 3),
    ('内挖高', result2.inner_h_cm, 45.0, 3),
    ('上边距', result2.margin_top_cm, 6.0, 2),
    ('下边距', result2.margin_bottom_cm, 9.0, 2),
    ('左边距', result2.margin_left_cm, 36.0, 3),
    ('右边距', result2.margin_right_cm, 112.0, 3),
]
for name, actual, expected, tol in checks2:
    ok = abs(actual - expected) <= tol
    if not ok:
        all_ok = False
    print(f'  {name}: {actual:.1f} vs {expected:.1f} (tol={tol}) {"OK" if ok else "FAIL"}')

print()
print('='*60)
print(f'总体结果: {"✅ 所有测试通过" if all_ok else "❌ 部分测试失败"}')
