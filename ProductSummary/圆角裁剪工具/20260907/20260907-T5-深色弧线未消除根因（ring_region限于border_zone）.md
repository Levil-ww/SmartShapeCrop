# 2026-09-07 T5：深色弧线未消除根因——ring_region 限于 border_zone

## 概述

T4 修复后用户反馈蔓生花/素锦的深色弧线仍未消除。根因是 ring_region 保护了弧线外侧的深色边框残留，**没有区分直边区域与弧线区域**。

***

## 根因

圆角 mask 的 `ring_region` 在圆角 mask 中保护了弧线外侧的深色边框残留。

直边区域的 border_zone 需要保护（直边边框不能切），但**弧线外侧的区域应该切到白底**，否则会残留深色弧线。

之前的 ring_region 没有做这个区分，导致弧线外侧的深色边框像素被 ring_region 保护，无法被切白。

***

## 修复（core/image_cropper_mask.py）

1. **无条件定义 border_zone**（之前可能因条件判断未定义）
2. **ring_region 限于 border_zone**：确保弧线外侧区域不被 ring_region 保护，从而被正确切到白色背景
3. 直边边框（border_zone 内）仍受保护，不被误切

***

## 验证

- 受影响素材（蔓生花、素锦）非 border_zone 深色像素：**0**
- 所有回归测试通过，零新增失败
- 64 项圆角相关测试通过

***

## 工程约定沉淀（写入 memory）

> 圆角 mask 的 ring_region 必须限于 border_zone（直边区域），防止保护弧线外侧深色边框残留。

违反后果：弧线外侧残留深色边框线（如蔓生花、素锦的深色弧形缺口线）。
