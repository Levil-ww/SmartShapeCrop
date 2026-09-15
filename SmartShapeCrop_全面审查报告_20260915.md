# SmartShapeCrop 全面代码审查报告

**审查日期**: 2026-09-15  
**审查范围**: 全项目（core/, gui/, services/, workers/, models/, tests/, scripts/, packaging/）  
**审查方式**: 只读审查，未修改任何源代码  
**基线**: 对比 2026-09-11 两份审查报告（core_findings.md、gui_pool_findings.md），逐条复核修复状态并扫描新问题

---

## 一、总体评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 功能正确性 | ★★★★☆ | 核心裁剪/边框/L形逻辑健壮，三级回退链已修复 |
| 线程安全 | ★★★☆☆ | 关键并发问题已修复，但 OCR 不可中断仍未根治 |
| 内存效率 | ★★★★☆ | float64 整图转换已修复，deepcopy 已消除 |
| 测试覆盖 | ★★★☆☆ | 34个测试文件覆盖核心路径，但关键模块仍有缺口 |
| 工程规范 | ★★★☆☆ | _archive 脚本堆积，依赖未锁定精确版本 |
| 安全性 | ★★★★☆ | 未发现注入/遍历等高危风险，路径校验可加强 |

**整体结论**: 相比 2026-09-11 审查，项目质量显著提升。15条核心问题中 10条已完全修复、4条部分修复、1条仍存在；12条 GUI/Worker 问题中 8条已修复、3条部分修复或仍存在。剩余风险集中在 OCR 超时不可中断、warmup 多订阅竞态、测试覆盖缺口三个方面。

---

## 二、历史问题复核

### 2.1 核心模块（core/）历史问题

| 编号 | 问题 | 严重度 | 当前状态 | 证据 |
|------|------|--------|----------|------|
| P1-1 | V13 patch 失败不回退 | P1 | **已修复** | `lshape_border.py:666-683` V13 失败后继续回退 Profile/旧路径；`image_ops.py:1312-1316` 补全失败补画黑框兜底 |
| P1-2 | 全图 float64 转换 4 处 | P1 | **已修复** | `image_cropper_border.py:344-353`、`lshape_border.py:66-77/134-145` 均改为降采样；`image_cropper_mask.py:729` 改为 uint8；死代码中残留 1 处（见 P2-2） |
| P1-3 | LOD deepcopy 内存翻倍 | P1 | **已修复** | `image_ops.py:508` 改为 `design.clone()` |
| P1-4 | nested_rects 恒被丢弃 | P1 | **已修复** | `image_cropper_border.py:319-324` 调用已移除；`image_cropper_mask.py:353-356` B 段已删除 |
| P1-5 | template_matcher 无锁 | P1 | **已修复** | `services/parser/template_matcher.py:386` `self._lock = threading.RLock()`；6 个公开方法均受保护 |
| P1-6 | 圆角检测 2px 阈值 | P1 | **仍存在** | `config.py:102` `BORDER_SCAN_STEP_PX=2`、`config.py:114` `BORDER_MIN_LAYER_THICKNESS_PX=2` 数值未变 |
| P2-1 | GAP_* 常量未集中 | P2 | **已修复** | 已迁移至 `config.py:134-144` |
| P2-2 | 死代码 _estimate_outer_background | P2 | **部分修复** | `_analyze_corner_sector_content`、`_corner_sector_has_content` 已删除；`_estimate_outer_background` 仍存在（`image_cropper_mask.py:360-368`），内部仍有整图 float64 |
| P2-3 | B 段不可达 | P2 | **已修复** | 嵌套矩形恢复段已删除 |
| P2-4 | 误丢最内层 | P2 | **已修复** | `detection.py:138-145` 改为先判断再 pop |
| P2-5 | docstring 漂移 | P2 | **已修复** | `lshape_border.py:566-584` 已更新 |
| P2-6 | scale 换算偏差 | P2 | **已修复** | `lshape_border.py:743-750` 改为几何平均 |
| P2-7 | 模块级状态无锁 | P2 | **已修复** | `lshape_border_route.py:36-38` 声明无可变惰性状态 |
| P2-8 | cm↔px 换算分散 | P2 | **部分修复** | `config.py:157-164` 已定义 `cm_to_px`/`px_to_cm`；`detection.py:80` 仍有内联换算 |
| P2-9 | QSettings/JSON 不一致 | P2 | **部分修复** | 写入侧已统一 `json.dumps`（`app_settings.py:351-354`）；读取侧类型差异仍存 |

### 2.2 GUI/Worker 层历史问题

| 编号 | 问题 | 严重度 | 当前状态 | 证据 |
|------|------|--------|----------|------|
| P0-1 | OCR 多尺度循环无整体超时 | P0 | **部分修复** | `vision.py:466-468` 已加 `check_cancel`；`numbers.py` 多处检查（631/642/668/764/780/897）；但 `_multi_scale_ocr_scan` 跨尺度循环间无检查；L 形后段 `_assign_labels_by_geometry`/`_resolve_dimensions` 无 deadline |
| P0-2 | closeEvent 未接管后台线程 | P0 | **已修复** | `main.py:446-456` 已调用 `panel.shutdown()`、`lshape_panel.shutdown()` |
| P1-1 | warmup quit()+terminate() 强杀 | P1 | **已修复** | `property_panel_poolbox.py:309-322` 改为 requestInterruption + finished→deleteLater |
| P1-2 | warmup 期间多 Worker 并行 | P1 | **部分修复** | `generate.py:85-88` 预热后断开回调；`_pool_start_generate_worker` 有 isRunning 守卫；但 warmup 多次订阅问题仍存 |
| P1-3 | LShape cancel 退役不完整 | P1 | **已修复** | `lshape_panel.py:912-927` requestInterruption + finished→deleteLater；`929-941` shutdown 退役 |
| P1-4 | OCR 不可中断 | P1 | **仍存在** | Worker.run() 只在结束后检查 isInterruptionRequested；解析器主体依赖 monotonic deadline，不检查线程中断 |
| P2-1 | _PARSE_TIMEOUT_SEC 死代码 | P2 | **已修复** | `sketch_parser.py:45-48` 已删除重复定义 |
| P2-2 | deadline 注释夸大 | P2 | **已修复** | `sketch_parser.py:87-89` 语义已修正 |
| P2-3 | LShape 提示文案失真 | P2 | **已修复** | `lshape_panel.py:581` 改为"通常约 10 秒~2 分钟，最坏可达十余分钟" |
| P2-4 | except Exception 吞异常 | P2 | **部分修复** | 多数 OCR 异常已加 `logger.debug(exc_info=True)`；`vision.py:461-463`、`numbers.py:635-637` 的 PIL.fromarray 失败仍静默 |
| P2-5 | 防抖注释残留 | P2 | **已修复** | `property_panel_layers.py:191-195` 已标注删除 |
| P2-6 | target_w/h_cm 未参与 OCR | P2 | **仍存在** | `_multi_scale_ocr_scan` 签名不含此参数 |

---

## 三、当前仍存在的问题

### P0 高危（1 条）

#### P0-1: OCR 多尺度循环仍有局部无超时路径

**严重度**: P0（降级为 P1 亦可，触发面窄）

**现状**: 相比 2026-09-11，已大幅改进——`check_cancel` 回调已注入 `_multi_scale_ocr_scan` 和 `_extract_direction_label_numbers`，numbers 模块多处检查点。但以下路径仍无保护：

1. **`sketch_parser_vision.py:518-550`**: 外层 `for scale in scales:` 跨尺度循环间无 `check_cancel` 调用，仅在内层 `_run_one` 入口检查
2. **`lshape_sketch_parser.py:1261-1263`**: `ocr_numbers` 完成后调用 `_assign_labels_by_geometry()` 和 `_resolve_dimensions()`，这两个几何解析函数无 `check_cancel` 参数
3. **Worker 层**: `_SketchParseWorker.run()`（`property_panel_workers.py:934-941`）和 `_LShapeParseWorker.run()`（`:971-979`）只在解析返回后检查 `isInterruptionRequested()`，解析器主体不感知线程中断

**影响**: 极端情况下用户点击"取消"后，OCR 仍需跑完当前尺度档才能退出；几何解析阶段完全不可中断。但相比之前"最坏 29 分钟不可中断"，当前已有显著缓解。

**建议**: 在 `for scale in scales:` 循环体首行加 `check_cancel()`；为 `_assign_labels_by_geometry`/`_resolve_dimensions` 传入 `check_cancel`；或在 Worker 中将 `isInterruptionRequested` 包装为 `check_cancel` 闭包传入解析器。

---

### P1 中危（4 条）

#### P1-1: warmup 多订阅竞态仍存

- **位置**: `gui/property_panel_generate.py:78-90`
- **现状**: warmup 运行期间多次点击"生成预览"，每次都会 `connect` 到 `warmup.finished_ok`；虽然 `:85-88` 预热后断开当前回调、`_pool_start_generate_worker` 有 `isRunning` 守卫，但事件队列仍可能残留旧请求
- **影响**: warmup 结束后可能触发多次 generate 回调，首次保护有效但后续仍有竞态窗口
- **建议**: 在连接前先 `disconnect` 旧回调，或使用 `Qt.UniqueConnection`

#### P1-2: 圆角检测 2px 步长/最小层厚仍未调整

- **位置**: `config.py:102` `BORDER_SCAN_STEP_PX=2`、`config.py:114` `BORDER_MIN_LAYER_THICKNESS_PX=2`
- **现状**: 阈值已集中管理（正面），但数值未变。L 形补全已被 V13/Profile 绕过，圆角重绘主链仍受 2px 约束
- **影响**: 对 < 2px 的细密边框层检测能力不足
- **建议**: 可将步长降至 1，最小层厚降至 1，评估性能影响后决定

#### P1-3: OCR Worker 不可中断

- **位置**: `workers/property_panel_workers.py:934-941`、`:971-979`
- **现状**: `isInterruptionRequested()` 只在解析返回后用于丢弃旧结果，解析器主体依赖 `monotonic()` deadline，不检查线程中断标志
- **影响**: 用户点击"取消"后无法立即中断 OCR，须等 deadline 到期或全部完成
- **建议**: 将 `isInterruptionRequested` 包装为 `check_cancel` 闭包注入解析器

#### P1-4: _estimate_outer_background 死代码 + 残留 float64

- **位置**: `core/image_cropper_mask.py:360-368`
- **现状**: 该函数仅被 `image_cropper_border.py:60` import，无实际调用；内部第 368 行仍 `np.array(img, dtype=np.float64)` 整图转换
- **影响**: 死代码上的内存陷阱，删除可同时消除
- **建议**: 确认无调用后删除该函数

---

### P2 低危（7 条）

#### P2-1: PIL.fromarray 失败静默吞异常

- **位置**: `services/sketch_parser/sketch_parser_vision.py:461-463`、`sketch_parser_numbers.py:635-637`
- **现状**: `PILImage.fromarray(img)` 失败时直接 `return out`，无 `logger.debug` 或 `exc_info=True`
- **影响**: Tesseract 路径配置错误时，整轮 OCR 静默失败，排障困难
- **建议**: 加 `logger.debug("PIL fromarray failed", exc_info=True)`

#### P2-2: target_w/h_cm 未参与 OCR 缩放

- **位置**: `services/sketch_parser/sketch_parser_vision.py:445`
- **现状**: `_multi_scale_ocr_scan` 签名已不含 `target_w_cm`/`target_h_cm`，缩放档固定 1.0/2.5/4.0
- **影响**: 无实际功能影响，但文档/注释中可能仍提及"按目标尺寸缩放"
- **建议**: 清理相关注释或实现按目标尺寸的自适应缩放

#### P2-3: 路径安全校验缺失

- **位置**: `core/image_ops.py:1261-1263`
- **现状**: `os.path.isfile` 后直接 `load_image_rgb`，未做路径安全过滤
- **影响**: 虽然当前路径来自内部状态而非直接用户输入，但若未来路径来源扩展则有风险
- **建议**: 增加路径白名单或 `os.path.realpath` + 前缀检查

#### P2-4: 补全异常仅 debug 级日志

- **位置**: `core/image_ops.py:1304-1305`
- **现状**: border completion 异常仅 `logger.debug`
- **影响**: 生产环境中补全失败可能被忽略
- **建议**: 降级为 `logger.warning`

#### P2-5: _post_cleanup_gap_regions 重复清理逻辑

- **位置**: `core/image_cropper_mask.py:696-699`、`:725-729`
- **现状**: 两处 `arr[...] = bg_arr.reshape(1, 3).astype(np.uint8)` 作用相近
- **建议**: 合并或提取为通用清理函数

#### P2-6: detection.py 内联 cm 换算

- **位置**: `core/corner/detection.py:80`
- **现状**: `px_per_cm = dpi / CM_PER_INCH` 内联换算，未使用 `config.cm_to_px`
- **影响**: 换算点分散，调整 DPI 语义时易漏改
- **建议**: 改为使用 `config.cm_to_px`

#### P2-7: 依赖版本未锁定

- **位置**: `requirements.txt`
- **现状**: 所有依赖使用 `>=` 约束，未锁定精确版本
- **影响**: 不同环境安装的版本可能不一致，引入兼容性风险
- **建议**: 生成 `requirements.lock` 或使用 `pyproject.toml` + 锁定文件

---

## 四、测试覆盖评估

### 4.1 已覆盖模块（34 个测试文件）

| 测试目录 | 覆盖模块 | 文件数 |
|----------|----------|--------|
| tests/core/ | geometry, image_cropper, lshape_border, lshape_border_route, lshape_sketch_parser, corner_detection, template_matcher, name_parser, rounded_corner | 11 |
| tests/gui/ | cropper_panel, lshape_panel, property_panel, main_window, signals_contract, gui_smoke | 7 |
| tests/integration/ | config, f1-f19 fixes, concurrency, pool_lshape_flow, final_verification | 9 |
| tests/sketch/ | multi_hole_parser, sketch_parser_logic, input_validation, characterization | 4 |
| tests/border/ | complex_pattern_safety, gap_fix, user_reported_cases | 3 |

### 4.2 测试缺口

| 模块 | 严重度 | 说明 |
|------|--------|------|
| `core/image_cropper_mask.py` | P1 | 核心裁剪掩码逻辑，仅间接覆盖 |
| `core/image_cropper_border.py` | P1 | 边框裁剪逻辑，仅间接覆盖 |
| `core/app_settings.py` | P1 | 应用设置持久化，无测试 |
| `workers/` | P1 | 所有 Worker 线程无测试 |
| `models/design_model.py` | P1 | 数据模型无测试 |
| `core/compat/` | P2 | 兼容层 |
| `core/psd/` | P2 | PSD 加载 |
| `core/log_setup.py` | P2 | 日志配置 |

### 4.3 测试基础设施

- `conftest.py` + `pytest.ini` 已配置忽略 `scripts/_archive` 等目录（正面）
- `.workbuddy/` 下存在 3 个诊断脚本，非正式测试（P2）
- 最近测试运行: 433 passed, 0 failed（2026-09-12 memory 记录）

---

## 五、工程结构评估

### 5.1 _archive 堆积

- `scripts/diagnose/_archive/` 约 73 个 .py 文件（verification_scripts/、debug_scripts/、ocr_scripts/）
- `scripts/_archive/` 另有历史脚本
- `scripts/verify/_archive/` 同类堆积
- **风险**: 维护成本高，可能误导开发；`conftest.py` 已屏蔽收集，但代码本身仍占空间
- **建议**: 定期归档到 git history 或独立分支

### 5.2 打包配置

- `packaging/README.md` 明确唯一打包入口为 `packageV2.2.py`（正面）
- 但 `packaging/legacy/` 仍存 4 个历史版本（package.py/packageV2.0.py/packageV2.1.py/packageV2.1.2.py）
- 根目录存在 `.spec` 文件（`智能裁剪设计器V2.2.1.spec`、`智能裁剪设计器V2.2.spec`）
- **建议**: 清理 legacy 和根目录 .spec，统一到 `packaging/specs/`

### 5.3 无 pyproject.toml

- 项目使用 `requirements.txt` 管理 Python 依赖
- 缺少 `pyproject.toml`，无法利用现代 Python 打包生态（PEP 517/518）
- **建议**: 添加 `pyproject.toml`，迁移配置

---

## 六、安全性评估

### 6.1 路径遍历

- **风险**: 低。`services/sketch_parser/` 的 `image_path` 参数有 `os.path.isfile` 检查（`sketch_parser_vision.py:135-140`）
- **建议**: 增加 `os.path.realpath` + 白名单前缀检查

### 6.2 subprocess 调用

- **风险**: 低。`packaging/packageV2.2.py` 使用 `subprocess.run()` 调用 PyInstaller，参数为固定值，非用户输入
- **正面**: 服务层通过 `pytesseract` 间接调用 Tesseract，未直接使用 `subprocess` 或 `os.popen()`

### 6.3 日志信息

- **风险**: 极低。日志记录 `image_path` 和 `found_exe`（Tesseract 路径），属诊断信息，非敏感凭证
- **建议**: 生产环境可考虑脱敏路径中的用户名

---

## 七、正面确认

1. **三级回退链完整**: V13 检测失败→Profile→旧路径，patch 失败也继续回退，补全失败有黑框兜底（`lshape_border.py:666-683`、`image_ops.py:1312-1316`）
2. **内存优化到位**: 4 处活代码 float64 整图转换均已改为降采样；LOD 改用 `clone()` 避免 deepcopy 像素翻倍
3. **线程锁覆盖**: TemplateMatcher 6 个公开方法均受 `RLock` 保护
4. **closeEvent 接管完整**: `main.py:446-456` 已接管 panel/lshape_panel/cropper/canvas 四处 shutdown
5. **warmup 退役规范**: 改为 requestInterruption + finished→deleteLater，不再 terminate 强杀
6. **LShape 取消规范**: `lshape_panel.py:912-927` requestInterruption + deleteLater 兜底
7. **提示文案修正**: LShape 面板提示已改为"通常约 10 秒~2 分钟，最坏可达十余分钟"
8. **常量集中管理**: GAP_*、cm_to_px/px_to_cm 已迁入 config.py
9. **scale 换算改进**: 几何平均替代算术平均，减少非等比缩放偏差
10. **死代码清理**: B 段不可达代码、_PARSE_TIMEOUT_SEC 重复定义、防抖注释残留均已清除

---

## 八、修复优先级建议

| 优先级 | 编号 | 问题 | 工作量 |
|--------|------|------|--------|
| 1 | P0-1 | OCR 循环 check_cancel 补全 + Worker 中断注入 | 中 |
| 2 | P1-3 | Worker isInterruptionRequested 注入解析器 | 小 |
| 3 | P1-1 | warmup 多订阅：connect 前 disconnect | 小 |
| 4 | P1-4 | 删除 _estimate_outer_background 死代码 | 小 |
| 5 | P1-2 | 评估 2px→1px 步长调整 | 中 |
| 6 | P2-1 | PIL.fromarray 加 logger.debug | 小 |
| 7 | P2-4 | 补全异常改 warning | 小 |
| 8 | P2-7 | 锁定依赖版本 | 小 |
| 9 | P2-6 | detection.py 内联换算改用 config.cm_to_px | 小 |
| 10 | P2-3 | 路径安全校验 | 小 |

---

## 九、与上次审查的改进对比

| 指标 | 2026-09-11 | 2026-09-15 | 变化 |
|------|-----------|-----------|------|
| P0 高危 | 2 条 | 1 条 | -50% |
| P1 中危 | 10 条（core 6 + gui 4） | 4 条 | -60% |
| P2 低危 | 15 条（core 9 + gui 6） | 7 条 | -53% |
| 已修复 | — | 18/27 条原始问题 | 67% |
| 测试通过 | 433 passed | 433 passed（无回归） | 持平 |

**核心改进领域**:
- 线程安全：TemplateMatcher 加锁、closeEvent 接管、warmup/LShape 退役规范化
- 内存效率：float64 降采样、deepcopy 消除
- 回退链健壮性：V13 patch 失败继续回退 + 黑框兜底
- 死代码清理：B 段、重复常量定义、防抖注释

**剩余重点**:
- OCR 可中断性（P0-1 + P1-3 联动）
- warmup 多订阅竞态（P1-1）
- 测试覆盖缺口（image_cropper_mask/border、workers/、models/）

---

*审查结束。只读审查，未修改任何源代码。*
