# -*- coding: utf-8 -*-
"""
package.py
SmartShapeCrop 打包脚本（Python 版）

功能：
    将 main.py 打包为 Windows 桌面可执行文件：
      - exe 文件名：智能裁剪设计器
      - exe 文件图标：images/SmartShapeCrop.ico
      - 运行时窗口图标：images/logo.png（由 main.py 的 set_app_icon 加载）

使用方式：
    python package.py            # 默认打包（one-folder 模式）
    python package.py --onefile  # 单文件模式（生成单个 exe）
    python package.py --clean    # 清理旧构建后打包

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
# 项目根目录（package.py 所在目录）
PROJECT_ROOT = Path(__file__).resolve().parent

# 入口脚本
ENTRY_SCRIPT = PROJECT_ROOT / "main.py"

# exe 名称（桌面显示名）
APP_NAME = "智能裁剪设计器"

# exe 文件图标（桌面 .exe 文件本身的图标）
ICON_FILE = PROJECT_ROOT / "images" / "SmartShapeCrop.ico"

# 运行时窗口图标资源（打包进 exe，由 main.py 的 resource_path 加载）
LOGO_FILE = PROJECT_ROOT / "images" / "logo.png"

# 输出目录
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"


def _print_step(idx: int, total: int, msg: str) -> None:
    print(f"\n[{idx}/{total}] {msg}")


def _fail(msg: str) -> None:
    print(f"\n[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def parse_args() -> dict:
    """解析命令行参数"""
    args = {
        "onefile": False,
        "clean": False,
    }
    for arg in sys.argv[1:]:
        if arg == "--onefile":
            args["onefile"] = True
        elif arg == "--clean":
            args["clean"] = True
        elif arg in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            _fail(f"未知参数: {arg}（使用 --help 查看帮助）")
    return args


def check_resources() -> None:
    """检查打包所需的资源文件是否存在"""
    _print_step(1, 4, "检查资源文件...")

    if not ENTRY_SCRIPT.is_file():
        _fail(f"缺少入口脚本: {ENTRY_SCRIPT}")

    if not ICON_FILE.is_file():
        _fail(f"缺少 exe 图标: {ICON_FILE}（桌面 .exe 图标必须存在）")

    if not LOGO_FILE.is_file():
        _fail(f"缺少窗口图标: {LOGO_FILE}（运行时窗口图标必须存在）")

    print(f"      OK: 入口脚本  {ENTRY_SCRIPT.name}")
    print(f"      OK: exe 图标 {ICON_FILE.name}")
    print(f"      OK: 窗口图标 {LOGO_FILE.name}")


def ensure_pyinstaller() -> None:
    """确保 PyInstaller 已安装"""
    _print_step(2, 4, "检查 PyInstaller...")

    result = subprocess.run(
        [sys.executable, "-m", "pip", "show", "pyinstaller"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("      PyInstaller 未安装，正在安装...")
        install_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            capture_output=True,
            text=True,
        )
        if install_result.returncode != 0:
            print(install_result.stdout)
            print(install_result.stderr, file=sys.stderr)
            _fail("PyInstaller 安装失败，请手动执行: pip install pyinstaller")
    print("      OK: PyInstaller 已就绪")


def clean_build() -> None:
    """清理旧的构建产物"""
    if DIST_DIR.exists():
        print(f"      清理 {DIST_DIR}")
        shutil.rmtree(DIST_DIR, ignore_errors=True)
    if BUILD_DIR.exists():
        print(f"      清理 {BUILD_DIR}")
        shutil.rmtree(BUILD_DIR, ignore_errors=True)


def build_exe(onefile: bool) -> None:
    """调用 PyInstaller 执行打包"""
    _print_step(3, 4, "开始打包（PyInstaller）...")
    print(f"      exe 名称: {APP_NAME}")
    print(f"      exe 图标: {ICON_FILE.name}")
    print(f"      打包模式: {'单文件 (onefile)' if onefile else '目录 (one-folder)'}")
    print("      注意：首次打包较慢（约 1-3 分钟），请耐心等待...\n")

    # 构建 PyInstaller 命令
    # Windows 下 --add-data 的分隔符为 ';'
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",           # 自动覆盖 dist/build，无需交互
        "--clean",               # 清除旧的 build 缓存
        "--name", APP_NAME,      # exe 文件名
        "--icon", str(ICON_FILE),  # exe 文件图标
        "--windowed",            # GUI 应用，不显示控制台窗口
        "--add-data", f"{LOGO_FILE};images",  # 运行时窗口图标资源
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd.append(str(ENTRY_SCRIPT))

    # 切换到项目根目录执行（保证相对路径正确）
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

    if result.returncode != 0:
        _fail("打包失败，请检查上方错误信息")


def show_result(onefile: bool) -> None:
    """显示打包结果"""
    _print_step(4, 4, "打包完成")

    if onefile:
        exe_path = DIST_DIR / f"{APP_NAME}.exe"
    else:
        exe_path = DIST_DIR / APP_NAME / f"{APP_NAME}.exe"

    print("\n" + "=" * 60)
    print("  打包成功！")
    print("=" * 60)
    print(f"  输出目录 : {DIST_DIR}")
    print(f"  可执行文件: {exe_path}")
    print("=" * 60)
    print("\n  说明：")
    print(f"    - exe 文件名        : {APP_NAME}")
    print(f"    - exe 文件图标      : {ICON_FILE.name}")
    print(f"    - 运行时窗口图标    : {LOGO_FILE.name}")
    print("    - 发送到桌面: 右键 exe → 发送到 → 桌面快捷方式")
    print()


def main() -> None:
    args = parse_args()

    print("=" * 60)
    print(f"  SmartShapeCrop 打包工具")
    print(f"  exe 名称: {APP_NAME}")
    print(f"  exe 图标: {ICON_FILE.name}")
    print("=" * 60)

    check_resources()
    ensure_pyinstaller()

    if args["clean"]:
        _print_step(3, 5, "清理旧构建产物...")
        clean_build()
        _print_step(4, 5, "开始打包（PyInstaller）...")
        build_exe(args["onefile"])
        _print_step(5, 5, "打包完成")
        show_result(args["onefile"])
    else:
        build_exe(args["onefile"])
        show_result(args["onefile"])


if __name__ == "__main__":
    main()
