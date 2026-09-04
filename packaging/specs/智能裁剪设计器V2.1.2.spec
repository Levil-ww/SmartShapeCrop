# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = [('F:/SmartShapeCrop/images/logo.png', 'images'), ('D:/Programs/Tesseract-OCR', 'tesseract')]
binaries = []
hiddenimports = ['PyQt5.sip', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets', 'PyQt5.QtPrintSupport', 'PIL', 'PIL.Image', 'PIL.ImageTk', 'numpy', 'cv2', 'pytesseract', 'psd_tools', 'psd_tools.api', 'psd_tools.constants', 'core', 'core.config', 'core.geometry', 'core.image_cropper', 'core.image_ops', 'core.log_setup', 'core.app_settings', 'core.artifact_cleanup', 'core.compat', 'core.image_cropper_border', 'core.image_cropper_mask', 'core.pool_designer', 'core.pool_designer.sketch_parser', 'core.pool_designer.sketch_parser_base', 'core.pool_designer.sketch_parser_cache', 'core.pool_designer.sketch_parser_margins', 'core.pool_designer.sketch_parser_multihole', 'core.pool_designer.sketch_parser_numbers', 'core.pool_designer.sketch_parser_vision', 'core.pool_designer.lshape_sketch_parser', 'core.corner', 'core.corner.sector_render', 'core.corner.detection', 'core.corner.algorithm', 'core.parser', 'core.parser.name_parser', 'core.parser.template_matcher', 'core.psd', 'core.psd.loader', 'gui', 'gui.canvas_widget', 'gui.property_panel', 'gui.cropper_panel', 'gui.property_panel_dialogs', 'gui.property_panel_generate', 'gui.property_panel_layers', 'gui.property_panel_poolbox', 'gui.property_panel_widgets', 'gui.property_panel_workers']
datas += copy_metadata('Pillow')
datas += copy_metadata('numpy')
datas += copy_metadata('pytesseract')
datas += copy_metadata('psd-tools')
hiddenimports += collect_submodules('PyQt5')
hiddenimports += collect_submodules('psd_tools')
tmp_ret = collect_all('PyQt5.Qt5')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pytesseract')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['F:/SmartShapeCrop/main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='智能裁剪设计器V2.1.2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['F:/SmartShapeCrop/images/SmartShapeCrop.ico'],
)
