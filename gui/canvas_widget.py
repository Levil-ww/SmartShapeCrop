"""
gui/canvas_widget.py
预览画布组件：
- 实际渲染：始终使用 design.canvas_w_px × canvas_h_px 的全分辨率（用于保存）
- 界面显示：按 QWidget 窗口大小等比缩放居中（仅预览，不影响保存）
- LOD 模式：所有预览先显示 LOD 低分辨率代理图，确保即时反馈
- 异步渲染：完整分辨率在后台 QThread 渲染，完成后无缝替换预览
- 避免把"预览缩放后的 QPixmap"作为保存源（经验：799713）
"""
from __future__ import annotations
import io
import logging
import time
from PIL import Image
from PyQt5.QtCore import Qt, QSize, pyqtSignal, QThread
from PyQt5.QtGui import QPixmap, QImage, QPainter, QColor, QPalette
from PyQt5.QtWidgets import QWidget, QSizePolicy

logger = logging.getLogger(__name__)

# LOD 触发阈值：像素量超过此值时使用低分辨率渲染
LOD_PIXEL_THRESHOLD = 1_000_000  # 100万像素
LOD_SCALE_FACTOR = 0.25  # 1/4 分辨率

# 异步渲染阈值：超过此像素量的设计使用后台线程渲染
ASYNC_RENDER_THRESHOLD = 200_000  # 20万像素


class PreviewRenderWorker(QThread):
    """
    后台渲染 Worker：在独立线程中执行全分辨率 render_design。
    避免 UI 主线程阻塞，渲染完成后通过 finished_ok 信号返回结果。
    """
    finished_ok = pyqtSignal(object, float)   # (PIL.Image, elapsed_seconds)
    finished_err = pyqtSignal(str)

    def __init__(self, design, parent=None):
        super().__init__(parent)
        self._design = design

    def run(self):
        try:
            from core.image_ops import render_design
            t0 = time.perf_counter()
            img = render_design(self._design, quality='preview')
            elapsed = time.perf_counter() - t0
            if self.isInterruptionRequested():
                return
            self.finished_ok.emit(img, elapsed)
        except Exception as e:
            logger.exception("[PreviewRenderWorker] 后台渲染异常")
            if self.isInterruptionRequested():
                return
            self.finished_err.emit(str(e))


class PreviewCanvas(QWidget):
    """显示当前 CropDesign 的预览画布（只读，显示用）"""

    rendered = pyqtSignal(object)   # 每次重绘全尺寸图像后发射，方便外部保存

    def __init__(self, parent=None):
        super().__init__(parent)
        self._design = None
        self._full_image: Image.Image | None = None  # 预览渲染图（LOD 或全分辨率）
        self._preview_pixmap: QPixmap | None = None  # 缩放后的预览图
        self._use_lod: bool = False  # 当前是否使用 LOD 渲染
        self._is_rendering: bool = False  # 是否正在后台渲染
        self._render_worker: PreviewRenderWorker | None = None  # 后台渲染 Worker
        self._last_render_id: int = 0  # 渲染版本号，用于丢弃过期结果
        self.setMinimumSize(QSize(400, 500))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setBackgroundRole(QPalette.Dark)
        self.setAutoFillBackground(True)

    # ---- 外部接口 ----
    def set_design(self, design) -> None:
        """设置设计并触发重渲染（异步分级渲染）"""
        self._design = design
        self._render_async()

    def full_image(self) -> Image.Image | None:
        """返回全尺寸渲染图（保存时使用这个，不要用预览图）"""
        return self._full_image

    def is_lod_active(self) -> bool:
        """当前预览是否使用 LOD 低分辨率渲染"""
        return self._use_lod

    def is_rendering(self) -> bool:
        """当前是否正在后台渲染"""
        return self._is_rendering

    # ---- 异步分级渲染 ----
    def _render_async(self) -> None:
        """
        异步分级渲染流程：
        1. 立即同步渲染 LOD 低分辨率预览（<100ms），让用户立刻看到效果
        2. 在后台 QThread 中渲染全分辨率
        3. 完成后无缝替换预览图
        """
        if self._design is None:
            self._full_image = None
            self._preview_pixmap = None
            self._use_lod = False
            self.update()
            return

        total_pixels = self._design.canvas_w_px * self._design.canvas_h_px

        # 取消并清理之前未完成的后台渲染
        # [F16 修复] 不再只 requestInterruption() 就丢弃引用——旧 worker 注册
        # finished → deleteLater，由 Qt 事件循环在线程真正结束后再释放对象，
        # 避免出现 "QThread: Destroyed while thread is still running"。
        self._retire_worker()

        render_id = self._last_render_id + 1
        self._last_render_id = render_id

        # 1. 立即渲染 LOD 预览（保证即时反馈）
        if total_pixels > LOD_PIXEL_THRESHOLD:
            # 大图直接 LOD
            self._render_lod()
        else:
            # 小图也先用 LOD 快速预览
            self._render_lod()

        self._update_preview_pixmap()
        self.update()

        # 2. 对较大的设计，启动后台全分辨率渲染
        if total_pixels > ASYNC_RENDER_THRESHOLD:
            self._is_rendering = True
            logger.info(
                f"[PreviewCanvas] 启动后台全分辨率渲染: "
                f"{total_pixels}px (LOD已显示，将替换为全分辨率)"
            )
            # [F4 修复] 把独立快照交给后台线程：clone() 复制可变字段（borders / border_text /
            # 标量），并共享只读的 _cached_outer_image。此后主线程原地修改 self.design
            # 不会再影响正在后台运行的渲染，消除跨线程读写竞争。
            worker = PreviewRenderWorker(self._design.clone(), self)
            worker.finished_ok.connect(
                lambda img, t, rid=render_id: self._on_full_render_done(img, t, rid)
            )
            worker.finished_err.connect(self._on_full_render_err)
            self._render_worker = worker
            worker.start()
        else:
            # 小图直接在主线程渲染全分辨率（<200Kpx 通常 <100ms）
            self._render_full_sync()

    def _on_full_render_done(self, img: Image.Image, elapsed: float, render_id: int) -> None:
        """后台渲染完成：替换预览图"""
        self._is_rendering = False
        # 丢弃过期结果（用户已经切换了设计）
        if render_id != self._last_render_id:
            logger.info("[PreviewCanvas] 丢弃过期渲染结果")
            return
        self._full_image = img
        self._use_lod = False
        self._update_preview_pixmap()
        self.update()
        self.rendered.emit(self._full_image)
        logger.info(
            f"[PreviewCanvas] 全分辨率渲染完成: "
            f"{img.width}x{img.height}px, 耗时 {elapsed:.2f}s"
        )

    def _on_full_render_err(self, err_msg: str) -> None:
        """后台渲染异常"""
        self._is_rendering = False
        logger.error(f"[PreviewCanvas] 后台渲染失败: {err_msg}")

    # ---- [F16] worker 生命周期管理 ----
    def _retire_worker(self) -> None:
        """
        退役当前后台渲染 worker：
        - 清空 self._render_worker 引用（新 worker 可以立即启动）；
        - 若线程仍在运行：requestInterruption() 并连接 finished → deleteLater，
          保证线程结束后对象才被释放，绝不在运行中析构 QThread；
        - 若已结束：直接 deleteLater()。
        过期结果由 _on_full_render_done 的 render_id 校验自然丢弃，无需额外断连。
        """
        old = self._render_worker
        self._render_worker = None
        if old is None:
            return
        if old.isRunning():
            old.requestInterruption()
            try:
                old.finished.connect(old.deleteLater)
            except TypeError:
                # 理论上不会发生；防御重复连接时直接释放
                old.deleteLater()
        else:
            old.deleteLater()

    def shutdown(self, timeout_ms: int = 3000) -> None:
        """
        应用退出前调用：中断后台渲染并等待线程真正结束。
        避免父窗口析构时销毁仍在运行的渲染线程（崩溃/警告根源）。
        """
        worker = self._render_worker
        self._retire_worker()
        if worker is not None and worker.isRunning():
            if not worker.wait(timeout_ms):
                logger.warning("[PreviewCanvas] 后台渲染线程未在超时内结束，放弃等待")

    # ---- 同步渲染（用于小图或保存） ----
    def _render_full_sync(self) -> None:
        """全分辨率同步渲染（小图或用于保存）"""
        from core.image_ops import render_design
        t0 = time.perf_counter()
        self._full_image = render_design(self._design, quality='preview')
        self._use_lod = False
        elapsed = time.perf_counter() - t0
        self._update_preview_pixmap()
        self.rendered.emit(self._full_image)
        logger.info(
            f"[PreviewCanvas] 同步渲染完成: "
            f"{self._full_image.width}x{self._full_image.height}px, 耗时 {elapsed:.2f}s"
        )

    def _render_lod(self) -> None:
        """LOD 低分辨率渲染（即时预览）"""
        from core.image_ops import render_design_lod
        self._full_image = render_design_lod(self._design, scale=LOD_SCALE_FACTOR)
        self._use_lod = True

    def _render_full_for_save(self) -> Image.Image | None:
        """
        专门用于保存的全分辨率 LANCZOS 渲染。
        与预览渲染分离，确保导出质量。
        """
        if self._design is None:
            return None
        from core.image_ops import render_design
        return render_design(self._design, quality='export')

    def _update_preview_pixmap(self) -> None:
        if self._full_image is None:
            self._preview_pixmap = None
            return
        # 若源图远大于 widget 显示区域，先降采样再转 QPixmap，避免大图转换开销
        src = self._full_image.convert('RGB')
        w, h = max(1, self.width()), max(1, self.height())
        src_w, src_h = src.size
        # 如果源图像素 > widget 像素 2x 以上，先缩小到 widget 2x 再转换
        # 防止百万级像素图像转换为 QImage/QPixmap 耗时巨大
        widget_pixels = w * h
        src_pixels = src_w * src_h
        if src_pixels > widget_pixels * 4:
            # [Fix 2026-08-26] 原实现 src.resize((w*2, h*2)) 分别限制宽高 = STRETCH 变形
            # widget 的宽高比 (w:h) 通常 ≠ 设计图源图宽高比 (src_w:src_h)
            # 导致水池模式下预览严重变形（看截图1左侧：竖版花被横向拉扁）
            # 修复：按等比缩放（保持 src_w:src_h 比例），避免预览变形
            max_display_w = w * 2
            max_display_h = h * 2
            # 用 contain 比例：等比缩放到不超过 max_display 框
            scale = min(max_display_w / src_w, max_display_h / src_h)
            target_w = max(1, int(src_w * scale))
            target_h = max(1, int(src_h * scale))
            src = src.resize((target_w, target_h), Image.BILINEAR)

        data = src.tobytes('raw', 'RGB')
        qimg = QImage(data, src.width, src.height, src.width * 3, QImage.Format_RGB888).copy()
        self._source_pixmap = QPixmap.fromImage(qimg)
        self._rescale_preview()

    def _rescale_preview(self):
        if not hasattr(self, '_source_pixmap') or self._source_pixmap is None:
            self._preview_pixmap = None
            return
        # LOD 预览使用 FastTransformation 加速（LOD 已缩小 4×，放大无需平滑）
        # 全分辨率预览使用 SmoothTransformation 保证显示质量
        w, h = max(1, self.width()), max(1, self.height())
        transform = Qt.FastTransformation if self._use_lod else Qt.SmoothTransformation
        self._preview_pixmap = self._source_pixmap.scaled(
            w, h, Qt.KeepAspectRatio, transform)

    # ---- Qt 事件 ----
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_preview()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))  # 画布周围深灰
        if self._preview_pixmap is None or self._preview_pixmap.isNull():
            p.setPen(QColor(180, 180, 180))
            if self._is_rendering:
                p.drawText(self.rect(), Qt.AlignCenter,
                           "正在渲染预览图…")
            else:
                p.drawText(self.rect(), Qt.AlignCenter,
                           "请在右侧设置参数后生成预览")
            return
        # 居中绘制
        pm_w, pm_h = self._preview_pixmap.width(), self._preview_pixmap.height()
        x = (self.width() - pm_w) // 2
        y = (self.height() - pm_h) // 2
        p.drawPixmap(x, y, self._preview_pixmap)
        # 画一个浅色边框
        p.setPen(QColor(120, 120, 120))
        p.drawRect(x, y, pm_w - 1, pm_h - 1)
        # LOD 模式提示
        if self._use_lod:
            p.setPen(QColor(255, 200, 50))
            font = p.font()
            font.setPointSize(8)
            p.setFont(font)
            tip = "预览为低分辨率代理图"
            if self._is_rendering:
                tip += "（正在渲染全分辨率…）"
            else:
                tip += "，保存时会渲染全分辨率"
            p.drawText(
                self.rect().adjusted(5, 5, -5, -5),
                Qt.AlignTop | Qt.AlignRight,
                tip
            )
