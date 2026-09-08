# 2026-09-07 T7：L 形 GUI 卡死 3 分钟修复

## 概述

用户反馈 L 形 GUI 界面在后台运行时冻结近 3 分钟。分析识别出双重根因，修复后启动从 149 秒降至秒级，GUI 在预热期间保持响应。

***

## 双重根因

| # | 根因 | 量化影响 |
|---|---|---|
| 1 | **DiskCache 未持久化 dir_mtime**，每次启动强制全量扫描 210k+ 文件 | 启动耗时 149 秒 |
| 2 | **主线程阻塞在模板库预热**（warmup.wait） | UI 完全无响应 |

***

## 修复

### 修复 1：DiskCache dir_mtime 持久化（core/parser/template_matcher.py）

- 在 DiskCache 中增加 `dir_mtime` 字段的磁盘持久化
- 启动时先比较根目录 mtime，未变则快速跳过（2ms），避免全量扫描
- **用户操作**：需删除旧缓存文件使新 schema 生效
  ```
  C:\Users\Administrator\.smartshapecrop\caches\*.cache.pickle
  ```

### 修复 2：信号槽机制异步预热（gui/property_panel_generate.py）

- 主线程不再阻塞等待模板库预热完成
- 改用 signal-slot 机制：预热完成后通过信号触发 Worker 启动
- UI 在预热期间保持响应

### 修复 3：挖角尺寸识别增强（core/pool_designer/lshape_sketch_parser.py）

- 当外切边尺寸值不可用时，从内挖角区域提取尺寸作为备选
- 提高识别鲁棒性（与 T6 草图三修复协同）

***

## 验证

- 启动时间从 149 秒降至秒级（dir_mtime 命中时 2ms 跳过扫描）
- GUI 在预热期间保持响应，无冻结
- 挖角尺寸识别在边缘值缺失时仍能正确工作

***

## 工程约定沉淀（写入 memory）

| # | 约定 | 违反后果 |
|---|---|---|
| 1 | 模板库预热必须用 signal-slot 异步触发，主线程不得阻塞 wait | UI 冻结数分钟 |
| 2 | DiskCache 必须持久化 dir_mtime，根目录未变时快速跳过扫描 | 每次启动全量扫描 210k+ 文件（149s） |
