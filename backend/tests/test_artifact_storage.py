"""Tests for the filesystem booster storage (Fase 2.1)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.services import artifact_storage


def test_save_and_load_round_trip(tmp_path: Path, monkeypatch) -> None:
    """A saved booster comes back byte-for-byte, with hash verified."""
    monkeypatch.setenv("PROAI_MODEL_STORAGE_ROOT", str(tmp_path))
    payload = '{"booster":"json-bytes"}'
    descriptor = artifact_storage.save_booster_json("test_model", "run-1", payload)
    assert descriptor["sha256"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert descriptor["bytes"] == str(len(payload))
    loaded = artifact_storage.load_booster_json(descriptor)
    assert loaded == payload


def test_load_rejects_tampered_file(tmp_path: Path, monkeypatch) -> None:
    """A descriptor whose sha does not match the on-disk file must return
    None (the caller falls back to the heuristic engine)."""
    monkeypatch.setenv("PROAI_MODEL_STORAGE_ROOT", str(tmp_path))
    descriptor = artifact_storage.save_booster_json("test_model", "run-2", "original")
    # Tamper with the file on disk.
    Path(descriptor["path"]).write_text("tampered", encoding="utf-8")
    assert artifact_storage.load_booster_json(descriptor) is None


def test_load_returns_none_when_file_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PROAI_MODEL_STORAGE_ROOT", str(tmp_path))
    descriptor = {"path": str(tmp_path / "does-not-exist.json"), "sha256": "deadbeef"}
    assert artifact_storage.load_booster_json(descriptor) is None


def test_descriptor_keeps_path_under_storage_root(tmp_path: Path, monkeypatch) -> None:
    """Sanity: the descriptor's path lives under the configured root."""
    monkeypatch.setenv("PROAI_MODEL_STORAGE_ROOT", str(tmp_path))
    descriptor = artifact_storage.save_booster_json("custom_model", "run-3", "payload")
    assert Path(descriptor["path"]).is_relative_to(tmp_path)
    assert Path(descriptor["path"]).name == "run-3.json"
