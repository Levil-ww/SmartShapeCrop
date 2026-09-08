# 2026-09-05 T3：7 个回归诊断与分类

## 概述

修复 17 个失效断言（T2）后，暴露出 7 个被 `return True/False` 掩盖的真实回归。诊断脚本 `.workbuddy/diag/diag7.py`，报告 `ProductSummary/SmartShapeCrop分析报告/SmartShapeCrop-V2.2-回归诊断报告.html`。

***

## 7 个回归清单

| # | 测试 | 现象 |
|---|---|---|
| 1 | `test_complex_pattern_with_gaps` | 间隙区域存在 241 个错误黑色填充（预期 0） |
| 2 | `test_multilayer_border_with_mixed_colors` | tr 与 br 间隙保持白色失败 |
| 3 | `test_extreme_colors_and_anti_aliasing` | 右下角间隙保持白色失败 + 133 个异常像素 |
| 4 | `test_corner_smoothness_no_gap_fill` | 80% 缺口率 |
| 5 | `test_border_smoothness` | 80% 缺口率 |
| 6 | `test_no_wrong_gap_fill` | 56.9% 黑色占比（核心区域错误填充） |
| 7 | `test_name_parser` | 3 个边界 case 误判长宽方向 |

***

## 根因归类（2 类）

### 类别 A：圆角 mask 边界（影响 #1–#6）

共同入口：`core/image_cropper.apply_rounded_corners` + `core/image_cropper_border.apply_border_only_corners`
共同现象：圆弧处边框/间隙的几何关系错位

两个独立 bug 同时存在：
- **Bug A（#4/#6 体现）**：圆弧内侧，间隙被外边框色覆盖（241/879 个像素）
  - 修复方向：`_redraw_border_on_corner` 按边框总厚度 T 做径向偏移，确保只覆盖 [r-T, r]
- **Bug B（#5 体现）**：圆弧外侧，边框被 mask 错误裁切（缺口率 80%）
  - 修复方向：`carve_corner_on_mask` 的 mask 圆心应=边框外边角点，半径=r

非对称失败（#2 tr/br 失败但 tl/bl 通过）指向 `corner_protect_map` 的方向敏感 bug。

### 类别 B：水池模式长宽方向语义分歧（影响 #7）

- 代码逻辑（name_parser.py:514-530）：水池模式统一按"长边为宽、短边为高"
- 测试期望：水池模式按"第一个数字为宽、第二个为高"，高>宽=竖版
- 失败 3 个 case：`60.5x133CM水池`、`50x100CM水池`、`100x200CM裁剪有图`

这是**测试与代码的语义分歧**，需用户拍板。

***

## 下一步建议

1. **先 5 分钟拍板 #7**：查 git log 看这 3 个 case 是回归测试还是新加错误测试
2. **再 30 分钟读码 + 1-2 小时改 #1–#6**：从 `corner_protect_map` 与 `_redraw_border_on_corner` 入手
3. 整套修复预估：**半人天**（含回归测试）
