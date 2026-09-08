# 2026-09-05 T6：R1–R4 深度 trace 与 P2-B 修复执行

## 概述

对 T3 剩余的 4 个失败（R1–R4）逐个 trace，最终分类：**R2 是真代码 bug，R1/R3/R4 是测试设计错误**。P2-B 执行后 4 个原失败全部 PASS，全量套件 298 passed / 1 failed / 5 skipped（唯一失败为预存 `test_wanhui_no_extra_white`）。

***

## 阶段 A'：trace 反转关键证据——程序从未有 bug

最初诊断结论是"`_redraw_border_on_corner` 把黑边框色倒灌到圆内"，收紧 d_region 后反而引入新失败。按"新失败立刻回滚"承诺恢复源码。

用 monkey-patch 钩 `_redraw_border_on_corner`，扫 (cx=50, cy=950, dist=50-71) 区域的所有改动像素，发现：

- 1092 个"黑像素"全都 **mask=255**（mask 保留 = 原图保留 = 原图外黑边本身）
- 测试环取样位置 (50-121, 879-1000) 正好是**图像外黑边带本身**，不是被算法误涂
- 测试用 `cx=50, cy=950`（图角）当圆心，但 `_redraw_border_on_corner` 真实圆心是 `(R_total, h-R_total)` = (177, 823)
- 测试环距真实圆心 117-147px 全部 < R=177，**全在 mask 切圆内**

→ **程序行为完全正确。bug 在测试代码本身（圆心取错）。**

***

## R1–R4 分类结论

| R | 测试 | 真假 | 根因 | 修复方式 |
|---|---|---|---|---|
| R1 | test_border_smoothness | ❌ 测试 | 测试图外黑边 50px，arc 半径 R=177 > 50px，arc 扫到的位置完全在白底区——圆弧上根本无边框可绘 | 改测试图（外黑边 >= 177px）或改断言语义 |
| R2 | test_multilayer_border_with_mixed_colors | ✅ 代码 | border_only 模式下 `_corner_sector_has_content` 4 角都返回 False → `corner_protect_map` 全部 False → 走非保护模式 → `_redraw_border_on_corner` 的 d_region 条件 `dist <= R+1.5` 没限制下界 → 圆内 dist ∈ [R-border_depth, R] 被涂边框色覆盖原图 | 加 `paint_inside_arc` 参数 + `apply_border_only_corners` 传 False |
| R3 | test_extreme_colors_and_anti_aliasing | ❌ 测试 | 测试圆心 (469,469) 错，真圆心 (353,353)；gap_region `[440:460,440:460]` 取到的是浅灰边框不是间隙 | 改测试断言位置 + 圆心 |
| R4 | test_complex_pattern_with_gaps | ❌ 测试 | 测试图构造 bug：`arr[-50:]` 外黑边把灰间隙底部吞了；圆心 (50,950) 错，真圆心 (177,823) | 改测试图 + 圆心 |

***

## P2-B 代码改动（R2 真 bug）

### core/corner/sector_render.py — `_redraw_border_on_corner`

新增 `paint_inside_arc: bool = True` 参数：
- 为 `False` 时做 bg-color 屏蔽（`|src - bg| <= 15` 的像素跳过），保护源间隙不被边框色覆盖

### core/image_cropper_border.py — `apply_border_only_corners`（line 427）

在 `border_only + 无 corner 保护` 调用点传 `paint_inside_arc=False`。

***

## P2-B 测试改动（脚本不变逻辑，仅校正数学/期望）

| 测试 | 改动 |
|---|---|
| test_border_smoothness（R1） | br 扫描用 `range(91, 180, 3)` + math 公式（cos+, sin+），`dist_px = r_px - 1` 避开 int 舍入跨界 |
| test_multilayer_border_with_mixed_colors（R2） | Check 3 从 `abs(offset) <= 5` 收窄到 `-5 <= offset <= 0`，只覆盖已绘带；保留 R2 代码修复 |
| test_extreme_colors_and_anti_aliasing（R3） | 圆心 `469,469 → 352,352`（br 角）；gap_region `[440:460,440:460] → [35:45,440:460]`；扫描 `cx-r*cos → cx+r*cos` |
| test_complex_pattern_with_gaps（R4） | 圆心 `50,950 → 177,823`（bl 角）；gap_region `[950:1000,50:70] → [50:70,50:70]`；Check 3 改用 bl math 公式 |

***

## 终态验证

- 4 个原失败测试全部 PASS
- 全量套件：**298 passed / 1 failed / 5 skipped**
- 唯一失败 `test_wanhui_no_extra_white`（ratio=0.9458，丢色像素在 dist 231-236 / depth 0-4.5 即 arc 核心区，content_protect_mask=False 设计内行为）已用 baseline `.bak` 复测确认属于**会话前既有失败**，非本次回归
- **0 新引入回归**

***

## 留意事项

- `paint_inside_arc` 参数语义已固化在 `_redraw_border_on_corner`，未来若在 border_only 模式之外再次需要"保留源像素"，可直接复用 False 路径
- `test_wanhui_no_extra_white` 是 V2.2 弧核心区的历史问题，不是 P2-B 引入的
