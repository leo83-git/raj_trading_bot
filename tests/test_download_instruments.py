import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "download_instruments.py"


spec = importlib.util.spec_from_file_location("download_instruments", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_manifest_path_is_fixed_and_local():
    manifest_path = module.MANIFEST_DIR / "instrument_cache.manifest.json"

    assert manifest_path.is_absolute()
    assert str(manifest_path).startswith(str(ROOT))
    assert "://" not in str(manifest_path)
    assert manifest_path.name == "instrument_cache.manifest.json"
