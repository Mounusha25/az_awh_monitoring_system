"""Tests for push_to_main_repo.py: exclusion rules and the diff planner."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import push_to_main_repo as p  # noqa: E402


@pytest.mark.parametrize("rel", [
    "awh_az/backend/serviceAccountKey.json",
    "awh_az/backend/awh-project-460421-52cd6ebf2aa3.json",
    "awh_az/water-station-dashboard/.env.local",
    "awh_az/water-station-dashboard/.env.production",
    ".env",
    "firebase-key.json",
    "keys/server.pem",
    "awh_az/water-station-dashboard/node_modules/react/index.js",
    "awh_az/water-station-dashboard/.next/cache/x",
    "awh_az/backend/venv/lib/x.py",
    "awh_az/backend/__pycache__/main.cpython-313.pyc",
    "awh_az/water-station-dashboard/next-env.d.ts",
    "awh_az/water-station-dashboard/tsconfig.tsbuildinfo",
])
def test_excluded(rel):
    assert p.exclusion_reason(rel)


@pytest.mark.parametrize("rel", [
    "awh_az/backend/main.py",
    "awh_az/backend/.env.example",
    "awh_az/water-station-dashboard/.env.local.example",
    "guides/ARCHITECTURE.md",
    "awh_az/backend/tests/test_api.py",
])
def test_allowed(rel):
    assert p.exclusion_reason(rel) is None


def _write(base: Path, rel: str, text: str):
    f = base / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def test_plan_classifies_files(tmp_path):
    local, clone = tmp_path / "local", tmp_path / "clone"
    _write(clone, "awh_az/backend/same.py", "x")
    _write(clone, "awh_az/backend/old.py", "old")
    _write(clone, "awh_az/backend/only_in_main.py", "m")
    _write(local, "awh_az/backend/same.py", "x")
    _write(local, "awh_az/backend/old.py", "new")
    _write(local, "awh_az/backend/brand_new.py", "n")
    _write(local, "awh_az/backend/serviceAccountKey.json", "SECRET")
    _write(local, "research_extension/model.py", "r")   # top-level absent in clone
    _write(local, "RPi_USB_Package.zip", "z")           # top-level file absent in clone

    files = ["awh_az/backend/same.py", "awh_az/backend/old.py", "awh_az/backend/brand_new.py",
             "awh_az/backend/serviceAccountKey.json", "research_extension/model.py", "RPi_USB_Package.zip"]
    res = p.plan(local, clone, files)

    assert res["changed"] == ["awh_az/backend/old.py"]
    assert res["new"] == ["awh_az/backend/brand_new.py"]
    assert "awh_az/backend/serviceAccountKey.json" in res["excluded"]
    assert res["skipped_top"] == {"research_extension", "RPi_USB_Package.zip"}


def test_plan_allow_new_top_level(tmp_path):
    local, clone = tmp_path / "local", tmp_path / "clone"
    clone.mkdir()
    _write(local, "research_extension/model.py", "r")
    res = p.plan(local, clone, ["research_extension/model.py"], allow_new_top_level=True)
    assert res["new"] == ["research_extension/model.py"]


def test_plan_skips_oversized(tmp_path, monkeypatch):
    local, clone = tmp_path / "local", tmp_path / "clone"
    (clone / "guides").mkdir(parents=True)
    _write(local, "guides/big.bin", "x" * 50)
    monkeypatch.setattr(p, "MAX_BYTES", 10)
    res = p.plan(local, clone, ["guides/big.bin"])
    assert res["too_big"] == ["guides/big.bin"] and not res["new"]


def test_apply_requires_message():
    with pytest.raises(SystemExit):
        p.main(["--apply"])
