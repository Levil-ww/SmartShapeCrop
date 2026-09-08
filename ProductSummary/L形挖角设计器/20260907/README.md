# 2026年09月07日 L 形挖角设计器 - 文档索引

## 目录说明

本目录集中收录 2026年9月7日 与「L 形挖角识别精度」、「L 形 GUI 性能」相关的全部产品总结与技术文档。

全日 L 形相关 **2 大主题簇**，核心为：草图三识别精度修复（删 MORPH_CLOSE + 凸包差法 + 大 bbox 过滤）、L 形 GUI 卡死 3 分钟修复（DiskCache dir_mtime 持久化 + 信号槽异步预热）。

圆角相关 5 主题见 `ProductSummary/圆角裁剪工具/20260907/`。

## 顶层总览

- `ProductSummary/2026-09/20260907-任务分类整理总结.md` — 全日跨模块聚合总结

## 分主题专项文档

| # | 主题 | 文件名 | 内容摘要 |
|---|---|---|---|
| T6 | 草图三识别 | `20260907-T6-草图三识别精度修复（删MORPH_CLOSE+凸包差法+大bbox过滤）.md` | 三重根因（MORPH_CLOSE 粘连/OCR 小数误识/大 bbox 噪声）；凸包差法检测挖角；数量级+几何一致性检查；143×47.5 / 42.3×15 正确识别；46/46 测试通过 |
| T7 | GUI 卡死 | `20260907-T7-L形GUI卡死3分钟修复（DiskCache dir_mtime持久化+信号槽预热）.md` | 双重根因（DiskCache 未持久化 dir_mtime 149s + 主线程阻塞 wait）；dir_mtime 磁盘持久化 2ms 跳过；signal-slot 异步预热；内挖角尺寸备选提取 |

## 核心修改文件分布

```
core/pool_designer/lshape_sketch_parser.py  ← T6 删 MORPH_CLOSE + 凸包差法 + 大 bbox 过滤 + 数量级/几何一致性
                                              ← T7 内挖角尺寸备选提取
core/parser/template_matcher.py             ← T7 DiskCache dir_mtime 磁盘持久化
gui/property_panel_generate.py              ← T7 信号槽机制异步预热
```

## 9.7 L 形关键结论一句话

> **草图三识别经删 MORPH_CLOSE + 凸包差法 + 大 bbox 过滤 + 数量级/几何一致性检查后，143×47.5 外框与 42.3×15 挖角正确识别（46/46 通过）；L 形 GUI 卡死 3 分钟经 DiskCache dir_mtime 持久化（2ms 跳过扫描）+ 信号槽异步预热后，启动降至秒级且 UI 保持响应。**
