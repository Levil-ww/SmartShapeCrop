# 2026-09-07 T2：圆角白线与内层误圆角修复

## 概述

两个问题：(1) 圆角接缝处出现竖向/横向白色细线（BR/TR 角 0°/360° 绕接处）；(2) 大半径（6.5cm）时内层花纹/文字带仍被圆角化。

***

## 问题 1：圆角接缝白线

### 根因

BR/TR 扇区在右边缘的直边边框被误切出白点——0°/360° 绕接处角度判定未包含边界像素。

### 修复

新增统一的 `_angle_in_corner_sector()` helper（`core/corner/algorithm.py`）处理 0°/360° 绕接，替换各处散落的角度判定逻辑：
- `core/image_cropper_mask.py`：移除本地 `_angle_in_corner_sector()`，改为从 `algorithm.py` 导入；`_post_cleanup_gap_regions` 也改用该 helper
- `core/corner/sector_render.py`：`_redraw_border_on_corner` 的 `valid_angle` 改用 `_angle_in_corner_sector()`
- `core/image_cropper_border.py`：`_redraw_outer_border_on_corners` 的 `in_angle` 改用 helper

***

## 问题 2：大半径内层仍被圆角化

### 根因

`apply_border_only_corners` 仍使用完整 `border_layers` 构建 mask，导致 border_zone 过宽，内层图案/文字带被纳入圆角范围。

### 修复（core/image_cropper_border.py）

`apply_border_only_corners` **仅用最外层边框层**构建：
- mask
- validity_mask
- 重绘
- cleanup

`raw_depth` 也仅用最外层厚度，确保内层图案/文字带保持直角。

***

## 验证

- 全量相关测试通过（47 passed / 5 skipped）
- 合成样例 `scripts/verify/large_radius_no_inner_round_demo.py`（1200×2100，r=6.5cm）：外层黑边圆角化、内层文字带保持直角、四个角无白线
