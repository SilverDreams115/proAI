from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.connectors.base import SourceConnector
from app.connectors.base import SourceDocument


class LocalContextJsonConnector(SourceConnector):
    """Reads a local JSON context pack for verified match context and availability."""

    kind = "local_context_json"
    description = "Reads local structured context packs from /data/progol_context."
    DEFAULT_ROOT = "/data/progol_context"
    URL_SCHEME = "local-context"

    def __init__(self, name: str, file_path: str, allowed_root: str | None = None) -> None:
        self.name = name
        self.base_url = self.to_base_url(file_path)
        self.file_path = self.resolve_allowed_path(file_path, allowed_root=allowed_root)

    def fetch(self) -> list[SourceDocument]:
        with self.file_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)

        items = payload if isinstance(payload, list) else payload.get("items", [])
        if not isinstance(items, list):
            return []

        captured_at = datetime.now(timezone.utc)
        documents: list[SourceDocument] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_item(item, index)
            documents.append(
                SourceDocument(
                    source_name=self.name,
                    source_url=str(item.get("source_url") or item.get("url") or self.base_url),
                    captured_at=captured_at,
                    payload=normalized,
                )
            )
        return documents

    # Context packs emitted by the upstream Progol-context tooling tag
    # historical_results with a competition name like "Liga MX Recent
    # Form" — a generator artifact, not a real competition. If we
    # let those names through, every league grows a phantom twin in
    # the `competitions` table, the walk-forward backtest lists them
    # as zero-match leagues, and feature lookups can't cross-match
    # against the real history. Strip the suffix here so the
    # downstream entity resolver sees the real league name.
    _RECENT_FORM_SUFFIX = " Recent Form"

    def _normalize_item(self, item: dict[str, object], index: int) -> dict[str, object]:
        teams = item.get("teams")
        if not isinstance(teams, list):
            teams = [
                item.get("home_team"),
                item.get("away_team"),
            ]
        teams = [str(team).strip() for team in teams if str(team or "").strip()]
        title = str(item.get("title") or (" vs ".join(teams) if len(teams) >= 2 else f"Context item {index}"))
        summary = str(item.get("summary") or item.get("context_summary") or title)
        headings = item.get("headings")
        if not isinstance(headings, list):
            headings = [title]

        historical_results_raw = item.get("historical_results", [])
        historical_results = self._strip_recent_form_suffix(historical_results_raw)

        return {
            "title": title,
            "summary": summary,
            "headings": headings,
            "teams": teams,
            "competition": item.get("competition"),
            "team_stats": item.get("team_stats", []),
            "match_stats": item.get("match_stats", []),
            "historical_results": historical_results,
            "fixture_candidates": item.get("fixture_candidates", item.get("fixtures", [])),
            "availability_reports": item.get("availability_reports", []),
            "catalog_metadata": item.get("catalog_metadata", {}),
            "context_summary": item.get("context_summary", summary),
            "article_prediction": item.get("article_prediction"),
        }

    @classmethod
    def _strip_recent_form_suffix(cls, results: object) -> list[dict[str, object]]:
        if not isinstance(results, list):
            return []
        cleaned: list[dict[str, object]] = []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            name = entry.get("competition_name")
            if isinstance(name, str) and name.endswith(cls._RECENT_FORM_SUFFIX):
                entry = dict(entry)
                entry["competition_name"] = name[: -len(cls._RECENT_FORM_SUFFIX)]
            cleaned.append(entry)
        return cleaned

    @classmethod
    def to_base_url(cls, file_path: str) -> str:
        if file_path.startswith(f"{cls.URL_SCHEME}:"):
            return file_path
        return f"{cls.URL_SCHEME}://{file_path if file_path.startswith('/') else '/' + file_path}"

    @classmethod
    def resolve_allowed_path(cls, file_path: str, allowed_root: str | None = None) -> Path:
        root = Path(
            allowed_root or os.getenv("PROAI_LOCAL_CONTEXT_ROOT") or cls.DEFAULT_ROOT
        ).resolve()
        parsed = urlparse(file_path)
        raw_path = parsed.path if parsed.scheme == cls.URL_SCHEME else file_path
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve(strict=False)
        if root != resolved and root not in resolved.parents:
            raise ValueError(f"Local context path must stay under {root}.")
        return resolved
