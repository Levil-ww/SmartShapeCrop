# 2026年09月03日 GUI 参数修改防抖渲染机制（debounce 200ms / 800ms max）

## 概述
9.3 傍晚集中修复用户投诉的 GUI 卡顿问题：用户在水池设计器和 L 形面板中**连续调整 SpinBox 数值**（如外框宽 100→101→102→103 鼠标滚轮滚动 4 次）时，UI 出现严重卡顿——每一次 valueChanged 都触发**全量同步渲染**（单帧 100–500ms），主线程被阻塞，SpinBox 数值显示"打字机式"逐字跳、滚轮滑完 2 秒后屏幕才追上。本日用 QTimer debounce 机制实现了参数修改的合并渲染，同时保证关键操作路径（生成预览按钮、L 形识别完成）仍立即渲染。核心修改在 `gui/property_panel_layers.py`（防抖基础设施）和 `gui/property_panel.py`（SpinBox 信号改间接调度）。

---

## 问题一：SpinBox 连续调节卡顿（每改必渲 → 主线程阻塞）

### 现象（用户描述）
1. 在水池设计器面板中用鼠标滚轮**滚动修改外框宽**（DoubleSpinBox，步长 0.5cm，从 100→105 滚 10 步），每次步进都卡 0.3-0.5s，总耗时 5+ 秒。
2. 连续修改圆角半径（0→3cm，点"↑"6 次）期间，预览 canvas 没有响应、关闭按钮点不动、状态栏不刷新。
3. L 形面板修改挖角宽高 4-5 次时，识别按钮变灰（渲染锁）迟迟不解除。

### 根因定位
`gui/property_panel.py` 中所有 SpinBox 的 `valueChanged` 信号**直接连到立即渲染槽函数**：
```python
# 旧版（无防抖）：
self.spin_outer_w.valueChanged.connect(self._apply_and_render_immediate)
self.spin_outer_h.valueChanged.connect(self._apply_and_render_immediate)
self.spin_corner_r.valueChanged.connect(self._apply_and_render_immediate)
# ... 20+ 个 SpinBox 全部直接连立即渲染
```
而 `_apply_and_render_immediate` 的执行剖面（大素材+LOD 0.5 下）：
```
_apply_params_to_design: 12ms
  └─ 参数对象赋值
render_design (同步):   380ms
  ├─ 素材图 adapt_pool_material: 110ms
  ├─ build_lshape_mask / 几何:   40ms
  ├─ 边框带检测:                  90ms
  └─ 像素赋值 + LOD:            140ms
canvas.set_image (UI):   35ms
──────────────────────────
Total:                   427ms / 每次 valueChanged
```
→ 滚轮 10 步连续触发：10×427ms = **4.27s 主线程 100% 占满**，UI 事件循环被饿死，SpinBox 数值回显延迟、按钮无响应。

### 修复：_LayersMixin 新增 debounce timer（200ms delay / 800ms maxWait）

#### ① 基础设施：在 `_LayersMixin` 中新增通用防抖调度器
```python
# gui/property_panel_layers.py 新增：
class _LayersMixin:
    DEBOUNCE_DELAY_MS = 200      # 延迟启动：参数静止 200ms 后才触发渲染
    DEBOUNCE_MAX_WAIT_MS = 800   # 最大等待：就算一直调参数，最多 800ms 也必须渲一次

    def _init_debounce(self):
        self._render_debounce_timer = QTimer(self)
        self._render_debounce_timer.setSingleShot(True)
        self._render_debounce_timer.timeout.connect(self._do_debounced_render)

        # maxWait 用第二个计时器：首次触发开始计时 800ms
        self._render_deadline_timer = QTimer(self)
        self._render_deadline_timer.setSingleShot(True)
        self._render_deadline_timer.timeout.connect(self._do_debounced_render)

        self._render_scheduled = False

    def _schedule_apply_quiet(self):
        """SpinBox valueChanged → 只调度不立即渲染。幂等。"""
        # 先把参数写进 design 对象（立即，不阻塞）
        self._apply_params_to_design()  # 只写值，10ms 内搞定

        if not self._render_scheduled:
            # 首次调度：启动 deadline（绝对 800ms 封顶）
            self._render_deadline_timer.start(self.DEBOUNCE_MAX_WAIT_MS)
            self._render_scheduled = True

        # 每次参数变化都 delay timer（经典 debounce：等参数"静止" 200ms）
        self._render_debounce_timer.start(self.DEBOUNCE_DELAY_MS)

    def _do_debounced_render(self):
        """真正执行渲染的统一出口。两个计时器到点都走这里。"""
        self._render_debounce_timer.stop()
        self._render_deadline_timer.stop()
        self._render_scheduled = False
        self._apply_and_render_immediate()   # 复用原来的立即渲染实现
```

#### ② SpinBox 信号调度者改为 `_schedule_apply_quiet`
```diff
# gui/property_panel.py 所有 SpinBox 连接统一替换：
- self.spin_outer_w.valueChanged.connect(self._apply_and_render_immediate)
+ self.spin_outer_w.valueChanged.connect(self._schedule_apply_quiet)
  self.spin_outer_h.valueChanged.connect(self._schedule_apply_quiet)
  self.spin_corner_r.valueChanged.connect(self._schedule_apply_quiet)
  self.spin_cut_w.valueChanged.connect(self._schedule_apply_quiet)
  self.spin_cut_h.valueChanged.connect(self._schedule_apply_quiet)
  # ... 共 19 处替换（水池面板 12 + L 形面板 5 + 圆角裁剪 2）
```

#### ③ 关键操作路径保立即渲染（防抖只对 SpinBox，不劫持用户主动触发动作）
```python
# 以下路径保持直接连 _apply_and_render_immediate：
def _on_generate_preview_clicked(self):      # [生成预览] 按钮
    self._apply_params_to_design()
    self._apply_and_render_immediate()

def _on_lshape_parse_finished(self, result): # L 形识别完成
    self._apply_parse_result_to_design(result)
    self._apply_and_render_immediate()       # 识别完成立即渲，不合并

def _on_material_selected(self, idx):        # 下拉框选素材（操作频率低）
    self.design.material_name = self.combo_material.currentText()
    self._apply_and_render_immediate()

def _on_mode_switched(self, idx):            # Tab / 模式切换（切换成本身就重）
    self._apply_and_render_immediate()
```

---

## 验证结果（滚轮 10 步 100→105cm 连续滚动场景）

| 指标 | 旧版（无防抖） | 新版（debounce 200/800） | 提升率 |
|---|---|---|---|
| 渲染次数 | 10 次（每步一次） | **1–2 次**（滚得慢合并成 1 次；滚得快 maxWait 保底渲 2 次） | **5–10× 渲染减少** |
| 主线程累计阻塞时长 | 4.27s（10×427ms） | **0.43–0.85s**（1-2×渲染，且 maxWait 800ms 从首次调度计时，不累计） | **5–10× 卡顿时间下降** |
| SpinBox 数值回显流畅度 | "打字机"式，滚轮滚完 2s 后数值还在逐字跳 | **跟手**，valueChanged 立即写 QDoubleSpinBox，参数对象同步更新，显示零延迟 | 主观评分 2 → 5 |
| 生成预览按钮响应（非 SpinBox 路径） | ~400ms（立即渲） | **~400ms 不变**（关键路径走立即，不进 debounce） | 无退化 ✅ |
| L 形识别完成回填后渲染 | ~500ms（立即） | **~500ms 不变**（识别 finished 信号→立即） | 无退化 ✅ |

---

## 核心修改文件对照

| 文件 | 改动点 | 行数变化 |
|---|---|---|
| `gui/property_panel_layers.py` | `_LayersMixin` 新增 `_init_debounce()`、`_schedule_apply_quiet()`、`_do_debounced_render()`；常量 `DEBOUNCE_DELAY_MS=200` / `DEBOUNCE_MAX_WAIT_MS=800`；初始化时在 mixin `__init__` 调用 `_init_debounce()` | +89 / -0 |
| `gui/property_panel.py` | 12 个 SpinBox（水池单洞）valueChanged 从 `_apply_and_render_immediate` → `_schedule_apply_quiet`；`_on_generate_preview_clicked` 等 4 个关键入口仍保持立即渲染 | +6 / -12 |
| `gui/lshape_panel.py` | 5 个 SpinBox（outer_w/h、cut_w/h、corner_r）valueChanged → `_schedule_apply_quiet`；`_on_lshape_parse_finished` → 立即 | +3 / -5 |
| `gui/cropper_panel.py` | 圆角裁剪工具 2 个 SpinBox（宽高/圆角）valueChanged → `_schedule_apply_quiet`；自动匹配完成 → 立即 | +2 / -2 |

---

## 9.3 T3 关键结论一句话
> **经典 debounce（200ms delay + 800ms max）通过 mixin 一劳永逸覆盖 19 个 SpinBox；滚轮 10 步连续调节的渲染次数从 10 次压缩到 1-2 次，卡顿时间 5–10× 下降，数值显示跟手零延迟，关键操作路径零退化。**
