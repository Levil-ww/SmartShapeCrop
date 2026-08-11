"""静态校验：导入所有重构后的模块，验证向后兼容性。"""

# ============================================================
# PROJECT_ROOT auto-inject (added by test-dir cleanup 2026-08-11)
# 脚本从 scripts/ 子目录运行时仍能正确定位 core/, psd_demo/, Test/output 等
import sys as _sys
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
_D = str(_PROJECT_ROOT)
# ============================================================

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def check(label, fn):
    try:
        fn()
        print(f"  [OK] {label}")
        return True
    except Exception as e:
        print(f"  [FAIL] {label}: {type(e).__name__}: {e}")
        return False


def main():
    print("=== 1. 验证 shim 文件可正常导入 ===")
    ok1 = True
    ok1 &= check("core.rounded_corner (shim)",
                 lambda: __import__("core.rounded_corner", fromlist=["carve_corner_on_mask"]))
    ok1 &= check("core.name_parser (shim)",
                 lambda: __import__("core.name_parser", fromlist=["parse_filename"]))
    ok1 &= check("core.template_matcher (shim)",
                 lambda: __import__("core.template_matcher", fromlist=["TemplateMatcher"]))
    ok1 &= check("core.psd_loader (shim)",
                 lambda: __import__("core.psd_loader", fromlist=["load_psd_flattened"]))

    print("\n=== 2. 验证新子包可正常导入 ===")
    ok2 = True
    ok2 &= check("core.corner (subpackage)",
                 lambda: __import__("core.corner", fromlist=["carve_corner_on_mask"]))
    ok2 &= check("core.parser (subpackage)",
                 lambda: __import__("core.parser", fromlist=["parse_filename"]))
    ok2 &= check("core.psd (subpackage)",
                 lambda: __import__("core.psd", fromlist=["load_psd_flattened"]))

    print("\n=== 3. 验证旧导入路径仍可用（向后兼容） ===")
    ok3 = True

    def t_old_image_cropper():
        from core.image_cropper import (
            CropConfig, crop_image, batch_crop,
            apply_rounded_corners, apply_border_only_corners,
            detect_nested_rect_layers,
            _detect_border_layers, _redraw_border_on_corner,
            _get_border_layers_robust, _scan_edge_boundaries,
            _build_border_sector_mask,
            _sample_border_color,
            _DEFAULT_BORDER_WIDTH_CM,
            BORDER_TOTAL_DEPTH_CM,
            _BORDER_SCAN_STEP, _BORDER_COLOR_DIFF_THRESHOLD,
            _BORDER_MIN_GAP_PX, _BORDER_MAX_LAYERS, _EDGE_IGNORE_PX,
            load_source_image, get_corner_name, get_default_corners,
            get_mode_description,
        )
    ok3 &= check("core.image_cropper (all old names)", t_old_image_cropper)

    def t_old_rounded_corner():
        from core.rounded_corner import (
            CORNER_ANGLES, carve_corner_on_mask,
            get_corner_square, get_corner_pieslice_bbox,
        )
    ok3 &= check("core.rounded_corner (old names)", t_old_rounded_corner)

    def t_old_name_parser():
        from core.name_parser import (
            ParsedFilename, CN_NUM, SHAPE_SUFFIXES,
            parse_filename, parse_size_dims, generate_filename,
            get_image_info, get_base_pattern_name, normalize_flower_name,
            _cn_to_int, _normalize_str, _extract_size_pair,
            _extract_size_pair_manual, _parse_corners, _fmt_num,
        )
    ok3 &= check("core.name_parser (old names)", t_old_name_parser)

    def t_old_template_matcher():
        from core.template_matcher import (
            TemplateEntry, TemplateMatcher,
            scan_template_directory, match_template,
        )
    ok3 &= check("core.template_matcher (old names)", t_old_template_matcher)

    def t_old_psd_loader():
        from core.psd_loader import (
            PsdLayer, is_psd_file, load_psd_layers, load_psd_flattened,
            export_psd_layers_as_jpgs, _try_import_psd_tools, _safe_name,
        )
    ok3 &= check("core.psd_loader (old names)", t_old_psd_loader)

    print("\n=== 4. 验证新旧导入指向同一对象（无重复定义） ===")
    ok4 = True

    def t_consistent_corner():
        from core.rounded_corner import carve_corner_on_mask as f1
        from core.corner.algorithm import carve_corner_on_mask as f2
        from core.corner import carve_corner_on_mask as f3
        assert f1 is f2 is f3, "carve_corner_on_mask 不一致"
    ok4 &= check("carve_corner_on_mask 同源", t_consistent_corner)

    def t_consistent_detection():
        from core.image_cropper import _detect_border_layers as f1
        from core.corner.detection import _detect_border_layers as f2
        assert f1 is f2, "_detect_border_layers 不一致"
    ok4 &= check("_detect_border_layers 同源", t_consistent_detection)

    def t_consistent_sector_render():
        from core.image_cropper import _redraw_border_on_corner as f1
        from core.corner.sector_render import _redraw_border_on_corner as f2
        assert f1 is f2, "_redraw_border_on_corner 不一致"
    ok4 &= check("_redraw_border_on_corner 同源", t_consistent_sector_render)

    def t_consistent_name_parser():
        from core.name_parser import parse_filename as f1
        from core.parser.name_parser import parse_filename as f2
        assert f1 is f2, "parse_filename 不一致"
    ok4 &= check("parse_filename 同源", t_consistent_name_parser)

    def t_consistent_template_matcher():
        from core.template_matcher import TemplateMatcher as f1
        from core.parser.template_matcher import TemplateMatcher as f2
        assert f1 is f2, "TemplateMatcher 不一致"
    ok4 &= check("TemplateMatcher 同源", t_consistent_template_matcher)

    def t_consistent_psd_loader():
        from core.psd_loader import load_psd_flattened as f1
        from core.psd.loader import load_psd_flattened as f2
        assert f1 is f2, "load_psd_flattened 不一致"
    ok4 &= check("load_psd_flattened 同源", t_consistent_psd_loader)

    print("\n=== 5. 验证核心依赖关系（geometry / image_ops / image_cropper） ===")
    ok5 = True
    ok5 &= check("core.geometry",
                 lambda: __import__("core.geometry", fromlist=["CropDesign"]))
    ok5 &= check("core.image_ops",
                 lambda: __import__("core.image_ops", fromlist=["render_design"]))
    ok5 &= check("core.image_cropper",
                 lambda: __import__("core.image_cropper", fromlist=["crop_image"]))
    ok5 &= check("core.config",
                 lambda: __import__("core.config", fromlist=["DEFAULT_DPI"]))
    ok5 &= check("core.log_setup",
                 lambda: __import__("core.log_setup", fromlist=["setup_logging"]))

    print("\n=== 6. 简单功能冒烟测试（不改逻辑） ===")
    ok6 = True

    def t_smoke_corner():
        from PIL import Image
        from core.corner import carve_corner_on_mask
        mask = Image.new('L', (100, 80), 255)
        carve_corner_on_mask(mask, (0, 0, 100, 80), {'br': 10})
        arr = list(mask.getdata())
        assert arr[80 * 99 + 79] == 0, "右下角顶点未裁掉"
    ok6 &= check("carve_corner_on_mask 冒烟", t_smoke_corner)

    def t_smoke_apply():
        from PIL import Image
        from core.image_cropper import apply_rounded_corners
        img = Image.new('RGB', (100, 80), (0, 0, 0))
        result = apply_rounded_corners(img, {'br': 5.0}, dpi=150,
                                       bg_color=(255, 255, 255))
        assert result.getpixel((99, 79)) == (255, 255, 255), "右下角未填充背景色"
    ok6 &= check("apply_rounded_corners 冒烟", t_smoke_apply)

    def t_smoke_parse():
        from core.name_parser import parse_filename
        p = parse_filename("双面格;竖版41x55cm右下角圆角半径2cm.jpg")
        assert p.width_cm == 41, f"width={p.width_cm}"
        assert p.height_cm == 55, f"height={p.height_cm}"
        assert p.corners.get('br') == 2.0, f"corners={p.corners}"
    ok6 &= check("parse_filename 冒烟", t_smoke_parse)

    print("\n" + "=" * 60)
    all_ok = ok1 and ok2 and ok3 and ok4 and ok5 and ok6
    if all_ok:
        print("✓ 所有静态校验通过！向后兼容性 100% 保留，无循环依赖。")
    else:
        print("✗ 部分校验失败，请检查上面的 FAIL 项。")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
