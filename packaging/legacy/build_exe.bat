@echo off
chcp 65001 >nul
REM ================================================
REM SmartShapeCrop 打包脚本（build_exe.bat）
REM 双击运行即可生成桌面可执行文件
REM ================================================
REM 打包图标：
REM   - exe 文件本身图标：images\SmartShapeCrop.ico （通过 SmartShapeCrop.spec 的 EXE(icon=...)）
REM   - 运行时窗口图标：images\logo.png           （通过 main.py 的 set_app_icon()）
REM ================================================

setlocal

cd /d "%~dp0"

echo.
echo [1/3] 检查图标资源...
if not exist "images\SmartShapeCrop.ico" (
    echo [ERROR] 缺少 images\SmartShapeCrop.ico，无法设置 exe 图标
    pause
    exit /b 1
)
if not exist "images\logo.png" (
    echo [ERROR] 缺少 images\logo.png，无法设置窗口图标
    pause
    exit /b 1
)
echo       OK: SmartShapeCrop.ico + logo.png 都存在

echo.
echo [2/3] 检查 PyInstaller...
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo       PyInstaller 未安装，正在安装...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller 安装失败，请手动执行: pip install pyinstaller
        pause
        exit /b 1
    )
)
echo       OK: PyInstaller 已就绪

echo.
echo [3/3] 开始打包（使用 SmartShapeCrop.spec）...
echo       注意：首次打包会较慢（约 1-3 分钟），请耐心等待...
echo.

REM --noconfirm: 自动覆盖 dist/build，无需交互
REM --clean: 清除旧的 build 缓存，避免旧资源残留
python -m PyInstaller --noconfirm --clean SmartShapeCrop.spec

if errorlevel 1 (
    echo.
    echo [ERROR] 打包失败，请检查上方错误信息
    pause
    exit /b 1
)

echo.
echo ================================================
echo  打包成功！
echo  输出目录: %~dp0dist\SmartShapeCrop\
echo  可执行文件: %~dp0dist\SmartShapeCrop\SmartShapeCrop.exe
echo ================================================
echo.
echo  说明：
echo    - SmartShapeCrop.exe 文件本身的图标: images\SmartShapeCrop.ico
echo    - 运行时窗口标题栏/任务栏图标: images\logo.png
echo    - 如需发送桌面快捷方式，右键 exe → 发送到 → 桌面快捷方式
echo.

pause
endlocal
