"""切换水池填充方式、匹配新素材后，保留 UI 中的边距和四角半径。"""
from PIL import Image

from core.image_ops import _get_inner_pixel_mask
import numpy as np


def test_fill_switch_and_material_match_keep_corners(property_panel, tmp_path, monkeypatch):
    panel = property_panel
    panel._sp_w.setValue(24)
    panel._sp_h.setValue(18)
    margins = (4, 3, 5, 2)
    for spin, value in zip((panel._sp_mt, panel._sp_mb, panel._sp_ml, panel._sp_mr), margins):
        spin.setValue(value)
    radii = dict(tl=1, tr=2, bl=3, br=4)
    for key, value in radii.items():
        panel._sp_design_corners[key].setValue(value)
    monkeypatch.setattr(panel, '_pool_finish_tail', lambda *args: None)
    expected_mask = None
    for mode in ("blank", "image", "blank", "image"):
        panel._pool_hole_mode.setCurrentIndex(panel._pool_hole_mode.findData(mode))
        panel._collect()
        if mode == "image":
            # 经真实匹配回调更换两次素材，再通过实际 UI 快照回写。
            for name in ("first.png", "second.png"):
                path = tmp_path / name
                Image.new("RGB", (120, 80), (121, 78, 46)).save(path)
                panel._on_inner_match_done({"single": {"path": str(path)}})
                panel._collect()
                assert panel.design.pool_inner_material_image == str(path)
                assert np.array_equal(_get_inner_pixel_mask(panel.design), expected_mask)
        else:
            expected_mask = _get_inner_pixel_mask(panel.design)
        assert tuple(getattr(panel.design, f'inner_margin_{key}_cm')
                     for key in ("top", "bottom", "left", "right")) == margins
        assert {key: getattr(panel.design, f'corner_{key}_cm') for key in radii} == radii
        assert {key: spin.value() for key, spin in panel._sp_design_corners.items()} == radii
