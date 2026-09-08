# 2026-09-07 T4：蔓生花/素锦深色弧线 + 南瓜无忧白隙修复

## 概述

用户反馈两个问题：(1) 蔓生花/素锦圆角边框外侧多了一层深色弧形缺口线；(2) 南瓜无忧白色空隙未完全消除。

详见已有报告：`ProductSummary/圆角裁剪工具/20260907-四角边框弧线白色空隙修复验证报告.md`

***

## 问题一：深色弧形缺口线（蔓生花/素锦）

### 根因

`_build_multi_layer_corner_mask` 中 `ring_region` 保护像素到 `dist = r + 1.5`（弧线外侧），这些像素保留了原始深色边框色；但 Step A（`_redraw_border_on_corner`）只绘制 `dist <= r`，外侧保护像素未被重绘 → 弧线外侧残留深色弧形线。

### 修复

**文件 1**：`core/image_cropper_mask.py` — ring_region 上界从 `r + 1.5` 收窄到 `r + 1.0`：

```python
ring_region = (angle >= ang_min) & (angle <= ang_max) & \
              (dist >= ring_inner_bound) & (dist <= float(r) + 1.0)
```

**文件 2**：`core/corner/sector_render.py` — 最外层（d=0）绘制扩展到抗锯齿边界：

```python
if d == 0:
    d_region = valid_region & (depth < d + 1) & (dist <= float(R_total) + 1.0)
else:
    d_region = valid_region & (depth >= d) & (depth < d + 1) & (dist <= float(R_total) + 1.5)
```

### 验证

- 弧线外侧（`dist > r + 1.0`）深色像素数：**0**
- `(r, r+1.0]` 容差带内仅 198 个深色像素（边框边缘抗锯齿，正常）
- 四角对称性：✅

***

## 问题二：白色空隙（南瓜无忧）

### 根因

`protect_content=True` 时，`inner_cut` 将 `border_zone` 内 `dist <= r` 的**所有**像素切白，包括超出边框厚度的内容像素。这些内容像素不在 Step A 的绘制深度范围内（`depth > total_border_depth`），无法被重绘 → 留下白色空隙。

南瓜无忧的深色纹理背景可能触发 `_corner_sector_has_content` 返回 `True`（纹理导致 `unique_colors >= 8` 或 `variance > 800`），从而进入保护模式。

### 修复（core/image_cropper_mask.py）

`inner_cut` 仅裁切边框环带，不裁切内容区：

```python
border_ring_inner = max(0.0, float(r) - float(raw_depth) - 2.0)
inner_cut = (dist <= r) & border_zone & (dist >= border_ring_inner)
```

仅裁切 `dist` 在 `[r - raw_depth - 2, r]` 的边框环带，内容区（`dist < r - raw_depth - 2`）保持直角不被切白。

### 验证

- 强制 `protect_content=True`，内容区（`dist <= r - raw_depth - 5`）白色像素数：**0**
- 边框弧（`dist` 在 `[r - raw_depth, r]`）深色像素占比：**100%**
- 四角对称性：✅
