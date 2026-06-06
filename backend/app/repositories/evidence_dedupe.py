import json
from typing import Any

from app.models.tables import EvidenceItemModel
from app.models.tables import SourceDocumentModel


def evidence_identity_from_values(
    *,
    match_id: str,
    source_id: str,
    kind: str,
    summary: str,
    payload: dict[str, object],
) -> tuple[str, str, str, str, str]:
    title_key = str(
        payload.get("normalized_key")
        or payload.get("source_title")
        or payload.get("title")
        or summary
    ).strip().lower()
    url_key = str(payload.get("source_url") or "").strip().lower()
    return (match_id, source_id, kind, title_key, url_key)


def evidence_identity(item: EvidenceItemModel) -> tuple[str, str, str, str, str]:
    return evidence_identity_from_values(
        match_id=item.match_id,
        source_id=item.source_id,
        kind=item.kind,
        summary=item.summary,
        payload=_decode_payload(item.payload_json),
    )


def dedupe_evidence_items(items: list[EvidenceItemModel]) -> list[EvidenceItemModel]:
    deduped: list[EvidenceItemModel] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item in items:
        identity = evidence_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(item)
    return deduped


def dedupe_source_documents(items: list[SourceDocumentModel]) -> list[SourceDocumentModel]:
    deduped: list[SourceDocumentModel] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        identity = (
            item.source_id,
            (item.normalized_key or item.title).strip().lower(),
            item.external_url.strip().lower(),
        )
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(item)
    return deduped


def _decode_payload(payload_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
