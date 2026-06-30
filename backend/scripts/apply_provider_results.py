"""R6.3 — Apply free-provider results to match_results (GUARDED, not active).

Intentionally inert in this phase. Applying external results to the productive
``match_results`` table requires an explicit, typed confirmation token AND the
provider to be enabled out of dry-run-only mode. Until that is deliberately
turned on, this script refuses to write and exits non-zero — it documents the
apply contract without performing it.

Required to even attempt an apply::

    python -m scripts.apply_provider_results --draw-code PG-2338 \
        --apply --confirm APPLY-PROVIDER-RESULTS-ONLY

Even then it is blocked unless ``PROAI_RESULTS_PROVIDER_ENABLED=true`` and
``PROAI_RESULTS_PROVIDER_DRY_RUN_ONLY=false``. No automatic apply ever runs.
"""
from __future__ import annotations

import argparse

from app.core.settings import settings

CONFIRM_TOKEN = "APPLY-PROVIDER-RESULTS-ONLY"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply provider results to match_results (GUARDED — not active in R6.3)."
    )
    parser.add_argument("--draw-code", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    if not args.apply or args.confirm != CONFIRM_TOKEN:
        print(
            "BLOCKED: apply requires --apply --confirm "
            f"{CONFIRM_TOKEN}. No results were written."
        )
        return 2

    if not settings.results_provider_enabled or settings.results_provider_dry_run_only:
        print(
            "BLOCKED: results provider is disabled or dry-run-only "
            "(PROAI_RESULTS_PROVIDER_ENABLED / PROAI_RESULTS_PROVIDER_DRY_RUN_ONLY). "
            "Apply is not permitted in this phase. No results were written."
        )
        return 3

    # Reaching here would be a deliberate, future opt-in. Until the apply path is
    # implemented and reviewed, refuse rather than write productive data.
    print(
        "NOT IMPLEMENTED: the provider-results apply path is intentionally not "
        "implemented in R6.3. No results were written."
    )
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
