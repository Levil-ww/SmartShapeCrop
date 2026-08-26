# -*- coding: utf-8 -*-
"""
packageV2.0.py
SmartShapeCrop V2.0 打包脚本（Python 版）

功能：
    将 main.py 打包为 Windows 桌面可执行文件（单文件模式，双击即可运行）：
      - exe 文件名：智能裁剪设计器V2.0
      - exe 文件图标：images/SmartShapeCrop.ico
      - 运行时窗口图标：images/logo.png（由 main.py 的 set_app_icon 加载）

V2.0 相比 V1 的新增功能：
      - 水池设计器草图识别（OCR + 方向标签 + 几何自洽）
      - 智能形状裁剪与圆角处理（单步扇形切割、多层边框保护）
      - 内孔圆角 1:1 映射与内孔边框绘制
      - GUI 识别状态与预览显示

使用方式：
    python packageV2.0.py                    # 单文件模式（默认，生成单个 exe，双击即可运行）
    python packageV2.0.py --onedir           # 目录模式（更稳定，若 onefile 有问题可用）
    python packageV2.0.py --debug            # 调试模式（显示控制台窗口，便于排查错误）
    python packageV2.0.py --clean            # 清理旧构建后打包
    python packageV2.0.py --with-tesseract   # 自动把本机 Tesseract-OCR 复制到 dist（用户免安装）

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
APP_NAME = "智能裁剪设计器V2.0"
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
    # V2.0 新增：水池设计器草图识别依赖
    "pytesseract",
    "psd_tools",
    "psd_tools.api",
    "psd_tools.constants",
    # 项目内部模块（确保打包时被正确收集）
    "core",
    "core.pool_designer",
    "core.pool_designer.sketch_parser",
    "core.corner",
    "core.corner.sector_render",
    "core.corner.detection",
    "core.corner.algorithm",
    "core.parser",
    "core.parser.name_parser",
    "core.parser.template_matcher",
    "core.psd",
    "core.psd.loader",
    "gui",
    "gui.canvas_widget",
    "gui.property_panel",
    "gui.cropper_panel",
]

# V2.0 新增：需要复制元数据的包（运行时依赖包的元数据）
COPY_METADATA_PACKAGES = [
    "Pillow",
    "numpy",
    "pytesseract",
    "psd-tools",
]

# V2.0 新增：需要 collect-all 的包（包含动态加载的子模块/资源）
COLLECT_ALL_PACKAGES = [
    "PyQt5.Qt5",
    "pytesseract",
]

# V2.0 新增：需要 collect-submodules 的包
COLLECT_SUBMODULES_PACKAGES = [
    "PyQt5",
    "psd_tools",
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
        "with_tesseract": False,  # V2.0：打包后自动把本机 Tesseract 便携版复制到 dist 目录
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
        elif arg == "--with-tesseract":
            args["with_tesseract"] = True
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
    # 仅清理本脚本生成的 .spec，避免误删其他手工维护的 .spec
    v2_spec = PROJECT_ROOT / f"{APP_NAME}.spec"
    if v2_spec.exists():
        print(f"      清理 {v2_spec.name}")
        v2_spec.unlink(missing_ok=True)


def _build_cmd(mode: str, debug: bool) -> list[str]:
    """构建 PyInstaller 命令行参数"""
    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", APP_NAME,
        "--icon", str(ICON_FILE),
        "--add-data", f"{str(LOGO_FILE)};images",
    ]

    # collect-all：收集包的全部子模块、二进制和数据文件
    for pkg in COLLECT_ALL_PACKAGES:
        cmd.extend(["--collect-all", pkg])

    # collect-submodules：仅收集子模块
    for pkg in COLLECT_SUBMODULES_PACKAGES:
        cmd.extend(["--collect-submodules", pkg])

    # copy-metadata：复制包的元数据（部分运行时依赖）
    for pkg in COPY_METADATA_PACKAGES:
        cmd.extend(["--copy-metadata", pkg])

    # hidden-import：显式声明隐藏导入
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


def _find_tesseract_install() -> Path | None:
    """查找本机已安装的 Tesseract-OCR 目录（包含 tesseract.exe + tessdata 子文件夹）。"""
    # 使用 PathResolver 跨平台查找
    try:
        from core.config import PathResolver
        exe_path, tessdata_path = PathResolver.find_tesseract()
        if exe_path and tessdata_path and tessdata_path != 'system':
            return Path(os.path.dirname(exe_path))
        elif exe_path:
            return Path(os.path.dirname(exe_path))
    except Exception:
        pass
    
    # Fallback：保留原有搜索逻辑作为兜底
    candidates = [
        r"C:\Program Files\Tesseract-OCR",
        r"C:\Program Files (x86)\Tesseract-OCR",
        os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR"),
        r"D:\Tesseract-OCR", r"E:\Tesseract-OCR",
        # macOS
        "/usr/local/opt/tesseract",
        "/opt/homebrew/opt/tesseract",
        # Linux
        "/usr/share/tesseract-ocr",
        "/usr/lib/tesseract",
    ]
    for c in candidates:
        p = Path(c)
        if (p / "tesseract.exe").is_file() and (p / "tessdata").is_dir():
            return p
        if (p / "tesseract").is_file() and (p / "tessdata").is_dir():
            return p
    return None


def _copy_tesseract_portable(mode: str) -> tuple[bool, str]:
    """V2.0：将本机已安装的 Tesseract-OCR 复制为 dist 内的便携版。
    放置位置：
      - onefile：与 exe 同级的 tesseract/ 子目录（sketch_parser.py 会自动检测）
      - onedir：APP_NAME 目录下的 tesseract/ 子目录
    返回 (成功, 说明)
    """
    src_dir = _find_tesseract_install()
    if src_dir is None:
        return False, "本机未找到已安装的 Tesseract-OCR，请先安装后重试，或设置环境变量 TESSERACT_PATH"

    if mode == "onefile":
        dst_dir = DIST_DIR / "tesseract"
    else:
        dst_dir = DIST_DIR / APP_NAME / "tesseract"

    try:
        if dst_dir.exists():
            shutil.rmtree(dst_dir, ignore_errors=True)
        # 仅拷核心文件：tesseract.exe + 必要 DLL + tessdata（chi_sim.traineddata + eng.traineddata 必保）
        dst_dir.mkdir(parents=True, exist_ok=True)
        total_size = 0
        for item in src_dir.iterdir():
            if item.is_file():
                ext = item.suffix.lower()
                if ext in {".exe", ".dll", ".txt"}:
                    shutil.copy2(item, dst_dir / item.name)
                    total_size += item.stat().st_size
            elif item.name.lower() == "tessdata":
                dst_td = dst_dir / "tessdata"
                dst_td.mkdir(parents=True, exist_ok=True)
                for td in item.iterdir():
                    if td.is_file() and td.suffix.lower() == ".traineddata":
                        shutil.copy2(td, dst_td / td.name)
                        total_size += td.stat().st_size
        size_mb = total_size / (1024 * 1024)
        return True, f"已复制 Tesseract 便携版到 {dst_dir}（约 {size_mb:.1f}MB）"
    except Exception as e:
        return False, f"复制 Tesseract 失败: {e}"


def show_result(mode: str, with_tesseract: bool = False) -> None:
    _print_step(5, 5, "打包完成")
    if mode == "onefile":
        exe_path = DIST_DIR / f"{APP_NAME}.exe"
    else:
        exe_path = DIST_DIR / APP_NAME / f"{APP_NAME}.exe"

    tesseract_info = ""
    if with_tesseract:
        ok, msg = _copy_tesseract_portable(mode)
        if ok:
            tesseract_info = f"\n  [OCR] {msg}"
        else:
            tesseract_info = f"\n  [OCR] ⚠️ {msg}"

    print("\n" + "=" * 60)
    print("  打包成功！")
    print("=" * 60)
    print(f"  输出目录 : {DIST_DIR}")
    print(f"  可执行文件: {exe_path}")
    if tesseract_info:
        print(tesseract_info)
    print("=" * 60)

    if mode == "onefile":
        print("\n  使用说明（单文件模式）:")
        print(f"    直接双击 {APP_NAME}.exe 即可运行，无需其他文件。")
        print("    首次启动需要解压临时文件（到系统临时目录），可能需要 5-15 秒。")
        print()
        print("    重要提示：")
        print("      1. 拷贝到其他机器之前，先在本机双击测试能否正常运行")
        print("      2. 若仍打不开，在 exe 同目录会生成 crash.log，把该日志发给开发者")
        if with_tesseract:
            print("      3. [已启用OCR便携版] 已把 Tesseract 放到 exe 同目录 tesseract 子文件夹，")
            print("         用户无需单独安装，完整文件夹一起分发即可")
        else:
            print("      3. [可选] 草图OCR识别需 Tesseract-OCR 引擎，未安装时自动降级为几何估算，")
            print("         可让用户自行安装，或重新打包时加 --with-tesseract 参数（用户免安装）")
    else:
        folder = DIST_DIR / APP_NAME
        print(f"\n  使用说明（目录模式）:")
        print(f"    1. 保持整个文件夹 ({folder.name}) 完整，不要单独移动 exe")
        print(f"    2. 双击文件夹内的 {APP_NAME}.exe 即可运行")
        print(f"    3. 如需分发：右键整个文件夹 → 发送到 → 压缩(zipped)文件夹")
        if with_tesseract:
            print("      4. [已启用OCR便携版] 文件夹内已包含 tesseract 子目录，用户无需单独安装")
    print()


def main() -> None:
    args = parse_args()
    print("=" * 60)
    print(f"  SmartShapeCrop V2.0 打包工具")
    print(f"  exe 名称: {APP_NAME}")
    print(f"  exe 图标: {ICON_FILE.name}")
    print(f"  Python:   {sys.version.split()[0]}")
    print(f"  含Tesseract: {'是（用户免安装OCR）' if args['with_tesseract'] else '否（用户机器需自装或降级）'}")
    print("=" * 60)

    check_resources()
    ensure_pyinstaller()

    if args["clean"]:
        _print_step(3, 5, "清理旧构建产物...")
        clean_build()

    build_exe(args["mode"], args["debug"])
    show_result(args["mode"], args["with_tesseract"])


if __name__ == "__main__":
    main()
