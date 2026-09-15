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

## ⚠️ ProductSummary 旧日期目录会「复活」（2026-09-15 复核：仍未清掉）

2026-09-10 C 档治理已移除 `ProductSummary/2026-08/` 与 `2026-09/`，
但 **2026-09-11 08:30 整批重现**（提交 `b3a68b7 restore: 恢复本地最新改动`）。
**2026-09-15 复核：11 个文件仍在索引中**（2026-08 7 个 + 2026-09 4 个），
且与 `月度总结/` 同名内容**重复并存**（2026-08 里还是旧文件名 `近两日工作总结_2026-08-28.md`，
月度总结里是改名后的 `20260828-近两日工作总结.md`）。

**教训：目录结构的「已删除」结论不是终态。** 凡因 Git 历史恢复而回流的旧路径，
重新检出时会一并回来 —— 这类清理**必须配套 `git rm --cached` + 提交**，
否则下一次 checkout/恢复就会复活。**只删目录不做索引处理 = 无效治理。**

复核方法（每次整理前先跑）：
```bash
git ls-files "ProductSummary/2026-0*"   # 期望 0 行
```

## 📁 周度总结的组织方式（2026-09-15 确立）

- **周度总结与单日/隔日总结同放 `ProductSummary/月度总结/`**，命名一律 `YYYYMMDD-DD-任务分类整理总结.md`
  （跨日区间用 `-DD`，如 `20260908-10-`、`20260908-12-`）。README 索引只列这一类，不逐一登记文件，**写新篇无需改 README**。
- **动手前先查该区间是否已有单篇** —— 有则精炼引用、只深挖缺失区间，避免重复梳理。
- **审查类文档的实际主线在 `ProductSummary/项目审查报告/`（4 个 md），不在 `SmartShapeCrop分析报告/`（html）**。
  该目录**未登记进 README 索引**，与后者职责重叠（见治理债）。
- 改动量统计必须分两个口径：全仓库（`git diff --shortstat A B`）**含文档搬迁会严重高估**，
  源码口径要显式限定路径 `-- core services gui workers models tests packaging main.py`。

## 📂 单日总结的两层结构：总览 + 模块子目录（2026-09-15 确立）

**南烛要求「细分到子目录」时，采用「`月度总结/` 总览 + 各模块子目录专项文档」两层结构。**
（9.14 首批实践：4 份模块文档 + 1 份总览）

**落位判断（重要）**
1. `L形挖角设计器/`、`圆角裁剪工具/` → **平铺 `YYYYMMDD-主题.md`**（每天 1 份，扁平更优），
   另可加 `YYYYMMDD-文档索引.md`（对齐 9.8–9.10 的索引实践）
2. `水池设计器/` → **`YYYYMMDD/README.md`**（历史既有约定：每天多份故保留日期子目录）
3. 架构 / 跨层改动（同时涉及 core+services+models+gui）→ **`SmartShapeCrop分析报告/`**
   （不属于任何单一业务模块，**必须单列**）
4. **双向指路**：模块文档开头写「跨模块聚合总结见 …」，总览的模块表里给专项文档路径

**总览 vs 模块文档的分工**
- 总览（`月度总结/YYYYMMDD-任务分类整理总结.md`）= 总体数据 + 分时提交清单 + 模块总览表
  + 每模块 1 节**能独立成立的摘要** + 文件清单 + 工程约定 + 遗留事项 + 一句话总结
- 模块子目录 = 完整叙述（背景/根因/方案/验证/代码片段），**不重复总览的统计表**
- 「一次收口多模块」的提交（如 `c039c72` 同时修 L形边框颜色/圆角抗锯齿/水池挖洞填充）：
  总览表里标注该提交，各模块各写一份，**性质不符的改动并入最相近模块叙述**

## 引用安全：同层级目录搬移不破坏相对链接

`ProductSummary/项目审查报告/` → `ProductSummary/SmartShapeCrop分析报告/` 这类
**同层级（同为 ProductSummary/<dir>/）** 的搬移，文档内 `../` 与 `../../` 的解析结果
**完全不变**，因此无需改任何链接。判断搬移是否安全，先比深度，再比文件名。

## ⚠️ 改 `CropDesign.mode` 判断必须同时搜 `==` 与 `!=`（2026-09-15）

`mode == / != 'rect_lshape'` 全工程共 **19 处**（image_ops 9 / property_panel_generate 6 /
geometry 2 / design_model 2），**其中 3 处是 `!=`**：
`models/design_model.py:114`、`gui/property_panel_generate.py:220`、`:327`。
只搜 `== 'rect_lshape'` 会漏掉这 3 处 —— 本项目"改了这个坏那个"的典型成因。

**新增几何模式时必须四类分治，禁止字符串级批量替换：**
1. **渲染语义**（8 处，image_ops 733/759/779/1050/1068/1144/1204/1223）→ 改成
   `mode in LSHAPE_LAYOUT_MODES`（收敛 helper）
2. **分派点**（2 处：`image_ops.py:1502 _get_inner_pixel_mask`、`geometry.py:584 compute_border_bands`）
   → **必须新增独立分支**。误改成 membership 会得到"并集"而非"差集"（洞被填满）**且不报错**
3. **展示/同步**（4 处，property_panel_generate 204/270/479/522）→ 扩展（易被误判为参数守卫而漏改）
4. **参数守卫**（5 处）→ 重写逻辑

**两处隐藏耦合（新开面板/mode 必查）**
- `gui/property_panel.py:926` 硬编码索引映射 `{'rect_hole':0,'rect_lshape':1,'ellipse_hole':2}` →
  不同步则模板加载**静默回落 `rect_hole`**，形状错误却不报错。应改为 `_cb_mode.findData(mode)`
- `core/app_settings.py:317` `if src not in (CROPPER, POOL, LSHAPE): src = CROPPER` →
  新增第 4 个历史源**必须同步白名单**，否则历史记录静默写进圆角裁剪工具

## 几何验证的零改动 POC 套路（2026-09-15 验证有效）

新形状可行性验证不需改源码：写 `scripts/diagnose/_diag_*.py`，只读调用 `core.geometry` 公开原语
（`build_lshape_mask(cuts=...)` / `fill_rect_mask` / `CropDesign` 的 `*_px()`），
用**集合代数恒等式**（而非面积数值）做断言，避免被 PIL 1px 栅格化误差干扰。
实例：`_diag_composite_shape_poc.py`（综合形状 = L形 − 洞），4 项断言全 PASS。
注意 PIL `ImageDraw.rectangle` 边界是**包含式**，面积会偏大约 0.03%，属正常。

**10px 黑框的绘制规则**：当两个移除区域**不相交且不相邻**时，必须**分形状各自绘制环带后 OR 合并**，
不能用"整体 union 再内缩"（联合内缩会在边界产生错误缺口）。判断方法：`m_a & m_b` 为 0 且
1px 膨胀后仍为 0 → 可 OR 合并。
