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
    # google-chrome first, then chromium. On GitHub's ubuntu runners
    # /usr/bin/chromium is a snap wrapper, and a snap-confined browser hangs
    # in a non-interactive CI environment: --dump-dom never returned there,
    # not even after 90s, while the same page dumps in under a second against
    # a normally packaged chromium. Google Chrome ships as a real deb on those
    # runners, so preferring it sidesteps the confinement entirely.
    for candidate in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
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
            # --virtual-time-budget alone never settles on this page: app.js
            # installs setInterval(tickCountdown, 1000) and
            # setInterval(pollActiveSlate, 60000) at load, so there is always
            # another task queued and Chromium waits forever with --dump-dom.
            # It hung past 90s in CI. --timeout forces the dump after a fixed
            # wall-clock window regardless of what the page still has pending.
            "--virtual-time-budget=5000",
            "--timeout=15000",
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
