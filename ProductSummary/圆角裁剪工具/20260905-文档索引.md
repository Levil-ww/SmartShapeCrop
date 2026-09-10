# 2026年09月05日 圆角裁剪工具 - 文档索引

## 目录说明

本目录集中收录 2026年9月5日 与「圆角裁剪边框渲染」相关的全部产品总结与技术文档。

全日圆角相关 **3 大主题簇**，核心为：7 个回归诊断分类（6 个圆角 mask 边界 + 1 个 name_parser 语义）、圆角断触/白缝/粗细不一致修复（回退多层结构感知重绘）、R1–R4 深度 trace 与 P2-B 修复（R2 真代码 bug 修 paint_inside_arc + R1/R3/R4 测试设计错误修正）。

## 顶层总览

- `ProductSummary/2026-09/20260905-任务分类整理总结.md` — 全日跨模块聚合总结
- 9.5 其余主题（V2.2 体检 / P0 失效断言修复 / name_parser 校准 / 多洞+L 形）见 `ProductSummary/水池设计器/20260905/`

## 分主题专项文档

| # | 主题 | 文件名 | 内容摘要 |
|---|---|---|---|
| T3 | 7 回归诊断 | `20260905-7个回归诊断与分类（圆角mask边界+name_parser语义）.md` | 修复 17 失效断言后暴出 7 failed；类别 A 圆角 mask 边界（#1-#6）+ 类别 B name_parser 语义分歧（#7） |
| T4 | 圆角修复 | `20260905-圆角裁剪断触白色空隙粗细不一致修复（多层结构感知重绘）.md` | `96d3031` 只改最外层策略导致内层断触/白缝/粗细不一；回退 `d246636` 多层结构感知重绘，保留有效边界容差 |
| T6 | R1-R4+P2-B | `20260905-R1-R4深度trace与P2-B修复（paint_inside_arc参数）.md` | trace 反转证据：程序无 bug，bug 在测试圆心取错；R2 真代码 bug（加 paint_inside_arc 参数）；R1/R3/R4 测试设计错误；全量 298 passed/1 failed（预存） |

## 核心修改文件分布

```
core/corner/sector_render.py        ← T4 回退多层结构感知重绘 + T6 新增 paint_inside_arc 参数
core/image_cropper_border.py        ← T6 apply_border_only_corners 传 paint_inside_arc=False
tests/integration/test_final_verification.py  ← T6 圆心修正
tests/border/test_gap_fix_verification.py     ← T6 圆心 + corner_positions
tests/border/test_complex_pattern_safety.py   ← T6 R4 圆心 + gap_region
tests/border/test_user_reported_cases.py      ← T6 R1 扫描公式
```

## 9.5 圆角相关关键结论一句话

> **圆角裁剪回退多层结构感知重绘解决断触/白缝/粗细不一致；R1–R4 trace 发现 1 个真代码 bug（border_only 模式圆内被涂边框色，修 paint_inside_arc=False）+ 3 个测试设计错误（圆心取错），4 个原失败全部转绿，无新增回归。**
