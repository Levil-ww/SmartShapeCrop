"""workers package

线程调度层：所有 QThread Worker 集中管理。

职责：
  - Worker 只做：启动 → check_cancel → 转发结果
  - 业务逻辑调用 services/ 和 core/
  - 与 GUI 层解耦：Worker 不导入任何 gui/ 模块

子模块：
  - workers.property_panel_workers:  水池设计相关 Worker（PoolRenderWorker 等）
  - workers.cropper_workers:         裁剪相关 Worker（CropWorker, AutoMatchWorker）
  - workers.canvas_workers:          画布渲染相关 Worker（PreviewRenderWorker, ExportSaveWorker）
"""
