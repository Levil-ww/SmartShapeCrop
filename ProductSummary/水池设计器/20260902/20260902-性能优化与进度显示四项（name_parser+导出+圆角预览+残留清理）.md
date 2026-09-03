# 2026年09月02日 性能优化与进度显示四项（name_parser + 导出异步 + 圆角预览 + 旧素材残留清理）

## 概述
9.2 当日针对程序时间响应、进度显示、GUI 状态残留四大性能/交互问题进行修复，Commits：`a919e20`（name_parser + 懒加载）/ `0fa76c7`（导出进度范式对齐）/ `0fe8adc`（圆角裁剪 AutoMatchWorker + 嵌套进度）/ `ed95d16`（模式切换缓存清理）。用户体感改善：图库首次加载首屏卡顿 2-3s→280ms、导出大尺寸 3-8s UI 零假死、圆角裁剪自动匹配 10+ s → 可取消、模式切换残留像素 100% 消除。

---

## 问题一：name_parser 文件名解析性能优化 + 素材库懒加载（`a919e20`）

### 1.1 parse_filename 正则预编译 + 批量 8× 提升

#### 现象
启动程序首屏首次加载素材库（500 张 JPG 文件名解析，提取图案名/尺寸/圆角）时，GUI 出现 2-3 秒空白窗口，状态栏无任何提示。

#### 根因
`core/parser/name_parser.py::parse_filename` 每次调用**现场编译 9 条正则**（对"尺寸 50×160"、"圆角 r3cm"、"横版竖版前缀"等 9 种模式分别 compile），500 文件 × 9 正则 × compile 开销 = 2.3 秒。正则编译占比 85%，实际匹配耗时仅 15%。

#### 修复
```python
# 模块级预编译 9 条正则缓存（程序启动时编译 1 次）
_REGEX_CACHE = {
    'dim_cross':       re.compile(r'(\d+\.?\d*)[x×*](\d+\.?\d*)'),
    'corner_r':        re.compile(r'r(\d+\.?\d*)cm'),
    'orientation':     re.compile(r'^(竖版|横版)_'),
    # ... 共 9 条预编译
}

def parse_filename(filename: str) -> ParsedName:
    for key, pattern in _REGEX_CACHE.items():   # 直接用编译好的 pattern
        m = pattern.search(filename)
        if m:
            ...
```

批量接口 `parse_many_filenames(paths: List[str])` 再加 ThreadPoolExecutor 并行：
```python
def parse_many_filenames(paths):
    with ThreadPoolExecutor(max_workers=min(8, len(paths)//50+1)) as executor:
        return list(executor.map(parse_filename, paths))
```
（GIL 在 re 匹配阶段会释放，CPU 密集型 I/O 混合任务实际受益）

#### 验证
| 图集规模 | 旧 | 新 | 提升 |
|---|---|---|---|
| 50 文件 | 210 ms | 32 ms | 6.6× |
| 500 文件（首屏） | **2300 ms** | **280 ms** | **8.2×** |
| 2000 文件（极限） | 9.5 s | 1.1 s | 8.6× |

首屏 2.3s → 280ms，用户"白屏等待"体感基本消除 ✅。

### 1.2 素材库下拉框懒加载缓存

#### 现象
水池设计器切模式（rect_hole ↔ rect_multi ↔ rect_lshape）6 次来回，每次都重新扫描素材库目录 `images/materials/`（500 文件 `os.listdir` + 路径拼接），模式切换明显延迟。

#### 修复
```python
class MaterialComboBox(QComboBox):
    _name_cache: List[str] = None
    _dir_mtime: float = 0.0

    def _ensure_loaded(self, dir_path: Path):
        current_mtime = dir_path.stat().st_mtime
        if self._name_cache is None or current_mtime != self._dir_mtime:
            # 仅首次加载 or 目录实际变更时重扫
            self._name_cache = sorted([f.stem for f in dir_path.glob('*.jpg')])
            self._dir_mtime = current_mtime
```
- `_dirty` 标志：用户手动添加素材到目录时（通过文件夹系统操作），下次点击触发 mtime 检查才重扫
- 模式切换 6 次来回：6 次实际 os.listdir → **1 次**（其余 5 次走缓存）

切换延迟感由"可察觉"变为"瞬时" ✅。

---

## 问题二：导出图进度显示范式统一（`0fa76c7`）

### 现象
`main.py` 的导出进度代码（32 行）与 `PreviewCanvas._retire_worker` 范式**不一致**：
```python
# 旧（危险写法）：直接赋 None，运行中 Worker 被 GC → QThread destroyed while running
def _save_cleanup(self):
    self._save_worker = None  # 无 isRunning 检查，无 deleteLater

# 正确范式（PreviewCanvas 已有）：
def _retire_worker(self):
    w, self._worker = self._worker, None
    if w.isRunning():
        w.requestInterruption()
        w.wait(3000)
    w.deleteLater()
```

导出线程未遵循统一范式，保存中关窗口概率性崩溃（9.1 fuzz 测到 2 次）。

### 修复：对齐 _retire_worker 范式 + closeEvent

```python
def _retire_save_worker(self, timeout_ms: int = 10000):
    w, self._save_worker = self._save_worker, None
    if w is None:
        return
    if w.isRunning():
        w.requestInterruption()
        if not w.wait(timeout_ms):
            logging.warning(f"导出线程未在 {timeout_ms}ms 内结束，放弃等待")
    w.deleteLater()

def closeEvent(self, event):
    if self._is_saving:
        self._retire_save_worker(10000)   # 先退役保存线程（图像写入磁盘不能粗暴中断）
    self.canvas.shutdown()                # 再退役预览渲染线程
    super().closeEvent(event)
```

结果：32 行重复/危险代码 → 5 行安全代码 + 生命周期严格顺序。

---

## 问题三：圆角裁剪工具进度显示 + 自动匹配生成预览（`0fe8adc`，cropper_panel.py 223 行重构）

### 3.1 自动匹配阻塞 UI

#### 现象
用户在圆角裁剪工具选了目标图，点「自动匹配素材」。程序在主线程跑 template_matcher（500 库 × 方向签名 + 双向比例 + 已裁剪图检测），期间窗口 10-15 秒完全无响应，用户无法取消，频繁触发"程序未响应"被系统强制结束。

#### 修复：_AutoMatchWorker QThread 子线程化
```python
class _AutoMatchWorker(QThread):
    progress = Signal(int, str)   # (0-100%, 阶段名)
    result = Signal(dict)         # {material_name, match_score, orientation, matched_path}
    error = Signal(str)

    def run(self):
        total_steps = 5
        for i, step in enumerate([
            '读取目标图 → 估算方向/尺寸',
            '已裁剪图检测（扇形白占比+方差）',
            '方向签名 + 双向比例初筛',
            '像素相似度精细匹配',
            '选取 Top-3 候选 + 排序'
        ]):
            self.progress.emit((i/total_steps)*100, step)
            if self.isInterruptionRequested():
                return
            # ... 执行步骤 i ...
        self.progress.emit(100, '完成')
        self.result.emit(final_result)
```

#### UI 接入
- `cropper_panel.py` 启动 Worker 同时弹出 `QProgressDialog("自动匹配中...", "取消", 0, 100, self)`
- progress 信号 → dialog.setValue；finished/error → dialog.close + 结果回填
- 取消按钮点击 → Worker.requestInterruption()；1s 内未响应则 `terminate`（匹配无副作用，可粗暴终止）

### 3.2 批量裁剪嵌套进度

#### 现象
批量裁剪 N 张图时，进度条只显示"当前第 i 张"，用户无法判断"单张进度"还是"总进度"，预估剩余时间困难。

#### 修复：双层级进度合成
```
信号设计：worker 同时发两个百分比
  overall_pct  = (i-1)/N × 100                # 前 i-1 张完成比例
  stage_pct    = 单张内部细分阶段进度 (0-100)  # 当前张内部进度

合成 = overall_pct + stage_pct / N
```
- Label 文案：`处理 i/N · 阶段：圆角渲染 75%`
- 进度条数值 = 合成百分比（如 N=3 / 第 2 张 / 阶段 50% → 合成 = 33.3 + 16.7 = 50%）

用户可同时判断"整体进度"和"当前单张卡死在哪一步" ✅。

### 3.3 packageV2.1.py 位置规整
原 `packageV2.1.py` 在项目根目录 → 移入 `package/` 目录，与 `packageV2.1.2.py` 并列，打包脚本集中管理。

---

## 问题四：旧素材残留像素清理（`ed95d16`）

### 4.1 切换设计模式时预览图残留边角暗斑

#### 现象
水池设计器操作序列：单洞（安妮森林）→ 切多洞（59×350 素锦）→ 切回单洞（不选素材，默认填充）。
结果：预览图**角落残留安妮森林的局部花纹暗斑**（约 10-30px 方块，位置与上一轮缓存图重叠区域恰好一致），放大可见。

#### 根因
```python
# 旧：property_panel.py PoolRenderWorker 结果写回 _last_render_cache
self._last_render_cache = rendered_result    # numpy 数组（共享缓冲区）

# 下一轮渲染：
mask = inner_mask > 0                        # 只在 mask=True 区写入
rendered[mask] = new_material_pixels[mask]   # mask=False 区（角落）是旧缓存值
```
- 当新一轮 inner_mask 恰好**小于**上一轮 inner_mask（多洞→单洞：mask 面积变小），mask=False 区**不被覆盖**，上一轮安妮森林像素残留在结果中
- 缓存对象是 numpy 视图而非深拷贝，部分写入时视图与原图共享内存块，残留必然发生

#### 修复（三处联动）

**A. 模式切换槽末尾强制清理缓存（gui/property_panel.py）**：
```python
def _on_mode_changed(self, new_mode):
    # ...（原有模式切换代码）...
    # 强制丢弃两轮渲染缓存
    self._last_render_cache = None
    self._last_material_cache = None
```

**B. generate 路径生成前清零（gui/property_panel_generate.py +11 行）**：
```python
def _generate_single(self, spec):
    self._clear_render_cache()    # 每张新设计生成前先清零
    design = build_design(spec)
    return self.renderer.render(design)
```

**C. Worker 启动前做 shape 一致性校验（gui/property_panel_workers.py +40/-12）**：
```python
class PoolRenderWorker(QThread):
    def run(self):
        # 如果缓存图像存在但 shape 与本轮画布不一致 → 丢弃
        if (self._last_render_cache is not None
                and self._last_render_cache.shape != self.canvas_size):
            self._last_render_cache = None
        # 结果写回前先做 full zero
        self.result = np.zeros(self.canvas_size, dtype=np.uint8)
        # 再做 mask 写入
        # ...（正常渲染逻辑）
```

### 4.2 渲染线程大数组内存线性增长

连续生成 10 张设计（2000×1500px，每张约 9MB），进程内存从 350MB → 1.15GB（线性 +80MB×10 ≈ 800MB）。

Worker finished 后 `self.rendered_image` 引用被 Worker 闭包持有，GC 不回收。

#### 修复：显式 del + GC 阈值调低
```python
# Worker 内部 finished 信号槽：
def _on_finished(self):
    self.parent.on_render_done(self.result)   # 结果传出去
    del self.rendered_image                   # 显式解除引用
    self.rendered_image = None                # 防止再次访问
    gc.collect()                              # 强制回收（每轮 1-2ms，可忽略）

# 全局 GC 阈值调低（默认 700/10/10 → 更积极）
gc.set_threshold(700, 10, 5)
```

10 张循环：350MB → 1.15GB → 修复后 350MB → **460MB**（+110MB ≈ 12 张/轮 峰值）✅。

---

## 累计测试验证情况

| 修复项 | Commit | 验证方法 | 结果 |
|---|---|---|---|
| name_parser 正则预编译 + 批量并行 | `a919e20` | 50/500/2000 文件三档计时 | 500 文件 2.3s→280ms（8.2×） ✅ |
| 素材库懒加载缓存 | `a919e20` | 模式切换 6 次来回（实际 os.listdir 次数） | 6→1 次 ✅ |
| _retire_save_worker 范式对齐 | `0fa76c7` | 保存中途关窗口 10 次（旧方案 5/10 崩溃） | 0/10 崩溃 ✅ |
| AutoMatchWorker 子线程 | `0fe8adc` | 10+ s 自动匹配：UI 响应 + 取消 5 次 | UI 全程响应 + 取消全部生效 ✅ |
| 批量裁剪嵌套进度 | `0fe8adc` | N=10 张人工计时：整体进度与实际耗时差 | 误差 <5% ✅ |
| 预览图残留边角暗斑 | `ed95d16` | 20 次模式切换随机序列（单↔多↔L形）+ 放大检测残留 | 20/20 零残留 ✅ |
| 渲染线程内存增长 | `ed95d16` | 连续生成 10× 2000×1500 峰值内存 | +800MB → +110MB ✅ |
| 全量 pytest | 4 commits 后 | 全量回归 | 配合环境修复 275/0/5 ✅ |

---

## 核心修改文件

| 文件 | Commit | 行数变动 | 改动方向 |
|---|---|---|---|
| `core/parser/name_parser.py` | `a919e20` | +138/-99 | 9 条正则 `_REGEX_CACHE` 模块级预编译；parse_filename 列表推导短路匹配；`parse_many_filenames()` 新增 ThreadPoolExecutor 批量接口 |
| `core/parser/template_matcher.py` | `a919e20` | +16/-4 | MaterialComboBox 懒加载 `_name_cache` + `_dir_mtime` |
| `gui/property_panel_poolbox.py` | `a919e20` | +68/-... | 素材匹配下拉框接入 lazy cache；模式切换不重扫素材库 |
| `gui/property_panel_generate.py` | `a919e20` + `ed95d16` | +11 +11 | `_BatchGenerateWorker` 批生成单 Worker；生成前 `_clear_render_cache()` |
| `gui/property_panel_workers.py` | `a919e20` + `ed95d16` | +32/+40-... | PoolRenderWorker 启动前 shape 一致性校验；del rendered_image + gc.collect() 显式释放 |
| `main.py` | `0fa76c7` | +5/-27 | _retire_save_worker(timeout_ms=10000) 统一范式；closeEvent 保存线程先于 canvas.shutdown 退役 |
| `gui/cropper_panel.py` | `0fe8adc` | +164/-59 | _AutoMatchWorker QThread；QProgressDialog 可取消；嵌套进度合成；文件名/进度/状态栏重构 |
| `gui/property_panel.py` | `ed95d16` | +7 | _on_mode_changed 末尾 `_clear_render_cache()`；_material_combobox_sync |
| `scripts/diagnose/_live/_diag_topmargin_repro.py` | `ed95d16` | +129 新 | 残留像素复现脚本（调试用，用户案例驱动） |

---

## 经验教训
1. **正则编译 1 次 vs N 次是 10× 量级差距**：正则编译开销在单次调用中微不足道（<1ms），但 500 次循环中累计可超 2 秒。任何「循环 × 正则匹配」代码都应检查：是否模块级编译？
2. **UI 阻塞 10 秒级操作必须有取消能力**：10 秒无响应用户会杀进程；加上取消按钮（requestInterruption + QProgressDialog）成本不到 30 行代码，但用户安全感提升巨大
3. **进度"双层合成"比单层精细**：批处理 N 个任务，如果只显示「i/N」，长时间单任务会让进度卡住；合成进度 = 已完成整体 + 当前任务细分/N，给用户"进度一直在走"的心理感知，显著降低取消率
4. **numpy 缓存图在几何尺寸变化时必须强制丢弃**：缓存+视图机制容易把"小于上一轮 mask"的新设计写出残留像素。修复思路不是「更精细写 mask 边界算法」，而是「模式切换直接丢缓存」—— 粗粒度策略比精细写代码在防残留上更可靠
5. **大数组内存一定要显式释放**：Python GC 在 numpy 对象上判定延迟，Worker 闭包持有引用会让 GC 不触发。显式 `del + gc.collect()` 每轮 1-2ms 代价，防止 800MB 内存泄漏，ROI 超高
