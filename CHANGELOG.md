# SmartShapeCrop 变更日志

本文件记录项目的重要修复和改进，按日期倒序排列。

---

## 2026-09-17

### 性能优化
- **detection.py**: 使用 `np.diff()` 向量化边框层间隙计算，替代 Python 循环
- **sector_render.py**: 无源图时跳过内容保护掩码计算（提前裁剪）

### 历史归档清理
- 删除 `scripts/_archive/`（过期诊断脚本，清理窗口已过）
- 删除 `scripts/verify/_archive/`（过期验证脚本）
- 删除 `packaging/legacy/`（V2.0-V2.2.1 历史打包脚本）

---

## 2026-09-16

### L 形边框
- **lshape_border.py**: 修复坐标溢出问题
- **lshape_border_route.py**: 修复坐标溢出问题

---

## 2026-09-14

### L 形边框检测
- **lshape_border.py**: `detect_border_v13` 返回值扩展为四元组 `(edge, band, black_color, band_color)`，新增黑描边代表色
- **lshape_border.py**: 黑描边色取自检测结果（四元组第 3 项），替换硬编码纯黑
- **lshape_border.py**: band 必须是"中间段"：其后还要有接续段，否则不是有效 band
- **verify_v13_fix.py**: 适配四元组返回值

---

## 2026-09-12

### 性能优化
- **lshape_border.py**: 降采样避免全图 float64（2 亿像素≈4.8GB）
- **image_ops.py**: 使用 `clone()` 代替 `deepcopy`，提升素材缓存效率

### 几何计算
- **lshape_border.py**: 统一为几何平均 `sqrt(sx*sy)`，与 Profile 路径一致

---

## 2026-09-08

### 素材处理
- **image_ops.py**: 素材底色采样，给 border completion 当 bg_color 用（用来过滤背景色）

---

## 2026-09-05

### 圆角边框
- **sector_render.py**: 修复圆角断触/白色空隙/粗细不一致问题
  - 三区域渐进内容保护掩码（核心边框区/过渡区/边框外）
  - 过渡区宽度固定 2px，平衡实心度与内容保留
  - 直边附近过渡区保护紧贴边框的花纹/文字
- **sector_render.py**: 弧线外侧越界像素清为背景色（容差 R-0.5）
- **detection.py**: 边框层间隙上限校验，防止内容区元素被误检为内层边框
- **detection.py**: 花纹周期截断（判据：连续交替模式 + 层数硬上限）

### 多洞解析器
- **sketch_parser_multihole.py**: Phase D.5 面积预过滤 + 同洞分割否决
- **sketch_parser_multihole.py**: Phase D.6 洞数一致性验证（面积差距否决 + 几何否决）

---

## 2026-09-04

### 素材缓存
- **image_ops.py**: 缓存来源校验：素材路径已变则缓存作废，回退从磁盘加载

---

## 2026-09-02

### 导出功能
- **main.py**: 导出 JPG 异步化，防重复点击 + UI 阻塞
- **main.py**: 导出期间禁用菜单/快捷键
- **main.py**: 安全退役导出线程（对齐 PreviewCanvas._retire_worker 的已验证范式）
- **main.py**: 关闭窗口前确保所有后台线程已结束
- **main.py**: 补齐 cropper.shutdown()，避免 running QThread 被析构
- **main.py**: 退役 PropertyPanel 与 LShapePanel 后台线程，避免关窗时 running QThread 被析构
- **canvas_workers.py**: 导出 JPG 专用后台 Worker

### 预览显示
- **canvas_widget.py**: 预览缩放因子从 0.25 调整（原 1/4 边长 = 1/16 像素，预览显示"马赛克"感）
- **canvas_widget.py**: 始终使用 SmoothTransformation（双线性）做最后一英里显示缩放
- **image_ops.py**: 严重下采样抗锯齿：LOD 预览时素材 4-8× 下采样，NEAREST → BILINEAR

### 素材处理
- **image_ops.py**: 修复"两侧黑色背景框"：当某侧延展量较大(>5% 画布边长)且源图该侧带黑色边缘时，改用 contain 模式
- **image_ops.py**: 采用简单拉伸模式，解决用户反馈的两侧黑边和边框线条被裁剪问题
- **image_ops.py**: 三重校验 (cond_a∧cond_b∧cond_c) 过严问题修复

### 属性面板
- **property_panel_workers.py**: 用户手动修改的边距优先于草图识别结果
- **property_panel_workers.py**: 不再修改 sketch_result 对象（引用传递会污染原始数据）
- **property_panel_workers.py**: 传递素材原始设计方向尺寸（文件名方向，未经过 oriented 交换）

---

## 2026-08-28

### 属性面板
- **property_panel_workers.py**: 用户手动修改的边距：仅记录日志，不覆盖草图识别结果

---

## 2026-08-27

### 素材处理
- **image_ops.py**: 采用简单拉伸模式，解决用户反馈的两侧黑边和边框线条被裁剪问题

---

## 2026-08-26

### 素材处理
- **image_ops.py**: 处理 EXIF 方向：相机/手机拍的照片会带 Orientation tag（如旋转 90 度）
- **image_ops.py**: 改用 `adapt_pool_material`（方向校正 + contain 等比 + 边缘延展填充）
- **image_ops.py**: L 形 + 外背景图特性：边框带应显示外背景图，故跳过着色
- **image_ops.py**: 素材填充模式下 `pool_hole_transparent=False`

### 属性面板
- **property_panel_workers.py**: 素材设计方向尺寸（文件名原始方向，供渲染旋转判断）
- **property_panel_workers.py**: 传递素材原始设计方向尺寸（文件名方向，未经过 oriented 交换）

### 预览显示
- **canvas_widget.py**: 原实现 `src.resize((w*2, h*2))` 分别限制宽高 = STRETCH 变形，修复为等比缩放

---

## 早期修复（日期未标注）

### 草图解析器
- **sketch_parser.py**: 多洞解析器基础功能
- **sketch_parser_numbers.py**: 数值识别与空间归属
- **sketch_parser_vision.py**: 矩形检测与 OCR 扫描
- **lshape_sketch_parser.py**: L 形草图解析

### 模板匹配
- **template_matcher.py**: 模板匹配算法优化

### 几何计算
- **geometry.py**: 几何计算工具函数

### 主程序
- **main.py**: 窗口关闭线程安全处理

### GUI 组件
- **cropper_panel.py**: 裁剪面板功能完善
- **property_panel.py**: 属性面板功能完善
- **lshape_panel.py**: L 形面板功能完善

---

## 说明

本 changelog 从代码中的 `[Fix ...]` 注释整理而来，记录了项目演进过程中的关键修复决策。

详细的技术背景和根因分析请参考：
- `ProductSummary/项目审查报告/` —— 全部项目审查报告归档目录
  （原根目录的 `项目全面审查报告.md` 已于 2026-09-24 归位至此）
- 各模块的 docstring 注释
- Git commit history
