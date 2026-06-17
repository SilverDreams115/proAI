"""Securely check whether a password matches the deployed proAI hash.

Reads the PBKDF2 hash from (in order): the PROAI_AUTH_PASSWORD_HASH env
var, or the `.env` file at the repo root. Prompts for the password
without echo and prints ONLY a boolean verdict — never the password,
never the hash. Use this to confirm an access problem is a
password/hash mismatch rather than a frontend/cookie issue.

    python backend/scripts/verify_password.py
    python backend/scripts/verify_password.py --env-file /path/to/.env
"""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from app.core.auth import is_valid_password_hash, verify_password


def _hash_from_env_file(env_file: Path) -> str | None:
    if not env_file.exists():
        return None
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("PROAI_AUTH_PASSWORD_HASH="):
            value = line.split("=", 1)[1].strip()
            # Strip optional surrounding quotes that a human may have added.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            return value or None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=str(Path(__file__).resolve().parents[2] / ".env"),
        help="Path to the .env file holding PROAI_AUTH_PASSWORD_HASH.",
    )
    args = parser.parse_args()

    password_hash = os.getenv("PROAI_AUTH_PASSWORD_HASH") or _hash_from_env_file(Path(args.env_file))
    if not password_hash:
        print("NO_HASH_FOUND: PROAI_AUTH_PASSWORD_HASH no está en el entorno ni en el .env.")
        return 2
    if not is_valid_password_hash(password_hash):
        # Do not print the hash; only its shape so we can debug format issues.
        print(
            "INVALID_HASH_FORMAT: el hash existe pero no tiene el formato "
            "pbkdf2_sha256$iter$salt$digest "
            f"(longitud={len(password_hash)})."
        )
        return 3

    password = getpass.getpass("Password (no se muestra): ")
    if verify_password(password, password_hash):
        print("OK: la contraseña COINCIDE con el hash desplegado.")
        return 0
    print("FAIL: la contraseña NO coincide con el hash desplegado.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
