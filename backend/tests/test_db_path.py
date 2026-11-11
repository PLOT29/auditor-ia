"""La BD nunca vive dentro del repo (SPEC §5.5, §12 regla 8)."""
from pathlib import Path

import db

REPO = Path(__file__).resolve().parents[2]


def test_default_path_is_outside_repo(monkeypatch):
    monkeypatch.delenv("AUDITOR_DB_PATH", raising=False)
    path = db.db_path()
    assert path == Path(r"C:\auditor-data\auditor.db")
    assert REPO not in path.resolve().parents


def test_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", "  ")
    assert db.db_path() == db.DEFAULT_DB_PATH


def test_env_var_is_used_and_folder_created(tmp_path, monkeypatch):
    target = tmp_path / "no" / "existe" / "aun.db"
    monkeypatch.setenv("AUDITOR_DB_PATH", str(target))
    assert db.db_path() == target
    db.init_db()
    assert target.exists()
