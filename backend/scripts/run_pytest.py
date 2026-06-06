from __future__ import annotations

import sys

import pytest


def main() -> int:
    return pytest.main(sys.argv[1:] or ["-q"])


if __name__ == "__main__":
    raise SystemExit(main())
