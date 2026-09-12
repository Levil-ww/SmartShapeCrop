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

- **打包工具链（2026-09-12 18:00 复核，已修正）**：`.venv` 内 **PyInstaller 已安装**，
  PyQt5 / PIL / numpy / cv2 / psd_tools 全部 OK，可随时出包。
  （同日早些时候记录的 `pyinstaller=MISSING` 已过期作废。）
  `dist/智能裁剪设计器V2.2.exe` 永远要检查时间戳是否 ≥ 最新源码时间戳 ——
  实测 2026-09-12：exe 14:22 < `main.py` 15:11，**交付物落后于源码**。
- **误追踪产物（2026-09-12 17:00 复核）**：`.gitignore` 未覆盖 **`.workbuddy/`、`.dumate/`、
  `.trae-html-share-packages/`** 三个目录族。当前误跟踪 **54 个文件 / 76.86 MB**：
  `.workbuddy/` 49 个（PyQt5 wheel 47.76 MB + 6.55 MB、tmp_samples 诊断图 27.1 MB）、
  `.dumate/` 15 个（22.1 MB 测试大图）、`.trae-html-share-packages/` 1 个。
  `.git` 达 149.3 MB 与此直接相关。清理前须 `git rm --cached`。
- **`.gitignore` 的 `_archive/` 规则匹配「任意层级」**，因此 `scripts/_archive/`、
  `scripts/verify/_archive/`、`scripts/diagnose/_archive/` 都被它命中 —— 但
  **`scripts/_archive/` 下 35 个文件仍在索引中**（规则对已追踪文件无效，老坑的又一实例）。
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

## ⚠️ ProductSummary 旧日期目录会「复活」（2026-09-12 发现）

2026-09-10 C 档治理已 `rmdir` 移除 `ProductSummary/2026-08/` 与 `2026-09/`，
并在 `SmartShapeCrop_C档执行报告_V1.3.html` 中标记为已删除。但 **2026-09-11 08:30
它们整批重现（11 个文件重新被追踪）**，与当日 git 事故后的历史恢复/重新检出高度吻合。

**教训：目录结构的「已删除」结论不是终态。** 凡因 Git 历史恢复而回流的旧路径，
重新检出时会一并回来 —— 这类清理**必须配套 `git rm --cached` + 提交**，
否则下一次 checkout/恢复就会复活。**只 rmdir 不做索引处理 = 无效治理。**

复核方法（每次整理前先跑）：
```bash
git ls-files | grep -c "ProductSummary/2026-"   # 期望 0
```

## 引用安全：同层级目录搬移不破坏相对链接

`ProductSummary/项目审查报告/` → `ProductSummary/SmartShapeCrop分析报告/` 这类
**同层级（同为 ProductSummary/<dir>/）** 的搬移，文档内 `../` 与 `../../` 的解析结果
**完全不变**，因此无需改任何链接。判断搬移是否安全，先比深度，再比文件名。
