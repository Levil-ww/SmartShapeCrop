# -*- coding: utf-8 -*-
"""
packageV2.1.py
SmartShapeCrop V2.1 打包脚本（Python 版）

功能：
    将 main.py 打包为 Windows 桌面可执行文件（单文件模式，双击即可运行）：
      - exe 文件名：智能裁剪设计器V2.1
      - exe 图标：images/SmartShapeCrop.ico
      - 运行时窗口图标：images/logo.png（由 main.py 的 set_app_icon 加载）
      - 默认内嵌本机 Tesseract-OCR 到 exe 内部（用户免安装即可使用草图 OCR）

V2.1 相比 V2.0 的新增/优化功能：
      - 水池设计器【素材填充（花型匹配填充）】：按匹配花型名称与内挖尺寸
        自动填充中间空白区域，外圈花型背景保持可见
      - 边距严格采用草图识别值（不再追加 1cm 损耗偏移），
        内挖尺寸由「画布尺寸 - 边距之和」自动派生
      - GUI 状态栏显示画布尺寸、内挖尺寸、边距值（上/下/左/右）
      - 边缘镜像对称扩展填充（_mirror_extend_top_bottom /
        _mirror_extend_left_right），替代单点颜色填充，消除边缘色块
      - 生成图像文件名使用匹配目标文件名中的尺寸（含 1cm 损耗），
        输出无多余背景
      - 修复素材填充覆盖外圈花型背景的问题（is_pool_with_material
        条件改为按 outer/inner 素材是否存在判定，保留外圈花型）

使用方式：
    python packageV2.1.py                    # 单文件模式（默认，生成单个 exe，双击即可运行）
    python packageV2.1.py --onedir           # 目录模式（更稳定，若 onefile 有问题可用）
    python packageV2.1.py --debug            # 调试模式（显示控制台窗口，便于排查错误）
    python packageV2.1.py --clean            # 清理旧构建后打包
    python packageV2.1.py --no-tesseract     # 不内嵌 Tesseract（默认已内嵌；OCR 不可用时草图识别将失败，需自装 Tesseract）

依赖：
    PyInstaller（脚本会自动检测并尝试安装）
    本机已安装 Tesseract-OCR（默认嵌入 exe，用户机器无需另装）
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
APP_NAME = "智能裁剪设计器V2.1"
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
    # 草图识别依赖
    "pytesseract",
    "psd_tools",
    "psd_tools.api",
    "psd_tools.constants",
    # 项目内部模块（确保打包时被正确收集）
    "core",
    # V2.1 补齐：main.py 直接依赖的核心运行时模块（V2.0 仅靠 PyInstaller
    # 依赖分析隐式收集，V2.1 显式声明以提升可复现性与打包稳定性）
    "core.config",          # PathResolver：跨平台 Tesseract 自动定位
    "core.geometry",        # CropDesign / BorderLayer 数据模型
    "core.image_cropper",   # 裁剪主流程
    "core.image_ops",       # 渲染 + 镜像对称扩展填充（V2.1 边缘填充核心）
    "core.log_setup",       # 日志初始化
    "core.app_settings",    # 应用设置
    "core.artifact_cleanup",  # 产物清理
    # 水池设计器草图识别（OCR + 方向标签 + 几何自洽）
    "core.pool_designer",
    "core.pool_designer.sketch_parser",
    # 智能形状裁剪与圆角处理（单步扇形切割、多层边框保护）
    "core.corner",
    "core.corner.sector_render",
    "core.corner.detection",
    "core.corner.algorithm",
    # 文件名解析与模板匹配
    "core.parser",
    "core.parser.name_parser",
    "core.parser.template_matcher",
    # PSD 加载
    "core.psd",
    "core.psd.loader",
    # GUI
    "gui",
    "gui.canvas_widget",
    "gui.property_panel",
    "gui.cropper_panel",
]

# 需要复制元数据的包（运行时依赖包的元数据）
COPY_METADATA_PACKAGES = [
    "Pillow",
    "numpy",
    "pytesseract",
    "psd-tools",
]

# 需要 collect-all 的包（包含动态加载的子模块/资源）
COLLECT_ALL_PACKAGES = [
    "PyQt5.Qt5",
    "pytesseract",
]

# 需要 collect-submodules 的包
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
        "mode": "onefile",          # onefile | onedir（默认单文件）
        "debug": False,
        "clean": False,
        "embed_tesseract": True,    # 默认内嵌 Tesseract 到 exe，用户机器免安装即可用 OCR
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
        elif arg == "--no-tesseract":
            args["embed_tesseract"] = False
        elif arg == "--with-tesseract":
            # 兼容旧参数：默认已内嵌，此参数仅作为旧用法兼容，无实际效果
            print("      [提示] --with-tesseract 已弃用（默认即内嵌 Tesseract），可忽略")
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


def _build_cmd(mode: str, debug: bool, tesseract_src_dir: Path | None = None) -> list[str]:
    """构建 PyInstaller 命令行参数

    Args:
        tesseract_src_dir: 本机 Tesseract 安装目录；非空则通过 --add-data 内嵌到 exe，
            运行时由 PathResolver 在 _MEIPASS/tesseract 下自动定位。
    """
    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", APP_NAME,
        "--icon", str(ICON_FILE),
        "--add-data", f"{str(LOGO_FILE)};images",
    ]

    # 内嵌 Tesseract（onefile 运行时解压到 _MEIPASS/tesseract，由 PathResolver 自动发现）
    if tesseract_src_dir is not None:
        sep = ";" if os.name == "nt" else ":"
        cmd.extend(["--add-data", f"{str(tesseract_src_dir)}{sep}tesseract"])

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
        # 内嵌的 Tesseract 同样会解压到 _MEIPASS/tesseract，由 PathResolver 自动定位
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd.append(str(ENTRY_SCRIPT))
    return cmd


def _run_build(mode: str, debug: bool, tesseract_src_dir: Path | None = None) -> bool:
    cmd = _build_cmd(mode, debug, tesseract_src_dir)
    print(f"      命令: {' '.join(cmd)}")
    print("      注意：首次打包较慢（约 2-5 分钟），请耐心等待...\n")

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode == 0


def build_exe(mode: str, debug: bool, embed_tesseract: bool) -> tuple[bool, str]:
    """打包 exe，返回 (是否内嵌了 Tesseract, 说明文字)。

    若 embed_tesseract=True 但本机未找到 Tesseract 安装目录，
    则发出警告并继续打包（运行时草图 OCR 将不可用，需用户自装 Tesseract）。
    """
    _print_step(3, 5, "开始打包（PyInstaller）...")
    py_ver = sys.version.split()[0]
    pyinst_ver = _get_pyinstaller_version()
    print(f"      Python 版本: {py_ver}")
    print(f"      PyInstaller : {'.'.join(str(v) for v in pyinst_ver) if pyinst_ver else '待安装'}")
    print(f"      exe 名称: {APP_NAME}")
    print(f"      exe 图标: {ICON_FILE.name}")
    print(f"      打包模式: {'单文件 (onefile, 默认)' if mode == 'onefile' else '目录 (onedir)'}")
    print(f"      调试模式: {'是（显示控制台）' if debug else '否（无控制台）'}")

    tess_src_dir: Path | None = None
    tess_info: str = ""
    if embed_tesseract:
        tess_src_dir = _find_tesseract_install()
        if tess_src_dir is None:
            tess_info = "本机未找到 Tesseract-OCR 安装目录，未内嵌；运行时草图 OCR 将不可用（7 步法无几何降级路径）"
            print(f"\n      [!] {tess_info}")
            print("          可设置环境变量 TESSERACT_PATH 指向 tesseract.exe 后重试")
        else:
            size_mb = _dir_size_mb(tess_src_dir)
            tess_info = f"已内嵌 Tesseract（源 {tess_src_dir}，约 {size_mb:.1f}MB）到 exe"
            print(f"      内嵌 Tesseract: {tess_src_dir} (约 {size_mb:.1f}MB)")
    else:
        tess_info = "未启用内嵌（用户机器如需草图 OCR 需自装 Tesseract-OCR 引擎）"
        print(f"      内嵌 Tesseract: 否")

    success = _run_build(mode, debug, tess_src_dir)
    if not success and mode == "onefile":
        print("\n      [!] onefile 打包失败，自动回退到 onedir 模式...")
        success = _run_build("onedir", debug, tess_src_dir)
        if success:
            print("\n      [√] onedir 模式打包成功！")
    if not success:
        _fail("打包失败，请检查上方错误信息")

    return (tess_src_dir is not None, tess_info)


def _dir_size_mb(path: Path) -> float:
    """估算目录总大小（MB），仅用于打包日志显示。"""
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except Exception:
        pass
    return total / (1024 * 1024)


def _find_tesseract_install() -> Path | None:
    """查找本机已安装的 Tesseract-OCR 目录（包含 tesseract.exe + tessdata 子文件夹）。"""
    # 使用 PathResolver 跨平台查找
    try:
        from core.config import PathResolver
        PathResolver.clear_cache()
        exe_path, tessdata_path = PathResolver.find_tesseract()
        if exe_path:
            return Path(os.path.dirname(exe_path))
    except Exception:
        pass

    # Fallback：保留原有搜索逻辑作为兜底
    candidates = [
        r"C:\Program Files\Tesseract-OCR",
        r"C:\Program Files (x86)\Tesseract-OCR",
        os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR"),
        r"D:\Tesseract-OCR", r"E:\Tesseract-OCR", r"F:\Tesseract-OCR",
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
    # 环境变量覆盖
    env_path = os.environ.get('TESSERACT_PATH', '')
    if env_path and os.path.isfile(env_path):
        return Path(os.path.dirname(env_path))
    return None


def show_result(mode: str, tesseract_embedded: bool, tess_info: str) -> None:
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
    print(f"  OCR 状态: {'已内嵌 Tesseract，用户免安装即可使用草图 OCR' if tesseract_embedded else tess_info}")
    print("=" * 60)

    if mode == "onefile":
        print("\n  使用说明（单文件模式）:")
        print(f"    直接双击 {APP_NAME}.exe 即可运行，无需其他文件。")
        print("    首次启动需要解压临时文件（到系统临时目录），可能需要 5-15 秒。")
        print()
        print("    重要提示：")
        print("      1. 拷贝到其他机器之前，先在本机双击测试能否正常运行")
        print("      2. 若仍打不开，在 exe 同目录会生成 crash.log，把该日志发给开发者")
        if tesseract_embedded:
            print("      3. [OCR 已内嵌] Tesseract 已打包进 exe 内部，单 exe 分发即可使用草图 OCR")
            print("         用户无需单独安装 Tesseract，无需附带任何外部文件夹")
        else:
            print("      3. [OCR 未内嵌] 草图识别需 Tesseract-OCR，未内嵌时识别将失败（无几何估算降级路径）；")
            print("         可让用户自行安装，或本机装好 Tesseract 后重新打包")
    else:
        folder = DIST_DIR / APP_NAME
        print(f"\n  使用说明（目录模式）:")
        print(f"    1. 保持整个文件夹 ({folder.name}) 完整，不要单独移动 exe")
        print(f"    2. 双击文件夹内的 {APP_NAME}.exe 即可运行")
        print(f"    3. 如需分发：右键整个文件夹 → 发送到 → 压缩(zipped)文件夹")
        if tesseract_embedded:
            print("      4. [OCR 已内嵌] Tesseract 已打包进 exe 内部，文件夹分发即可使用草图 OCR")
    print()


def main() -> None:
    args = parse_args()
    print("=" * 60)
    print(f"  SmartShapeCrop V2.1 打包工具")
    print(f"  exe 名称: {APP_NAME}")
    print(f"  exe 图标: {ICON_FILE.name}")
    print(f"  Python:   {sys.version.split()[0]}")
    print(f"  内嵌Tesseract: {'是（用户免安装 OCR）' if args['embed_tesseract'] else '否（用户机器需自装或降级）'}")
    print("=" * 60)

    check_resources()
    ensure_pyinstaller()

    if args["clean"]:
        _print_step(3, 5, "清理旧构建产物...")
        clean_build()

    embedded, tess_info = build_exe(args["mode"], args["debug"], args["embed_tesseract"])
    show_result(args["mode"], embedded, tess_info)


if __name__ == "__main__":
    main()
