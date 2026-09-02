"""
main.py
应用入口：主窗口，整合预览画布 + 属性面板 + 裁剪面板 + 菜单 + 保存 JPG。
"""
from __future__ import annotations
import sys
import os
import logging
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QHBoxLayout,
    QAction, QFileDialog, QMessageBox, QStatusBar, QLabel, QTabWidget,
    QProgressDialog,
)

from core.geometry import CropDesign, BorderLayer
from core.log_setup import setup_logging
from core.image_ops import save_jpg, render_design
from gui.canvas_widget import PreviewCanvas, ExportSaveWorker
from gui.property_panel import PropertyPanel
from gui.cropper_panel import CropperPanel


def resource_path(relative_path: str) -> str:
    """
    获取资源文件的绝对路径。
    支持两种运行模式：
      1) 源码模式：基于 main.py 所在目录
      2) 打包模式（PyInstaller one-folder/one-file）：基于 sys._MEIPASS 临时目录
    """
    if hasattr(sys, '_MEIPASS'):
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def set_app_icon(app: QApplication) -> None:
    """
    设置全局应用图标。
    QApplication.setWindowIcon 会影响所有未单独设置图标的顶级窗口（QMainWindow/QDialog）。
    图标来源：images/logo.png（打包时由 .spec 收集到内部资源目录）
    """
    logo_path = resource_path(os.path.join('images', 'logo.png'))
    if os.path.isfile(logo_path):
        app.setWindowIcon(QIcon(logo_path))


# 内置模板：对应你 5 张示例图的常用样式
def _preset_rect_nested() -> CropDesign:
    """图 1 风格：矩形嵌套 + 3 层边框 + 米色背景 + 边框文字"""
    d = CropDesign(
        mode='rect_hole',
        canvas_w_cm=50, canvas_h_cm=70, dpi=150,
        outer_margin_cm=1.0,
        inner_margin_top_cm=10, inner_margin_bottom_cm=10,
        inner_margin_left_cm=10, inner_margin_right_cm=10,
        borders=[
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(255, 255, 255)),
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
        ],
        outer_bg_color=(0, 0, 0),
        hole_bg_color=(250, 245, 230),
    )
    return d


def _preset_lshape() -> CropDesign:
    """图 2/4 风格：L 形挖角 + 浅米色 + 简单边框"""
    d = CropDesign(
        mode='rect_lshape',
        canvas_w_cm=80, canvas_h_cm=30, dpi=150,
        outer_margin_cm=0.5,
        inner_margin_top_cm=3, inner_margin_bottom_cm=3,
        inner_margin_left_cm=3, inner_margin_right_cm=3,
        l_corner='br', l_cut_w_cm=20, l_cut_h_cm=15,
        borders=[
            BorderLayer(offset_cm=0.15, fill_type='solid', color=(0, 0, 0)),
        ],
        outer_bg_color=(0, 0, 0),
        hole_bg_color=(250, 245, 230),
    )
    return d


def _preset_ellipse() -> CropDesign:
    """图 3 风格：椭圆嵌套 + 3 层边框 + 白色画布"""
    d = CropDesign(
        mode='ellipse_hole',
        canvas_w_cm=60, canvas_h_cm=80, dpi=150,
        outer_margin_cm=1.0,
        ellipse_rx_ratio=0.30, ellipse_ry_ratio=0.28,
        borders=[
            BorderLayer(offset_cm=0.4, fill_type='solid', color=(0, 0, 0)),
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(255, 255, 255)),
            BorderLayer(offset_cm=0.4, fill_type='solid', color=(0, 0, 0)),
        ],
        outer_bg_color=(0, 0, 0),
        hole_bg_color=(255, 255, 255),
    )
    return d


def _preset_tile() -> CropDesign:
    """图 5 风格：用于填充瓷砖花纹的矩形嵌套（等你选素材图后自动覆盖背景）"""
    d = CropDesign(
        mode='rect_hole',
        canvas_w_cm=70, canvas_h_cm=60, dpi=150,
        outer_margin_cm=0.8,
        inner_margin_top_cm=12, inner_margin_bottom_cm=12,
        inner_margin_left_cm=25, inner_margin_right_cm=8,
        borders=[
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(240, 230, 210)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(0, 0, 0)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(255, 255, 255)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(0, 0, 0)),
        ],
        outer_bg_color=(0, 0, 0),
        hole_bg_color=(250, 245, 230),
    )
    return d


PRESETS = [
    ("图1 矩形嵌套+文字", _preset_rect_nested),
    ("图2/4 L形挖角",    _preset_lshape),
    ("图3 椭圆嵌套",     _preset_ellipse),
    ("图5 瓷砖嵌套",     _preset_tile),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SmartShapeCrop - 水池圆角裁剪设计器")

        # 中央：左边画布 + 右边标签页（属性面板/裁剪面板）
        splitter = QSplitter(Qt.Horizontal)
        self.canvas = PreviewCanvas()
        
        # 右侧标签页
        self._tabs = QTabWidget()
        self.panel = PropertyPanel()
        self.cropper = CropperPanel()
        self._tabs.addTab(self.cropper, "圆角裁剪工具")
        self._tabs.addTab(self.panel, "水池设计器")
        
        splitter.addWidget(self.canvas)
        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        # 右侧面板最小宽度——保证"自动匹配"等按钮完整显示
        self._tabs.setMinimumWidth(500)
        splitter.setSizes([820, 580])

        central = QWidget()
        lay = QHBoxLayout(central); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter)
        self.setCentralWidget(central)

        # 状态栏
        sb = QStatusBar(); self.setStatusBar(sb)
        self._status_size = QLabel("画布尺寸：-")
        sb.addPermanentWidget(self._status_size)

        # [Fix 2026-09-02 B] 导出 JPG 状态保护（防重复点击 + UI 阻塞）
        self._is_saving: bool = False
        self._save_worker: ExportSaveWorker | None = None
        self._save_progress: QProgressDialog | None = None
        self._a_save: QAction | None = None   # 稍后在 _build_menu 赋值

        # 菜单
        self._build_menu()

        # 信号连接
        self.panel.design_changed.connect(self._on_design_changed)
        self.panel.save_requested.connect(self._on_save)
        self.panel.sketch_loaded.connect(self._on_sketch_loaded)
        self.canvas.rendered.connect(self._on_rendered)

        # 裁剪面板信号
        self.cropper.image_cropped.connect(self._on_cropped_image)

        # [自适应窗口] 所有 UI 构建完成后再调整大小和位置
        self._init_window_geometry()

    def _init_window_geometry(self):
        """
        初始化窗口大小和位置。
        使用 QTimer.singleShot(0, ...) 延迟到事件循环第一帧执行，
        确保 showMaximized() 在窗口真正显示后生效。
        """
        QTimer.singleShot(0, self._do_init_geometry)

    def _do_init_geometry(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1400, 900)
            self._center_on_screen()
            return

        available = screen.availableGeometry()
        screen_w = available.width()
        screen_h = available.height()

        # 默认窗口尺寸为屏幕可用区域的 80%，保证一眼看到所有按钮
        target_w = min(1600, int(screen_w * 0.82))
        target_h = min(1000, int(screen_h * 0.80))
        self.resize(target_w, target_h)
        self._center_on_screen()

    def _center_on_screen(self):
        """将窗口居中到屏幕中央"""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    # -------- 菜单 --------
    def _build_menu(self):
        mb = self.menuBar()
        m_file = mb.addMenu("文件(&F)")

        a_save = QAction("导出 JPG…", self); a_save.setShortcut(QKeySequence.Save)
        a_save.triggered.connect(self._on_save); m_file.addAction(a_save)
        self._a_save = a_save   # [Fix B] 导出期间禁用菜单/快捷键

        m_file.addSeparator()
        a_quit = QAction("退出", self); a_quit.setShortcut(QKeySequence.Quit)
        a_quit.triggered.connect(self.close); m_file.addAction(a_quit)

        m_tpl = mb.addMenu("模板(&T)")
        for name, fn in PRESETS:
            a = QAction(name, self)
            a.triggered.connect(lambda _=False, f=fn, n=name: self._apply_preset(f, n))
            m_tpl.addAction(a)

        m_help = mb.addMenu("帮助(&H)")
        a_about = QAction("关于", self)
        a_about.triggered.connect(self._about); m_help.addAction(a_about)

    def _about(self):
        QMessageBox.about(
            self, "关于 SmartShapeCrop",
            "<h3>SmartShapeCrop</h3>"
            "<p>矩形 / L形 / 椭圆 挖水池裁剪设计器</p>"
            "<ul>"
            "<li>尺寸单位：厘米（按 DPI 自动换算像素）</li>"
            "<li>导出格式：JPG（印刷级）</li>"
            "<li>素材支持：JPG 成品图 + PSD 分层（自动裁掉透明边导出）</li>"
            "</ul>")

    # -------- 逻辑 --------
    def _apply_preset(self, factory, name):
        self._apply_design(factory())
        self.statusBar().showMessage(f"已应用模板：{name}", 3000)

    def _apply_design(self, design: CropDesign):
        # 把设计同步到面板（让面板的 UI 控件显示正确的数值）
        self.panel.design = design
        self._sync_panel_from_design(design)
        # 触发面板 → 设计变更（会再次收集并渲染，确保所有控件与设计一致）
        self.panel.apply()

    def _sync_panel_from_design(self, d: CropDesign):
        """把设计对象数值写回面板控件（避免模板加载后 UI 还显示旧值）"""
        p = self.panel
        p._sp_w.setValue(d.canvas_w_cm); p._sp_h.setValue(d.canvas_h_cm); p._sp_dpi.setValue(d.dpi)
        idx = {'rect_hole': 0, 'rect_lshape': 1, 'ellipse_hole': 2}.get(d.mode, 0)
        p._cb_mode.setCurrentIndex(idx)
        p._sp_outer_margin.setValue(d.outer_margin_cm)
        p._sp_mt.setValue(d.inner_margin_top_cm); p._sp_mb.setValue(d.inner_margin_bottom_cm)
        p._sp_ml.setValue(d.inner_margin_left_cm); p._sp_mr.setValue(d.inner_margin_right_cm)
        ci = {'tl': 0, 'tr': 1, 'bl': 2, 'br': 3}.get(d.l_corner, 3)
        p._cb_lcorner.setCurrentIndex(ci)
        p._sp_lw.setValue(d.l_cut_w_cm); p._sp_lh.setValue(d.l_cut_h_cm)
        p._sp_erx.setValue(d.ellipse_rx_ratio); p._sp_ery.setValue(d.ellipse_ry_ratio)
        p._btn_outer_color.set_color(d.outer_bg_color); p._btn_hole_color.set_color(d.hole_bg_color)
        p._ed_outer_img.setText(d.outer_bg_image or ""); p._ed_hole_img.setText(d.hole_bg_image or "")
        if d.border_text is not None:
            p._gb_txt.setChecked(True)
            p._ed_txt.setText(d.border_text.text)
            p._sp_fs.setValue(d.border_text.font_size_px)
            p._btn_txt_color.set_color(d.border_text.color)
            p._ck_mirror.setChecked(d.border_text.mirror_bottom)
        else:
            p._gb_txt.setChecked(False)
        p._on_mode_change()
        p._update_layers_label()

    def _on_design_changed(self, design):
        self.canvas.set_design(design)

    def _on_rendered(self, img):
        self._status_size.setText(f"画布尺寸：{img.width} × {img.height} px ({img.width/img.info.get('dpi',(150,))[0]*2.54:.1f}cm × {img.height/img.info.get('dpi',(150,))[1]*2.54:.1f}cm @ DPI {img.info.get('dpi',(150,))[0]:.0f})" if 'dpi' in img.info else f"画布尺寸：{img.width} × {img.height} px")

    def _on_cropped_image(self, pil_img):
        """裁剪面板生成的图片：在画布上显示预览"""
        self.canvas._full_image = pil_img
        self.canvas._update_preview_pixmap()
        self.canvas.update()
        self.canvas.rendered.emit(pil_img)

    def _on_sketch_loaded(self, pil_img):
        """草图上传后直接显示在主画布上；传入 None 表示清除草图显示"""
        if pil_img is None:
            # 清除：若有设计则回到设计渲染，否则清空画布
            if self.panel.design is not None:
                self.canvas.set_design(self.panel.design)
            else:
                self.canvas._full_image = None
                self.canvas._preview_pixmap = None
                self.canvas.update()
            return
        # 直接在主画布显示草图（不悬浮在侧栏小缩略图里）
        self.canvas._full_image = pil_img
        self.canvas._update_preview_pixmap()
        self.canvas.update()
        self.canvas.rendered.emit(pil_img)

    def _on_save(self):
        """
        [Fix 2026-09-02 B] 导出 JPG 异步化：
          渲染与保存全流程移至 ExportSaveWorker（后台 QThread），主线程不再阻塞 5-15 秒。
          语义、输出与原实现 100% 一致：
            · 始终走 design.clone() 快照（F4 跨线程竞争保护）
            · quality='export'（全分辨率 LANCZOS）
            · quality=95 JPEG + DPI 元数据
          新增交互：
            · 防重复点击（_is_saving 守卫 + 保存动作禁用）
            · QProgressDialog（不确定进度条 + 取消按钮 → requestInterruption）
            · 完成/失败/取消 三种分支提示
        """
        if self.panel.design is None:
            QMessageBox.warning(self, "无法保存", "请先生成预览再保存")
            return

        # ---- 防重复点击守卫（菜单 Ctrl+S / 面板"导出 JPG"按钮 双入口）----
        if self._is_saving:
            QMessageBox.information(
                self, "正在导出",
                "上一次导出正在后台进行中，请稍候…\n可通过进度条取消本次导出。")
            return

        # ---- LOD 提示确认（与原逻辑相同，文案更新为"后台渲染"以匹配新行为）----
        if self.canvas.is_lod_active():
            reply = QMessageBox.question(
                self, "保存确认",
                "当前预览为低分辨率代理图。\n"
                "保存时将在后台渲染全分辨率印刷级图像（LANCZOS 高质量），\n"
                "可通过进度窗口取消，不会卡住界面。\n\n是否继续保存？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.No:
                return

        # ---- 输出路径选择 ----
        base_name = self.panel.get_output_filename()
        default_name = base_name + ".jpg"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 JPG", default_name, "JPEG 图片 (*.jpg *.jpeg)")
        if not path:
            return

        # ---- 进入保存状态 ----
        self._is_saving = True
        dpi = self.panel.design.dpi
        if self._a_save is not None:
            self._a_save.setEnabled(False)
        self.statusBar().showMessage("导出：正在后台渲染全分辨率图像…", 0)

        # ---- 不确定进度对话框（取消 → worker.requestInterruption）----
        #   setRange(0,0) 显示"忙碌"条；不调用 exec_()（非模态，保留事件循环）
        dlg = QProgressDialog(self)
        dlg.setWindowTitle("导出 JPG")
        dlg.setLabelText(
            f"正在为导出渲染 & 保存全分辨率图像…\n\n目标：{path}\n"
            f"画布：{self.panel.design.canvas_w_cm:.1f} × {self.panel.design.canvas_h_cm:.1f} cm "
            f"@ {dpi} DPI（印刷级 LANCZOS）\n"
            f"大画布可能需要数秒，界面不会卡住。")
        dlg.setRange(0, 0)   # 不确定进度
        dlg.setCancelButtonText("取消导出")
        dlg.setMinimumDuration(0)  # 立即显示
        dlg.setWindowModality(Qt.WindowModal)
        self._save_progress = dlg

        # ---- 启动后台 Worker（快照 防 F4 竞争，与 PreviewRenderWorker 同法）----
        worker = ExportSaveWorker(
            self.panel.design.clone(),
            out_path=path,
            dpi=dpi,
            jpeg_quality=95,
            parent=self,
        )
        self._save_worker = worker

        # ---- 信号连线 ----
        worker.save_ok.connect(self._on_save_ok)
        worker.save_err.connect(self._on_save_err)
        worker.save_cancelled.connect(self._on_save_cancelled)
        dlg.canceled.connect(worker.requestInterruption)

        # ---- 启动 ----
        worker.start()
        dlg.show()

    # ---- [Fix B] Export 异步信号处理器 ----
    def _retire_save_worker(self, timeout_ms: int = 10000) -> None:
        """
        [Fix 2026-09-02 C] 安全退役导出线程（对齐 PreviewCanvas._retire_worker 的已验证范式）：
          - 先取走引用，避免新旧 worker 混淆；
          - 仍在运行则 requestInterruption() 并 wait() 等其真正结束；
          - 结束（或超时）后才 deleteLater()，绝不在运行中析构 QThread。

        缺陷回溯：原 _save_cleanup 直接 `self._save_worker = None` 丢弃引用，
        而 main() 的 app.aboutToQuit 只连接了 canvas.shutdown（仅管渲染线程），
        导出线程完全无人接管。导出大图（38MP 画布需数秒）途中关闭窗口，
        必然触发 "QThread: Destroyed while thread is still running" 崩溃。
        """
        w = self._save_worker
        self._save_worker = None
        if w is None:
            return
        if w.isRunning():
            w.requestInterruption()
            if not w.wait(timeout_ms):
                logging.getLogger(__name__).warning(
                    "[MainWindow] 导出线程未在超时内结束，放弃等待")
        w.deleteLater()

    def _save_cleanup(self) -> None:
        """（内部）统一重置保存相关状态。"""
        self._is_saving = False
        if self._a_save is not None:
            self._a_save.setEnabled(True)
        if self._save_progress is not None:
            self._save_progress.close()
            self._save_progress = None
        # 不再直接置 None，改为安全退役（中断 + 等待 + deleteLater）
        self._retire_save_worker()

    def _on_save_ok(self, path: str, elapsed: float, img_w: int, img_h: int) -> None:
        self._save_cleanup()
        self.statusBar().showMessage(f"已保存：{path}（耗时 {elapsed:.1f}s）", 8000)
        QMessageBox.information(
            self, "保存成功",
            f"✅ 已导出 JPG：\n{path}\n\n"
            f"尺寸：{img_w}×{img_h} px\n"
            f"总耗时：{elapsed:.1f} 秒（后台执行，界面保持响应）")

    def _on_save_err(self, msg: str) -> None:
        self._save_cleanup()
        self.statusBar().showMessage("保存失败", 5000)
        QMessageBox.critical(self, "保存失败", msg)

    def _on_save_cancelled(self) -> None:
        self._save_cleanup()
        self.statusBar().showMessage("导出已取消", 3000)
        # 不弹错误对话框（用户主动取消），避免打扰

    def closeEvent(self, event) -> None:
        """
        [Fix 2026-09-02 C] 关闭窗口前确保所有后台线程已结束。

        原先仅靠 main() 中的 app.aboutToQuit → canvas.shutdown 处理渲染线程，
        导出线程无人接管。此处补齐：若正在导出则先中断并等待其结束，
        再交由画布清理渲染线程，避免关闭瞬间析构仍在运行的 QThread。
        """
        if self._is_saving:
            self._retire_save_worker()
        self.canvas.shutdown()
        super().closeEvent(event)


def _write_crash_log(exc_type, exc_value, tb) -> None:
    """
    全局异常处理器：将崩溃信息写入 exe 同目录的 crash.log。
    PyInstaller --windowed 打包后没有控制台，用户双击 exe 若崩溃毫无提示，
    通过此日志即可定位根因（缺 DLL、缺模块、资源路径问题等）。
    """
    import traceback
    try:
        if getattr(sys, 'frozen', False):
            log_dir = os.path.dirname(sys.executable)
        else:
            log_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(log_dir, "crash.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            from datetime import datetime
            f.write(f"崩溃时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"sys.frozen: {getattr(sys, 'frozen', False)}\n")
            f.write(f"sys.executable: {sys.executable}\n")
            if hasattr(sys, '_MEIPASS'):
                f.write(f"sys._MEIPASS: {sys._MEIPASS}\n")
            f.write(f"sys.path[:3]: {sys.path[:3]}\n")
            f.write("-" * 60 + "\n")
            traceback.print_exception(exc_type, exc_value, tb, file=f)
            f.write("\n")
    except Exception:
        pass


def main():
    # 注册全局异常钩子（必须放在最前面）
    sys.excepthook = _write_crash_log

    # 初始化结构化日志（程序入口调用一次即可，幂等保护）
    # 调试时设置环境变量 LOG_LEVEL=DEBUG 即可输出详细日志
    setup_logging()

    # [F19 修复] 启动时自动清理调试产物（logs/ 与 debug_output/ 中的过期截图
    # 与中间图，保留滚动日志与 crash.log；交付物目录不受影响）。失败不阻断启动。
    try:
        from core.artifact_cleanup import cleanup_debug_artifacts
        cleanup_debug_artifacts()
    except Exception as e:
        logging.getLogger(__name__).warning(
            f'[main] 调试产物自动清理失败（不影响启动）: {e}')

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # [图标] 设置全局窗口图标，所有顶级窗口(QMainWindow/QDialog)自动继承
    set_app_icon(app)
    w = MainWindow()
    w.show()
    # [F16 修复] 退出前等待后台渲染线程结束，避免 "QThread destroyed while running"
    app.aboutToQuit.connect(w.canvas.shutdown)
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
