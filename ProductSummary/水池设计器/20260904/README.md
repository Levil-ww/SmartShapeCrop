# 2026年09月04日 水池设计器 - 文档索引

## 目录说明

本目录集中收录 2026年9月4日 与「9.3 遗留问题复检补丁」、「项目结构整理」、「L 形挖角边框 V13 集成」相关的全部产品总结与技术文档。

全日 **6 大主题簇**，核心为：9.3 体检 P0/P2 问题以补丁形式落地（hole_bg_color 生效 + 缓存来源校验 + 死代码清理）、项目结构整理（packaging 集中）、P2-1 阈值深挖确认为死代码并清理、池面板清除草图 Bug 修复（PyQt5 clicked bool 污染）、L 形挖角边框 V13 优先检测 + V2.2 方向修正。

## 顶层总览

- `ProductSummary/月度总结/20260904-任务分类整理总结.md` — 全日跨模块聚合总结

## 分主题专项文档（按时间线排列）

| # | 主题 | 文件名 | 内容摘要 |
|---|---|---|---|
| T1 | 9.3 复检补丁 | `20260904-9.3遗留问题复检与补丁落地（hole_bg_color+缓存来源校验+死代码清理）.md` | hole_bg_color 配置项静默失效修复；素材残留真根因=缓存永不失效（非 clone 共享引用）；删 `_clear_inner_arc_to_bg` 死代码；3 补丁应用后 299 passed |
| T2 | 项目整理 | `20260904-项目结构整理与卫生治理（packaging集中+scripts清理）.md` | 8/31 §10.5 六项收尾（3 项已完成，执行 3 项）；四代打包脚本+3 spec+bat 集中到 packaging/；核心源码零改动 |
| T3 | 状态报告 | `20260904-V2.1.2程序当前状态分析报告.md` | 21,598 行生产代码 / 299 passed；零测试覆盖 6,289 行；技术债 5 项；结论=止血已完成进入固本期 |
| T4 | 阈值死代码 | `20260904-P2-1阈值深挖与死代码清理（50vs30实为死代码）.md` | 推翻"有意差异"判断：50 是 `_filter_gap_layers` 死代码参数，生产零调用；删除 41 行死代码 + split_image_cropper.py |
| T5 | 清除草图 | `20260904-池面板清除草图缩略图不消失修复（PyQt5 clicked bool污染）.md` | clicked(bool) 参数污染 source 形参；lambda 吞掉 bool + 面板间严格隔离；类坑提醒 |
| T6 | L 形边框 | `20260904-L形挖角边框自动检测与方向修正（V13集成+V2.2）.md` | V13 优先检测（旧检测假阳性卡死兜底）；删手动 GroupBox；V2.2 方向修正（保留区侧非缺口内侧）；黑 9px + 棕 170px |

## 核心修改文件分布

```
core/image_ops.py                 ← T1 hole_bg_color 生效 + T6 V13 patch 执行点
gui/property_panel_workers.py     ← T1 缓存来源路径校验
core/image_cropper_mask.py        ← T1 删 _clear_inner_arc_to_bg 85 行
core/geometry.py                  ← T1 缓存来源字段
core/image_cropper.py             ← T4 删 _filter_gap_layers 41 行 + CROP_BG_SIMILARITY=50
gui/property_panel_poolbox.py     ← T5 清除草图 lambda + 面板隔离
core/lshape_border.py             ← T6 V13 优先 + patch_lshape_cut 等 V13 移植
gui/lshape_panel.py               ← T6 删手动覆盖 GroupBox + 6 方法
```

## 9.4 全日关键结论一句话

> **9.3 体检 P0 止血项以补丁形式真正落地（299 passed 零红灯）；下午转向 L 形挖角缺边框核心投诉，V13 优先检测 + 删手动 GroupBox + V2.2 方向修正三步闭环；同时修复 PyQt5 clicked(bool) 污染和 50 vs 30 死代码阈值。**
