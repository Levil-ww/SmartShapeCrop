# SmartShapeCrop 上线前全检报告

**日期**：2026-09-19
**场景**：上线前检查（产品评审 + 安全审计 + QA 测试三线并行）
**参与成员**：产品官（代码审查）+ 安全卫士（安全审计）+ 质量门神（QA 测试与发布）
**基线提交**：`b444ada`（`v2.2.3-L形挖角按模块设计分类总结`，2026-09-19 13:14:45）
**报告版本**：v1.1（含主理人交叉复核与工作树时间线修订）

---

## 📌 TL;DR（执行摘要）

- **整体结论：🟡 有条件通过** —— 发布基线 `b444ada` 健康（672 全绿、无高危安全问题）；工作树经主理人实测**当前也已全绿（673/673）**且补上了旗舰功能的用例覆盖，但**存在活跃写入者、仍在变动，无法冻结**，故**不建议直接用它出包**。
- **阻塞项数量：4 项**（1 项「无法冻结」状态 + 2 项旗舰功能缺陷 + 1 项发布标识错误）。
- **关键裁决**：代码审查报告称「工作区干净」，经主理人实测**该结论为误判**（根因是本机命令输出被吞没，把「无输出」当成「无改动」）—— 详见「第 0 节 主理人裁决」，含完整时间线。
- **下一步**：**先停止对仓库的写入** → 确认改动作者 → 二选一冻结（`b444ada` 稳，或当前工作树 673 全绿更完整）→ 修 H-1/H-2/M-1 → 重打包 → 回归 → 出包。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟡 条件 Go —— `b444ada`（672 全绿，最稳）或当前工作树（673 全绿，更完整），**但须先冻结** |
| 严重度分布 | 🔴 1 / 🟠 4 / 🟡 7 / 🟢 6 |
| 关键行动项 | 6 条（P0 两条） |
| 建议负责人 | 项目主程（南烛）自行处理，本次全检未改动任何源码 |
| 发布阻塞项 | 4 项，见下 |
| 回滚预案 | 已备（第 5 节），本次未执行任何回退 |

### ⛔ 上线阻塞项清单（必须清零）

| # | 阻塞项 | 来源 | 说明 |
|---|--------|------|------|
| B-1 | 工作树存在活跃写入者，无法冻结 | 主理人实测 + QA | `core/lshape_border.py` / `tests/core/test_lshape_border.py` 未提交且在途修改（13:48:42 仍在写）。工作树**已收敛全绿（673/673）**，但快照随时过期 → 必须停止写入后冻结 |
| B-2 | **H-1** LOD 阶梯几何未缩放 | 产品官 | `core/image_ops.py:538-554` 漏缩放 `l_cut_rects` → LOD 预览与真实导出不一致（旗舰功能） |
| B-3 | **H-2** `lshape_cut_w/h` 死守卫 | 产品官 | `core/image_ops.py:920,1391` 字段名不存在（真名 `l_cut_w_cm`），守卫恒真，属重命名埋雷 |
| B-4 | **M-1** 打包版本号错位 | 产品官 + QA | `packaging/packageV2.2.2.py` 的 `APP_NAME`/文件名仍为 V2.2.2，仓库已是 v2.2.3 → 产出错版本 exe |

---

## 0. 主理人裁决（成员结论冲突的核实）

**冲突点**：产品官报告「工作区干净（`git status` 空）」，质量门神报告「工作树变红、有一项未提交的失败测试」。两者不能同时成立。

**主理人实测核实**（直接调 `git`，非转述）：

```
=== STATUS ===
 M core/lshape_border.py
 M tests/core/test_lshape_border.py
?? .pytest_tmp/  ?? .pytest_tmp_root.txt  ?? .qa_git.txt  ?? .qa_scan.txt
=== HEAD ===
b444adabd462367f60e277dff18133f8ca600319  2026-09-19 13:14:45  v2.2.3-L形挖角按模块设计分类总结
=== DIFFSTAT ===
 2 files changed, 81 insertions(+), 2 deletions(-)
```

**产品官随后二次申辩**，称其复测 `git status --short` 与 `git diff --stat` 均为空、工作树 == HEAD，并据此推断「失败用例是已提交的真实回归」。该申辩若成立将改变发布判定，故主理人做了**决定性验证** —— 直接检查 HEAD 提交里有没有这段代码：

```
=== [1] porcelain status ===
' M core/lshape_border.py\n M tests/core/test_lshape_border.py\n?? .pytest_tmp/ ... ?? deliverables/\n'
=== [2] HEAD 版本的测试文件里是否已有该用例 ===
含 test_staircase_inner_corner ? -> False      ← 关键
HEAD 版测试文件行数: 535
=== [3] HEAD 版本 lshape_border.py 是否已有新逻辑 ===
含 flip_x ? -> False
含 canonical_rects ? -> False
=== [4] 工作树文件 ===
工作树含 flip_x ? -> True
```

**裁决**：
1. **质量门神结论正确；产品官两次给出的「工作区干净／工作树 == HEAD」均为误判**。决定性证据：`git show HEAD:tests/core/test_lshape_border.py` 中**不含** `test_staircase_inner_corner`，`git show HEAD:core/lshape_border.py` 中**不含** `flip_x` / `canonical_rects` —— 而工作树中两者都在。故该用例**确为未提交的新增改动**，不是「已提交的真实回归」。
2. **误判根因（方法论教训）**：本机命令输出捕获不稳定 —— PowerShell 工具返回 `exit code 0` 但 **stdout 为空**，产品官把「命令无输出」当成了「`git status` 无改动」。主理人改用「Bash + `.venv\Scripts\python.exe` 调 `subprocess`」后输出稳定可靠。**凡涉及状态判定的命令，不能以「无输出」作为「无内容」的证据，须改用可回显的方式复验。**
3. **对发布判定的影响**：产品官审查的代码内容对应 `b444ada`，H-1/H-2/M-1 **对发布基线仍全部有效**（三处位置均不在未提交改动范围内，已核对）。
4. **工作树的改动是一次进行中的功能开发**，非团队所为（三位成员全程只读）。改动实质：在 `core/lshape_border.py::_draw_staircase_union_layers` 中新增「同角位阶梯翻转至右上角规范方向 + 内凹转角按相邻水平边距离局部重画第一层」逻辑，并配套新增测试 `test_staircase_inner_corner_keeps_separator_between_border_layers`。
5. **⚠️ 存在「活跃写入者」，工作树是移动靶**。主理人实测时间线：

| 时刻 | 工作树状态 | 证据来源 |
|---|---|---|
| 13:40:33 | 新增用例失败（工作树变红） | 质量门神 |
| 13:43 | 双文件在途；目标用例通过，但**既有用例回退失败** | 质量门神快照 B |
| 13:45:09 | `core/lshape_border.py` 再次被写入（diff 62→65 行） | 主理人 mtime |
| **13:45:29** | `tests/core/test_lshape_border.py` → **37 passed 全绿** | 主理人实跑 |
| 13:48:42 | 两文件再次被同时写入（diff 82 行） | 主理人 mtime |
| **13:48:56** | **全量 673 tests / 0 failures / 0 errors / 0 skipped（182.8s）** | 主理人实跑 + junit XML 解析 |

   即：写入者在 13:45 前后已修掉「既有用例回退」冲突，工作树**当前已收敛到全绿，且比 HEAD 多 1 个用例（673 vs 672）**。
6. **产品官独立定位了该冲突的机制**（基于工作副本只读分析，该冲突随后被消除）：内凹角覆盖块**无条件覆盖、绕过 `paint_mask`**，且受 `if len(layers) < 3: break` 门控、`local_dy` 从 0 起配合 `searchsorted('right')` 越界 clip 到末层 —— 三者叠加导致「满足新用例」会破坏既有用例 `test_staircase_union_paints_only_exposed_step_boundary`（该处被盖成主色带 `[220,180,120]`，期望黑）。
7. **结论（已更新）**：
   - `b444ada` 仍是**最稳的发布基线**（672 全绿、无活跃写入、可复现）。
   - 但工作树**当前也已全绿（673）**，并补上了旗舰功能「阶梯内凹角层间分隔带」的用例覆盖 —— **单从质量看优于 HEAD**。
   - **唯一障碍是「冻结」**：写入者仍在活动（13:48:42 仍在写），任何快照都会立即过期。**必须先停止写入、确认作者，再决定以哪一版出包。**

> ⚠️ **请确认**：`core/lshape_border.py` 与 `tests/core/test_lshape_border.py` 的改动是否为您本人正在进行的开发？若非本人所为，需排查并行会话/进程。**建议在全检收尾期间暂停对该仓库的写入**，以便冻结一个可信的发布基线。

---

## 1. 各成员核心结论

### 🔍 产品官（代码审查）—— 🟡 有条件发布

- **核心判断**：工程质量整体偏高 —— 几何有 F1 退化守卫、线程生命周期由主窗 `closeEvent` + `aboutToQuit` 双接管、异常多数走 `logger.exception` 而非 `except: pass`；无崩溃/数据损坏级缺陷。但 2 项高优先问题**恰好落在 v2.2.3 旗舰功能（L 形阶梯挖角）上**。
- **⚠️ 其「工作区干净」结论已在第 0 节被修正**（误判，根因为命令输出被吞没）。此为工作区状态判断问题，**不影响其代码审查发现本身** —— H-1/H-2/M-1 均已由主理人独立核对确认有效。
- **关键建议**：修 H-1（LOD 阶梯几何）、H-2（死守卫）后再发；M-2~M-5（句柄泄漏、`os.popen`、core 反向依赖、热路径 print）低成本，建议同批处理。
- **架构边界**：`workers/` 未导入 `gui/` ✅、`gui/` 单向依赖 ✅、`services/` 未反向依赖 `gui/workers` ✅；**`core/` 反向 import `services/` ⚠️**（`core/__init__.py:28`、`core/compat/__init__.py:43-60`），运行期未炸但属「脆弱但可用」。

### 🛡️ 安全卫士（OWASP + STRIDE 审计）—— 🟡 无高危，1 项中危

- **核心判断**：该工具离线单机运行、无网络服务端，攻击面 = 不可信文件输入 + 本机文件系统；**未发现远程可利用漏洞、无明文凭据、无命令注入、无远程/RCE 可达路径**。1 项中危需本机写入用户 profile 目录才可利用。
- **关键建议**：修 F-01（`services/parser/template_matcher.py:295` 的 `pickle.load` 在校验 `schema_version`/`template_dir` **之前**执行，缓存文件名可预测 → 本地反序列化 RCE 面），成本极低（JSON 分支代码已存在）。次要：升级 `PyQt5-Qt5>=5.15.13`、`os.popen` 改 `subprocess`。
- **正向确认（7 条）**：`python-qt5` 恶意包**未被引入**（已全量枚举包元数据核实）；解压炸弹双层防护（200MP 上限 + 40MP/50MB 头校验）；PSD 导出文件名白名单化**阻断路径穿越**；OCR 子进程 list 参数无 `shell=True`；输出保存走原子写 `tmp + os.replace`；打包 `debug=False/console=False`；**无任何硬编码密钥/盘符**。

### ✅ 质量门神（QA 测试与发布）—— 🟢 基线可发布 / 🟡 工作树（移动靶，须冻结）

- **核心判断**：已提交基线 `b444ada` **672 passed / 0 failed / 0 error / 0 skipped**（两次独立复跑一致），健康分 88/100；GUI 子集 offscreen 97 项全绿。工作树在 13:40~13:44 两度变红（新增用例失败 → 13:43 修好后既有用例回退），**经主理人 13:48:56 全量复跑确认已收敛为 673 全绿 / 0 失败（182.8s）**。
- **关键建议**：发布基线二选一（`b444ada` 最稳 / 工作树 673 更完整），但**前提是先停止写入并冻结**。`test_lshape_border.py` 的新增用例已可通过。
- **重要澄清**：基线文档中「626 passed / 45 errors」是**环境伪失败**，已定位根因 —— 普通权限下 pytest 收尾清理 `%TEMP%\pytest-of-Administrator` 触发本机 safe-delete 批量确认护栏（149 项 > 阈值 50）。用 `--basetemp` 指向项目内可写目录即可稳定规避，**与项目代码无关**。
- **历史告警可关闭**：长期失败的 `test_render_design_lshape_degenerate_no_crash` 已修复（`core/geometry.py:349-352` 新增 `if edge_length_cm <= 0: continue` 退化守卫），单跑 1 passed。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|--------|------|------|---------|------|------|
| B-1 | 🔴 | 发布基线 | 工作树 | 81 行未提交改动 + 1 项新增测试失败，工作树偏离 `b444ada` | 冻结 `b444ada` 出包；改动另行收尾 | 主理人核实 / QA |
| H-1 | 🟠 | 正确性 | `core/image_ops.py:538-554` | `_make_lod_design` 缩放了 canvas 与各 cut 字段，**独漏 `l_cut_rects`** → LOD 预览几何错误而导出正确（预览≠导出） | 同步缩放 `l_cut_rects` 的 w/h/offset；加「预览几何==导出几何」回归测试 | 产品官 |
| H-2 | 🟠 | 状态一致性 | `core/image_ops.py:920-921, 1391-1392` | `getattr(design,'lshape_cut_w',0.0)` 字段不存在（真名 `l_cut_w_cm`）→ 恒返回 0.0，守卫恒真 | 改用正确字段名或删死条件；补断言防回归 | 产品官 |
| F-01 | 🟠 | 反序列化 | `services/parser/template_matcher.py:295` | `pickle.load` 在 `schema_version`/`template_dir` 校验**之前**执行；缓存路径 `~/.smartshapecrop/caches/<名>_<md5>.cache.pickle` 可预测 | 缓存改用已有的 JSON 分支；若保留 pickle 须先校验哈希/HMAC | 安全卫士 |
| M-1 | 🟠 | 版本一致性 | `packaging/packageV2.2.2.py:8,83,514` | 打包入口与 `APP_NAME` 仍为 V2.2.2，仓库已 v2.2.3 → 产出 `智能裁剪设计器V2.2.2.exe` | 更新至 V2.2.3；同步 README / CHANGELOG（后者停在 2026-09-17） | 产品官 + QA |
| M-2 | 🟡 | 资源管理 | `core/image_ops.py:31`、`services/parser/name_parser.py:834`、`workers/property_panel_workers.py:47`、`services/psd/loader.py:98,148,161` | `Image.open`/`PSDImage.open` 未用 `with`、未 close → Windows 下可能锁文件 | 改 `with` 上下文或 `load()` 后 close | 产品官 |
| M-3 | 🟡 | 命令执行规范 | `core/config.py:346` | `os.popen('tesseract --list-langs 2>&1')` 起 shell 且未 close | 改 `subprocess.run([...], capture_output=True)`（命令为常量、**当前不可注入**） | 产品官 + 安全卫士（独立发现，交叉印证） |
| M-4 | 🟡 | 架构边界 | `core/__init__.py:28`；`core/compat/__init__.py:43-60`；`core/{psd,parser,pool_designer}/__init__.py:5` | `core/` 反向 import `services/`，违反「core 纯业务逻辑」分层；compat shim 链使「导入顺序」成隐性契约 | 明确分层策略；如坚持分层需拆出服务聚合层 | 产品官 |
| M-5 | 🟡 | 代码异味 | `core/image_ops.py:829-835` | 渲染热路径内**无条件 `print()`** 调试语句（每次 L 形渲染都执行） | 删除或降级为 `logger.debug` | 产品官 |
| F-03 | 🟡 | 二进制植入 | `core/config.py:200-221`；`services/sketch_parser/sketch_parser_vision.py:84-86` | Tesseract 优先信任 `{exe_dir}/tesseract` 与环境变量，赋值给 `pytesseract.tesseract_cmd` 后执行 | 优先信任 Program Files；校验数字签名；便携模式显式提示 | 安全卫士 |
| F-04 | 🟡 | 供应链 | `.venv`：`PyQt5-Qt5==5.15.2` | 内嵌 Qt 5.15.2 携带已知 CVE（OpenSSL / zlib / PCRE2），**可达性低**（离线、不加载不可信图片、无 TLS） | 升级 `PyQt5-Qt5>=5.15.13` | 安全卫士 |
| L-1 | 🟢 | 一致性 | `workers/property_panel_workers.py:531`；`models/design_model.py:140` | `l_cut_rects` 按**总数** `[:3]` 截断，而 `validate()` 是**每锚定角 ≤3**（总数可 >3）→ 多锚定阶梯第 4 条被静默丢弃 | 统一为按锚定角分组截断 | 产品官 |
| F-05 | 🟢 | 加固 | `core/artifact_cleanup.py:79-102` | 清理时跟随符号链接删除 | 删除前跳过 `p.is_symlink()` | 安全卫士 |
| F-06 | 🟢 | 信息暴露 | `main.py:462-488` | `crash.log` 写入 `sys.executable` / `sys._MEIPASS` / `sys.path` / 完整 traceback（仅本地） | 可选收敛写入内容 | 安全卫士 |
| L-3 | 🟢 | 代码异味 | `core/image_ops.py:583` | 遗留 `_dbg=False` 开关 + 大段调试分支 | 清理 | 产品官 |
| L-4 | 🟢 | 性能 | `core/artifact_cleanup.py:91` | 推导式内每次重建集合 → O(n²) | 提取 `expired_set = set(expired)` | 产品官 |
| L-2 | 🟢 | 测试缺口 | `tests/` | 无「LOD × 阶梯 `l_cut_rects`」用例；`test_f1` 仅覆盖退化矩形未覆盖阶梯 | 增阶梯 LOD 回归 + 预览/导出一致性断言 | 产品官 |

---

## 3. 交付清单（发布前检查表）

### 代码变更
本次全检**未产生任何源码变更**（三位成员全程只读）。工作树现存 81 行改动为**外部并发开发**，非本次交付内容。

### 测试覆盖现状

| 套件 | passed | failed | error | skipped | 耗时 |
|------|--------|--------|-------|---------|------|
| 全量 `tests/`（基线 `b444ada`） | 672 | 0 | 0 | 0 | ~3m06s |
| `tests/gui/`（offscreen） | 97 | 0 | 0 | 0 | — |
| 工作树 @13:45（单文件 `test_lshape_border.py`） | 37 | 0 | 0 | 0 | 0.88s |
| **工作树 @13:48（全量，主理人实跑）** | **673** | **0** | **0** | **0** | 182.8s |

分模块（collected=672）：border 10 ／ core 340 ／ gui 97 ／ integration 77 ／ models 27 ／ sketch 64 ／ sketch_parser 57。

**覆盖薄弱模块**（建议补测，按价值排序）：
1. `services/psd/loader.py`（191 行，**零测试**）—— PSD 分层读取/扁平化/JPG 导出属对外特性
2. `core/app_settings.py`（368 行，无直接单测）
3. `workers/canvas_workers.py` / `cropper_workers.py`（线程取消与信号链路未单测）
4. `core/image_cropper_mask.py`（635 行，仅间接覆盖）
5. `process_image.py`（CLI 入口，仅源码字符串检查）

### 发布检查清单

- [ ] **停止对仓库的写入**，并冻结发布基线（`b444ada` 672 全绿，或当前工作树 673 全绿）
- [ ] 修复 H-1 / H-2（旗舰功能预览≠导出、死守卫）
- [ ] 修复 M-1（打包版本号 → V2.2.3）+ 同步 README / CHANGELOG
- [ ] 修复 F-01（缓存改 JSON）
- [ ] 工作树改动收尾并跑到 673 全绿
- [ ] 重跑回归：`pytest tests/ -q --basetemp=F:\SmartShapeCrop\.pytest_tmp\bt_rel`
- [ ] 重打包：`python packaging/packageV<新版>.py`
- [ ] **核对 `dist/*.exe` 时间戳 ≥ 最新源码时间戳**（历史踩过坑）
- [ ] 冒烟：出包后人工启动 exe，验证 L 形阶梯挖角的预览与导出**一致**

> ⚠️ 打包命令会写入 `build/`、`dist/`，本次已按约束**未执行打包**。

### 回滚预案

1. **定位回退点**：`git log --oneline`（仓库无 tag，用 commit hash；上一稳定点示例 `4b7458b`，或退到 v2.2.1 的 `26c1460`）
2. **代码回退**（二选一）：快速 `git checkout <hash>`（detached）；保留历史用 `git revert <bad-commit> --no-edit`
3. **产物回退**：重新分发上一打包产物 `dist/智能裁剪设计器V2.2.2.exe`；如需重打包用当时的打包脚本
4. **历史快照兜底**：`_archive/bak_20260902/`、`_archive/快照_20260910/`
5. **用户侧降级**：本地桌面工具，**无服务端 / 无数据库 / 无数据迁移** → 直接用旧 exe 覆盖新 exe；如需彻底干净，清理用户目录下由 `core/app_settings`、`core/config` 生成的本地配置与 `logs/`
6. **回退后验证**：`pytest tests/ -q --basetemp=F:\SmartShapeCrop\.pytest_tmp\bt_rb` 应全绿

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 说明 |
|---|------|--------|--------|------|
| 1 | **暂停对仓库的写入**，确认 `core/lshape_border.py` / `tests/core/test_lshape_border.py` 改动的作者，然后冻结发布基线（`b444ada` 最稳；工作树 673 全绿更完整 —— 二选一） | 南烛 | **P0** | 写入者 13:48:42 仍在活动；不冻结则任何快照都会过期 |
| 2 | 修 `core/image_ops.py:538-554` 的 `l_cut_rects` 缩放缺失 + 加预览/导出一致性回归 | 南烛 | **P0** | 旗舰功能，风险最高 |
| 3 | 修 `core/image_ops.py:920,1391` 的字段名死守卫 | 南烛 | **P1** | 改正字段名或删条件，补断言防回归 |
| 4 | 更新 `packaging/packageV2.2.2.py` 版本号至 V2.2.3，同步 README / CHANGELOG | 南烛 | **P1** | 否则产物标识错误 |
| 5 | 修 `services/parser/template_matcher.py:295`：缓存改 JSON 分支 | 南烛 | **P1** | 唯一中危，修复成本极低 |
| 6 | 清理本次 QA 临时产物（`.pytest_tmp/`、`.qa_git.txt`、`.qa_scan.txt`、`.pytest_tmp_root.txt`） | 南烛 | P2 | 未跟踪文件；建议加入 `.gitignore` |

---

## ⚠️ 待完善 / 已知局限

- **审查未全量精读**：产品官按预算优先覆盖变更面，未逐行读 `services/sketch_parser/*`（multihole 2045 行、lshape 2006 行）、`core/lshape_border*.py`（1375+703 行）、`gui/` 各面板（数千行）、`services/parser/template_matcher.py`（1412 行）。如需对草图识别数值链深挖，可追加一轮。
- **依赖核查依据**：PyQt5 / `python-qt5` / psd-tools 结论来自联网核查（OSV.dev、Debian tracker、ReversingLabs、Snyk）；Pillow 12.3.0 / numpy 2.5.3 未逐版本联网比对全部 CVE。
- **架构分层 M-4 未给结论性方案**：`core/` 反向依赖 `services/` 属设计取舍，本次只标注风险未定改造方案（改动面大，建议单独立项）。
- **`PyQt5-Qt5` 未升级**：本次只读，未执行 `pip install`。
- **本次未做**：未打包、未安装依赖、未启动 GUI 冒烟、未做性能基准。
- **工作树并发改动（移动靶）**：本报告「工作树」结论基于 2026-09-19 **13:48:56 快照**（全量 673 全绿 / 0 失败 / 182.8s）。写入者在 13:45:09 与 13:48:42 均仍在活动，若其后有新写入，相关结论需重新核实。
- **活跃写入者身份未明**：三位成员全程只读，改动非团队所为。需用户确认是否为本人 IDE 操作，或存在并行会话；未确认前无法对工作树给出稳定的发布背书。

---

## 📚 成员产出索引

- **产品官**（`gstack-product-reviewer`）原始产出：代码审查报告，2 项高优先 + 4 项中优先 + 4 项低优先；工作区状态结论已由主理人修正为过时快照
- **安全卫士**（`gstack-security-officer`）原始产出：OWASP Top 10 + STRIDE 审计报告，1 中危 + 3 低危 + 2 信息 + 7 条正向确认；依赖供应链核查完成
- **质量门神**（`gstack-qa-lead`）原始产出：Standard 层级 QA 报告，含测试统计、失败项归因、环境伪失败辨析、覆盖薄弱清单、步骤化回滚预案

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
> 报告版本 v1.1（含主理人交叉复核 + 工作树时间线修订）｜ 生成日期 2026-09-19 ｜ 基线提交 `b444ada` ｜ 工作树快照 13:48:56
