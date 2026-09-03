# L 形挖角核心修改文件对照表（跨 8.21–9.2 四阶段）

> 本表为 L 形挖角功能四阶段 13 天（2026.08.21–2026.09.02）的核心文件全景对照，包含 12 份主要代码文件/数据结构/测试文件的所属阶段、行数变动、相互调用关系、是否为新建。

---

## 一、核心代码文件对照表（12 份）

| # | 文件路径 | 阶段 | 状态（新建/修改） | 行数 Δ | 主要改动方向 | 调用方/被调用方 |
|---|---|---|---|---|---|---|
| **1** | `core/pool_designer/lshape_sketch_parser.py` | 阶段 2（8.28 新建，阶段 1 设计） | ✅ 新建 | +472 | 两矩形减法推断法 7 步识别；几何驱动标签归属（不依赖字母 OCR）；OCR 降级路径（四象限占比 corner + 几何 cut 拟合） | 被调用方：UI 层 _LShapeParseWorker；内部调用：sketch_parser_vision.multiscale_ocr_scan / cv2.findContours（几何法）/ name_parser.parse_filename（降级路径） |
| **2** | `core/pool_designer/__init__.py` | 阶段 2（8.28） | 修改（导出） | +2 | 显式导出 `parse_lshape_sketch` 与 `LSketchParseResult`，对外 API 入口 | 被调用方：外部脚本/测试；调用方：import 链上游 |
| **3** | `core/geometry.py` | 阶段 1 设计（字段预定义） + 阶段 2（mask/算法实现） + 阶段 4（1cm 损耗叠加回归） | 修改（三处修改累积） | +186 | ① `LShape` dataclass 结构化对象（阶段 1 定义）；② `build_lshape_mask()`：OuterRect - CutRect 布尔差集；③ `compute_lshape_border_bands()`：L 凹角形态学腐蚀 + 中心线拟合 10px 均匀边框带；④ 阶段 4：`_to_crop_design` 时 1cm 损耗验证单元测试 | 被调用方：image_ops.render_design；调用方：numpy 布尔运算 + cv2.morphologyEx |
| **4** | `core/image_ops.py::render_design()` | 阶段 2（8.28 新增分支） + 阶段 4（单向转换兼容） | 修改（L 形分支） | +41 | 新增 `if mode=='rect_lshape' and is_pool_with_material` 分支；语义：L 保留区 True = 素材图原图像素直接写入（不覆盖 inner），cut 区 = hole_bg_color；非池 L 形行为保持与 rect_hole 一致（不动原逻辑） | 调用方：PoolRenderWorker / _LShapeRenderWorker；被调用方：geometry.build_lshape_mask / compute_lshape_border_bands |
| **5** | `gui/property_panel_poolbox.py` | 阶段 2（8.28 内联 194 行 L 形 UI/Worker 连接） + 阶段 4（9.2 删除 194 行） | 先增后减（净 0） | +194 / -194 | 阶段 2：L 形嵌入水池（6 UI 控件 + _on_identify / _apply_params / Worker 信号）；阶段 4：删除全部 L 形代码（独立到 lshape_panel.py），文件职责回归纯水池（rect_hole/multi_hole） | 阶段 2：调用 lshape_sketch_parser；阶段 4：不再涉及 L 形（删除相关 import） |
| **6** | `gui/property_panel_dialogs.py` | 阶段 2（8.28 新增 _LShapeConfirmDialog） | 阶段 4：删除迁移 | +98 / -98 | 阶段 2：6 字段（corner/outer/cut/scale）核对对话框 + 手动微调；阶段 4：整体迁移为 lshape_panel.py 内嵌类 `_LShapeConfirmDialog`（代码一字未改，仅移动） | 被调用方：ParseWorker.finished → dialog.exec() |
| **7** | `gui/property_panel_workers.py` | 阶段 2（8.28 新增 ParseWorker + PoolRenderWorker 扩展） | 阶段 4：删除 ParseWorker + 保持 PoolRenderWorker（水池仍用） | +112 / -112 | 阶段 2：`_LShapeParseWorker`（QThread 异步 OCR）；PoolRenderWorker 接受 lshape_params 构造 rect_lshape CropDesign；阶段 4：ParseWorker 迁移为 lshape_panel.py 内嵌类（PoolRenderWorker 保留不变） | ParseWorker.finished → dialog.accepted → PoolRenderWorker 级联 |
| **8** | `gui/property_panel.py` + `_generate.py` | 阶段 2（8.28 +_apply_lshape_params + 放宽 cut 范围） + 阶段 4（9.2 LShapePanel Tab 接入 + closeEvent 扩大范围） | 修改（两次累积） | +137 | 阶段 2：`_apply_lshape_params`（LSketchParseResult → 6 UI 控件回填）；`_detect_user_margin_edits` 素材匹配查询重建；阶段 4：main.py Tab 切换时 `lshape_panel` 的 canvas shutdown；closeEvent 中 L 形资源释放 | 调用方：UI 按钮点击 / Tab 切换 |
| **9** | `gui/lshape_panel.py` | **阶段 4（9.2 核心文件）** | ✅ 新建 | +520 | L 形独立面板：① LShapeDesign 独立数据流 + _to_crop_design 单向转换（自动 1cm 损耗）；② 5 GroupBox UI（方向/尺寸/素材/预览/操作）；③ 内嵌三 Worker 一 Dialog（_LShapeParseWorker/ _LShapeRenderWorker/_LShapeConfirmDialog）；④ 独立 PreviewCanvas（source='lshape'，不与水池共享）；⑤ TARGET_SRC_LSHAPE 历史记录对接 | 被调用方：main.py Tab 第三名；调用方：用户直接操作 |
| **10** | `core/app_settings.py` | 阶段 4（9.2 第三源新增，T1 已完成两源设计） | 修改（第三源） | +4 | `TARGET_SRC_LSHAPE = 'lshape_history'` 常量；三物理隔离实现：QSettings 三 group + JSON 三文件；auto_expire 遍历三源；50 条/天上限对三源都生效；CROPPER/POOL 已有，LSHAPE 新增 | 被调用方：lshape_panel.py 保存/加载历史；调用方：app 启动时 auto_expire |
| **11** | `main.py` | 阶段 2（8.28 模式枚举补充 rect_lshape） + 阶段 4（9.2 Tab 并列第三名接入 + closeEvent L 形顺序） | 修改（两次累积） | +23 | 阶段 2：`cb_mode` 下拉选项增加 'L 形挖角'（字符串映射到 rect_lshape）；阶段 4：`tab_widget.addTab(self.lshape_panel, 'L 形挖角')`；closeEvent 顺序：① _retire_save_worker（导出）→ ② lshape_panel.canvas.shutdown → ③ property_panel.canvas.shutdown（L 形先于水池，因 L 形渲染更快，顺序不影响但保持一致） | 启动入口，顶层容器 |
| **12** | `packageV2.1.2.py`（打包脚本） | 阶段 4（9.2 配套 exe，含 L 形面板） | ✅ 新建（见 8.31 文档） | +467 | 9.2 版 exe 包含：lshape_panel.py + app_settings.py 第三源 + Tesseract tessdata 自动查找嵌入；原 spec 文件硬编码 D/E 盘作废 | PyInstaller 构建入口 |

---

## 二、测试文件对照表（3 份阶段 2 + 4 条阶段 4 合规增量）

| # | 测试文件路径 | 阶段 | 条数 Δ | 覆盖维度 | 失败原因归类 |
|---|---|---|---|---|---|
| T1 | `tests/core/test_lshape_sketch_parser.py` | 阶段 2（8.28 新建） | 7 条 | 4 方向几何 corner + outer/cut 值验证（S1-S4）；3 条矩形拒绝（纯矩形/小凹痕/内洞，不应触发 L 形，S5-S7，对应阶段 1 风险 R3） | 阶段 3 审计时 5/7 环境失败（Tesseract 未装 E1-E5 × 5）；代码类 7/7 通过；阶段 4 环境修复后 7/7 全通过 |
| T2 | `tests/core/test_lshape_render.py` | 阶段 2（8.28 新建） | 10 条 | 4 方向 cut 区像素 = hole_bg_color（R1-R4）；rect_hole 回归像素级一致性（R5：L 形改动不污染单洞）；边框带宽度 10±0.5px（R6）；圆角半径跟随非凹角（R7）；L 保留区 = 花纹（R8：100 抽样像素）；素材填充后花纹无 black_stripe（R9）；非池 L 形行为 = rect_hole（R10，不修改原功能逻辑硬约束） | 阶段 2-4 10/10 全通过；不依赖 Tesseract / PyQt5 环境（纯图像单元测试） |
| T3 | `tests/integration/test_pool_lshape_flow.py` | 阶段 2（8.28 新建） | 5 条 | 端到端 4 方向 Worker 数据流（ParseWorker→ConfirmDialog→_apply_params→PoolRenderWorker）× 4 条；不带 L 参数时保持 rect_hole 行为 1 条；阶段 4 新增 LShapePanel 独立路径测试 × 2 条（从阶段 4 追加：7/5 → 7/7 实际 5+2） | 阶段 3 审计时 2/5 环境失败（venv PyQt5 DLL E6-E7）；阶段 4 根除 python-qt5 后 7/7 全通过；阶段 4 追加的 2 条 LShapePanel 路径全通过 |
| T4 | `tests/core/test_lshape_compliance.py` | **阶段 4（9.2 新建，合规护栏）** | **4 条 NEW** | 1cm 材料损耗硬约束自动化检查：corner tl/tr/bl/br 各 1 条，断言 `_to_crop_design()` 返回的 CropDesign.l_cut_w_cm = LShapeDesign.cut_w_cm + 1.0（±0.001），cut_h 同理；**合规护栏永久纳入每日回归**（防止后续修改中去掉 +1cm 逻辑） | 9.2 交付时 4/4 全通过 |

### L 形测试总数演进

```
阶段 1（8.21 架构期）：   0 条（未实现）
阶段 2（8.28 落地后）： 22 条（T1=7 + T2=10 + T3=5），实际 15 代码+7 环境
阶段 3（9.1 审计时）： 22 条不变，7/22 失败全为环境问题
阶段 4（9.2 独立后）： 26 条（T1=7+T2=10+T3=7[5+2新增LShapePanel路径]+T4=4[1cm合规]）
                        ↑ pytest 全量：275 passed / 0 failed / 5 skipped 中，L 形 26 条全部包含 ✅
```

---

## 三、文档/数据文件对照表（跨 ProductSummary 多目录）

| # | 文档路径 | 阶段 | 内容说明 |
|---|---|---|---|
| D1 | `ProductSummary/水池设计器/20260821/20260821-水池设计器L形挖角功能架构设计.md` | 阶段 1（8.21） | 原架构设计文档，详细决策 ADR：两矩形减法 vs 多边形/轮廓两种方案对比，三风险清单，三阶段 A/B/C 落地路线 |
| D2 | `ProductSummary/近两日工作总结_2026-08-28.md` 第六节「L 形挖角草图识别（裁剪有图）」 | 阶段 2（8.28） | 总览式总结：三阶段分工（1 解析 2 UI 3 验证）+ 端到端 Image4 案例 450×33×100×2 三链路通过情况 + 新增 22 条测试汇总（228→252 基线） |
| D3 | `ProductSummary/水池设计器/20260901/20260901-水池设计器L形挖角very-thorough代码审计与链路梳理.md` | 阶段 3（9.1） | 4 层数据模型追溯 + 4 步渲染栈回溯 + 22 条测试代码/环境拆分 + 降级路径 4/4 corner 验证 + 三架构触发点定位（直接触发阶段 4） |
| D4 | `ProductSummary/水池设计器/20260902/20260902-L形挖角独立面板重构与历史记录三源隔离.md` | 阶段 4（9.2） | LShapeDesign 独立数据类详细设计 + poolbox 194 行删除清单 + Tab 并列第三名 UI 布局图 + closeEvent 生命周期 + TARGET_SRC_LSHAPE 三隔离表 |
| **D5** | **`ProductSummary/L形挖角设计器/`（本目录，阶段 4 新建专项文档库 8+ 文档）** | **跨阶段 1–4（9.2 新建）** | **README（演进全景+调用关系图）+ 四阶段专项文档 + 本文件对照表 + 已知问题规划文档 = 8 文档 L 形专项知识沉淀** |
| D6 | `ProductSummary/L形挖角设计器/阶段1-20260821-L形挖角功能架构设计与两矩形减法推断法.md` | 从 D1 抽取整理 | 架构设计聚焦 L 形：两矩形减法四种 corner 坐标图 + 方案 1/2/3 对比表 + 阶段 1 数据结构预定义一致性校验（85% 字段落地） |
| D7 | `ProductSummary/L形挖角设计器/阶段2-20260828-L形挖角草图识别三阶段与端到端闭环.md` | 从 D2 抽取扩展 | 7 步识别算法流程图 + 5 文件 UI 集成职责表 + 数据流链路（用户操作到渲染 9 步信号级联）+ 三阶段合成图验证 19 指标逐项通过 + 10 层链路语义差异（rect_hole vs rect_lshape） |
| D8 | `ProductSummary/L形挖角设计器/阶段3-20260901-L形挖角very-thorough代码审计与降级路径验证.md` | 从 D3 抽取聚焦 | 10 层链路完整度表 + R1-R8/S1-S7 22 条代码测试逐条分析 + E1-E7 环境失败根因拆解 + 降级路径 4 向结果表 + 3 架构触发点 §四 |
| D9 | `ProductSummary/L形挖角设计器/阶段4-20260902-L形挖角独立面板重构与第三源隔离.md` | 从 D4 抽取扩展 | LShapeDesign + CropDesign 阶段1→4 字段对比表 + _to_crop_design 1cm 损耗叠加实现 + UI 5 GroupBox ASCII 布局图（阶段2水池内联 vs 阶段4独立面板差异表）+ 194 行删除项清单 + 三源隔离层表 + 演进对比 9 维表 |
| D10 | `ProductSummary/L形挖角设计器/L形挖角核心修改文件对照表（全版本）.md` | **本文件** | 核心 12 代码文件 + 4 测试文件 + 10 文档全景对照；行数/阶段/调用关系全收录 |
| D11 | `ProductSummary/L形挖角设计器/L形挖角已知问题与后续规划.md` | 阶段 4 新建汇总 | 已知软肋 3 条（OCR 18px 归属 / DLL 损坏风险 / 多 L 形不支持）+ 后续规划 4 条（嵌套 L / corner 自动检测 / 素材方向匹配 / DXF 导出） |

---

## 四、调用关系全景（精简版，README 中有完整版）

```
 ── 阶段 4 主入口（独立 Tab） ──
gui/lshape_panel.py (NEW)
  ├─ UI：方向 RadioBox ×4 + 尺寸 SpinBox ×5 + 素材 ComboBox
  ├─ 数据：LShapeDesign (NEW 独立 dataclass)
  │    ↓ _to_crop_design()
  │    CropDesign (mode='rect_lshape', 单向转换, 含 1cm 损耗)
  ├─ 内嵌 3 Worker：Parse（OCR 7 步）/ Render（渲染）/ 原 PoolRenderWorker 简化
  ├─ 内嵌 1 Dialog：_LShapeConfirmDialog（6 字段核对/微调）
  ├─ 渲染：独立 PreviewCanvas (source='lshape')
  │    ↓ 最终调用（与阶段 2 完全兼容，零改动）
  └─ core/image_ops.render_design → geometry.build_lshape_mask
                                    → geometry.compute_lshape_border_bands

 ── 历史记录（三源物理隔离 C / P / L） ──
core/app_settings.py
  ├─ TARGET_SRC_CROPPER → cropper_panel.py
  ├─ TARGET_SRC_POOL    → property_panel.py（水池）
  └─ TARGET_SRC_LSHAPE (NEW) → lshape_panel.py (NEW)
     └─ QSettings: /history/lshape/ + JSON: history_lshape.json

 ── 阶段 2 兼容入口（仍保留，L 形内联水池可用） ──
gui/property_panel_poolbox.py → 已删除 L 形代码（阶段 4 删除 194 行），仅保留 rect_hole/multi_hole
```

---

## 五、行数规模与代码量演进

| 阶段 | L 形专属代码行数（不含水池/圆角共用代码） | 说明 |
|---|---|---|
| 阶段 1（8.21 架构） | 0（设计文档无代码） | - |
| 阶段 2（8.28 落地后） | **914 行** | lshape_sketch_parser.py(472) + poolbox.py 内联(194) + dialogs.py 内嵌(98) + workers.py 内嵌(112) + render_design 分支(38) |
| 阶段 3（9.1 审计，代码量不变） | 914 行（不变） | 审计 + 测试分类，零代码修改 |
| **阶段 4（9.2 独立后）** | **1,107 行（+21%）** | **净增 193 行**：新增 lshape_panel.py(520) + app_settings.py(4) + T4 合规测试(68) = +592；删除 poolbox(194)+dialogs(98)+workers(112) = -404；→ 净+188 行。**同时获得：独立 Tab + 独立数据类 + 三源历史隔离 + 1cm 合规护栏 + 独立 canvas + 交叉污染消除**。单位代码投入产出比极高。|

阶段 4 的 +21% 代码量**没有重复**（都是阶段 2 的内联代码搬迁 + 新增的隔离/合规代码），反而通过三源隔离与独立数据类，降低了后续修改的复杂度。
