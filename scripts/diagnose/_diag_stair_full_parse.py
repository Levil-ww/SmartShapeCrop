# -*- coding: utf-8 -*-
"""诊断3：真实阶梯草图跑完整 parse_lshape_sketch，看用户今天实际拿到什么（只读）。"""
import sys, logging
sys.path.insert(0, r'F:/SmartShapeCrop')
logging.basicConfig(level=logging.ERROR)

P = r'E:\智能裁剪设计器\测试草图文件-L型挖角\吸水皮革-定制-裁剪有图-安妮森林;55x93.5CM裁剪有图.png'

from services.sketch_parser.lshape_sketch_parser import parse_lshape_sketch

res = parse_lshape_sketch(P, target_outer_w_cm=93.5, target_outer_h_cm=55.0)
print('=== parse_lshape_sketch 结果 ===')
print('success          :', res.success)
print('message          :', res.message)
print('notches_detected :', res.notches_detected)
print('notches_consumed :', res.notches_consumed)
print('corner           :', getattr(res, 'corner', None))
print('outer_w_cm       :', getattr(res, 'outer_w_cm', None))
print('outer_h_cm       :', getattr(res, 'outer_h_cm', None))
print('cut_w_cm         :', getattr(res, 'cut_w_cm', None))
print('cut_h_cm         :', getattr(res, 'cut_h_cm', None))
print('cuts_cm          :', getattr(res, 'cuts_cm', None))
print()
dbg = getattr(res, 'debug', {}) or {}
print('debug keys:', list(dbg.keys()))
print('  all_corners:', dbg.get('all_corners'))
print('  cuts_cm    :', dbg.get('cuts_cm'))
print('  n_consumed :', dbg.get('n_consumed'))
print('  g1_blocked :', dbg.get('g1_blocked'))
print()
print('=== 对照真实答案 ===')
print('  真实 outer = 93.5 x 55')
print('  真实阶梯 = 两级: 级1(深6.5,宽10) 级2(深3.5,宽8.5)')
print('  真实总挖除 = 6.5x10(右上角) + 10x8.5(整体)  → 等效 = ')
print('      开挖: 上边从 x=75 起退 6.5cm 高; 右边从 y=? ')
