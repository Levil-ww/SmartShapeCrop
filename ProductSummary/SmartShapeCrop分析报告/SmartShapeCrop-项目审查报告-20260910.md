# SmartShapeCrop 项目整体审查报告

- **审查日期**：2026-09-10
- **被审查版本**：V2.2（git HEAD `ee1097b`，最近一次提交 2026-09-10）
- **审查方式**：只读审查，未修改任何项目文件
- **审查范围**：全部源码（core / gui / tests / packaging / scripts / 配置与文档），约 1.48 万行 Python

---

## 一、审查范围与方法

| 项 | 说明 |
|---|---|
| 代码统计 | core+gui 共 30 个 Python 文件，约 14,760 行（不含测试、脚本、打包配置） |
| 测试基线 | 实测 `.venv`（Python 3.13.14）运行 `pytest tests/`：**320 passed / 5 skipped**，50.7s，全绿 |
| 检查方式 | 四个子系统并行细读（圆角裁剪核心 / 草图识别 / L 形挖角与渲染 / GUI 层）+ 关键声称独立抽查（打包 hidden imports、全图转换、嵌套矩形分支）+ git/日志/文档交叉核对 |
| 证据标注 | 本报告所有问题均标注 `文件:行号`，可直接定位 |

---

## 二、项目概览与总体评价

**SmartShapeCrop（智能形状裁剪设计器 V2.2）** 是面向印刷/定制设计行业的 Windows 桌面工具（PyQt5），核心能力三块：

1. **圆角裁剪工具**：成品图等比缩放 + 四角独立圆角裁剪 + 多层边框自动检测与圆弧重绘；
2. **水池设计器**：参数化/草图 OCR 智能识别（7 步串行流程），支持多洞嵌套、椭圆挖孔；
3. **L 形挖角设计器**：独立面板 + 草图识别 + V2.2 新增的"素材边框自动补全"（三级路由）。

**总体评价**：这是一个**业务价值明确、算法功底扎实、工程习惯良好**的成熟内部工具，但**质量账面上存在"文档宣称 ⊋ 实际实现"的系统性漂移**，且**发布链路（打包配置）尚未跟上 V2.2 的代码演进**，存在"发布版主功能静默失效"的实际风险。

- **优点**：模块化拆分到位（facade + 子包分工清晰）、单元/集成测试完备且全绿、README 极其详尽、线程退役范式（`canvas_widget._retire_worker`）规范、性能优化记录可追溯（ROI、向量化、LOD）。
- **主要风险**：① PyInstaller 打包漏收 V2.2 两个核心模块；② GUI 层 5 处 worker 未接入统一退役协议（F 系列问题的同源残留）；③ 多处"README 声称的能力在代码中不存在或被旁路"；④ 大图路径存在多处全图 float64 转换，与"支持 2 亿像素"宣称冲突；⑤ 子系统中魔法数字大量未收敛到 `core/config.py`。

---

## 三、架构分析

### 3.1 分层结构（健康）

```
main.py（入口：主窗口 + 三标签页 + 崩溃钩子 + 导出编排）
  └── gui/（PyQt5 界面层；面板 facade + mixin 拆分 + QThread worker）
        └── core/（纯业务层，不依赖 GUI）
              ├── geometry.py       设计对象模型（CropDesign/BorderLayer）+ mask 几何
              ├── image_ops.py      渲染/保存（Layout 宿主，1684 行，最重）
              ├── image_cropper*.py 裁剪服务门面（拆分为 border/mask 两个子模块）
              ├── corner/           圆角算法（algorithm/detection/sector_render 三角色清晰）
              ├── pool_designer/    草图识别（facade + base/cache/vision/numbers/margins/multihole/lshape）
              ├── lshape_border*.py L 形边框补全（入口 + Profile 路由，函数内延迟导入）
              ├── parser/           文件名解析 + 模板匹配
              ├── psd/              PSD 分层
              └── config.py         统一配置 + PathResolver
```

- 依赖方向单向（gui → core），`core` 内部无环，`compat` 兼容层用 `sys.modules` 别名保持旧导入路径可用，这些设计都是正确的。
- `property_panel.py` 用多继承 mixin（`_LayersMixin/_GenerateMixin/_PoolBoxMixin`）拆分 905 行大文件，方向正确，但尾部 822-905 行是空注释占位，拆分未完全收尾。

### 3.2 单一来源原则的落实情况（部分失效）

| 声称 | 实际 | 影响 |
|---|---|---|
| README：「mask 创建统一走 `carve_corner_on_mask`」 | 生产主路径 mask 由 `image_cropper_mask.py:59` 的 `_build_multi_layer_corner_mask` 自建另一套 mgrid 距离场；`carve_corner_on_mask` 仅走 `apply_rounded_corners`（非生产主入口）与 `geometry.py:366` | 两套圆角几何并存，语义差异被测试固化 |
| README：「间隙层判定统一为 `classify_gap_layers`」 | 部分成立，但 mask 路径（`image_cropper_mask.py:264-282`）仍存在与 classify 并行的独立 gap 移除逻辑 | 存在 4 个文件内的独立阈值实现（见 §5 P2-05） |
| config.py：宣称"与圆角几何相关的常量请勿散落到其他文件" | `corner/detection.py:237-241` 定义 GAP_*/SENTINEL 常量；sector_render/mask/border 均有函数局部魔法数（10.0/25.0/5.0/12.0/30.0/6.0…），草图识别更甚（40+ 处） | 改一个阈值需跨文件联查，"多处独立逻辑互相矛盾"的温床仍在 |
| `core/__init__.py:8-9`：「所有圆角处理必须经过 algorithm 模块…完全一致」 | 主路径不经过 algorithm（见上） | 文档失真 |

---

## 四、关键业务路径

### 路径 1｜圆角裁剪（上传 → 识别 → 预览 → 导出）

```
CropperPanel（选图/文件名解析/模板匹配）
  → crop_image（image_cropper.py:269）
      ├─ 缩放（simple_resize/cover/contain/light_cover/auto）      :320-333
      ├─ 边框检测 _get_border_layers_robust + 厚度硬上限           :296-318
      ├─ 外背景过滤 + 内容参考色采样（全图 float64，21×21 中值）   :284-356
      ├─ _build_multi_layer_corner_mask 四角 ROI 距离场 mask       :59
      ├─ border_only_corners 圆角遮蔽 + validity_mask 保护          :367-392
      ├─ Step A 弧线重绘（ROI 化，only_outermost，间隙智能处理）   :157+
      ├─ Step B 直边外轮廓补绘 / Step C 残留清理
  → CropWorker（无 parent QThread）→ 画布预览 → ExportSaveWorker 后台导出
```

**实现质量**：核心几何正确（半径钳制、0 尺寸守卫、角度 360° 绕接、距离容差均有防护）；**主要问题**：多层嵌套矩形检测结果在生产路径被丢弃（见 P0-03）、多处女全图 float64 转换与 2 亿像素宣称冲突（P0-02）、小图直边采样越界风险（P1-01）。

### 路径 2｜草图识别（拖入草图 → 8 字段自动回填 → 生成）

```
上传草图（主线程解码，大草图卡 UI，见 P1-07）
  → parse_sketch（sketch_parser.py:433）
      ├─ validate → 多洞快速分流（try_parse_multi_hole 预检，9 步法 Phase A-E）  :466-522
      ├─ 单洞：7 步串行（矩形检测→8 区划分→多尺度 OCR→小数修复→方向标签→空间映射→几何校验）
      └─ L 形支线：两矩形减法 + OCR 兜底 + 纯 CV 几何降级
  → 红色框标注 → SpinBox blockSignals 回填 → PoolRenderWorker 渲染
```

**实现质量**：OCR 稳定性投票（位置聚类 + 众数）、多尺度扫描、方向标签三重门等设计成熟；**主要问题**：OCR 循环无真正 deadline，最坏场景可累计到数百秒（P0-04）、L 形数量级修正存在错改对象风险（P1-02）、350px 间距死限在放大尺度静默失效（P1-03）、像素→厘米几何校验为死代码（P1-04）。

### 路径 3｜L 形挖角（参数/草图 → 一键生成 → 边框自动补全）

```
LShapePanel（挖角方向/尺寸/外框 + 草图识别结果回填）
  → _lshape_run_generate → _pool_run_generate（与水池共用生成链）
  → render_design（image_ops.py）：
      素材铺满 → L 形挖角区白填 → 统一黑框（_skip_unified 条件跳过）
      → 边框补全 apply_lshape_border_completion（三级路由）→ Stale-Decor 清理 → 文字
  → 后台 PoolRenderWorker → 画布
```

**三级路由**（`lshape_border.py:493-641`）：**Profile 路径**（lshape_border_route.py，1D 颜色剖面 + 锚点对齐 + 三层封顶）→ **V13 路径**（黑描边 + 主色带）→ **旧路径**（纯黑框兼容）。README 声称"任一环节失败自动落到下一环节"，但让位/补算两分支 `return _apply_v13_path(...)` 失败后不回退（P0-01）。

**实现质量**：Profile 剖面扫描实现闭环、注释详实，是 V2.2 最扎实的新代码；**主要问题**：打包漏收两个模块导致发布版整链失效（P0-00）、回退链断裂（P0-01）、无边框素材切边裸边回归（P1-05）、三条路由 scale 公式与补边方向不一致（P1-06）。

### 路径 4｜GUI 交互与后台线程

```
主线程：面板 UI ↔ design 对象 ↔ 信号 → main 窗口编排
后台：PoolRenderWorker（解析+匹配+渲染）/_SketchParseWorker/_LShapeParseWorker/
     CropWorker/ExportSaveWorker/_WarmupScanWorker
生命周期：canvas._retire_worker 规范退役（requestInterruption+wait+deleteLater）
         但 PropertyPanel/LShapePanel/CropperPanel 侧 worker 未接入同协议
```

**实现质量**：LOD 分级渲染（≥20 万像素走后台全分辨率）、preview/export 品质分层、导出快照克隆防竞争（F4）、sketch worker sender 过期校验——这些都是好实践；**主要问题**：面板侧 5 类 worker 无退役协议（P0-05）、TemplateMatcher 无锁跨线程共享（P0-06）、warmup 信号竞态可致永久"⏳"或双 worker（P0-07）、跨线程信号连接纯 Python 可调用对象导致 GUI 代码在 worker 线程执行（P0-08）、模板匹配在 GUI 线程全扫描卡死（P1-08）。

---

## 五、问题清单（按优先级，全部带证据）

### P0 —— 发布阻断 / 崩溃风险（建议进版前必须修复）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| P0-00 | **PyInstaller 打包漏收 V2.2 两个核心模块**。`core.lshape_border` / `lshape_border_route` 仅被函数内延迟导入（image_ops.py:1160、lshape_border.py:532），静态分析不追踪；HIDDEN_IMPORTS（packaging/packageV2.1.2.py:59-123）覆盖了 V2.1.2 全部新增模块但**唯独缺这两个**；README:243 已预警但打包脚本未跟进 | packageV2.1.2.py:59-123 | 发布版 L 形边框补全（V2.2 主卖点）运行时 ImportError 被 image_ops.py:1227-1228 静默吞掉，用户无提示，测试期不暴露。同时 APP_NAME 仍为"智能裁剪设计器V2.1.2"、dist 产物也是 V2.1.2.exe，版本号未随 V2.2 更新 |
| P0-01 | **路由回退链断裂**：让位分支（lshape_border.py:546-559）与补算分支（:594-607）`return _apply_v13_path(...)`，V13 patch 抛 ValueError 时（:760-762 捕获后返回 False）直接终止整个补全，Profile/旧路径不再尝试，与"任一环节失败自动落到下一环节"承诺不符 | lshape_border.py:546-559, 594-607 | 极小挖角/裁切贴角不齐等极端场景下素材失去边框补全 |
| P0-02 | **大图路径全图转换未收敛，与"支持 1-2 亿像素"（README:26）冲突**：`_estimate_outer_background` 仅需边缘带却整图 float64（mask.py:510）；内容参考色采样在 border.py:344 与 mask.py:729 **重复**整图 float64（20 亿像素 = 2×4.8GB）；border.py:92-94 三张全图 uint8 | image_cropper_border.py:92-94, 344; image_cropper_mask.py:510, 729 | 300dpi 印刷大图峰值内存 10GB+，大概率 OOM；README 性能承诺在生产主路径不成立 |
| P0-03 | **多层嵌套矩形检测结果被丢弃**：`corner_protect_map` 全角 True（border.py:369-371）→ mask.py:297-298 的 nested 恢复分支直接 `pass`；detect_nested_rect_layers（border.py:322）做了完整检测却无人使用结果 | image_cropper_border.py:369-371; image_cropper_mask.py:297-298 | README 主打"多层边框动态圆角 R_eff 逐层递减"（:270-273）实际未接线；检测全图开销纯浪费 |
| P0-04 | **OCR 循环无真实 deadline**：单洞 3 尺度×3~4 变体×3 PSM + 方向标签 5 图×2 语言×4 PSM ≈ 79 次 tesseract 调用，每次 timeout=20s（vision.py:464-465）；deadline（sketch_parser.py:556）从未传入 OCR 函数（签名无 deadline 形参），仅步骤边界检查 | core/pool_designer/sketch_parser_vision.py:445-546, 464-465 | 极端图片解析累计可达 800s，GUI worker 长时间阻塞，与"总 20s 超时"设计语义不符 |
| P0-05 | **GUI 面板侧 5 类 worker 未接入退役协议**：`_pool_worker`（generate.py:142 赋值，无 deleteLater/无 wait）、`_warmup_worker`（poolbox.py:307-316 用 terminate() 强杀线程）、`_sketch_parse_worker`（poolbox.py:655-656 wait 超时即替换）、`_lshape_parse_worker`（lshape_panel.py:489-494, 710-718）、CropWorker（cropper_panel.py:959/1010 无 parent、971 置 None 无 deleteLater）；main.py:521 aboutToQuit 只接管 canvas | gui/property_panel_generate.py:137-142; gui/canvas_widget.py:231-262; main.py:521 | 生成/识别/预热进行中替换或关窗 → "QThread destroyed while running"崩溃。**F16 修复只覆盖了 canvas 一条链路** |
| P0-06 | **TemplateMatcher 共享实例无锁**：worker 线程（workers.py:54-55 预热、160-163 匹配）与主线程（poolbox.py:340/344、generate.py:297/343/400/401）并发读写 `_cache`/`_dir_mtime`/`candidate.score`，全文无 Lock | core/parser/template_matcher.py（全文）; gui/property_panel_generate.py:297-401 | 生成中改模板库目录 → dict 迭代中变更（RuntimeError）或脏索引，匹配错乱/崩溃；F4 跨线程竞争在 matcher 层未修复 |
| P0-07 | **预热等待信号竞态**：generate.py:69-83 "isRunning 检查后再 connect finished"——若预热在其间结束，`_after_warmup` 永不执行 → 永久"⏳ 模板库预热扫描中"；反向：每次点击生成都追加连接且不复查 isRunning → 并发启动多个 PoolRenderWorker | gui/property_panel_generate.py:69-83, 87-143 | 生成无响应只能重启；或双 worker 双渲染 |
| P0-08 | **跨线程信号连接纯 Python 可调用对象**（PyQt5 DirectConnection 语义，回调在发射线程执行）：generate.py:74-79 `_after_warmup` 在线程里 setText 并 start 新 worker；:137-141 lambda 改按钮状态；cropper_panel.py:1011 lambda 执行 `_on_export_done`（弹 QMessageBox） | gui/property_panel_generate.py:74-79, 137-141; gui/cropper_panel.py:1011 | 线程不安全 GUI 访问，偶发崩溃；按钮不恢复类 bug 多源于此 |

### P1 —— 正确性与健壮性（建议近期修复）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| P1-01 | 小图直边采样越界/负索引：`straight_samples` 用深度 d 当行号，d 可 ≥ 图高；bl/br 用 `h-1-d`/`w-1-d` 负索引静默反向取样 | core/image_cropper_mask.py:836-866 | 小缩略图清理错位且不报错；建议 clip + 范围守卫 + 回归测试 |
| P1-02 | L 形数量级修正错改对象：`if 7.0 < ratio < 13.0: A = A/10`（只改 A 不区分哪个值被 OCR 放大 10×）；且真实长宽比 7~13 的素材会被无证据除以 10，自洽评分可能仍给高分（同比例缩放不降分） | core/pool_designer/lshape_sketch_parser.py:980-990, 1047-1076 | 尺寸错误且不可见；建议改为逐侧独立整十因子检查 |
| P1-03 | 350px 绝对间距上限在放大尺度静默失效：`gap_x > 350` 不随 scale 缩放，而 `3*min(w1,w2)` 随缩放变大，4× 图上先触发的是 350 死限；注释与行为相反 | core/pool_designer/sketch_parser_numbers.py:554-560, 622-654 | 方向字与数值绑定在放大扫描中丢失 → OCR 召回率下降（只降精度不出错值）；建议 `350*scale` |
| P1-04 | 像素→厘米几何校验为死代码：`_validate_geometric_constraints`（margins.py:586-626）被导入但无调用点；`_build_assignment` 中 `est_tw/est_th` 计算后未使用；sketch_parser.py:607-661 遗留 12 个"兼容占位"函数（仅 scripts 引用） | core/pool_designer/sketch_parser_margins.py:586-626, 647-658 | OCR 读出 10.0→1.0 的倍数级错误只能靠评分拦截，几何兜底缺失 |
| P1-05 | 无边框素材切边裸边回归：`_skip_unified` 对 `rect_lshape + 池素材` 无条件跳过统一黑框（image_ops.py:1136），补全三层检测全失败返回 False（lshape_border.py:610-612）且异常仅 debug 静默（image_ops.py:1227-1228） | core/image_ops.py:1131-1143, 1227-1228 | 纯花纹无边框素材的 L 形切边完全无框线（V2.1 至少还有统一黑框）；建议"补全成功才跳过统一黑框" |
| P1-06 | 三条路由 scale 公式不一致（V13/旧路径算术均值 `(sx+sy)/2`，Profile 几何均值 `sqrt(sx*sy)`），补边方向也不同（旧路径向挖空区延伸，V13/Profile 在保留区覆盖） | core/lshape_border.py:617, 708; core/lshape_border_route.py:568-569 | 非等比拉伸素材在同素材跨路由切换时边框厚度/方向突变 |
| P1-07 | 草图上传在主线程解码全分辨率图（poolbox.py:560-564 `Image.open().convert`；`_SketchViewerDialog` 全分辨率 QPixmap） | gui/property_panel_poolbox.py:560-564 | 大草图拖入即卡 UI |
| P1-08 | `_on_pool_finished_ok` 在 GUI 线程执行模板库全扫描 + 匹配（generate.py:297/343/400/401，另用 processEvents 制造可重入） | gui/property_panel_generate.py:297-401 | 首扫可达数十秒级 UI 卡死 + 与预热并发竞争 matcher |
| P1-09 | `_enforce_border_thickness_caps` 的"截断最末层"分支不可达（while 退出时必满足 total≤MAX），轻微超限（如 3.0→3.2cm）最末层整层被丢弃；注释声称的截断实现未实现 | core/corner/detection.py:134-163 | 信息损失大于设计意图 |
| P1-10 | `_post_cleanup_gap_regions` 与 sector_render 的 final_beyond（:464-471）与 :367 完全重复（谓词相同、中间循环只写 valid_region），是冗余全 ROI 扫描；sector_render.py:26-81/84-154 的 `_build_border_sector_mask`/`_sample_border_color` 为死代码仍被导出 | core/corner/sector_render.py:367-375, 464-471; core/corner/__init__.py:37-41 | 每角冗余扫描数百万像素；死代码误导维护 |

### P2 —— 维护性与一致性改善

| # | 问题 | 证据 |
|---|---|---|
| P2-01 | **README 与实现系统性漂移**（详见 §六 文档漂移清单），含"白色扇形伪影检测"整个功能不存在 | README:20, 293-297 |
| P2-02 | **防抖渲染是死代码**：`_init_apply_debouncer`/`_schedule_apply_quiet`（property_panel_layers.py:386-408）无任何调用者（所有连接在 property_panel.py:158-165/166-169/248-253/277/504/519/796/812 被注释），渲染实际只由生成按钮驱动；README:495/776 声称的"200ms 防抖"未生效 | gui/property_panel_layers.py:386-408 |
| P2-03 | 死信号/文档漂移：`lshape_params_changed` 连接存在但发射端已注释（lshape_panel.py:462-464）；lshape_panel.py:14-15 文档提到不存在的 `sync_sketch_to_lshape()/sync_target_to_lshape()`（真实方法 `sync_sketch_preview`/`sync_target_from_panel`） | gui/lshape_panel.py:14-15, 52, 462-464; gui/property_panel.py:492 |
| P2-04 | **魔法数字大量散落**：corner/detection.py:237-241（GAP_*/SENTINEL）、sector_render.py:333-346/394/425、mask.py:831-930（10.0/25.0/5.0/12.0）、border.py:96/291/305、pool_designer 全目录 40+ 处（0.30/0.9/0.8/350…）均未进 `core/config.py`；`_ALGO_VERSION` 双重定义（sketch_parser.py:43=7 vs cache=11） | 多处 |
| P2-05 | 箭头映射表 `_ARROW_CHAR_MAP` 重复键（multihole.py:66 `'←'` 两次）+ 错误条目（:69 `'↑': 'margin_right'`，靠 :72 后写覆盖才碰巧正确） | core/pool_designer/sketch_parser_multihole.py:64-77 |
| P2-06 | 静默吞异常无日志：multihole.py:1528-1533、numbers.py:760-762、vision.py:916-920 的 `except Exception: pass`（小数修复失效不可见）；建议至少 logger.exception | 多处 |
| P2-07 | 多洞结果不写缓存且单洞缓存查询在多洞预检之后（sketch_parser.py:466-534），同一张图第二次拖入仍完整重跑多洞 9 步法；quick_check 与 9 步法重复全图矩形检测 | core/pool_designer/sketch_parser.py:466-534 |
| P2-08 | `pool_mode` 字段与 `is_pool_mode()` 不一致：仅"裁剪有图"置位字段（name_parser.py:474/490/494），"水池"关键词只被方法识别；template_matcher.py:907/1304 直接读字段 → 含"水池"文件名走错评分分支 | core/parser/name_parser.py:35-46; core/parser/template_matcher.py:907, 1304 |
| P2-09 | PSD 加载：隐藏层仍 composite（loader.py:104-108）；失败静默返回 1000×1000 灰占位（:151）；层导出名含序号索引（:180），素材池按名引用会断链 | core/psd/loader.py:104-108, 151, 180 |
| P2-10 | 模板缓存 6 小时快速跳过上限（template_matcher.py:467）对子目录新增素材最长 6 小时不发现（NTFS 目录 mtime 不传播，注释已自述）；15000 候选均匀降采样可能丢最佳匹配（:1084-1088） | core/parser/template_matcher.py:464-467, 1084-1088 |
| P2-11 | LOD 预览 deepcopy 含 `_cached_outer_image` 的 design（image_ops.py:506/608），2 亿像素素材预览首帧内存翻倍；orig_* 变量保存后从未恢复（死代码 :475-477） | core/image_ops.py:475-477, 506 |
| P2-12 | 根目录卫生：未跟踪调试图 `_dbg_tl/tr/bl/br.png`（6.7MB×4）、`_debug_v13.py`、`Test-multiplhole.py`（拼写错误 + 引用不存在路径）；scripts/README 已约定"不要放根目录"，但产出未遵守；`tests/gui/` 为空目录但 README:115 声称有 GUI 模拟测试 | 根目录文件; tests/gui/; README:115 |
| P2-13 | 面板历史记录三套同构实现（cropper/poolbox/lshape_panel.py:723+），仅 source 键不同，可提取公共 mixin；1cm 损耗常量三处重复（lshape_panel.py:432/596、generate.py:235、workers.py:237）；cropper 预览成功弹强制 MessageBox 打断连续调参（cropper_panel.py:974-980）；`_render_async` 两分支完全相同死代码（canvas_widget.py:177-182） | 多处 |
| P2-14 | `_looks_like_tile` 用 `'hua'` 子串匹配（image_ops.py:1359-1362），"黄桦树""画卷"等误命中；热点采样函数 `draw_border_layers_on_cut_edges` 等遗留 print 改 logger（border.py:309/318） | core/image_ops.py:1359-1362; core/image_cropper_border.py:309 |
| P2-15 | `save_jpg` 未显式指定 subsampling，Pillow 默认 4:2:0 色度抽样，印刷细线/文字边缘易糊；建议 4:4:4 并加测试 | core/image_ops.py:1655 |

---

## 六、文档与实现漂移清单（README 需修正）

| README 声称 | 实现事实 | 位置 |
|---|---|---|
| 白色扇形伪影检测（<20 保留 / ≥50 清除 / 3px 簇） | **功能不存在**；实际是 beyond_arc 全清 + content_protect 保护 | README:20, 293-297 |
| mask 创建统一走 carve_corner_on_mask | 主路径用 `_build_multi_layer_corner_mask` 自建距离场 | README:66, 259 |
| 仅最外层圆角化 + protect_content 常驻 | 仅 `apply_border_only_corners` 主路径成立；`apply_rounded_corners`（测试参照物）传完整层、无保护，行为不同 | README:19, 275-279 |
| 亮度突变阈值 25 | detection.py:805 实际 `_BORDER_COLOR_DIFF_THRESHOLD * 3 = 75`（3 线均值差分专用） | README:306 |
| 颜色通道极差法 ≤8.0 | 实际标准差 `COLOR_STD_THRESH=10.0` | README:322 |
| OUTER_BAND 缩减至 3px | 实际 = 5 | README:326 |
| `is_outermost_solid` 标志 | 不存在，实际是 `i==0` 判断 | README:284 |
| 半径 70% 深度限制 | 未找到对应实现 | README:344 |
| 三处入口完全一致 | 仅 mask 几何统一到 algorithm 的声称不实（geometry 真用 carve，cropper 主路径不用） | README:663-668 |
| 测试 299 passed / 5 skipped | 实测 320 passed / 5 skipped | README:103 |
| tests/gui 有 GUI 模拟测试 | 目录为空 | README:115 |
| 防抖渲染 200ms/800ms | 死代码，未生效 | README:495, 776 |

> 风险说明：维护者按 README 理解系统行为会误判（尤其"伪影检测"这类**实际不存在的能力**），且测试以 `apply_rounded_corners` 为验收参照物会固化旧语义。建议进版前同步一次文档（改文档或改代码，以代码为真源）。

---

## 七、工程实践亮点（建议保留）

1. **线程退役范式**：`canvas_widget._retire_worker`（requestInterruption + wait + deleteLater，canvas_widget.py:231-262）是全项目最规范的模板，应推广到其余 worker；
2. **单测纪律**：17 个回归测试曾用 `return` 替代 `assert`，pytest.ini 把 PytestReturnNotNoneWarning 升为 ERROR 并在过滤规则中注明缘由（pytest.ini），防复发手段扎实；
3. **诊断体系**：scripts/ 有命名约定与生命周期（30 天清理）、根 conftest 防御性屏蔽非 tests 目录的 test_*.py（conftest.py），scripts/README 规范明确；
4. **性能工程记录**：README 的优化记录可追溯（EDT→圆角矩形差集 33×、连通分量 np.isin 毫秒级、LOD）；ROI 化的 Step A 弧线重绘本身实现正确；
5. **防御性编程**：半径钳制（≥4 入口）、0 尺寸守卫、OCR 数值范围校验（0.3-500cm）、全局唯一性检查、2 亿像素炸弹防御（config.py 相关段落 + image_ops）；
6. **崩溃可诊断性**：全局 excepthook 写 crash.log（main.py:468-494），PyInstaller 无控制台排障关键；
7. **历史记录物理隔离**：三个面板三套 TARGET_SRC_* 独立存取（app_settings.py），语义清晰。

---

## 八、改进路线图

### 短期（进版前，1-2 周）
1. **打包红线**：`packageV2.1.2.py` HIDDEN_IMPORTS 补 `core.lshape_border` / `core.lshape_border_route`，APP_NAME/产物版本升为 V2.2；打包后用 `pyi-archive_viewer` 或冒烟用例验证两模块存在；
2. **回退链修复**：让位/补算分支把 V13 当"可失败的一环"（成功才 return True，失败继续 Profile/旧路径），补"V13 命中但 patch 抛 ValueError"单测；
3. **GUI 线程安全收口**：以 `_retire_worker` 为模板统一 5 类面板 worker 退役；warmup 竞态改为一次性连接 + 复查 isRunning；跨线程信号全部改连 QObject 槽；TemplateMatcher 加 RLock；
4. **OCR deadline 落地**：deadline 贯穿三个 OCR 函数并逐轮检查；4× 放大按图像像素总量动态降级。

### 中期（1-2 个月）
5. **大图内存收敛**：内容参考色采样收敛为降采样共享函数（参考 detection.py:178-216 已实现的降采样），`_estimate_outer_background` 只转边缘切片，消除 border.py:344 与 mask.py:729 的重复全图转换；
6. **正确性补丁**：P1-02 数量级逐侧检查、P1-03 350×scale、P1-09 厚度截断改单循环、P1-01 采样坐标 clip + 小图回归测试；
7. **README 同步**：按 §六 清单以代码为真源修正，或反向补代码；
8. **缓存/性能**：多洞结果入缓存、quick_check 矩形复用、模板匹配移入 worker、"补全成功才跳过统一黑框"。

### 长期（技术债清理）
9. **阈值收敛**：GAP_*/SENTINEL、sector_render/mask/border 局部魔法数、pool_designer 40+ 阈值迁入 `core/config.py` 的 `SKETCH_*`/`CORNER_*` 常量段，`_ALGO_VERSION` 单源；
10. **死代码清理**：防抖三件套、`lshape_params_changed` 死连接、`_build_border_sector_mask`/`_sample_border_color` 及其导出、`_validate_geometric_constraints`、12 个占位函数、`_render_async` 死分支、根目录调试文件（_dbg_*.png、_debug_v13.py、Test-multiplhole.py）；
11. **双入口收敛**：`apply_rounded_corners` 与 `apply_border_only_corners` 二选一收敛为单一语义（H4 双入口行为差会被测试固化）；
12. **输出质量**：save_jpg 显式 4:4:4、PSD 隐藏层跳过合成 + 失败显式返回 + 导出名稳定化、`_looks_like_tile` 黑名单化、历史记录/1cm 常量公共化。

---

## 九、结论

项目**功能完整、算法正确性基础扎实、测试与文档习惯远优于同类内部工具**；当前最现实的威胁不在算法而在**工程交付层**：打包漏收（P0-00）+ 回退链断裂（P0-01）+ 面板 worker 生命周期（P0-05/07/08）三者叠加，意味着"发布版 V2.2 主功能静默失效 / 特定操作崩溃"的风险真实存在，且测试期发现不了。**建议按 P0→P1 顺序整改，优先完成打包验证与 GUI 线程收口后再发布**；随后以"文档同步 + 大图内存收敛"为第二轮投入点。四项子系统的完整证据链（含全部行号）已在本报告 §五 给出，可直接转换为修复单。