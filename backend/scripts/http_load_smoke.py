from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from statistics import quantiles
from time import perf_counter
import os
import sys
import urllib.error
import urllib.request


def _request(base_url: str, path: str, api_key: str | None) -> tuple[int, float]:
    request = urllib.request.Request(f"{base_url}{path}")
    if api_key:
        request.add_header("X-API-Key", api_key)
    started = perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
            return response.status, (perf_counter() - started) * 1000
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, (perf_counter() - started) * 1000


def main() -> int:
    base_url = os.getenv("PROAI_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    api_key = os.getenv("PROAI_AUTH_API_KEY") or None
    total_requests = int(os.getenv("PROAI_LOAD_REQUESTS", "80"))
    concurrency = int(os.getenv("PROAI_LOAD_CONCURRENCY", "8"))
    p95_limit_ms = float(os.getenv("PROAI_LOAD_P95_LIMIT_MS", "750"))
    paths = ["/api/ready", "/api/health", "/"]
    if api_key:
        paths.append("/api/metrics")

    jobs = [paths[index % len(paths)] for index in range(total_requests)]
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda path: _request(base_url, path, api_key), jobs))

    failures = [(status, latency) for status, latency in results if status >= 500]
    latencies = [latency for _, latency in results]
    p95 = quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
    if failures:
        print(f"load_smoke=failed server_errors={len(failures)}", file=sys.stderr)
        return 1
    if p95 > p95_limit_ms:
        print(
            f"load_smoke=failed p95_ms={p95:.2f} limit_ms={p95_limit_ms:.2f}",
            file=sys.stderr,
        )
        return 1

    print(
        "load_smoke=ok "
        f"requests={total_requests} concurrency={concurrency} "
        f"p95_ms={p95:.2f} max_ms={max(latencies):.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
