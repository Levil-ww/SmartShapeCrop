"""
gui/cropper_panel.py
裁剪面板：上传成品图/PSD → 自动识别或手动输入裁剪参数 → 预览 → 导出 JPG。
"""
from __future__ import annotations
import os
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QDoubleSpinBox,
    QSpinBox, QPushButton, QFileDialog, QLineEdit, QComboBox, QCheckBox,
    QScrollArea, QMessageBox, QGridLayout, QSizePolicy, QColorDialog
)
from PIL import Image

from core.name_parser import parse_filename, generate_filename, get_image_info
from core.image_cropper import crop_image, CropConfig, get_corner_name, get_default_corners, get_mode_description


def pil_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    """PIL Image → QPixmap"""
    if pil_img.mode != 'RGB':
        pil_img = pil_img.convert('RGB')
    data = pil_img.tobytes('raw', 'RGB')
    qimg = QImage(data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


class CropperPanel(QWidget):
    """裁剪操作面板"""
    
    image_cropped = pyqtSignal(object)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._src_path: str = ""
        self._src_info: dict = {}
        self._last_result: Image.Image | None = None
        self._bg_color: tuple[int, int, int] = (255, 255, 255)
        self._build_ui()
    
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(10)
        scroll.setWidget(inner)
        root.addWidget(scroll)
        
        # ===== 1) 文件选择 =====
        gb_file = QGroupBox("1. 选择源图")
        fg = QVBoxLayout(gb_file)
        
        row_file = QHBoxLayout()
        self._ed_file = QLineEdit()
        self._ed_file.setPlaceholderText("JPG / PSD 文件路径…")
        btn_pick = QPushButton("浏览…")
        btn_pick.clicked.connect(self._pick_source)
        row_file.addWidget(self._ed_file, 1)
        row_file.addWidget(btn_pick)
        fg.addLayout(row_file)
        
        self._lbl_src_info = QLabel("尚未选择源图")
        self._lbl_src_info.setStyleSheet("color:#666;")
        fg.addWidget(self._lbl_src_info)
        
        btn_parse = QPushButton("2. 从文件名自动识别尺寸/圆角")
        btn_parse.setStyleSheet("padding:6px; background:#4a90d9; color:white; font-weight:bold;")
        btn_parse.clicked.connect(self._auto_parse)
        fg.addWidget(btn_parse)
        
        lay.addWidget(gb_file)
        
        # ===== 3) 裁剪参数 =====
        gb_param = QGroupBox("3. 裁剪参数")
        fp = QVBoxLayout(gb_param)
        
        # 产品名称
        row_name = QHBoxLayout()
        row_name.addWidget(QLabel("产品名称:"))
        self._ed_product = QLineEdit()
        self._ed_product.setPlaceholderText("如：双面格-定制-定制尺寸-简织")
        row_name.addWidget(self._ed_product, 1)
        fp.addLayout(row_name)
        
        # 布局 + 尺寸
        grid_size = QGridLayout()
        grid_size.addWidget(QLabel("布局:"), 0, 0)
        self._cb_layout = QComboBox()
        self._cb_layout.addItem("竖版（长边为高）", "portrait")
        self._cb_layout.addItem("横版（长边为宽）", "landscape")
        grid_size.addWidget(self._cb_layout, 0, 1)
        
        grid_size.addWidget(QLabel("宽(cm):"), 0, 2)
        self._sp_w = QDoubleSpinBox(); self._sp_w.setRange(1, 500); self._sp_w.setValue(55); self._sp_w.setDecimals(1)
        grid_size.addWidget(self._sp_w, 0, 3)
        
        grid_size.addWidget(QLabel("高(cm):"), 0, 4)
        self._sp_h = QDoubleSpinBox(); self._sp_h.setRange(1, 500); self._sp_h.setValue(41); self._sp_h.setDecimals(1)
        grid_size.addWidget(self._sp_h, 0, 5)
        
        grid_size.addWidget(QLabel("DPI:"), 1, 0)
        self._sp_dpi = QSpinBox(); self._sp_dpi.setRange(72, 600); self._sp_dpi.setValue(150)
        grid_size.addWidget(self._sp_dpi, 1, 1)
        
        grid_size.addWidget(QLabel("裁剪模式:"), 1, 2)
        self._cb_mode = QComboBox()
        self._cb_mode.addItem("轻度裁剪（推荐）", "light_cover")
        self._cb_mode.addItem("智能模式", "auto")
        self._cb_mode.addItem("裁剪填满", "cover")
        self._cb_mode.addItem("留白填充", "contain")
        grid_size.addWidget(self._cb_mode, 1, 3, 1, 3)
        
        fp.addLayout(grid_size)
        
        # 模式说明
        self._lbl_mode_desc = QLabel(get_mode_description('light_cover'))
        self._lbl_mode_desc.setStyleSheet("color:#666; font-size:11px;")
        self._lbl_mode_desc.setWordWrap(True)
        fp.addWidget(self._lbl_mode_desc)
        
        # 高级选项
        row_adv = QHBoxLayout()
        row_adv.addWidget(QLabel("最大裁剪比例(%):"))
        self._sp_max_crop = QDoubleSpinBox()
        self._sp_max_crop.setRange(1, 50)
        self._sp_max_crop.setValue(15)
        self._sp_max_crop.setSuffix(" %")
        row_adv.addWidget(self._sp_max_crop)
        
        row_adv.addWidget(QLabel("背景色:"))
        self._btn_bg_color = QPushButton()
        self._btn_bg_color.setStyleSheet("background-color: white; border: 1px solid #ccc; padding: 4px;")
        self._btn_bg_color.setFixedWidth(60)
        self._btn_bg_color.clicked.connect(self._pick_bg_color)
        row_adv.addWidget(self._btn_bg_color)
        
        row_adv.addStretch(1)
        fp.addLayout(row_adv)
        
        lay.addWidget(gb_param)
        
        # ===== 4) 圆角设置 =====
        gb_corner = QGroupBox("4. 圆角设置（厘米）")
        fc = QVBoxLayout(gb_corner)
        
        row_quick = QHBoxLayout()
        row_quick.addWidget(QLabel("快速设置:"))
        self._sp_quick_r = QDoubleSpinBox(); self._sp_quick_r.setRange(0, 50); self._sp_quick_r.setValue(2); self._sp_quick_r.setDecimals(1)
        self._sp_quick_r.setSuffix(" cm")
        row_quick.addWidget(self._sp_quick_r)
        
        btn_all = QPushButton("四角相同")
        btn_all.clicked.connect(self._apply_to_all)
        btn_tl_br = QPushButton("左上+右下")
        btn_tl_br.clicked.connect(lambda: self._apply_to_corners(['tl', 'br']))
        btn_tr_bl = QPushButton("右上+左下")
        btn_tr_bl.clicked.connect(lambda: self._apply_to_corners(['tr', 'bl']))
        row_quick.addWidget(btn_all)
        row_quick.addWidget(btn_tl_br)
        row_quick.addWidget(btn_tr_bl)
        row_quick.addStretch(1)
        fc.addLayout(row_quick)
        
        grid_corner = QGridLayout()
        self._sp_corners = {}
        for i, (key, name) in enumerate([('tl', '左上角'), ('tr', '右上角'), ('bl', '左下角'), ('br', '右下角')]):
            grid_corner.addWidget(QLabel(name), i // 2, (i % 2) * 3)
            sp = QDoubleSpinBox(); sp.setRange(0, 50); sp.setValue(0); sp.setDecimals(1); sp.setSuffix(" cm")
            grid_corner.addWidget(sp, i // 2, (i % 2) * 3 + 1)
            self._sp_corners[key] = sp
        
        fc.addLayout(grid_corner)
        lay.addWidget(gb_corner)
        
        # ===== 5) 输出命名预览 =====
        gb_name = QGroupBox("5. 输出文件名预览")
        fn = QVBoxLayout(gb_name)
        
        self._lbl_name_preview = QLabel("（选择源图并设置参数后自动生成）")
        self._lbl_name_preview.setStyleSheet("color:#4a90d9; padding:4px; background:#f0f4f8; border-radius:3px;")
        self._lbl_name_preview.setWordWrap(True)
        fn.addWidget(self._lbl_name_preview)
        
        row_name_opts = QHBoxLayout()
        self._ck_auto_name = QCheckBox("自动命名")
        self._ck_auto_name.setChecked(True)
        row_name_opts.addWidget(self._ck_auto_name)
        
        row_name_opts.addWidget(QLabel("手动命名:"))
        self._ed_custom_name = QLineEdit()
        self._ed_custom_name.setPlaceholderText("覆盖自动命名…")
        row_name_opts.addWidget(self._ed_custom_name, 1)
        fn.addLayout(row_name_opts)
        
        lay.addWidget(gb_name)
        
        # ===== 6) 操作按钮 =====
        row_actions = QHBoxLayout()
        btn_preview = QPushButton("生成预览")
        btn_preview.setStyleSheet("padding:10px; font-weight:bold;")
        btn_export = QPushButton("导出 JPG")
        btn_export.setStyleSheet("padding:10px; background:#27ae60; color:white; font-weight:bold;")
        row_actions.addWidget(btn_preview, 1)
        row_actions.addWidget(btn_export, 1)
        lay.addLayout(row_actions)
        
        btn_preview.clicked.connect(self._generate_preview)
        btn_export.clicked.connect(self._export)
        
        # 信号连接
        self._cb_layout.currentIndexChanged.connect(self._update_name_preview)
        self._sp_w.valueChanged.connect(self._update_name_preview)
        self._sp_h.valueChanged.connect(self._update_name_preview)
        for sp in self._sp_corners.values():
            sp.valueChanged.connect(self._update_name_preview)
        self._ed_product.textChanged.connect(self._update_name_preview)
        self._ck_auto_name.stateChanged.connect(self._update_name_preview)
        self._ed_custom_name.textChanged.connect(self._update_name_preview)
        self._cb_mode.currentIndexChanged.connect(self._on_mode_changed)
        
        self._update_name_preview()
    
    # ===== 事件处理 =====
    
    def _on_mode_changed(self):
        """裁剪模式改变时更新说明"""
        mode = self._cb_mode.currentData()
        self._lbl_mode_desc.setText(get_mode_description(mode))
    
    def _pick_bg_color(self):
        """选择背景色"""
        color = QColorDialog.getColor(QColor(*self._bg_color), self, "选择背景色")
        if color.isValid():
            self._bg_color = (color.red(), color.green(), color.blue())
            self._btn_bg_color.setStyleSheet(
                f"background-color: rgb{self._bg_color}; border: 1px solid #ccc; padding: 4px;"
            )
    
    def _pick_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择源图", "",
            "图片文件 (*.jpg *.jpeg *.png *.psd *.psb)"
        )
        if path:
            self._src_path = path
            self._ed_file.setText(path)
            try:
                self._src_info = get_image_info(path)
                size = self._src_info.get('size_cm', None)
                size_str = f"{size[0]}×{size[1]}cm" if size else f"{self._src_info['width_px']}×{self._src_info['height_px']}px"
                self._lbl_src_info.setText(
                    f"源图: {size_str} | DPI: {self._src_info['dpi'][0]} | 模式: {self._src_info['mode']}"
                )
            except Exception as e:
                self._lbl_src_info.setText(f"读取失败: {e}")
    
    def _auto_parse(self):
        """从文件名自动识别参数"""
        if not self._src_path:
            QMessageBox.information(self, "提示", "请先选择源图")
            return
        
        filename = os.path.basename(self._src_path)
        try:
            parsed = parse_filename(filename)
            
            if parsed.product_name:
                self._ed_product.setText(parsed.product_name)
            
            if parsed.layout:
                idx = self._cb_layout.findData(parsed.layout)
                if idx >= 0:
                    self._cb_layout.setCurrentIndex(idx)
            
            if parsed.width_cm > 0 and parsed.height_cm > 0:
                self._sp_w.setValue(parsed.width_cm)
                self._sp_h.setValue(parsed.height_cm)
            
            if parsed.corners:
                for key in ('tl', 'tr', 'bl', 'br'):
                    if key in parsed.corners:
                        self._sp_corners[key].setValue(parsed.corners[key])
            
            QMessageBox.information(
                self, "自动识别成功",
                f"已从文件名识别参数：\n\n"
                f"产品: {parsed.product_name}\n"
                f"尺寸: {parsed.width_cm}×{parsed.height_cm}cm\n"
                f"布局: {parsed.layout}\n"
                f"圆角: {self._format_corners_for_msg(parsed.corners)}"
            )
        except Exception as e:
            QMessageBox.critical(self, "识别失败", str(e))
    
    def _apply_to_all(self):
        r = self._sp_quick_r.value()
        for sp in self._sp_corners.values():
            sp.setValue(r)
    
    def _apply_to_corners(self, keys: list[str]):
        r = self._sp_quick_r.value()
        for key in keys:
            if key in self._sp_corners:
                self._sp_corners[key].setValue(r)
    
    def _format_corners_for_msg(self, corners: dict | None) -> str:
        if not corners:
            return "无"
        parts = []
        for key in ('tl', 'tr', 'bl', 'br'):
            if key in corners and corners[key] > 0:
                parts.append(f"{get_corner_name(key)}: {corners[key]}cm")
        return "、".join(parts) if parts else "无"
    
    def _get_corners_config(self) -> dict[str, float]:
        return {key: sp.value() for key, sp in self._sp_corners.items()}
    
    def _get_layout(self) -> str:
        return self._cb_layout.currentData()
    
    def _update_name_preview(self):
        """更新文件名预览"""
        if self._ck_auto_name.isChecked():
            product = self._ed_product.text().strip() or "产品"
            layout = self._get_layout()
            w = self._sp_w.value()
            h = self._sp_h.value()
            corners = self._get_corners_config()
            
            # 构建规格部分
            spec_parts = [f"{layout}{w}x{h}cm"]
            
            # 圆角描述
            non_zero = {k: v for k, v in corners.items() if v > 0}
            if non_zero:
                unique_radii = set(non_zero.values())
                if len(unique_radii) == 1 and len(non_zero) == 4:
                    r = list(unique_radii)[0]
                    spec_parts.append(f"四角圆角半径{r}cm")
                else:
                    for k in ('tl', 'tr', 'bl', 'br'):
                        if k in non_zero and non_zero[k] > 0:
                            spec_parts.append(f"{get_corner_name(k)}圆角半径{non_zero[k]}cm")
            
            spec_str = ''.join(spec_parts)
            name = f"{product};{spec_str}.jpg"
        else:
            name = self._ed_custom_name.text().strip() or "output.jpg"
        
        self._lbl_name_preview.setText(name)
    
    def _build_crop_config(self) -> CropConfig:
        """构建裁剪配置"""
        corners = self._get_corners_config()
        max_crop_ratio = self._sp_max_crop.value() / 100.0  # 转为比例
        return CropConfig(
            src_path=self._src_path,
            target_w_cm=self._sp_w.value(),
            target_h_cm=self._sp_h.value(),
            corners=corners,
            mode=self._cb_mode.currentData(),
            dpi=self._sp_dpi.value(),
            bg_color=self._bg_color,
            max_crop_ratio=max_crop_ratio,
        )
    
    def _generate_preview(self):
        """生成预览（不保存文件）"""
        if not self._src_path:
            QMessageBox.warning(self, "提示", "请先选择源图")
            return
        
        try:
            config = self._build_crop_config()
            config.output_path = ""
            
            result = crop_image(config)
            self._last_result = result
            
            self.image_cropped.emit(result)
            
            QMessageBox.information(
                self, "预览生成",
                f"裁剪完成！\n\n"
                f"输出尺寸: {result.width}×{result.height} px\n"
                f"目标: {self._sp_w.value()}×{self._sp_h.value()}cm @ {self._sp_dpi.value()} DPI\n"
                f"模式: {get_mode_description(self._cb_mode.currentData())}"
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "裁剪失败", str(e))
    
    def _export(self):
        """导出 JPG"""
        if not self._src_path:
            QMessageBox.warning(self, "提示", "请先选择源图")
            return
        
        if self._ck_auto_name.isChecked():
            output_name = self._lbl_name_preview.text()
        else:
            output_name = self._ed_custom_name.text().strip() or "output.jpg"
        
        default_dir = os.path.dirname(self._src_path) if self._src_path else ""
        default_path = os.path.join(default_dir, output_name)
        
        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 JPG", default_path,
            "JPEG 图片 (*.jpg *.jpeg)"
        )
        if not path:
            return
        
        try:
            config = self._build_crop_config()
            config.output_path = path
            
            crop_image(config)
            self._last_result = None
            
            QMessageBox.information(
                self, "导出成功",
                f"已导出:\n{path}\n\n"
                f"尺寸: {config.target_w_cm}×{config.target_h_cm}cm @ {config.dpi} DPI"
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "导出失败", str(e))
    
    def get_last_result(self) -> Image.Image | None:
        return self._last_result
    
    def set_source_path(self, path: str):
        self._src_path = path
        self._ed_file.setText(path)
        try:
            self._src_info = get_image_info(path)
        except Exception:
            pass