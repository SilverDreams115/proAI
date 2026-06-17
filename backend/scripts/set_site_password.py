"""Safely reset the proAI site password (no DB changes).

Prompts for a new password twice (no echo), generates a PBKDF2 hash and
updates ONLY the PROAI_AUTH_PASSWORD_HASH line in the target .env file
(other lines are preserved byte-for-byte). It never prints the password
or the resulting hash. The database is never touched.

    python backend/scripts/set_site_password.py
    python backend/scripts/set_site_password.py --env-file /path/to/.env

After running, recreate the app container so it reloads the env:

    docker compose up -d proai

This does NOT rotate the session secret, so existing valid sessions stay
valid; only the password needed to obtain a NEW session changes.
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from app.core.auth import hash_password

_KEY = "PROAI_AUTH_PASSWORD_HASH"


def _update_env_file(env_file: Path, new_hash: str) -> str:
    """Replace (or append) the password-hash line, preserving all others.

    Returns "updated" or "appended" so the caller can report the action.
    The value is written WITHOUT surrounding quotes; the hash contains no
    spaces or shell metacharacters that env_file parsing cares about.
    """
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    replaced = False
    for index, raw_line in enumerate(lines):
        if raw_line.strip().startswith(f"{_KEY}="):
            lines[index] = f"{_KEY}={new_hash}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{_KEY}={new_hash}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "updated" if replaced else "appended"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=str(Path(__file__).resolve().parents[2] / ".env"),
        help="Path to the .env file to update.",
    )
    args = parser.parse_args()
    env_file = Path(args.env_file)

    password = getpass.getpass("Nueva contraseña (no se muestra): ")
    if len(password) < 8:
        print("ABORTADO: usa al menos 8 caracteres.")
        return 2
    confirm = getpass.getpass("Confírmala: ")
    if password != confirm:
        print("ABORTADO: las contraseñas no coinciden.")
        return 2

    new_hash = hash_password(password)
    action = _update_env_file(env_file, new_hash)
    # Never print the password or the hash — only the outcome.
    print(f"OK: {_KEY} {action} en {env_file}.")
    print("Siguiente paso: docker compose up -d proai   (recrea el contenedor y recarga el env; no rebuild, no toca DB).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
