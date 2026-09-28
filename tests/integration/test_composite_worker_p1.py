"""P1 复合形状 Worker 回归：模板匹配、洞素材匹配与旧模式隔离。"""
from types import SimpleNamespace
from PIL import Image

from workers.property_panel_workers import PoolRenderWorker, _InnerMatchWorker


def _params(fill='blank'):
    return {
        'outer_w_cm': 185.0, 'outer_h_cm': 88.0,
        'hole_w_cm': 80.0, 'hole_h_cm': 60.0,
        'hole_margin_top_cm': 10.0, 'hole_margin_bottom_cm': 18.0,
        'hole_margin_left_cm': 45.0, 'hole_margin_right_cm': 60.0,
        'corner': 'tr', 'cut_w_cm': 35.0, 'cut_h_cm': 10.0,
        'cuts_cm': [{'corner': 'tr', 'cut_w_cm': 35.0, 'cut_h_cm': 10.0}],
        'hole_fill_mode': fill,
    }


def _matcher(path, found=True):
    class Matcher:
        def get_template_dir(self): return str(path.parent)
        def set_template_dir(self, value): pass
        def scan_library(self, **kwargs): pass
        def find_best_match(self, query):
            return (SimpleNamespace(path=str(path), score=99.0), []) if found else (None, [])
    return Matcher()


def test_pool_worker_builds_composite_design_from_template(tmp_path):
    material = tmp_path / '花型-裁剪有图-185x88CM.png'
    Image.new('RGB', (40, 20), 'red').save(material)
    worker = PoolRenderWorker(_matcher(material), str(tmp_path), '花型-185x88CM',
                              composite_params=_params('blank'))
    best = SimpleNamespace(path=str(material), score=99.0)
    design = worker._build_design(best, None, 185.0, 88.0, False)
    assert design.mode == 'rect_lshape_hole'
    assert design.pool_hole_transparent is True
    assert design.pool_outer_material_image == str(material)
    assert design.l_cuts_cm[0]['corner'] == 'tr'
    assert design.inner_rect_px().w > 0


def test_pool_worker_composite_template_failure_emits_error(tmp_path):
    worker = PoolRenderWorker(_matcher(tmp_path / 'missing.png', found=False),
                              str(tmp_path), '不存在-185x88CM',
                              composite_params=_params())
    errors = []
    worker.finished_err.connect(errors.append)
    worker.run()
    assert errors and '未找到匹配' in errors[0]


def test_composite_fill_mode_is_preserved_and_inner_match_succeeds(tmp_path):
    material = tmp_path / '花型-裁剪有图-185x88CM.png'
    inner = tmp_path / '花型-裁剪有图-80.0x60.0CM.png'
    Image.new('RGB', (40, 20), 'red').save(material)
    Image.new('RGB', (20, 20), 'blue').save(inner)
    worker = PoolRenderWorker(_matcher(material), str(tmp_path), '花型-185x88CM',
                              composite_params=_params('image'))
    design = worker._build_design(SimpleNamespace(path=str(material)), None, 185.0, 88.0, False)
    assert design.pool_hole_transparent is False

    class Matcher(_matcher(material).__class__):
        def find_best_match(self, query):
            return SimpleNamespace(path=str(inner), score=88.0), []
    iw = _InnerMatchWorker(Matcher(), str(tmp_path), '花型-185x88CM', False, [], (80.0, 60.0))
    out = []
    iw.finished_ok.connect(out.append)
    iw.run()
    assert out[0]['single']['path'] == str(inner)


def test_composite_inner_match_failure_is_recoverable(tmp_path):
    class Matcher:
        def get_template_dir(self): return str(tmp_path)
        def set_template_dir(self, value): pass
        def scan_library(self, **kwargs): pass
        def find_best_match(self, query): return None, []
    iw = _InnerMatchWorker(Matcher(), str(tmp_path), '花型-185x88CM', False, [], (80.0, 60.0))
    out = []
    iw.finished_ok.connect(out.append)
    iw.run()
    assert out[0]['single']['path'] is None
    assert '未找到匹配' in out[0]['inner_match_info']
