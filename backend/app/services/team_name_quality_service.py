from __future__ import annotations

import unicodedata


PLACEHOLDER_TEAM_NAMES = frozenset(
    {
        "g",
        "tbd",
        "por definir",
        "pendiente",
        "unknown",
        "?",
    }
)


def normalized_team_label(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    ascii_value = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_value.split())


def is_suspicious_team_name(value: str | None) -> bool:
    normalized = normalized_team_label(value)
    return not normalized or len(normalized) < 2 or normalized in PLACEHOLDER_TEAM_NAMES


def team_name_issue_flags(value: str | None, *, is_placeholder: bool = False) -> list[str]:
    flags: list[str] = []
    if is_placeholder:
        flags.append("PLACEHOLDER_TEAM")
    if is_suspicious_team_name(value):
        flags.append("SUSPICIOUS_TEAM_NAME")
    return flags


def suspicious_team_names(*names: str | None) -> list[str]:
    return [str(name or "") for name in names if is_suspicious_team_name(name)]
