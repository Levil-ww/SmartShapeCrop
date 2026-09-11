# core/ 图像处理核心链路只读代码审查发现清单

审查范围：F:\SmartShapeCrop\core\（PyQt5 印刷图像设计器 V2.2 / Python 3.13）
审查方式：只读，未修改任何文件
审查时间：2026-09-11
证据格式：文件:行号

---

## 汇总

| 严重度 | 条数 |
| --- | --- |
| P0 高危（崩溃/数据错误/主卖点失效） | 0 |
| P1 中危（性能/内存/并发风险） | 6 |
| P2 低危（死代码/文档漂移/可维护性） | 9 |
| **合计** | **15** |

---

## P0 高危（0 条）

无。未发现可复现的崩溃、数据错误或在常规输入下主卖点失效的问题（含 patch/绘制级回退断裂，见 P1-1，触发面窄，按 P1 处理但标注了升级语义）。

---

## P1 中危（6 条）

### P1-1 【回退链在绘制级仍断裂】V13 patch 失败直接 `return`，不回退 Profile/旧路径；失败被 `_skip_unified` + 丢弃返回值双重掩盖

- `lshape_border.py:546`、`lshape_border.py:594`：两处 V13 命中分支均为 `return _apply_v13_path(...)`。
- `_apply_v13_path` 内部有 4 条失败出口，全部 `return False`、无后续回退：
  - `lshape_border.py:696-698`（edge 缺失或 ≤0）
  - `lshape_border.py:732-734`（outer_rect 子图 < 4px）
  - `lshape_border.py:740-742`（挖角尺寸 < 1px）
  - `lshape_border.py:760-762`（`patch_lshape_cut` 抛 `ValueError`）
- 即：**检测级回退完整**（`detect_border_v13` 返回 None → `lshape_border.py:538-541/586-609` 流向 Profile 或旧 `detect_pool_material_borders`），但**一旦检测命中进入 patch，绘制失败即直接终止，不回 Profile、不回旧路径**。`lshape_border.py:575` 的"Profile 绘制失败→回退 V13/旧路径"只覆盖 profile 层，V13/patch 层无对称逻辑。
- 失败被三层掩盖：
  - `image_ops.py:1143` `_skip_unified = design.mode == 'rect_lshape' and is_pool_with_material` → `1149-1150` 跳过 `canvas_arr[border_mask] = BLACK_RGB` 统一黑框兜底，补全失败时切口边缘无任何边框；
  - `image_ops.py:1209` `_completion_ok = apply_lshape_border_completion(...)` 返回值**赋值后从未读取**（grep 全 core 仅 1 处出现）；
  - `image_ops.py:1234-1235` 外层 try/except 只兜异常，不兜 `False` 返回值。
- 用户可见后果：某素材 V13 检测命中但 patch 失败（异常结构/极小尺寸）时，L 形切口缺边框且无兜底、无日志告警（仅 info 级，见 697/733/741/761 行的 logger.info）。
- 按任务分级语义（主卖点失效）此条可视为 P0，但触发面窄（需 V13 命中且 patch 失败），故列 P1 并注明：**若要根治，应在 546/594 行把 `return _apply_v13_path(...)` 改为"patch 失败则继续向下回退"**。

### P1-2 【内存峰值仍存】全图 uint8→float64 整图转换共 4 处（活代码），与 README"1-2 亿像素"承诺冲突

- `image_cropper_border.py:344` `img_arr = np.array(img, dtype=np.float64)`：仅为了 344-356 行 21×21 采样中位色，却整图转换。2 亿像素 ×3 通道 ×8 字节 ≈ 4.8GB/处。
- `image_cropper_mask.py:729` `src_arr = np.array(src_img, dtype=np.float64)`：`_post_cleanup_gap_regions`（活代码，`image_cropper_border.py:452` 调用）内同样只为 21×21 采样（730-737 行）。
- `lshape_border.py:66`（`_is_real_border`，仅用 `arr[edge_mask].mean` / `arr[center_mask].mean`，80-81 行）与 `detect_pool_material_borders` 每次调用都会执行。
- `lshape_border.py:123`（`_filter_content_layers`，仅用中心区均值 130 行）。与上一处在同一条检测链路上各转换一次。
- 对照：`detection.py:196-205` 已对内容参考色做降采样（注释 186-188 明确写"避免 2 亿像素 ×3×8=4.8GB"），`lshape_border`/`image_cropper_border` 未复用该方案。
- 死代码上的同类转换另见 P2-2（`image_cropper_mask.py:510/552`）。

### P1-3 【内存峰值仍存】LOD 预处理 `deepcopy(design)` 内存翻倍

- `image_ops.py:504-506`：`lod_design = deepcopy(design)`。`design` 携带 `_cached_outer_image` 等 `PIL.Image`（见 `image_ops.py:609-619` 缓存读取路径），`PIL.Image.__deepcopy__` 会复制像素数据 → LOD 阶段整份素材内存翻倍，与 P1-2 的 float64 转换叠加时峰值更高。
- 建议：LOD 分支只复制轻量字段或先降采样再深拷贝。

### P1-4 【性能死路径】`detect_nested_rect_layers` 结果恒被丢弃：每次圆角裁剪白跑一次全图四边多层扫描

- `image_cropper_border.py:322` 调用 `detect_nested_rect_layers(img, border_layers=border_layers)`（异常时 323-325 兜底 `[]`，326-327 空则回退整图外框）。
- 结果经 `image_cropper_border.py:376-381` 传入 `_build_multi_layer_corner_mask(nested_rects=...)`；
- 但 `image_cropper_border.py:369-371` 构建的 `corner_protect_map` 对**所有角恒为 True**（`ck: (r_px > 0)`，r_px>0 才进 corners_px）；
- `image_cropper_mask.py:188-191` 每个角 `corner_protect` 恒 True → `image_cropper_mask.py:297-298` `if corner_protect: pass` → **B 段（300-387 行，nested_rects 逐层恢复）恒不执行** → `nested_rects` 恒为未使用输入。
- 代价：`detection.py:853` 全图 uint8 复制 + `855-858` 四边 `_scan_edge_boundaries` 全图步长 2 扫描 + `861-873` 层合成，在每次 `apply_border_only_corners` 时完整执行一遍后全部丢弃。
- 附带：`image_cropper_mask.py:108-117` 的 `nested_rects is None` 分支同样写死 `nested_rects = []`（注释自认"调用方应优先传入"），无真正重新检测能力。
- 建议：`corner_protect` 恒 True 时跳过检测并删除 B 段（或反之），两者互斥保留其一。

### P1-5 【并发风险】template_matcher 无任何线程锁：scan 写缓存与 find_best_match 读并发可竞态

- `core/parser/` 全目录 grep `threading|Lock|RLock` **零命中**。
- `template_matcher.py` 类状态 `_cache`、`_idx_pattern`、`_idx_layout`、`_idx_circular`、`_idx_ratio`、`_subdir_mtimes` 均为普通 dict；`scan_library` 扫描期间改写这些 dict 并写磁盘缓存（tmp+replace），`find_best_match` 会隐式触发 `scan_library`（并行读）；评分循环写 `e.score = score`（共享 `TemplateEntry` 可变字段）与扫描线程 `_upsert_entry` 替换同一 key 构成 check-then-act 竞态 → 可致 `RuntimeError: dictionary changed size during iteration`、评分交叉污染或缓存文件两次写之间的撕裂（多进程场景）。
- `name_parser.py`（848 行）为纯函数无状态，线程安全 ✅；`psd/loader.py` 每次独立加载，无共享可变状态 ✅。

### P1-6 【2px 检测阈值保持原样】圆角重绘主链仍按"步长 2 + 最小层厚 2"工作

- `config.py:102`：`BORDER_SCAN_STEP_PX = 2`、`BORDER_MIN_LAYER_THICKNESS_PX = 2`（阈值集中管理 ✅，但数值未变）。
- `detection.py:45-49`：从 config 别名导入 `_BORDER_SCAN_STEP`、`_BORDER_COLOR_DIFF_THRESHOLD = BORDER_LUMINANCE_DIFF_THRESHOLD`（=25，R+G+B 一阶差分阈值 ×3 = 75）、`_BORDER_MIN_GAP_PX`、`_BORDER_MAX_LAYERS`、`_EDGE_IGNORE_PX`。
- `detection.py:732` `_scan_edge_boundaries` 以步长 2 采样；`detection.py:819` 相邻边界最小间距按 `_BORDER_MIN_GAP_PX` 过滤。
- 现状：**L 形补全链路已被 V13（1D 段分析）+ Profile 路由绕过**（`lshape_border.py:577-607`，对"黑描边+主色带"不再依赖 2px 结构），但**圆角重绘主链**（`apply_border_only_corners` → `_get_border_layers_robust`/`detect_nested_rect_layers` 均走 `_scan_edge_boundaries`）仍受 2px 采样/最小层厚约束。历史"边框检测只 2px"问题的源码结构仍在，仅通过新检测路径部分缓解。

---

## P2 低危（9 条）

### P2-1 【常量未集中 + 注释漂移】GAP_*_GLOBAL 硬编码于 detection.py，config.py 无对应定义

- `detection.py:237-240`：`GAP_MAX_THICKNESS_GLOBAL = 40.0` / `GAP_NEIGHBOR_MIN_DIST_GLOBAL = 25.0` / `GAP_BG_DIST_GLOBAL = 80.0` / `GAP_CONTENT_DIST_GLOBAL = 70.0`。
- `config.py` 中无 `GAP_*` 键（grep 确认），而 `detection.py` 模块 docstring 第 9 行声称"所有阈值/步长常量定义在 core/config.py" → 文档漂移。
- `detection.py:230-231` 注释（INV-G2 40px / INV-G3 20）与代码（40.0 / 25.0）数值不一致（25 vs 20 注释漂移）。
- `GAP_NEIGHBOR_MIN_DIST_GLOBAL = 25.0` 与 `config.BORDER_LUMINANCE_DIFF_THRESHOLD = 25` 数值巧合但来源独立，易各自漂移。

### P2-2 【死代码 + 死代码上的内存陷阱】`_analyze_corner_sector_content` / `_estimate_outer_background` / `_corner_sector_has_content` 无调用方

- `image_cropper_mask.py:396`（`_analyze_corner_sector_content`）、`502`（`_estimate_outer_background`）、`531`（`_corner_sector_has_content`）定义后**无实质调用**：仅 `image_cropper.py:124` 与 `image_cropper_border.py:60` import；`image_cropper_border.py:358-361` 注释明确"不再依赖 _corner_sector_has_content 的自动判断"。
- 其中 `image_cropper_mask.py:510`（`_estimate_outer_background` 内全图 float64）与 `552/597` 是死代码上的内存陷阱，删除三条函数可同时消除。
- `image_cropper.py:209` 旧入口仍传 `skip_outside_arc=True`（非保护模式语义），与 border 新入口（`False`，见正面确认-2）并存，属设计分支非死代码。

### P2-3 【死代码】`image_cropper_mask.py:297-298 pass` + B 段（300-387）整体不可达

- 因 `corner_protect` 恒 True（见 P1-4），B 段 90 行"嵌套矩形逐层恢复"逻辑为语义死代码；`_completion_ok` 死变量（`image_ops.py:1209`）同列此条。

### P2-4 【逻辑与注释相悖】`_enforce_border_thickness_caps` Step 2 可能误丢最内层真实边框

- `detection.py:52` 定义，在 `_get_border_layers_robust`（`detection.py:439`）与 `_detect_border_layers` fallback 后（`detection.py:727`）两处调用，幂等。
- Step 2 while 循环"先 pop 最末层、后检查 total"：若 pop 后 total 恰好 ≤ `MAX_TOTAL_PX`，该层被无条件丢弃，与注释"若仅超出一点点则截断最末层"意图相反，可能误丢真实最内层边框 → 轻微视觉风险。

### P2-5 【文档漂移】`apply_lshape_border_completion` docstring 三级路由承诺与实际不符

- `lshape_border.py:482-492` docstring 描述"V13 返回 None → 回退旧路径"，`781-782` 注释一致，但均未覆盖 P1-1 的 patch 级回退缺失；注释写"两者都无 → 返回 False 跳过补全"，实际"V13 命中但 patch 失败"也直接返回 False（跳过补全），未走"回退旧路径"承诺。

### P2-6 【精度误差】素材拉伸方式（contain 等比+边缘延展）与 scale 整图比值换算存在 ±px 偏差

- `image_ops.py:630-637 / 644-651`：`adapt_pool_material` 用 contain 等比 + 边缘延展填充（`601-608` 注释说明设计决策）；`1193-1202` 的 `_scale_x/_scale_y = canvas/原始素材整图比值` 假设素材铺满全画布，含留白/logo 素材时失真；
- `lshape_border.py:617` `scale_avg = (scale_x + scale_y) / 2` 注释自认"非等比取平均" → 边框厚度换算 ±px 误差。

### P2-7 【潜在共享状态】`lshape_border_route.py` 模块级惰性搜索状态无锁

- `lshape_border_route.py`：`_SEARCH_STEPS`、`_optimize_center_and_radius`、`_inset_x_cm/perf` 等模块级惰性局部搜索状态（含 `_r_cm()` 换算）无锁共享。当前渲染走主线程单写，无实际并发；未来若后台并行预览线程接入即交叉污染（与 P1-5 同类，未达 P1 现势）。

### P2-8 【可维护性】cm↔px 单位换算分散多处，无集中 converter

- `image_cropper_border.py:335`（`round(radius_cm * dpi / 2.54)`）、`lshape_border_route.py`（`_r_cm` 等）、`image_ops.py` render 内多处 DPI 计算；`config.py` 仅提供 `DEFAULT_DPI`/`DEFAULT_BORDER_WIDTH_CM` 等静态值，无统一换算函数。调整 DPI 语义时易漏改。

### P2-9 【双后端一致性】`app_settings.py` QSettings 与 JSON 后备序列化行为不一致

- `app_settings.py:127-135`：QSettings 分支 `_read` 返回原始 str/List，JSON 分支返回 Python 对象；
- `198-213`：同一键 `KEY_TEMPLATE_HISTORY` 在 QT 分支 `json.dumps` 成字符串、JSON 分支存 list[dict] —— 切换后端时读取路径不同（`_load_history` 170-196 已兼容 str 分支，但 `_load_target_name_history` 324-352 仅处理 str/raw dict），跨模式迁移低风险不一致。

---

## 正面确认（按要求核验、无问题或已修复项）

1. **sector_render.py near_edge 四角分支**：TL/TR/BL/BR 分支已完整覆盖（2026-09-10 修复），无遗留单角遗漏。
2. **`skip_outside_arc=False` 补绘**：`image_cropper_border.py:438-443`（2026-09-10 Fix）在 `_redraw_outer_border_on_corners`（69 行）中以 `skip_outside_arc=False` 调用，`140-141` 行仅 True 时收窄 band，False 时补绘 `dist ∈ [max(0, r-5), r+1.5]` 全部 border_zone 像素 → 圆弧处边框连续性修复生效；`image_cropper.py:209` 旧入口 True 分支为设计语义（非保护模式），非缺陷。
3. **`detection.py:196-205` 内容参考色降采样**：最长边 >200px 先 `resize(BILINEAR)` 再转 float64，是为数不多的防 4.8GB 好实践，可复制到 P1-2 各处。
4. **`image_ops.py:1167` 延迟导入位置**：`from .lshape_border import ...` 位于 render 内 try 块，避免顶层导入负担；`lshape_border.py:532` 每次调用内导入 `lshape_border_route` 有模块缓存，开销可忽略。
5. **`name_parser.py`**：纯函数、无模块级可变状态，线程安全 ✅。
6. **`psd/loader.py`**：每次调用独立加载，`PsdLoadError`/`PsdLayer`/dependency_missing 分支完整（76-100 行 try/except），无共享状态 ✅。
7. **`artifact_cleanup.py`**：白名单目录隔离、保护名单（smartshapecrop.log/crash.log）、临时文件 `tmp + os.replace` 原子写、dry-run 预览，治理逻辑干净。
8. **`log_setup.py`**：`_logging_configured` 幂等保护，文件 handler 失败降级控制台不阻断；唯一弱点是幂等标志非原子（极端并发首次调用可能双 handler），概率极低未列级。

---

## 历史遗留问题 (a)-(d) 结论

- **(a) V13 失败是否仍不回退**：**检测级已修复，绘制级仍未修复**。`detect_border_v13` 返回 None → 正常回退 Profile/旧路径（`lshape_border.py:538-541/586-609`）；但 V13 命中后 `_apply_v13_path` 内部失败（`lshape_border.py:698/732-734/740-742/760-762`）仍**直接 return False 不回退**（`546/594` 行），叠加 `image_ops.py:1143` 关闭统一黑框兜底 + `1209` 丢弃返回值 → 补全失败静默且无视觉兜底。
- **(b) 内存峰值问题是否仍存在**：**仍存在**。全图 uint8→float64 整图转换活代码 4 处：`image_cropper_border.py:344`、`image_cropper_mask.py:729`、`lshape_border.py:66`、`lshape_border.py:123`（另有死代码 `image_cropper_mask.py:510/552`）；LOD `deepcopy(design)` 翻倍 `image_ops.py:504-506`；`detection.py:853` 全图 uint8 复制在 P1-4 死路径上。每处 2 亿像素 ≈ 4.8GB，与 README 声称相悖。唯一已修复点：`detection.py:196-205` 降采样。
- **(c) 边框检测 2px 问题现状**：**阈值逻辑未动**。`config.py:102` 步长 2 / 最小层厚 2 仍生效；`detection.py:732` 步长 2 扫描、差分阈值 = `BORDER_LUMINANCE_DIFF_THRESHOLD × 3 = 75`；L 形补全链路已被 V13/Profile 绕过（缓解），圆角重绘主链仍受 2px 结构约束。
- **(d) template_matcher 线程锁是否已加**：**未加**。`core/parser/` grep `threading|Lock|RLock` 零命中。

---

## 建议修复优先级排序

1. P1-1：`lshape_border.py:546/594` 改为 patch 失败继续回退（最小改动：把 `return _apply_v13_path(...)` 拆为 判断 + 条件向下）。
2. P1-2/P1-3：为 `image_cropper_border.py:344`、`image_cropper_mask.py:729`、`lshape_border.py:66/123` 引入降采样（复用 `detection.py:196-205` 方案）；LOD 分支避免深拷贝整图。
3. P1-4：`corner_protect` 恒 True 时跳过 `detect_nested_rect_layers` 并删除 B 段死代码。
4. P1-5：template_matcher 的 dict 读写加 `threading.RLock`，评分写入改局部副本。
5. P2 组按"常量收敛到 config + 删死代码 + 修注释"批量清理。