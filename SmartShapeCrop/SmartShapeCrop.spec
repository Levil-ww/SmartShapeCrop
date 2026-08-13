# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（SmartShapeCrop.spec）
生成：桌面 exe 文件，嵌入图标 + 运行时资源。

两条图标链路：
  1) EXE 文件本身图标（Windows 资源管理器/桌面显示）  → EXE(icon='images/SmartShapeCrop.ico')
  2) 运行时窗口左上角/任务栏图标                     → datas 收集 images/logo.png → main.py 中 QApplication.setWindowIcon()

使用方式：
    pyinstaller SmartShapeCrop.spec
    # 或双击 build_exe.bat
"""

block_cipher = None

# 需要打包到 exe 内部的资源文件（运行时通过 resource_path 访问）
# 格式: [(源路径, 目标目录), ...]
datas = [
    # 窗口图标（运行时加载显示在标题栏和任务栏）
    ('images/logo.png', 'images'),
    # 模板目录配置/资源如有需要，可继续在此追加
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SmartShapeCrop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 不显示控制台窗口（GUI 应用）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # [EXE 图标] 桌面 .exe 文件本身的图标（Windows 资源管理器中显示）
    icon='images/SmartShapeCrop.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SmartShapeCrop',
)
