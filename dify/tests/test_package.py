import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.package_submission import build_archive


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    for name in ["README.md", "station7.yml", "requirements.txt", "compose.yaml", "Dockerfile", ".env.example",
                 "station7/app.py", "station7/memory.py", "scripts/run_powercontext.py", "scripts/verify_dify.py"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    (root / ".env").write_text("MODEL_API_KEY=private-package-test-key\n", encoding="utf-8")
    for name in ["data/private.txt", "station7/__pycache__/cache.py", "dist/old.zip"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private runtime data", encoding="utf-8")
    return root


def test_archive_excludes_runtime_data_and_is_reproducible(source, tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    manifest = build_archive(source, first)
    build_archive(source, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.testzip() is None
        assert "dify/.env.example" in archive.namelist()
        assert "dify/.env" not in archive.namelist()
        assert not any("__pycache__" in name or "/data/" in name or "/dist/" in name for name in archive.namelist())
    assert json.loads(first.with_suffix(".manifest.json").read_text())["sha256"] == manifest["sha256"]


def test_archive_rejects_credentials_in_evidence_without_echoing_key(source, tmp_path):
    path = source / "evidence" / "unsafe.json"
    path.parent.mkdir()
    path.write_text('{"leak":"private-package-test-key"}', encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_API_KEY") as error:
        build_archive(source, tmp_path / "rejected.zip")
    assert "private-package-test-key" not in str(error.value)
    assert not (tmp_path / "rejected.zip").exists()
