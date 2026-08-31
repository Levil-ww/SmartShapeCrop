# 2026年08月29日 水池设计器 - 文档索引

## 目录说明
本目录集中收录 2026年8月29日 与「水池设计器（多洞功能专项）」相关的全部产品总结与技术文档。

全日共 **2 个 Session / 6 个主题节点 / 31 条拆解子问题**，全部聚焦**多洞（Multi-Hole）矩形嵌套挖洞**新功能的端到端落地与闭环修复，全日零改动单洞逻辑、零触及圆角裁剪工具模块。

## 顶层总览
- `20260829-任务分类整理总览.md` — 全日跨模块聚合总结（与ProductSummary根目录版本内容一致），含：统计、分类、修改文件、验证节点、决策原则。

## 分主题专项文档（按时间线排列，对应6个主题节点）

| # | 主题时间 | 文件名 | 内容摘要 | 子问题数 |
|---|---|---|---|---|
| T1 | 08:55 | `20260829-水池设计器多洞草图识别架构设计与零侵入落地.md` | ADR分阶段方案+方向标签策略+零侵入实现（独立文件+默认字段）+首版21单测/230回归 | 8条 |
| T2 | 09:25 | `20260829-水池设计器多洞识别PhaseA-E消包络盒与OCR加权众数投票.md` | 只渲染1洞（包络盒误判）+margin_top 5.0→11.5修复；Phase A-E五段算法+0.5cm量化加权众数 | 9条 |
| T3 | 09:56 | `20260829-水池设计器多洞GUI启动崩溃修复(QFormLayout与PySide6).md` | exit code=1双NameError：QFormLayout缺失+误用PySide6；GroupBox显示时序；offscreen冒烟门禁 | 5条 |
| T4 | 10:59 | `20260829-水池设计器多洞矩形四边10px黑边框缺失修复.md` | 多洞仅顶边黑色→每洞独立几何差集算四边10px环+合并；test_92像素级48点+8点反断言 | 3条 |
| T5 | 11:27 | `20260829-水池设计器多洞逐洞独立边距与GUI状态栏显示.md` | 不等边距98×433/等边距59×350双案例；HoleInfo独立边距+逐洞y坐标+状态栏加法分支 | 4条 |
| T6 | 15:25 | `20260829-水池设计器多洞边距优先级与素材字段继承修复.md` | 洞1左36→3.6（兜底优先级写反，修复为三级：全局→逐洞→最小）+安妮森林米色（rebuild漏材质字段，改用dataclasses.replace+深拷贝白名单） | 2条 |

## 核心修改文件分布
```
core/pool_designer/sketch_parser_multihole.py  ← T1/T2/T5 主力新文件
core/pool_designer/sketch_parser.py            ← T1 入口（仅2行委托，单洞不动）
core/geometry.py                               ← T1 HoleInfo字段扩展 / T5 effective_margins
core/image_ops.py                              ← T2 多洞mask / T4 每洞10px边框
gui/property_panel.py                          ← T3 QFormLayout/PySide6/显示时序
gui/property_panel_workers.py                  ← T2 多洞分支 / T4 分派 / T5 逐洞坐标 / T6 三级兜底
gui/property_panel_poolbox.py                  ← T5 状态栏逐洞显示
gui/property_panel_layers.py                   ← T6 _rebuild_holes_from_design 材质继承
tests/sketch/test_multi_hole_parser.py         ← T1/T2/T5/T6：T80/T81/T90/T91/T93/T94 + 21初版单测
tests/integration/test_final_verification.py   ← T4 test_92
tests/gui/test_gui_sim.py                      ← T3 多洞面板内容断言
conftest.py（根）                               ← T3 PySide6扫描门禁
main.py                                        ← T3 --run-init-check offscreen冒烟参数
```

## 8.29全日关键结论一句话
> **多洞功能必须在「识别、渲染、GUI、数据流、素材、边距」六层链路都切换到"逐洞语义"才算真正落地；任何一层仍然沿用单洞的"全局共享模型"都会出数量级错误。**
