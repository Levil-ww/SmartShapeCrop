# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = [('F:/SmartShapeCrop/images/logo.png', 'images')]

# [Q-04] Tesseract 内嵌目录改为读取 TESSERACT_PATH 环境变量（可指向安装目录或 tesseract.exe）。
# 未设置时回退历史默认目录；两者均无效则不内嵌（运行时由 core.config.PathResolver 自动定位/降级），
# 不再因写死路径导致换机打包失败。
def _tess_dir_valid(d):
    return (os.path.isfile(os.path.join(d, 'tesseract.exe'))
            or os.path.isfile(os.path.join(d, 'tesseract'))) \
        and os.path.isdir(os.path.join(d, 'tessdata'))


_tess_env = os.environ.get('TESSERACT_PATH', '').strip().strip('"')
if _tess_env and os.path.isfile(_tess_env):
    _tess_env = os.path.dirname(_tess_env)
_tess_dir = next(
    (d for d in (_tess_env, r'D:/Programs/Tesseract-OCR') if d and _tess_dir_valid(d)),
    None,
)
if _tess_dir:
    print(f'[spec] 内嵌 Tesseract: {_tess_dir} -> tesseract/')
    datas.append((_tess_dir, 'tesseract'))
else:
    print('[spec] 未找到有效 Tesseract 目录（可设置 TESSERACT_PATH 环境变量后重试），本次打包不内嵌 OCR')

binaries = []
hiddenimports = ['PyQt5.sip', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets', 'PyQt5.QtPrintSupport', 'PIL', 'PIL.Image', 'PIL.ImageTk', 'numpy', 'cv2', 'pytesseract', 'psd_tools', 'psd_tools.api', 'psd_tools.constants', 'core', 'core.config', 'core.geometry', 'core.image_cropper', 'core.image_ops', 'core.log_setup', 'core.app_settings', 'core.artifact_cleanup', 'core.compat', 'core.image_cropper_border', 'core.image_cropper_mask', 'core.lshape_border', 'core.lshape_border_route', 'core.corner', 'core.corner.algorithm', 'core.corner.detection', 'core.corner.sector_render', 'services', 'services.parser', 'services.parser.name_parser', 'services.parser.template_matcher', 'services.sketch_parser', 'services.sketch_parser.sketch_parser', 'services.sketch_parser.sketch_parser_base', 'services.sketch_parser.sketch_parser_cache', 'services.sketch_parser.sketch_parser_margins', 'services.sketch_parser.sketch_parser_multihole', 'services.sketch_parser.sketch_parser_numbers', 'services.sketch_parser.sketch_parser_vision', 'services.sketch_parser.lshape_sketch_parser', 'services.psd', 'services.psd.loader', 'core.parser', 'core.pool_designer', 'core.psd', 'gui', 'gui.canvas_widget', 'gui.property_panel', 'gui.cropper_panel', 'gui.property_panel_dialogs', 'gui.property_panel_generate', 'gui.property_panel_layers', 'gui.property_panel_poolbox', 'gui.property_panel_widgets', 'gui.property_panel_workers', 'gui.lshape_panel', 'gui.lshape_panel_bridge', 'workers', 'workers.property_panel_workers', 'workers.cropper_workers', 'workers.canvas_workers', 'models', 'models.design_model']
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
    name='智能裁剪设计器V2.2.3',
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
