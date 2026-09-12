# -*- coding: utf-8 -*-
"""
package.py
SmartShapeCrop 打包脚本（Python 版）

功能：
    将 main.py 打包为 Windows 桌面可执行文件（单文件模式，双击即可运行）：
      - exe 文件名：智能裁剪设计器
      - exe 文件图标：images/SmartShapeCrop.ico
      - 运行时窗口图标：images/logo.png（由 main.py 的 set_app_icon 加载）

使用方式：
    python package.py                    # 单文件模式（默认，生成单个 exe，双击即可运行）
    python package.py --onedir           # 目录模式（更稳定，若 onefile 有问题可用）
    python package.py --debug            # 调试模式（显示控制台窗口，便于排查错误）
    python package.py --clean            # 清理旧构建后打包

依赖：
    PyInstaller（脚本会自动检测并尝试安装）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# ==================== 配置 ====================
PROJECT_ROOT = Path(__file__).resolve().parent
ENTRY_SCRIPT = PROJECT_ROOT / "main.py"
APP_NAME = "智能裁剪设计器"
ICON_FILE = PROJECT_ROOT / "images" / "SmartShapeCrop.ico"
LOGO_FILE = PROJECT_ROOT / "images" / "logo.png"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"

HIDDEN_IMPORTS = [
    "PyQt5.sip",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
    "PyQt5.QtPrintSupport",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "numpy",
    "cv2",
]


def _print_step(idx: int, total: int, msg: str) -> None:
    print(f"\n[{idx}/{total}] {msg}")


def _fail(msg: str) -> None:
    print(f"\n[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def parse_args() -> dict:
    args = {
        "mode": "onefile",   # onefile | onedir（默认单文件）
        "debug": False,
        "clean": False,
    }
    for arg in sys.argv[1:]:
        if arg == "--onefile":
            args["mode"] = "onefile"
        elif arg == "--onedir":
            args["mode"] = "onedir"
        elif arg == "--debug":
            args["debug"] = True
        elif arg == "--clean":
            args["clean"] = True
        elif arg in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            _fail(f"未知参数: {arg}（使用 --help 查看帮助）")
    return args


def check_resources() -> None:
    _print_step(1, 5, "检查资源文件...")
    if not ENTRY_SCRIPT.is_file():
        _fail(f"缺少入口脚本: {ENTRY_SCRIPT}")
    if not ICON_FILE.is_file():
        _fail(f"缺少 exe 图标: {ICON_FILE}")
    if not LOGO_FILE.is_file():
        _fail(f"缺少窗口图标: {LOGO_FILE}")
    print(f"      OK: 入口脚本  {ENTRY_SCRIPT.name}")
    print(f"      OK: exe 图标 {ICON_FILE.name}")
    print(f"      OK: 窗口图标 {LOGO_FILE.name}")


def _get_pyinstaller_version() -> tuple[int, ...] | None:
    try:
        import PyInstaller
        parts = PyInstaller.__version__.split(".")
        return tuple(int(p) for p in parts[:3])
    except Exception:
        return None


def ensure_pyinstaller() -> None:
    _print_step(2, 5, "检查 PyInstaller...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "show", "pyinstaller"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("      PyInstaller 未安装，正在安装...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(r.stdout); print(r.stderr, file=sys.stderr)
            _fail("PyInstaller 安装失败，请手动执行: pip install pyinstaller")
    else:
        pyinst_ver = _get_pyinstaller_version()
        if pyinst_ver and pyinst_ver < (6, 0):
            print(f"      PyInstaller {'.'.join(str(v) for v in pyinst_ver)} 版本较旧，正在升级...")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                print("      [WARN] 升级失败，继续使用当前版本")
            else:
                print("      OK: PyInstaller 已升级")
    print("      OK: PyInstaller 已就绪")


def clean_build() -> None:
    if DIST_DIR.exists():
        print(f"      清理 {DIST_DIR}")
        shutil.rmtree(DIST_DIR, ignore_errors=True)
    if BUILD_DIR.exists():
        print(f"      清理 {BUILD_DIR}")
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    for spec in PROJECT_ROOT.glob("*.spec"):
        print(f"      清理 {spec.name}")
        spec.unlink(missing_ok=True)


def _build_cmd(mode: str, debug: bool) -> list[str]:
    """构建 PyInstaller 命令行参数"""
    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", APP_NAME,
        "--icon", str(ICON_FILE),
        "--add-data", f"{str(LOGO_FILE)};images",
        "--collect-all", "PyQt5.Qt5",
        "--collect-submodules", "PyQt5",
        "--copy-metadata", "Pillow",
        "--copy-metadata", "numpy",
    ]
    for imp in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", imp])
    if debug:
        cmd.append("--console")
    else:
        cmd.append("--windowed")

    if mode == "onefile":
        # onefile 模式：运行时自动解压到系统临时目录 %TEMP%\_MEIxxxxxx\
        # 不需要额外参数，PyInstaller 始终如此行为
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd.append(str(ENTRY_SCRIPT))
    return cmd


def _run_build(mode: str, debug: bool) -> bool:
    cmd = _build_cmd(mode, debug)
    print(f"      命令: {' '.join(cmd)}")
    print("      注意：首次打包较慢（约 2-5 分钟），请耐心等待...\n")

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode == 0


def build_exe(mode: str, debug: bool) -> None:
    _print_step(3, 5, "开始打包（PyInstaller）...")
    py_ver = sys.version.split()[0]
    pyinst_ver = _get_pyinstaller_version()
    print(f"      Python 版本: {py_ver}")
    print(f"      PyInstaller : {'.'.join(str(v) for v in pyinst_ver) if pyinst_ver else '待安装'}")
    print(f"      exe 名称: {APP_NAME}")
    print(f"      exe 图标: {ICON_FILE.name}")
    print(f"      打包模式: {'单文件 (onefile, 默认)' if mode == 'onefile' else '目录 (onedir)'}")
    print(f"      调试模式: {'是（显示控制台）' if debug else '否（无控制台）'}")

    success = _run_build(mode, debug)
    if not success and mode == "onefile":
        print("\n      [!] onefile 打包失败，自动回退到 onedir 模式...")
        success = _run_build("onedir", debug)
        if success:
            print("\n      [√] onedir 模式打包成功！")
    if not success:
        _fail("打包失败，请检查上方错误信息")


def show_result(mode: str) -> None:
    _print_step(5, 5, "打包完成")
    if mode == "onefile":
        exe_path = DIST_DIR / f"{APP_NAME}.exe"
    else:
        exe_path = DIST_DIR / APP_NAME / f"{APP_NAME}.exe"

    print("\n" + "=" * 60)
    print("  打包成功！")
    print("=" * 60)
    print(f"  输出目录 : {DIST_DIR}")
    print(f"  可执行文件: {exe_path}")
    print("=" * 60)

    if mode == "onefile":
        print("\n  使用说明（单文件模式）:")
        print(f"    直接双击 {APP_NAME}.exe 即可运行，无需其他文件。")
        print("    首次启动需要解压临时文件（到系统临时目录），可能需要 5-15 秒。")
        print()
        print("    重要提示：")
        print("      1. 拷贝到其他机器之前，先在本机双击测试能否正常运行")
        print("      2. 若仍打不开，在 exe 同目录会生成 crash.log，把该日志发给开发者")
    else:
        folder = DIST_DIR / APP_NAME
        print(f"\n  使用说明（目录模式）:")
        print(f"    1. 保持整个文件夹 ({folder.name}) 完整，不要单独移动 exe")
        print(f"    2. 双击文件夹内的 {APP_NAME}.exe 即可运行")
        print(f"    3. 如需分发：右键整个文件夹 → 发送到 → 压缩(zipped)文件夹")
    print()


def main() -> None:
    args = parse_args()
    print("=" * 60)
    print(f"  SmartShapeCrop 打包工具")
    print(f"  exe 名称: {APP_NAME}")
    print(f"  exe 图标: {ICON_FILE.name}")
    print(f"  Python:   {sys.version.split()[0]}")
    print("=" * 60)

    check_resources()
    ensure_pyinstaller()

    if args["clean"]:
        _print_step(3, 5, "清理旧构建产物...")
        clean_build()

    build_exe(args["mode"], args["debug"])
    show_result(args["mode"])


if __name__ == "__main__":
    main()
