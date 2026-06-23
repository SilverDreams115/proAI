import os
from functools import lru_cache
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from app.core.auth import is_valid_password_hash


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_csv(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = [item.strip() for item in raw.split(",")]
    return [item for item in values if item]


class Settings(BaseModel):
    model_config = ConfigDict(frozen=False)

    app_name: str = "proAI"
    app_version: str = "0.1.0"
    environment: str = Field(default="development")
    database_url: str = Field(default="sqlite:///./proai.db")
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=True)
    access_log_enabled: bool = Field(default=True)
    docs_enabled: bool = Field(default=True)
    request_id_header: str = Field(default="X-Request-ID")
    healthcheck_timeout_seconds: float = Field(default=2.0)
    auth_required: bool = Field(default=False)
    auth_api_key: str | None = Field(default=None)
    auth_password_hash: str | None = Field(default=None)
    session_secret: str | None = Field(default=None)
    auth_session_cookie_name: str = Field(default="proai_session")
    auth_session_ttl_seconds: int = Field(default=43200)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["*"])
    cors_allowed_origins: list[str] = Field(default_factory=list)
    force_https: bool = Field(default=False)
    enable_worker_routes: bool = Field(default=True)
    enforce_production_config: bool = Field(default=False)
    worker_poll_interval_seconds: int = Field(default=30)
    current_progol_auto_refresh_enabled: bool = Field(default=True)
    current_progol_refresh_interval_minutes: int = Field(default=60)
    current_progol_refresh_job_name: str = Field(default="current-progol-refresh")
    progol_proposal_observe_enabled: bool = Field(default=True)
    progol_proposal_observe_interval_minutes: int = Field(default=60)
    # Fase 3: auto-promote validated proposals when the active slate's
    # cierre is imminent (or no active slate exists). Threshold is in
    # hours; default 2 means "promote the next concurso once we're
    # inside the last 2h before the current one closes".
    progol_auto_promote_enabled: bool = Field(default=True)
    progol_auto_promote_threshold_hours: float = Field(default=2.0)
    # Live results observer: persist a final JornadaScore once a closed
    # slate has all-final results. Interval is intentionally coarse — the
    # observer is read-mostly and never fabricates a result.
    live_results_observe_enabled: bool = Field(default=True)
    live_results_observe_interval_minutes: int = Field(default=5)
    # Automated LN results fetch in the worker. Off by default: with no
    # configured URL the worker only finalizes already-ingested results.
    # Set the URL (and enable) to auto-pull official marcadores.
    live_results_fetch_enabled: bool = Field(default=False)
    live_results_source_url: str | None = Field(default=None)
    allow_pickle_model_artifacts: bool = Field(default=False)
    live_pick_ready_competitions: list[str] = Field(default_factory=lambda: ["e0", "premier-league"])
    live_pick_blocked_competitions: list[str] = Field(default_factory=lambda: ["i1", "serie-a", "d1", "bundesliga"])
    # Periodic prune of unlinked source_documents. The worker checks
    # every cycle but skips unless `interval_hours` have elapsed since
    # the last successful prune. 0 disables the maintenance entirely.
    source_documents_prune_interval_hours: int = Field(default=24)
    source_documents_retention_days: int = Field(default=90)
    # Global API rate limit. window_seconds == 60 + max_requests == 120
    # gives 2 rps sustained per IP — comfortable for an internal tool
    # and tight enough to absorb a misbehaving script before it pages
    # us. Set max_requests to 0 to disable; defaults to OFF in
    # non-production so dev loops aren't throttled.
    rate_limit_window_seconds: int = Field(default=60)
    rate_limit_max_requests: int = Field(default=0)
    # API-Football (api-sports.io v3) connector. Audit-only for now:
    # OFF by default, no key, no base_url. The connector never makes an
    # external call unless `enabled` is true AND a key is present, so the
    # app boots cleanly with these blank. Not wired into the worker.
    apifootball_enabled: bool = Field(default=False)
    apifootball_api_key: str | None = Field(default=None)
    apifootball_base_url: str | None = Field(default=None)
    # Sentry SDK is opt-in. With no DSN set the SDK import is skipped
    # entirely (zero overhead). When the DSN is present we tag events
    # with the environment and the asset version hash so each release is
    # distinguishable in the Sentry UI.
    sentry_dsn: str | None = Field(default=None)
    sentry_traces_sample_rate: float = Field(default=0.0)
    sentry_profiles_sample_rate: float = Field(default=0.0)
    # Team-rating feature read-only adapter (R3). OFF by default: with this
    # flag false the rating helper returns nothing and NOTHING in the
    # prediction/feature path consults a rating. Flipping it on only lets a
    # future, explicitly-wired feature layer READ the latest active rating
    # snapshot — it never changes predictions on its own. See
    # docs/team_rating_activation_protocol.md.
    team_rating_feature_enabled: bool = Field(default=False)
    # Team-rating controlled gate (R5.0). INACTIVE by default and NOT wired
    # into PredictionService: these flags only configure the pure gate
    # predicate / dry-run auditor. With team_rating_gate_enabled false the
    # gate always returns eligible=false (flag_disabled), so probabilities are
    # never affected. See docs/team_rating_gate_calibration_metadata.md.
    team_rating_gate_enabled: bool = Field(default=False)
    team_rating_gate_competitions: list[str] = Field(
        default_factory=lambda: ["International Friendlies"]
    )
    team_rating_gate_require_both_medium_plus: bool = Field(default=True)
    team_rating_gate_require_calibrator: bool = Field(default=True)
    team_rating_gate_min_test_rows: int = Field(default=150)

    # Team-rating controlled canary (R5.6-B). OFF by default. When enabled it
    # only post-processes the prediction API response for the configured
    # draw-codes/positions/competition: it recalibrates the *served* effective
    # probabilities via the approved temperature candidate. It never writes the
    # DB, never regenerates predictions, and never touches the ticket optimizer.
    team_rating_canary_enabled: bool = Field(default=False)
    # Scope policy (R5.6-D). "draw_code_allowlist" (default) limits the canary
    # to the configured draw_codes. "active_upcoming" applies it to every
    # active/upcoming slate by rule (and, if draw_codes is non-empty, still
    # restricted to those). In every case blockers/gating are never ignored.
    team_rating_canary_scope: str = Field(default="draw_code_allowlist")
    team_rating_canary_draw_codes: list[str] = Field(default_factory=list)
    team_rating_canary_positions: list[int] = Field(default_factory=list)
    team_rating_canary_calibrator_id: str = Field(
        default="international_friendlies_temperature_v1"
    )
    team_rating_canary_routing_policy: str = Field(
        default="rating_replaces_fallback"
    )
    team_rating_canary_competition_allowlist: list[str] = Field(
        default_factory=lambda: ["International Friendlies"]
    )

    @property
    def docs_url(self) -> str | None:
        return "/docs" if self.docs_enabled else None

    @property
    def redoc_url(self) -> str | None:
        return "/redoc" if self.docs_enabled else None

    @property
    def openapi_url(self) -> str | None:
        return "/openapi.json" if self.docs_enabled else None

    def validate_runtime(self) -> None:
        if self.environment.lower() == "production" and self.auth_required and not self.auth_api_key:
            raise ValueError("PROAI_AUTH_API_KEY must be configured when authentication is required.")
        if (
            self.environment.lower() == "production"
            and self.auth_required
            and self.auth_password_hash
            and not self.session_secret
        ):
            raise ValueError("PROAI_SESSION_SECRET must be configured when password authentication is enabled.")
        if self.environment.lower() == "production" and self.enforce_production_config:
            errors = self.production_config_errors()
            if errors:
                raise ValueError("Invalid production configuration: " + "; ".join(errors))

    def production_config_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.auth_required:
            errors.append("PROAI_AUTH_REQUIRED must be true")
        if self.docs_enabled:
            errors.append("PROAI_DOCS_ENABLED must be false")
        if self.enable_worker_routes:
            errors.append("PROAI_ENABLE_WORKER_ROUTES must be false")
        if self.allowed_hosts == ["*"] or "*" in self.allowed_hosts:
            errors.append("PROAI_ALLOWED_HOSTS must not allow '*'")
        if self.auth_required and _is_placeholder_secret(self.auth_api_key):
            errors.append("PROAI_AUTH_API_KEY must be a strong non-placeholder value")
        if self.auth_required and not is_valid_password_hash(self.auth_password_hash):
            errors.append("PROAI_AUTH_PASSWORD_HASH must be a valid PBKDF2-SHA256 hash")
        if self.auth_required and _is_placeholder_secret(self.session_secret):
            errors.append("PROAI_SESSION_SECRET must be a strong non-placeholder value")
        database_password = _database_password(self.database_url)
        if _is_placeholder_secret(database_password):
            errors.append("PROAI_DATABASE_URL must use a strong non-placeholder database password")
        return errors

    @property
    def safe_database_url(self) -> str:
        return redact_url_secret(self.database_url)


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    environment = os.getenv("PROAI_ENVIRONMENT", "development")
    settings = Settings(
        app_name=os.getenv("PROAI_APP_NAME", "proAI"),
        app_version=os.getenv("PROAI_APP_VERSION", "0.1.0"),
        environment=environment,
        database_url=os.getenv("PROAI_DATABASE_URL", "sqlite:///./proai.db"),
        api_host=os.getenv("PROAI_API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("PROAI_API_PORT", "8000")),
        log_level=os.getenv("PROAI_LOG_LEVEL", "INFO").upper(),
        log_json=_get_bool("PROAI_LOG_JSON", True),
        access_log_enabled=_get_bool("PROAI_ACCESS_LOG_ENABLED", True),
        docs_enabled=_get_bool("PROAI_DOCS_ENABLED", True),
        request_id_header=os.getenv("PROAI_REQUEST_ID_HEADER", "X-Request-ID"),
        healthcheck_timeout_seconds=float(os.getenv("PROAI_HEALTHCHECK_TIMEOUT_SECONDS", "2.0")),
        auth_required=_get_bool("PROAI_AUTH_REQUIRED", environment.lower() == "production"),
        auth_api_key=os.getenv("PROAI_AUTH_API_KEY"),
        auth_password_hash=os.getenv("PROAI_AUTH_PASSWORD_HASH"),
        session_secret=os.getenv("PROAI_SESSION_SECRET"),
        auth_session_cookie_name=os.getenv("PROAI_AUTH_SESSION_COOKIE_NAME", "proai_session"),
        auth_session_ttl_seconds=int(os.getenv("PROAI_AUTH_SESSION_TTL_SECONDS", "43200")),
        allowed_hosts=_get_csv("PROAI_ALLOWED_HOSTS", ["*"]),
        cors_allowed_origins=_get_csv("PROAI_CORS_ALLOWED_ORIGINS", []),
        force_https=_get_bool("PROAI_FORCE_HTTPS", False),
        enable_worker_routes=_get_bool("PROAI_ENABLE_WORKER_ROUTES", environment.lower() != "production"),
        enforce_production_config=_get_bool("PROAI_ENFORCE_PRODUCTION_CONFIG", False),
        worker_poll_interval_seconds=int(os.getenv("PROAI_WORKER_POLL_INTERVAL_SECONDS", "30")),
        current_progol_auto_refresh_enabled=_get_bool("PROAI_CURRENT_PROGOL_AUTO_REFRESH_ENABLED", True),
        current_progol_refresh_interval_minutes=int(
            os.getenv("PROAI_CURRENT_PROGOL_REFRESH_INTERVAL_MINUTES", "60")
        ),
        current_progol_refresh_job_name=os.getenv(
            "PROAI_CURRENT_PROGOL_REFRESH_JOB_NAME",
            "current-progol-refresh",
        ),
        progol_proposal_observe_enabled=_get_bool("PROAI_PROGOL_PROPOSAL_OBSERVE_ENABLED", True),
        progol_proposal_observe_interval_minutes=int(
            os.getenv("PROAI_PROGOL_PROPOSAL_OBSERVE_INTERVAL_MINUTES", "60")
        ),
        progol_auto_promote_enabled=_get_bool("PROAI_PROGOL_AUTO_PROMOTE_ENABLED", True),
        progol_auto_promote_threshold_hours=float(
            os.getenv("PROAI_PROGOL_AUTO_PROMOTE_THRESHOLD_HOURS", "2.0")
        ),
        live_results_observe_enabled=_get_bool("PROAI_LIVE_RESULTS_OBSERVE_ENABLED", True),
        live_results_observe_interval_minutes=int(
            os.getenv("PROAI_LIVE_RESULTS_OBSERVE_INTERVAL_MINUTES", "5")
        ),
        live_results_fetch_enabled=_get_bool("PROAI_LIVE_RESULTS_FETCH_ENABLED", False),
        live_results_source_url=os.getenv("PROAI_LIVE_RESULTS_SOURCE_URL") or None,
        allow_pickle_model_artifacts=_get_bool(
            "PROAI_ALLOW_PICKLE_MODEL_ARTIFACTS",
            environment.lower() != "production",
        ),
        live_pick_ready_competitions=_get_csv(
            "PROAI_LIVE_PICK_READY_COMPETITIONS",
            ["e0", "premier-league"],
        ),
        live_pick_blocked_competitions=_get_csv(
            "PROAI_LIVE_PICK_BLOCKED_COMPETITIONS",
            ["i1", "serie-a", "d1", "bundesliga"],
        ),
        source_documents_prune_interval_hours=int(
            os.getenv("PROAI_SOURCE_DOCUMENTS_PRUNE_INTERVAL_HOURS", "24")
        ),
        source_documents_retention_days=int(
            os.getenv("PROAI_SOURCE_DOCUMENTS_RETENTION_DAYS", "90")
        ),
        apifootball_enabled=_get_bool("PROAI_APIFOOTBALL_ENABLED", False),
        apifootball_api_key=os.getenv("PROAI_APIFOOTBALL_API_KEY") or None,
        apifootball_base_url=os.getenv("PROAI_APIFOOTBALL_BASE_URL") or None,
        team_rating_feature_enabled=_get_bool("PROAI_TEAM_RATING_FEATURE_ENABLED", False),
        team_rating_gate_enabled=_get_bool("PROAI_TEAM_RATING_GATE_ENABLED", False),
        team_rating_gate_competitions=_get_csv(
            "PROAI_TEAM_RATING_GATE_COMPETITIONS", ["International Friendlies"]
        ),
        team_rating_gate_require_both_medium_plus=_get_bool(
            "PROAI_TEAM_RATING_GATE_REQUIRE_BOTH_MEDIUM_PLUS", True
        ),
        team_rating_gate_require_calibrator=_get_bool(
            "PROAI_TEAM_RATING_GATE_REQUIRE_CALIBRATOR", True
        ),
        team_rating_gate_min_test_rows=int(
            os.getenv("PROAI_TEAM_RATING_GATE_MIN_TEST_ROWS", "150")
        ),
        team_rating_canary_enabled=_get_bool("PROAI_TEAM_RATING_CANARY_ENABLED", False),
        team_rating_canary_scope=os.getenv(
            "PROAI_TEAM_RATING_CANARY_SCOPE", "draw_code_allowlist"
        ),
        team_rating_canary_draw_codes=_get_csv(
            "PROAI_TEAM_RATING_CANARY_DRAW_CODES", []
        ),
        team_rating_canary_positions=[
            int(pos)
            for pos in _get_csv("PROAI_TEAM_RATING_CANARY_POSITIONS", [])
            if pos.lstrip("-").isdigit()
        ],
        team_rating_canary_calibrator_id=os.getenv(
            "PROAI_TEAM_RATING_CANARY_CALIBRATOR_ID",
            "international_friendlies_temperature_v1",
        ),
        team_rating_canary_routing_policy=os.getenv(
            "PROAI_TEAM_RATING_CANARY_ROUTING_POLICY", "rating_replaces_fallback"
        ),
        team_rating_canary_competition_allowlist=_get_csv(
            "PROAI_TEAM_RATING_CANARY_COMPETITION_ALLOWLIST", ["International Friendlies"]
        ),
        rate_limit_window_seconds=int(
            os.getenv("PROAI_RATE_LIMIT_WINDOW_SECONDS", "60")
        ),
        rate_limit_max_requests=int(
            os.getenv(
                "PROAI_RATE_LIMIT_MAX_REQUESTS",
                "120" if environment.lower() == "production" else "0",
            )
        ),
    )
    settings.validate_runtime()
    return settings

def _database_password(database_url: str) -> str | None:
    parsed = urlsplit(database_url)
    return parsed.password


def _is_placeholder_secret(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    if len(normalized) < 24:
        return True
    placeholder_tokens = (
        "replace",
        "change",
        "changeme",
        "placeholder",
        "secret",
        "password",
        "local",
        "dev",
        "proai",
        "example",
    )
    return any(token in normalized for token in placeholder_tokens)


def redact_url_secret(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.netloc or "@" not in parsed.netloc:
        return value
    username = parsed.username or ""
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    auth = f"{username}:***@" if username else "***@"
    return urlunsplit((parsed.scheme, f"{auth}{hostname}{port}", parsed.path, parsed.query, parsed.fragment))


settings = load_settings()
