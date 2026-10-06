"""Compare export against a Git revision, including exact pixel/JPEG equality.

Run from the project root: python scripts/benchmark_export.py --baseline HEAD
Outputs are kept in diag_output/export_performance; no existing files are deleted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import PIL
from PIL import Image
from core.geometry import CropDesign
from core import image_ops


def load_baseline(revision):
    source = subprocess.run(
        ['git', 'show', f'{revision}:core/image_ops.py'], cwd=ROOT,
        check=True, capture_output=True, encoding='utf-8',
    ).stdout
    module = types.ModuleType('core._export_baseline')
    module.__package__ = 'core'
    exec(compile(source, '<export baseline>', 'exec'), module.__dict__)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default='HEAD')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    baseline = load_baseline(args.baseline)
    out = ROOT / 'diag_output' / 'export_performance'
    out.mkdir(parents=True, exist_ok=True)
    # Fixed nonuniform texture exercises resampling, tiling and seam blending.
    rng = np.random.default_rng(20261005)
    material = out / 'benchmark_material.png'
    Image.fromarray(rng.integers(0, 256, (240, 320, 3), dtype=np.uint8)).save(material)
    cases = []
    for mode in ('rect_hole', 'ellipse_hole', 'rect_lshape'):
        cases.append((mode, CropDesign(
            mode=mode, canvas_w_cm=50, canvas_h_cm=70, dpi=150,
            inner_margin_top_cm=8, inner_margin_bottom_cm=8,
            inner_margin_left_cm=8, inner_margin_right_cm=8,
        )))
    for mode in ('rect_hole', 'ellipse_hole', 'rect_lshape'):
        cases.append((mode + '_material', CropDesign(
            mode=mode, canvas_w_cm=20, canvas_h_cm=25, dpi=150,
            inner_margin_top_cm=4, inner_margin_bottom_cm=4,
            inner_margin_left_cm=4, inner_margin_right_cm=4,
            l_cut_w_cm=5, l_cut_h_cm=5,
            outer_bg_image=str(material), hole_bg_image=str(material),
        )))
    for mode in ('rect_hole', 'ellipse_hole', 'rect_lshape'):
        cases.append((mode + '_pool', CropDesign(
            mode=mode, canvas_w_cm=20, canvas_h_cm=25, dpi=150,
            inner_margin_top_cm=4, inner_margin_bottom_cm=4,
            inner_margin_left_cm=4, inner_margin_right_cm=4,
            l_cut_w_cm=5, l_cut_h_cm=5,
            pool_outer_material_image=str(material), pool_hole_transparent=True,
        )))
    revision = subprocess.run(['git', 'rev-parse', args.baseline], cwd=ROOT,
                              check=True, capture_output=True, text=True).stdout.strip()
    report = {'baseline': revision, 'repeats': args.repeats,
              'python': sys.version.split()[0], 'numpy': np.__version__,
              'pillow': PIL.__version__, 'cases': []}
    for name, design in cases:
        samples = {'before': [], 'after': []}
        digest = None
        for run in range(args.repeats):
            # Alternate order so cache warming does not always favor the new version.
            order = [('before', baseline), ('after', image_ops)]
            if run % 2:
                order.reverse()
            outputs = {}
            for label, module in order:
                start = time.perf_counter()
                img = module.render_design(design.clone(), quality='export')
                rendered = time.perf_counter()
                path = out / f'{name}_{label}.jpg'
                module.save_jpg(img, str(path), quality=95, dpi=design.dpi)
                saved = time.perf_counter()
                samples[label].append({
                    'render_s': rendered - start, 'save_s': saved - rendered,
                    'total_s': saved - start,
                })
                outputs[label] = (hashlib.sha256(img.tobytes()).hexdigest(),
                                  hashlib.sha256(path.read_bytes()).hexdigest())
            assert outputs['before'] == outputs['after'], f'Output changed: {name}'
            digest = outputs['after']
        row = {'name': name, 'size': [design.canvas_w_px, design.canvas_h_px],
               'pixel_sha256': digest[0], 'jpeg_sha256': digest[1]}
        for label in samples:
            row[label] = {key: statistics.median(x[key] for x in samples[label])
                          for key in ('render_s', 'save_s', 'total_s')}
        row['improvement_percent'] = 100 * (1 - row['after']['total_s'] / row['before']['total_s'])
        report['cases'].append(row)
        (out / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(row, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
