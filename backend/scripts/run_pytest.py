from __future__ import annotations

from pathlib import Path
import sys

import pytest


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    return pytest.main(sys.argv[1:] or ["-q"])


if __name__ == "__main__":
    raise SystemExit(main())
