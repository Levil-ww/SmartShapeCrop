"""Keep the fast-start distribution complete and retain onefile compatibility."""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def packaging_script():
    spec = importlib.util.spec_from_file_location(
        '_directory_packaging_probe', ROOT / 'packaging' / 'packageV2.2.5.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_is_directory_with_ocr(packaging_script, monkeypatch):
    monkeypatch.setattr(packaging_script.sys, 'argv', ['packageV2.2.5.py'])
    options = packaging_script.parse_args()
    assert options['mode'] == 'onedir'
    assert options['embed_tesseract'] is True
    assert options['clean'] is False


@pytest.mark.parametrize('mode', ['onedir', 'onefile'])
def test_explicit_mode_preserves_dependencies(packaging_script, monkeypatch, mode, tmp_path):
    monkeypatch.setattr(packaging_script.sys, 'argv', ['packageV2.2.5.py', '--' + mode])
    options = packaging_script.parse_args()
    assert options['mode'] == mode
    tess = tmp_path / 'tesseract'
    command = packaging_script._build_cmd(mode, False, tess)
    assert '--' + mode in command
    assert '--' + ('onefile' if mode == 'onedir' else 'onedir') not in command
    assert '--windowed' in command
    assert command[command.index('--name') + 1] == (
        packaging_script.DIST_FOLDER_NAME if mode == 'onedir' else packaging_script.APP_NAME)
    assert str(tess) + (';' if packaging_script.os.name == 'nt' else ':') + 'tesseract' in command
    for dependency in ('cv2', 'pytesseract', 'psd_tools'):
        assert dependency in command


def test_ascii_resource_directory_and_chinese_exe(packaging_script, monkeypatch, tmp_path):
    assert packaging_script.DIST_FOLDER_NAME.isascii()
    assert packaging_script.APP_VERSION in packaging_script.DIST_FOLDER_NAME
    monkeypatch.setattr(packaging_script, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(packaging_script, 'DIST_DIR', tmp_path / 'dist')
    folder = packaging_script.DIST_DIR / packaging_script.DIST_FOLDER_NAME
    folder.mkdir(parents=True)
    (folder / '_internal').mkdir()
    resource = folder / '_internal' / 'resource.bin'
    resource.write_bytes(b'unchanged resources')
    original = folder / (packaging_script.DIST_FOLDER_NAME + '.exe')
    original.write_bytes(b'compiled executable')
    packaging_script._finalize_directory_exe()
    assert (folder / (packaging_script.APP_NAME + '.exe')).read_bytes() == b'compiled executable'
    assert resource.read_bytes() == b'unchanged resources'
