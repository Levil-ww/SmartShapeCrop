# V2.2.5 阶梯 L 形修复重新打包

- 阶梯 L 形修复提交：`6028527`。
- 原因：此前运行的 EXE 早于源码修复，未包含新的阶梯边框处理。
- 构建命令：使用现有虚拟环境运行 `python packaging/packageV2.2.5.py --onedir`。
- 构建环境：Python 3.13.14，PyInstaller 6.22.3；未新增依赖。
- 产物：`dist/SmartShapeCropV2.2.5/智能裁剪设计器V2.2.5.exe`。
- EXE 时间：2026-10-06 09:21:59，晚于最新运行源码时间 08:57:40。
- EXE SHA256：`4a970907b44b327c19252204beebfd3ae9a6d988992e2b53d5261e7ac4719646`。

## 验证

- EXE 内 `core.lshape_staircase_border` 和 `core.lshape_border` 字节码与当前源码一致。
- 全量测试：1898 passed，2 skipped，325.80 秒；跳过项为环境不支持符号链接。
- 隐藏启动验证：0.845 秒出现可响应窗口，正常关闭。
- 附带 Tesseract，语言列表包含 chi_sim、chi_sim_vert、eng、osd。
- 蔓生花 80×180 素材、171×78 画布、20×18 与 21×24 两级挖角：当前源码生成的内框线完整。

## 分发与构建范围

必须保留整个 `SmartShapeCropV2.2.5` 文件夹及 `_internal` 资源，不能仅复制 EXE。
运行时应关闭旧程序，启动上述新路径中的 EXE。

本次 EXE 从当时工作区构建，其中包含此前未提交的 `core/image_ops.py` 导出优化和
`gui/property_panel.py` 线程退出修复。按本次提交范围，这两项源码不纳入重新打包提交；
因此仅检出本提交重新构建，不能保证与该 EXE 完全相同。构建产物按现有规则不进入 Git。
