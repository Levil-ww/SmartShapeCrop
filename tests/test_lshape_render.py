"""L 形挖角功能测试 - Phase 1 & 2"""
import sys
import os
import tempfile
sys.path.insert(0, '.')
import numpy as np
from PIL import Image
from core.geometry import (
    CropDesign, BorderLayer, RectShape,
    build_lshape_mask, _get_lshape_cut_rect_at_offset,
    compute_border_bands, compute_lshape_border_bands,
)
from core.image_ops import render_design


def test_basic_lshape_render():
    """Test 1: Basic L-shape rendering"""
    print('=== Test 1: Basic L-shape rendering ===')
    design = CropDesign(
        canvas_w_cm=30.0, canvas_h_cm=20.0, dpi=150,
        mode='rect_lshape',
        outer_margin_cm=0.5,
        inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
        inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
        l_corner='br', l_cut_w_cm=8.0, l_cut_h_cm=6.0,
        hole_bg_color=(255, 255, 255),
        outer_bg_color=(0, 0, 0),
    )
    result = render_design(design, quality='export')
    print(f'  Size: {result.size}, Mode: {result.mode}')
    arr = np.array(result)
    H, W = arr.shape[:2]
    # Check the cut area center region (should be white)
    # cut rect at br: x = inner_rect.right - cut_w/2, y = inner_rect.bottom - cut_h/2
    inner_rect = design.inner_rect_px()
    cut_w_px = design.cm2px(design.l_cut_w_cm)
    cut_h_px = design.cm2px(design.l_cut_h_cm)
    cut_cx = int(inner_rect.right - cut_w_px * 0.3)
    cut_cy = int(inner_rect.bottom - cut_h_px * 0.3)
    cut_region = arr[cut_cy-20:cut_cy+20, cut_cx-20:cut_cx+20]
    mean_color = cut_region.mean(axis=(0, 1))
    print(f'  Cut center region avg: ({mean_color[0]:.0f}, {mean_color[1]:.0f}, {mean_color[2]:.0f})')
    assert result.size == (design.canvas_w_px, design.canvas_h_px), 'Canvas size mismatch'
    assert mean_color[0] > 250 and mean_color[1] > 250 and mean_color[2] > 250, \
        f'Cut region should be white (hole_bg_color), got ({mean_color[0]:.0f}, {mean_color[1]:.0f}, {mean_color[2]:.0f})'
    print('  PASSED')


def test_lshape_with_corners():
    """Test 2: L-shape with rounded corners"""
    print('=== Test 2: L-shape with rounded corners ===')
    design = CropDesign(
        canvas_w_cm=30.0, canvas_h_cm=20.0, dpi=150,
        mode='rect_lshape',
        outer_margin_cm=0.5,
        inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
        inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
        l_corner='br', l_cut_w_cm=8.0, l_cut_h_cm=6.0,
        corner_tl_cm=1.0, corner_tr_cm=1.0,
        corner_bl_cm=1.0, corner_br_cm=1.0,
        hole_bg_color=(255, 255, 255),
        outer_bg_color=(0, 0, 0),
    )
    result = render_design(design, quality='export')
    print(f'  Size: {result.size}')
    print('  PASSED')


def test_lshape_with_border_bands():
    """Test 3: L-shape with multi-layer border bands"""
    print('=== Test 3: L-shape with border bands ===')
    design = CropDesign(
        canvas_w_cm=30.0, canvas_h_cm=20.0, dpi=150,
        mode='rect_lshape',
        outer_margin_cm=0.5,
        inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
        inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
        l_corner='br', l_cut_w_cm=8.0, l_cut_h_cm=6.0,
        corner_tl_cm=0.0, corner_tr_cm=0.0,
        corner_bl_cm=0.0, corner_br_cm=0.0,
        borders=[
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(255, 255, 255)),
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
        ],
        hole_bg_color=(250, 245, 230),
        outer_bg_color=(0, 0, 0),
    )
    result = render_design(design, quality='export')
    print(f'  Size: {result.size}')
    print('  PASSED')


def test_cut_rect_offset():
    """Test 4: _get_lshape_cut_rect_at_offset"""
    print('=== Test 4: cut rect at offset ===')
    outer = RectShape(100, 100, 800, 500)

    for ck in ('tl', 'tr', 'bl', 'br'):
        cut0 = _get_lshape_cut_rect_at_offset(outer, ck, 200, 150, 0)
        cut50 = _get_lshape_cut_rect_at_offset(outer, ck, 200, 150, 50)
        # At offset 0, cut rect should align with outer rect edges
        if ck == 'br':
            assert abs(cut0.right - outer.right) < 1, f'{ck}: cut right {cut0.right} != outer right {outer.right}'
            assert abs(cut0.bottom - outer.bottom) < 1, f'{ck}: cut bottom {cut0.bottom} != outer bottom {outer.bottom}'
        elif ck == 'tr':
            assert abs(cut0.right - outer.right) < 1, f'{ck}: cut right mismatch'
            assert abs(cut0.y - outer.y) < 1, f'{ck}: cut top mismatch'
        elif ck == 'bl':
            assert abs(cut0.x - outer.x) < 1, f'{ck}: cut left mismatch'
            assert abs(cut0.bottom - outer.bottom) < 1, f'{ck}: cut bottom mismatch'
        elif ck == 'tl':
            assert abs(cut0.x - outer.x) < 1, f'{ck}: cut left mismatch'
            assert abs(cut0.y - outer.y) < 1, f'{ck}: cut top mismatch'
        # At offset 50, cut rect should shrink by 50px
        assert cut50.w == 150.0, f'{ck}: width should be 150, got {cut50.w}'
        assert cut50.h == 100.0, f'{ck}: height should be 100, got {cut50.h}'
        print(f'  {ck}: offset 0 -> ({cut0.x:.0f},{cut0.y:.0f},{cut0.w:.0f},{cut0.h:.0f}), '
              f'offset 50 -> ({cut50.x:.0f},{cut50.y:.0f},{cut50.w:.0f},{cut50.h:.0f})')
    print('  PASSED')


def test_build_lshape_mask():
    """Test 5: build_lshape_mask"""
    print('=== Test 5: build_lshape_mask ===')
    outer = RectShape(100, 100, 800, 500)

    for ck in ('tl', 'tr', 'bl', 'br'):
        m = build_lshape_mask(
            (1000, 700), outer, ck, 200, 150,
            {'tl': 20, 'tr': 20, 'bl': 20, 'br': 20}, fill_value=255)
        m_arr = np.array(m)
        assert m_arr.sum() > 0, f'{ck}: mask should have non-zero pixels'
        # Also test without cut (should be full rect)
        m_no_cut = build_lshape_mask(
            (1000, 700), outer, ck, 0, 0,
            {'tl': 20, 'tr': 20, 'bl': 20, 'br': 20}, fill_value=255)
        m_no_cut_arr = np.array(m_no_cut)
        assert m_no_cut_arr.sum() > 0, f'{ck}: no-cut mask should have non-zero pixels'
        print(f'  {ck}: mask sum={m_arr.sum()}, no-cut sum={m_no_cut_arr.sum()}')
    print('  PASSED')


def test_compute_border_bands_dispatch():
    """Test 6: compute_border_bands dispatch to L-shape"""
    print('=== Test 6: border bands dispatch ===')
    design = CropDesign(
        canvas_w_cm=30.0, canvas_h_cm=20.0, dpi=150,
        mode='rect_lshape',
        outer_margin_cm=0.5,
        inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
        inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
        l_corner='br', l_cut_w_cm=8.0, l_cut_h_cm=6.0,
        borders=[
            BorderLayer(offset_cm=0.5, fill_type='solid', color=(0, 0, 0)),
        ],
        hole_bg_color=(250, 245, 230),
        outer_bg_color=(0, 0, 0),
    )
    bands = compute_border_bands(design)
    assert len(bands) >= 1, f'Should have at least 1 band, got {len(bands)}'
    print(f'  Bands: {len(bands)}')
    for i, (band_mask, layer) in enumerate(bands):
        print(f'  Band {i}: {band_mask.sum()} pixels, color={layer.color}')
    print('  PASSED')


def test_rect_hole_still_works():
    """Test 7: rect_hole mode still works (no regression)"""
    print('=== Test 7: rect_hole mode still works ===')
    design = CropDesign(
        canvas_w_cm=30.0, canvas_h_cm=20.0, dpi=150,
        mode='rect_hole',
        outer_margin_cm=0.5,
        inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
        inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
        corner_tl_cm=1.0, corner_tr_cm=1.0,
        corner_bl_cm=1.0, corner_br_cm=1.0,
        borders=[
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
        ],
        hole_bg_color=(255, 255, 255),
        outer_bg_color=(0, 0, 0),
    )
    result = render_design(design, quality='export')
    print(f'  Size: {result.size}')
    bands = compute_border_bands(design)
    print(f'  Bands: {len(bands)}')
    assert len(bands) >= 1
    print('  PASSED')


def test_all_four_lshape_corners():
    """Test 8: All four L-shape corner positions"""
    print('=== Test 8: All four L-shape corners ===')
    for ck in ('tl', 'tr', 'bl', 'br'):
        design = CropDesign(
            canvas_w_cm=20.0, canvas_h_cm=15.0, dpi=150,
            mode='rect_lshape',
            outer_margin_cm=0.3,
            inner_margin_top_cm=1.5, inner_margin_bottom_cm=1.5,
            inner_margin_left_cm=1.5, inner_margin_right_cm=1.5,
            l_corner=ck, l_cut_w_cm=4.0, l_cut_h_cm=3.0,
            hole_bg_color=(255, 255, 255),
            outer_bg_color=(0, 0, 0),
        )
        result = render_design(design, quality='export')
        arr = np.array(result)
        H, W = arr.shape[:2]
        assert arr.shape[2] == 3  # RGB
        print(f'  {ck}: rendered {W}x{H} OK')
    print('  PASSED')


def test_lshape_with_outer_image():
    """Test 9: L-shape with outer background image (Phase 2)"""
    print('=== Test 9: L-shape with outer bg image ===')
    
    # Create a test outer bg image with a unique color (200, 100, 50)
    test_color = (200, 100, 50)
    test_img = Image.new('RGB', (500, 300), test_color)
    
    # Save to temp file
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
        test_img.save(f.name)
        test_img_path = f.name
    
    try:
        for ck in ('tl', 'tr', 'bl', 'br'):
            design = CropDesign(
                canvas_w_cm=30.0, canvas_h_cm=20.0, dpi=150,
                mode='rect_lshape',
                outer_margin_cm=0.5,
                inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
                inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
                l_corner=ck, l_cut_w_cm=8.0, l_cut_h_cm=6.0,
                outer_bg_image=test_img_path,
                hole_bg_color=(255, 255, 255),
                outer_bg_color=(0, 0, 0),
            )
            result = render_design(design, quality='export')
            arr = np.array(result)
            H, W = arr.shape[:2]
            
            outer = design.outer_rect_px()
            inner = design.inner_rect_px()
            cut_w_px = design.cm2px(design.l_cut_w_cm)
            cut_h_px = design.cm2px(design.l_cut_h_cm)
            
            # 1. Check border band region shows outer_bg_image color
            # Use a point in the border area (outer_rect near top, outside inner_rect)
            border_x = int(outer.x + 20)
            border_y = int(outer.y + 20)
            border_region = arr[border_y-10:border_y+10, border_x-10:border_x+10]
            border_color = border_region.mean(axis=(0, 1))
            assert abs(border_color[0] - 200) < 40, \
                f'{ck}: Border region should show outer image, got ({border_color[0]:.0f}, {border_color[1]:.0f}, {border_color[2]:.0f})'
            
            # 2. Check cut region has outer_bg_color (black)
            if ck == 'br':
                cut_cx = int(inner.right - cut_w_px * 0.3)
                cut_cy = int(inner.bottom - cut_h_px * 0.3)
            elif ck == 'tl':
                cut_cx = int(inner.x + cut_w_px * 0.3)
                cut_cy = int(inner.y + cut_h_px * 0.3)
            elif ck == 'tr':
                cut_cx = int(inner.right - cut_w_px * 0.3)
                cut_cy = int(inner.y + cut_h_px * 0.3)
            else:  # bl
                cut_cx = int(inner.x + cut_w_px * 0.3)
                cut_cy = int(inner.bottom - cut_h_px * 0.3)
            
            cut_region = arr[cut_cy-20:cut_cy+20, cut_cx-20:cut_cx+20]
            cut_color = cut_region.mean(axis=(0, 1))
            assert cut_color[0] < 50 and cut_color[1] < 50 and cut_color[2] < 50, \
                f'{ck}: Cut region should be outer_bg_color (black), got ({cut_color[0]:.0f}, {cut_color[1]:.0f}, {cut_color[2]:.0f})'
            
            # 3. Check inner region shows hole_bg_color (white)
            inner_cx = int((inner.x + inner.right) / 2)
            inner_cy = int((inner.y + inner.bottom) / 2)
            inner_region = arr[inner_cy-10:inner_cy+10, inner_cx-10:inner_cx+10]
            inner_color = inner_region.mean(axis=(0, 1))
            assert inner_color[0] > 250 and inner_color[1] > 250 and inner_color[2] > 250, \
                f'{ck}: Inner region should be hole_bg_color (white), got ({inner_color[0]:.0f}, {inner_color[1]:.0f}, {inner_color[2]:.0f})'
            
            # 4. Check canvas area outside outer_rect shows outer_bg_color
            top_region = arr[5:25, 5:25]
            top_color = top_region.mean(axis=(0, 1))
            assert top_color[0] < 50, \
                f'{ck}: Canvas outside outer_rect should be black, got ({top_color[0]:.0f}, {top_color[1]:.0f}, {top_color[2]:.0f})'
            
            print(f'  {ck}: OK - border has image, cut is black, inner is white')
        print('  PASSED')
    finally:
        os.unlink(test_img_path)


def test_lshape_outer_image_with_borders():
    """Test 10: L-shape with outer image and border bands (Phase 2)"""
    print('=== Test 10: L-shape with outer image + borders ===')
    
    test_color = (180, 120, 80)
    test_img = Image.new('RGB', (400, 250), test_color)
    
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
        test_img.save(f.name)
        test_img_path = f.name
    
    try:
        design = CropDesign(
            canvas_w_cm=25.0, canvas_h_cm=18.0, dpi=150,
            mode='rect_lshape',
            outer_margin_cm=0.3,
            inner_margin_top_cm=1.5, inner_margin_bottom_cm=1.5,
            inner_margin_left_cm=1.5, inner_margin_right_cm=1.5,
            l_corner='tr', l_cut_w_cm=5.0, l_cut_h_cm=4.0,
            outer_bg_image=test_img_path,
            borders=[
                BorderLayer(offset_cm=0.2, fill_type='solid', color=(0, 0, 0)),
                BorderLayer(offset_cm=0.15, fill_type='solid', color=(255, 255, 255)),
            ],
            hole_bg_color=(250, 245, 230),
            outer_bg_color=(0, 0, 0),
        )
        result = render_design(design, quality='export')
        arr = np.array(result)
        H, W = arr.shape[:2]
        
        outer = design.outer_rect_px()
        inner = design.inner_rect_px()
        cut_w_px = design.cm2px(design.l_cut_w_cm)
        cut_h_px = design.cm2px(design.l_cut_h_cm)
        
        # 1. Border region (between outer and inner) should show image
        border_x = int(outer.x + 10)
        border_y = int(outer.y + 10)
        border_region = arr[border_y-8:border_y+8, border_x-8:border_x+8]
        border_color = border_region.mean(axis=(0, 1))
        assert abs(border_color[0] - 180) < 40, \
            f'Border region should show image, got ({border_color[0]:.0f}, {border_color[1]:.0f}, {border_color[2]:.0f})'
        
        # 2. Cut region should be black (outer_bg_color)
        cut_cx = int(inner.right - cut_w_px * 0.3)
        cut_cy = int(inner.y + cut_h_px * 0.3)
        cut_region = arr[cut_cy-15:cut_cy+15, cut_cx-15:cut_cx+15]
        cut_color = cut_region.mean(axis=(0, 1))
        assert cut_color[0] < 50, \
            f'Cut region should be black, got ({cut_color[0]:.0f}, {cut_color[1]:.0f}, {cut_color[2]:.0f})'
        
        # 3. Inner region should show hole_bg_color (250, 245, 230)
        inner_center_x = int((inner.x + inner.right) / 2)
        inner_center_y = int((inner.y + inner.bottom) / 2)
        inner_region = arr[inner_center_y-15:inner_center_y+15, inner_center_x-15:inner_center_x+15]
        inner_color = inner_region.mean(axis=(0, 1))
        assert abs(inner_color[0] - 250) < 30, \
            f'Inner region should show hole_bg_color, got ({inner_color[0]:.0f}, {inner_color[1]:.0f}, {inner_color[2]:.0f})'
        
        # 4. Canvas area outside outer_rect should be black
        top_region = arr[3:15, 3:15]
        top_color = top_region.mean(axis=(0, 1))
        assert top_color[0] < 50, \
            f'Canvas outside outer_rect should be black, got ({top_color[0]:.0f}, {top_color[1]:.0f}, {top_color[2]:.0f})'
        
        print(f'  Border shows image: ({border_color[0]:.0f}, {border_color[1]:.0f}, {border_color[2]:.0f})')
        print(f'  Cut is black: ({cut_color[0]:.0f}, {cut_color[1]:.0f}, {cut_color[2]:.0f})')
        print(f'  Inner shows hole_color: ({inner_color[0]:.0f}, {inner_color[1]:.0f}, {inner_color[2]:.0f})')
        print('  PASSED')
    finally:
        os.unlink(test_img_path)


if __name__ == '__main__':
    test_basic_lshape_render()
    test_lshape_with_corners()
    test_lshape_with_border_bands()
    test_cut_rect_offset()
    test_build_lshape_mask()
    test_compute_border_bands_dispatch()
    test_rect_hole_still_works()
    test_all_four_lshape_corners()
    test_lshape_with_outer_image()
    test_lshape_outer_image_with_borders()
    print()
    print('=== ALL TESTS PASSED ===')