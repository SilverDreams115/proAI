from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8", errors="replace")


def _chromium_binary() -> str:
    configured = os.getenv("PROAI_CHROMIUM_BIN")
    if configured:
        return configured
    for candidate in ("chromium", "chromium-browser", "google-chrome"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError("No Chromium-compatible browser was found on PATH.")


def main() -> int:
    base_url = os.getenv("PROAI_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    index_html = _fetch(f"{base_url}/")
    required_assets = ("config.js", "ui-utils.js", "api-client.js", "app.js", "styles.css")
    missing_assets = [asset for asset in required_assets if asset not in index_html]
    if missing_assets:
        print(f"Missing asset references in HTML: {', '.join(missing_assets)}", file=sys.stderr)
        return 1

    browser = _chromium_binary()
    with tempfile.TemporaryDirectory(prefix="proai-chromium-") as user_data_dir:
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--user-data-dir={user_data_dir}",
            "--virtual-time-budget=5000",
            # A cold headless launch on a CI runner spends most of its time
            # here: no warm profile, no shader cache, no prefetched fonts.
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--dump-dom",
            f"{base_url}/",
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            # 20s was enough on a developer machine with a warm browser and
            # not on a GitHub runner, where the first headless launch is cold:
            # CI failed here with TimeoutExpired after the container itself was
            # already answering /api/ready. Overridable so a slow runner can be
            # given more room without a code change.
            timeout=float(os.getenv("PROAI_SMOKE_BROWSER_TIMEOUT", "90")),
        )
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)
        return completed.returncode

    dom = completed.stdout
    required_dom = (
        "Quiniela inteligente",
        "login-form",
        "auth-password",
        "ops-panel",
        "ticket-tabs",
        "match-menu",
    )
    missing_dom = [item for item in required_dom if item not in dom]
    if missing_dom:
        print(f"Missing expected DOM content: {', '.join(missing_dom)}", file=sys.stderr)
        return 1
    console_failures = ("Uncaught SyntaxError", "Uncaught ReferenceError", "Uncaught TypeError")
    if any(marker in completed.stderr for marker in console_failures):
        print(completed.stderr, file=sys.stderr)
        return 1

    print("frontend_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
