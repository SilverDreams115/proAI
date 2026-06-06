from __future__ import annotations

from collections import Counter
from collections import defaultdict
from threading import Lock


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricsStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._request_counts: Counter[tuple[str, str, int]] = Counter()
        self._request_latency_sums: defaultdict[tuple[str, str], float] = defaultdict(float)
        self._request_latency_counts: Counter[tuple[str, str]] = Counter()
        self._auth_failures: Counter[tuple[str, str]] = Counter()
        self._ingestion_runs: Counter[tuple[str, str]] = Counter()
        self._ingestion_duration_sums: defaultdict[tuple[str, str], float] = defaultdict(float)
        self._source_health_checks: Counter[tuple[str, str]] = Counter()
        # Model observability — Fase 2.2.
        self._model_brier: dict[str, float] = {}
        self._model_log_loss: dict[str, float] = {}
        self._model_hit_rate: dict[str, float] = {}
        self._model_matches_evaluated: dict[str, int] = {}
        self._prediction_engine_counts: Counter[str] = Counter()
        self._prediction_latency_sums: defaultdict[str, float] = defaultdict(float)
        self._prediction_latency_counts: Counter[str] = Counter()
        self._calibration_breakpoints: dict[tuple[str, str], int] = {}

    def record_request(self, *, method: str, path: str, status_code: int, duration_ms: float) -> None:
        with self._lock:
            self._request_counts[(method, path, status_code)] += 1
            self._request_latency_sums[(method, path)] += duration_ms / 1000.0
            self._request_latency_counts[(method, path)] += 1

    def record_auth_failure(self, *, method: str, path: str) -> None:
        with self._lock:
            self._auth_failures[(method, path)] += 1

    def record_ingestion_run(self, *, source_name: str, status: str, duration_ms: float) -> None:
        with self._lock:
            self._ingestion_runs[(source_name, status)] += 1
            self._ingestion_duration_sums[(source_name, status)] += duration_ms / 1000.0

    def record_source_health_check(self, *, source_name: str, status: str) -> None:
        with self._lock:
            self._source_health_checks[(source_name, status)] += 1

    def record_model_evaluation(
        self,
        *,
        competition_key: str,
        brier_score: float,
        log_loss: float,
        hit_rate: float,
        matches_evaluated: int,
    ) -> None:
        with self._lock:
            self._model_brier[competition_key] = brier_score
            self._model_log_loss[competition_key] = log_loss
            self._model_hit_rate[competition_key] = hit_rate
            self._model_matches_evaluated[competition_key] = matches_evaluated

    def record_prediction(self, *, engine: str, duration_seconds: float) -> None:
        with self._lock:
            self._prediction_engine_counts[engine] += 1
            self._prediction_latency_sums[engine] += duration_seconds
            self._prediction_latency_counts[engine] += 1

    def record_calibration_curve(
        self, *, competition_key: str, class_label: str, breakpoints: int
    ) -> None:
        with self._lock:
            self._calibration_breakpoints[(competition_key, class_label)] = breakpoints

    def render_prometheus(self, *, app_name: str, app_version: str, environment: str) -> str:
        lines = [
            "# HELP proai_app_info Static application metadata.",
            "# TYPE proai_app_info gauge",
            (
                "proai_app_info"
                f'{{app="{_escape_label(app_name)}",version="{_escape_label(app_version)}",'
                f'environment="{_escape_label(environment)}"}} 1'
            ),
            "# HELP proai_http_requests_total Total HTTP requests handled by the app.",
            "# TYPE proai_http_requests_total counter",
        ]
        with self._lock:
            for (method, path, status_code), count in sorted(self._request_counts.items()):
                lines.append(
                    "proai_http_requests_total"
                    f'{{method="{_escape_label(method)}",path="{_escape_label(path)}",status="{status_code}"}} {count}'
                )

            lines.extend(
                [
                    "# HELP proai_http_request_duration_seconds_sum Total request latency in seconds.",
                    "# TYPE proai_http_request_duration_seconds_sum counter",
                ]
            )
            for (method, path), total_seconds in sorted(self._request_latency_sums.items()):
                lines.append(
                    "proai_http_request_duration_seconds_sum"
                    f'{{method="{_escape_label(method)}",path="{_escape_label(path)}"}} {total_seconds:.6f}'
                )

            lines.extend(
                [
                    "# HELP proai_http_request_duration_seconds_count Number of latency samples.",
                    "# TYPE proai_http_request_duration_seconds_count counter",
                ]
            )
            for (method, path), count in sorted(self._request_latency_counts.items()):
                lines.append(
                    "proai_http_request_duration_seconds_count"
                    f'{{method="{_escape_label(method)}",path="{_escape_label(path)}"}} {count}'
                )

            lines.extend(
                [
                    "# HELP proai_http_auth_failures_total Failed authentication attempts.",
                    "# TYPE proai_http_auth_failures_total counter",
                ]
            )
            for (method, path), count in sorted(self._auth_failures.items()):
                lines.append(
                    "proai_http_auth_failures_total"
                    f'{{method="{_escape_label(method)}",path="{_escape_label(path)}"}} {count}'
                )

            lines.extend(
                [
                    "# HELP proai_ingestion_runs_total Ingestion runs by source and final status.",
                    "# TYPE proai_ingestion_runs_total counter",
                ]
            )
            for (source_name, status), count in sorted(self._ingestion_runs.items()):
                lines.append(
                    "proai_ingestion_runs_total"
                    f'{{source="{_escape_label(source_name)}",status="{_escape_label(status)}"}} {count}'
                )

            lines.extend(
                [
                    "# HELP proai_ingestion_run_duration_seconds_sum Total ingestion duration in seconds.",
                    "# TYPE proai_ingestion_run_duration_seconds_sum counter",
                ]
            )
            for (source_name, status), total_seconds in sorted(self._ingestion_duration_sums.items()):
                lines.append(
                    "proai_ingestion_run_duration_seconds_sum"
                    f'{{source="{_escape_label(source_name)}",status="{_escape_label(status)}"}} {total_seconds:.6f}'
                )

            lines.extend(
                [
                    "# HELP proai_source_health_checks_total Source health checks by source and status.",
                    "# TYPE proai_source_health_checks_total counter",
                ]
            )
            for (source_name, status), count in sorted(self._source_health_checks.items()):
                lines.append(
                    "proai_source_health_checks_total"
                    f'{{source="{_escape_label(source_name)}",status="{_escape_label(status)}"}} {count}'
                )

            # Model quality gauges by competition (Fase 2.2).
            lines.extend(
                [
                    "# HELP proai_model_brier_score Brier score from the most recent walk-forward evaluation.",
                    "# TYPE proai_model_brier_score gauge",
                ]
            )
            for competition_key, value in sorted(self._model_brier.items()):
                lines.append(
                    f'proai_model_brier_score{{competition="{_escape_label(competition_key)}"}} {value:.6f}'
                )
            lines.extend(
                [
                    "# HELP proai_model_log_loss Log loss from the most recent walk-forward evaluation.",
                    "# TYPE proai_model_log_loss gauge",
                ]
            )
            for competition_key, value in sorted(self._model_log_loss.items()):
                lines.append(
                    f'proai_model_log_loss{{competition="{_escape_label(competition_key)}"}} {value:.6f}'
                )
            lines.extend(
                [
                    "# HELP proai_model_hit_rate Top-class accuracy from the most recent walk-forward evaluation.",
                    "# TYPE proai_model_hit_rate gauge",
                ]
            )
            for competition_key, value in sorted(self._model_hit_rate.items()):
                lines.append(
                    f'proai_model_hit_rate{{competition="{_escape_label(competition_key)}"}} {value:.6f}'
                )
            lines.extend(
                [
                    "# HELP proai_model_matches_evaluated Match count behind the latest evaluation per competition.",
                    "# TYPE proai_model_matches_evaluated gauge",
                ]
            )
            for competition_key, count in sorted(self._model_matches_evaluated.items()):
                lines.append(
                    f'proai_model_matches_evaluated{{competition="{_escape_label(competition_key)}"}} {count}'
                )

            # Per-engine prediction throughput and latency.
            lines.extend(
                [
                    "# HELP proai_prediction_engine_total Predictions served, by scoring engine.",
                    "# TYPE proai_prediction_engine_total counter",
                ]
            )
            for engine, count in sorted(self._prediction_engine_counts.items()):
                lines.append(
                    f'proai_prediction_engine_total{{engine="{_escape_label(engine)}"}} {count}'
                )
            lines.extend(
                [
                    "# HELP proai_prediction_latency_seconds_sum Total scoring latency in seconds, by engine.",
                    "# TYPE proai_prediction_latency_seconds_sum counter",
                ]
            )
            for engine, total_seconds in sorted(self._prediction_latency_sums.items()):
                lines.append(
                    f'proai_prediction_latency_seconds_sum{{engine="{_escape_label(engine)}"}} {total_seconds:.6f}'
                )
            lines.extend(
                [
                    "# HELP proai_prediction_latency_seconds_count Scoring latency sample count, by engine.",
                    "# TYPE proai_prediction_latency_seconds_count counter",
                ]
            )
            for engine, count in sorted(self._prediction_latency_counts.items()):
                lines.append(
                    f'proai_prediction_latency_seconds_count{{engine="{_escape_label(engine)}"}} {count}'
                )

            # Calibration curve size (after PAV pooling) by (competition, class).
            lines.extend(
                [
                    "# HELP proai_calibration_curve_breakpoints PAV breakpoints kept per (competition, class).",
                    "# TYPE proai_calibration_curve_breakpoints gauge",
                ]
            )
            for (competition_key, class_label), count in sorted(self._calibration_breakpoints.items()):
                lines.append(
                    "proai_calibration_curve_breakpoints"
                    f'{{competition="{_escape_label(competition_key)}",class="{_escape_label(class_label)}"}} {count}'
                )
        lines.append("")
        return "\n".join(lines)


metrics_store = MetricsStore()
