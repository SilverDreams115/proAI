"""Filesystem storage for heavy ML artifacts (Fase 2.1).

Persists the XGBoost booster JSON in `/data/models/<model_name>/<id>.json`
with an SHA-256 sidecar so a tampered file is rejected at load time. The
DB artifact keeps only the metadata (filename + hash); the binary itself
never travels through the database.

Why this matters:
- The DB row stays small and fast to read.
- A SELECT on training_runs no longer exposes a deserializable blob.
- Booster retention can be governed by filesystem rotation independently
  from training_runs rows.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


DEFAULT_ROOT = Path("/data/models")


def _root() -> Path:
    return Path(os.getenv("PROAI_MODEL_STORAGE_ROOT") or DEFAULT_ROOT)


def storage_root() -> Path:
    """Public read accessor used by tests and admin reports."""
    return _root()


def save_booster_json(model_name: str, run_id: str, booster_json: str) -> dict[str, str]:
    """Write the booster to disk and return its storage descriptor.

    Args:
        model_name: e.g. `elo_poisson_blend`. Becomes the parent directory.
        run_id: stable identifier (matches the DB row id).
        booster_json: native XGBoost JSON, produced by
            `Booster.save_raw(raw_format="json")`.

    Returns:
        Storage descriptor `{path, sha256, bytes}` ready to round-trip in
        the artifact JSON. The DB layer never sees the booster bytes again.
    """
    root = _root() / model_name
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{run_id}.json"
    payload = booster_json.encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    target.write_bytes(payload)
    return {
        "path": str(target),
        "sha256": sha,
        "bytes": str(len(payload)),
    }


def load_booster_json(descriptor: dict[str, str]) -> str | None:
    """Read the booster, verifying the SHA-256 against the descriptor.

    Returns:
        Booster JSON text, or None if the file is missing or the hash does
        not match. Caller treats None as "fallback to heuristic engine".
    """
    path = Path(descriptor.get("path") or "")
    expected_hash = descriptor.get("sha256")
    if not path.is_file() or not expected_hash:
        return None
    payload = path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_hash:
        return None
    return payload.decode("utf-8", errors="replace")
