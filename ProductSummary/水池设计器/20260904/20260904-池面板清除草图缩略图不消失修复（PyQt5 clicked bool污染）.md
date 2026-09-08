# 2026-09-04 T5：池面板"清除草图"缩略图不消失修复

## 概述

水池设计器面板点击「清除草图」，草图缩略图仍显示，状态栏出现「L 形面板草图已清除（水池设计器缩略图保持不变）」。

***

## 根因：PyQt5 clicked(bool) 参数污染

`QPushButton.clicked` 信号会发送 `checked: bool`。

`_pool_clear_sketch(self, source='pool')` / `_pool_pick_sketch(self, source='pool')` 都带 `source` 形参。直接 `clicked.connect(self._xxx)` 会把 bool `False` 当作 `source` 传入 → `if source == 'pool'` 恒假 → 落进 else（lshape 分支）→ **池面板缩略图清除/显示代码被跳过**。

**佐证**：生成大按钮（`property_panel_poolbox.py:146-147`）早已用 `lambda _checked=False: ...(source='pool')` 修过同一坑，注释写明"吞掉 clicked(bool checked) 参数污染"。而「上传草图」「清除草图」两个按钮漏改。

> 拖拽上传走 `fileDropped(path)` 信号不带 bool，故正常。

***

## 修复（gui/property_panel_poolbox.py）

### 改动 1：按钮连接吞掉 bool 参数（第 103-110 行）

```python
# 修复前
btn_sk1.clicked.connect(self._pool_pick_sketch)
btn_sk2.clicked.connect(self._pool_clear_sketch)

# 修复后
btn_sk1.clicked.connect(lambda _checked=False: self._pool_pick_sketch(source='pool'))
btn_sk2.clicked.connect(lambda _checked=False: self._pool_clear_sketch(source='pool'))
```

### 改动 2：面板间严格隔离（_pool_clear_sketch，第 873-899 行）

用户明确需求：清除草图按面板隔离——水池面板点清除只清水池缩略图，L 形面板点清除只清 L 形缩略图，两者互不影响。

删掉 `source='pool'` 分支里对 L 形面板的三连调用（`clear_lshape_params()` / `cancel_running_parse()` / `sync_sketch_preview("")`），使两分支对称隔离。

***

## 隔离边界（关键设计判断）

| 数据 | 隔离策略 |
|---|---|
| 缩略图 + L 形参数 | **完全隔离**（各面板各管各的） |
| 共享层 `_sketch_path` / `_sketch_parse_result` / 主画布 overlay | **仍共享**，两个分支都清空——因为生成/识别/主画布共用同一份草图，主画布物理上只有一块 |

这是与上传逻辑一致的最小隔离粒度。

***

## 验证

- py_compile 通过
- 全量测试 299 passed / 5 skipped，无回归

***

## 类坑提醒（复用）

凡 PyQt5 里 `clicked` / `toggled` 这类**带 bool 参数**的信号，直接连到**签名带普通形参**（非仅 self）的槽时，会污染该形参。

**规范做法**：
- 要么槽只收 `checked` / 显式签名
- 要么用 lambda 吞掉：`lambda _checked=False: self._xxx(...)`

本面板其余按钮（`_pool_pick_template_dir` 等）因槽只有 self（0 个额外形参），Qt 自动丢弃多余 bool，未触发此坑。

***

## 遗留提示

当前 `_sketch_path` 仍是单份共享变量。若用户需要"池面板与 L 形面板同时各持一张不同草图"的完全独立，需把 `_sketch_path` 拆成 `_pool_sketch_path` / `_lshape_sketch_path`（约 8-9 处引用），属更大重构，本次未做。
