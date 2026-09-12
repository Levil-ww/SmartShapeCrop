# SmartShapeCrop 项目长期约定

## ProductSummary 文档分类约定（2026-09-10 C 档确立）

**单一维度：模块为主 + 月度总结单列。** 此前「按日期」与「按模块」两套正交体系平级混放，已废止。

```
ProductSummary/
├── 月度总结/        # 跨模块按日期聚合的横向总结（统一 YYYYMMDD- 前缀）
├── 水池设计器/      # 按「日期子目录」组织（每日多份，故保留子目录）
├── 圆角裁剪工具/    # 按「YYYYMMDD-主题」平铺命名（每日仅 1 份，扁平更优）
├── L形挖角设计器/   # 按「阶段 1-5 演进」+ 日期命名
└── SmartShapeCrop分析报告/
    ├── assets/     # 报告配图（html 以 assets/ 前缀引用）
    └── patches/    # 安全修复补丁
```

**判断规则（重要，避免重蹈覆辙）**
1. 文件名已含 `YYYYMMDD-` 前缀 → 可提平，排序天然按时间
2. 每天仅 1 个文档 → 绝不建日期子目录（会碎片化）；每天多份才值得下沉
3. 动手前先查 README 是否已有约定 —— README「ProductSummary 文档索引」节记载了各模块组织方式，
   **以它为准**

**移动文档时要区分两类引用**
- 导航引用（指引读者去哪找）→ 必须更新路径
- 历史叙事（记录过去某次动作）→ 刻意保留原文，改了会让历史失真

## 其他约定

- 报告产物输出 HTML，标题与页脚带版本号
- 整理/清理类操作：先只读扫描出方案，等南烛确认后再执行
- 源码零改动是硬要求，需能拿出证据（`git diff --stat` 为空 / 基线快照逐文件比对）

## ⚠️ Git 操作警示（2026-09-10 事故）

**`git gc` / `repack` 在本机不是零风险操作。** D 档执行 `git gc --prune=now` 后 `.git` 被清空，
全部历史丢失（详见当日日志）。推测为 Windows 安全软件在 pack 重组时误删。

后续做任何改动 `.git` 文件系统的操作前（gc / repack / fsck --unreachable / filter-repo）：
**先 `cp -r .git .git.bak` 做物理备份，再执行。**

另外：`.gitignore` 的 `_archive/` 等规则**只对未追踪文件生效**。
已被追踪的文件必须先 `git rm --cached`，忽略规则才会起作用。

## 环境与交付事实（2026-09-12 实测）

- **打包工具链缺失**：`.venv` 内 **未安装 PyInstaller**（`pyinstaller=MISSING`），
  改动后无法直接出包，需先 `pip install pyinstaller`。
  `dist/智能裁剪设计器V2.2.exe` 永远要检查时间戳是否 ≥ 最新源码时间戳。
- **`.workbuddy/` 与 `.dumate/` 未被 `.gitignore` 覆盖**（仅 `_archive/`、`.venv/`、`dist/`、
  `build/`、`.idea/`、`logs/` 已覆盖）。当前已误跟踪 51 个文件，含 47.76 MB 的 PyQt5 wheel 备份、
  约 30 MB 测试图、`.bak` 源码备份。清理前须 `git rm --cached`（详见 P1-03）。
- **测试必须在 `.venv` 下跑**：`F:\SmartShapeCrop\.venv\Scripts\python.exe`（3.13.14，含 PyQt5/PIL）。
  实测基线 **444 passed / 0 skipped / 0 failed**（74.1s，2026-09-12 收工前实测）。
  （433 是同日早些时候的数字，13:28 那批改动新增用例后升至 444。）
- **Tesseract 装在非默认路径** `D:\Programs\Tesseract-OCR`（`C:\Program Files\...` 下没有），
  由 `core.config.PathResolver` 探测到。排查 OCR 问题别只查 C 盘。
- **本机工具使用坑（2026-09-12 修正）**：Bash 工具**可用**，只是 PATH 里没有 Unix 工具
  （`ls` / `tail` / `head` / `dirname` / `cd` 全部 command not found，脚本里别用管道和这些命令）。
  **以绝对路径调用 exe 是可行的**，例如：
  ```bash
  "C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe" "C:/path/to/script.py"
  ```
  因此跑分析脚本的**首选路径 = Bash + Python 绝对路径**（stdout 正常回传），
  比 PowerShell（stdout 不回传）少一层「落盘再读」的绕行。
  需要管道/重定向时，把逻辑写进 Python 脚本内部，不要在 bash 层拼管道。
