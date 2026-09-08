# 2026-09-05 T5：name_parser 语义校准

## 概述

#7 `test_name_parser` 失败，3 个边界 case（60.5x133 / 50x100 / 100x200）误判长宽方向。用户拍板产品语义后校准测试，**代码零改动**。

***

## 用户拍板

**产品语义权威 = 长边=宽、短边=高，通用规则与水池模式统一。**

> "通用规则：443 行无关键词 → 横版，长边=宽；显式'竖版' → 短边=宽、长边=高。"

***

## 证据链

| 提交 | 日期 | 内容 |
|---|---|---|
| `c5c95aa` | 2026-08-14 | 早期实现"水池按原始顺序"（a=宽、b=高） |
| `f8beb2d` | 2026-08-26 | 把"水池规则"改成"长边为宽"，与通用规则统一 |

- 同步调整：`oriented_outer_w_h_cm` 直接返回 (width, height) 不再二次交换
- 下游 `gui/property_panel_poolbox.py:222-225` 注释明确 "58x121cm → width_cm=121, height_cm=58"
- 即**代码与下游已统一，只有 `tests/integration/test_fix_validation.py` 未跟进**

***

## 修复动作

- 改 `tests/integration/test_fix_validation.py::test_name_parser` 期望值
- 3 个失败 case → 全部按"长边=宽、短边=高"统一标注
- 加 8 行注释说明语义依据（指向 name_parser.py:443+514-530、f8beb2d、poolbox:222-225）
- **代码零改动，仅测试校准**

***

## 回归验证

- `pytest tests/integration/test_fix_validation.py::test_name_parser`：PASSED
- `pytest tests/` 实跑：6 failed / 293 passed / 5 skipped（从 7 failed 降到 6 failed）

***

## 旁支发现（P1 待办，可暂缓）

`core/parser/name_parser.py:514-530` 的水池分支已成为**死代码**：
- 逻辑与 443 行通用规则完全等价
- 唯一的差别是 `logger.debug` 文案
- 删掉可减 18 行代码、避免未来误读
- 不在本次修复范围，标记后续清理
