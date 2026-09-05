"""模拟GUI调用草图解析 - 用于诊断实际运行结果（开发诊断脚本，非 pytest 用例）。

历史说明：
  本文件原位于 tests/gui/test_gui_sim.py。因文件名匹配 pytest 的 test_*.py 规则会被收集，
  而其顶层代码在 import 阶段即执行真实 OCR 解析（且依赖已丢失的 _test_sketch1.png），
  既污染测试环境，又存在"未来若改为抛异常会导致整个测试套件无法收集"的风险。
  2026-09-05 移出 tests/ 并改为下划线前缀，确保不再被 pytest 收集。

运行方式（项目根目录）：
  python scripts/diagnose/_gui_sim_diag.py
"""
import sys
from pathlib import Path

# 以文件位置定位项目根，避免依赖当前工作目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
import logging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s %(message)s')

import core.pool_designer.sketch_parser as sp
# 清除缓存
sp._SKETCH_CACHE.clear()
sp._SKETCH_CONSISTENT_CACHE.clear()

from core.pool_designer.sketch_parser import parse_sketch

# 模拟GUI运行时的参数
# 从截图分析：
# - 画布尺寸: 121.00 x 59.50 (含1cm损耗)
# - 原始外框: 120.00 x 58.50 (不含损耗)
# - 水池模式: TRIM_UI = 1.0

# 测试草图1
sketch_path = 'scripts/diagnose/_test_sketch1.png'
target_outer_w = 120.0  # 外框参考宽
target_outer_h = 58.0   # 外框参考高

print(f"测试文件: {sketch_path}")
print(f"目标尺寸: {target_outer_w} x {target_outer_h} cm")
print()

# 清除缓存后重新解析
sp._SKETCH_CACHE.clear()

result = parse_sketch(
    sketch_path,
    target_outer_w_cm=target_outer_w,
    target_outer_h_cm=target_outer_h,
)

print()
print("=" * 60)
print("解析结果:")
print(f"  成功: {result.success}")
print(f"  消息: {result.message}")
print(f"  方法: {result.method}")
print()
print(f"  外框: {result.outer_w_cm:.2f} x {result.outer_h_cm:.2f} cm")
print(f"  内框: {result.inner_w_cm:.2f} x {result.inner_h_cm:.2f} cm")
print(f"  上: {result.margin_top_cm:.2f} cm")
print(f"  下: {result.margin_bottom_cm:.2f} cm")
print(f"  左: {result.margin_left_cm:.2f} cm")
print(f"  右: {result.margin_right_cm:.2f} cm")

# 模拟GUI的+1cm偏移 (水池模式)
TRIM_UI = 1.0
mt_ui = result.margin_top_cm + TRIM_UI
mb_ui = result.margin_bottom_cm + TRIM_UI
ml_ui = result.margin_left_cm + TRIM_UI
mr_ui = result.margin_right_cm + TRIM_UI

print()
print("GUI显示值 (+1cm 偏移):")
print(f"  上: {mt_ui:.2f} cm")
print(f"  下: {mb_ui:.2f} cm")
print(f"  左: {ml_ui:.2f} cm")
print(f"  右: {mr_ui:.2f} cm")

print()
print("期望值 (草图1): 外框120x58, 内挖57x42, 上6/下10/左10/右53")

# 对比
expected = {
    'outer_w': 120.0, 'outer_h': 58.0,
    'inner_w': 57.0, 'inner_h': 42.0,
    'top': 6.0, 'bottom': 10.0, 'left': 10.0, 'right': 53.0
}
actual = {
    'outer_w': result.outer_w_cm, 'outer_h': result.outer_h_cm,
    'inner_w': result.inner_w_cm, 'inner_h': result.inner_h_cm,
    'top': result.margin_top_cm, 'bottom': result.margin_bottom_cm,
    'left': result.margin_left_cm, 'right': result.margin_right_cm
}

print()
print("误差分析:")
tolerances = {
    'outer_w': 2, 'outer_h': 2,
    'inner_w': 3, 'inner_h': 3,
    'top': 2, 'bottom': 2, 'left': 3, 'right': 3
}
all_ok = True
for key in expected:
    ok = abs(actual[key] - expected[key]) <= tolerances[key]
    if not ok:
        all_ok = False
    print(f"  {key}: {actual[key]:.2f} vs {expected[key]:.1f} (tol={tolerances[key]}) {'OK' if ok else 'FAIL'}")

print()
print(f"总体: {'✅ 通过' if all_ok else '❌ 失败'}")

# 打印debug信息
print()
print("DEBUG信息:")
debug = result.debug
for key in ['ocr_values', 'geo_values']:
    if key in debug:
        print(f"  {key}: {debug[key]}")
