# 2026-09-07 T1：圆角裁剪仅外层边框圆角化

## 概述

`apply_border_only_corners` 会沿圆弧重绘内层装饰/深色带，导致圆角处出现紧贴黑色外框的深色弧形；且当角外侧扇形被判断为纯色背景时，内层矩形框/装饰带也被圆角化，与"仅边框圆角"语义冲突。

***

## 修复（core/image_cropper_border.py）

### 改动 1：only_outermost=True

`apply_border_only_corners` 的 Step A 调用 `_redraw_border_on_corner` 时传入 `only_outermost=True`：
- 只补绘与最外层边框颜色接近的像素
- 避免把内层颜色带进圆角

为 `_redraw_outer_border_on_corners` 新增 `only_outermost` 参数。

### 改动 2：protect_content=True 常驻

所有有效圆角统一启用 `protect_content=True`，不再依赖 `_corner_sector_has_content` 自动判断：
- 之前：角外侧扇形被判断为纯色背景时 → 保护关闭 → 内层矩形框/装饰带被圆角化
- 现在：始终保护内容，内层保持直角

`apply_rounded_corners` 保持 `only_outermost=False` 不变，整体圆角仍重绘多层边框。

***

## 验证

- `tests/core/test_image_cropper.py`、`tests/border/test_border_fix.py`、`tests/border/test_complex_pattern_safety.py` 通过
- `tests/core/test_rounded_corner.py`、`tests/integration/test_final_verification.py` 通过
- `tests/border/test_gap_fix_verification.py`、`tests/border/test_user_reported_cases.py` 通过
- 合成样例 `scripts/verify/corner_outermost_demo.py`：仅最外层黑边圆角化，内层棕边保持直角
