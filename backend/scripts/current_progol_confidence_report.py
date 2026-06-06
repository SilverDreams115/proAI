from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an auditable confidence report for the active Progol slate.")
    parser.add_argument("--base-url", default=os.getenv("PROAI_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--api-key", default=os.getenv("PROAI_AUTH_API_KEY"))
    parser.add_argument("--output", default="reports/current_progol_confidence.md")
    args = parser.parse_args()

    client = ApiClient(args.base_url.rstrip("/"), args.api_key)
    slates = client.get("/api/slates")
    if not slates:
        raise SystemExit("No active slates were returned by the API.")
    slate = slates[0]
    slate_id = slate["id"]
    predictions = client.get(f"/api/predictions/slates/{slate_id}")
    features = client.get(f"/api/predictions/slates/{slate_id}/features")
    quality = client.get(f"/api/predictions/slates/{slate_id}/quality")
    ticket = client.get(f"/api/predictions/slates/{slate_id}/ticket")

    report = build_report(slate, predictions, features, quality, ticket)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"confidence_report={output_path}")
    return 0


class ApiClient:
    def __init__(self, base_url: str, api_key: str | None) -> None:
        self.base_url = base_url
        self.api_key = api_key

    def get(self, path: str) -> Any:
        request = urllib.request.Request(f"{self.base_url}{path}")
        if self.api_key:
            request.add_header("X-API-Key", self.api_key)
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))


def build_report(
    slate: dict[str, Any],
    predictions: list[dict[str, Any]],
    features: list[dict[str, Any]],
    quality: list[dict[str, Any]],
    ticket: dict[str, Any],
) -> str:
    feature_by_match = {item["match_id"]: item.get("payload", {}) for item in features}
    quality_by_match = {item["match_id"]: item for item in quality}
    ticket_by_match = {item["match_id"]: item for item in ticket.get("recommendations", [])}
    lines = [
        f"# Reporte de confianza - {slate['draw_code']}",
        "",
        f"Generado: {datetime.now().isoformat(timespec='seconds')}",
        f"Boleta: {slate.get('label', 'sin etiqueta')}",
        "",
        "## Criterio",
        "",
        "- No se sube confianza por preferencia manual.",
        "- Un partido queda fuerte solo si benchmark, calidad de datos, evidencia y brecha del modelo apuntan en la misma direccion.",
        "- Si falta benchmark, forma reciente, H2H o evidencia, se recomienda cobertura o revision humana.",
        "",
        "## Resumen",
        "",
    ]
    rows = [
        classify_match(
            prediction,
            feature_by_match.get(prediction["match_id"], {}),
            quality_by_match.get(prediction["match_id"], {}),
            ticket_by_match.get(prediction["match_id"], {}),
        )
        for prediction in predictions
    ]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    for category in ("listo", "cautela", "benchmark bajo", "datos insuficientes"):
        lines.append(f"- {category}: {counts.get(category, 0)}")
    lines.extend(["", "## Partido Por Partido", ""])
    for row in rows:
        lines.extend(
            [
                f"### {row['position']}. {row['match']}",
                "",
                f"- Categoria: {row['category']}",
                f"- Jugada: {row['pick']}",
                f"- Probabilidades: {row['probabilities']}",
                f"- Benchmark: {row['readiness']}",
                f"- Calidad: {row['quality']}",
                f"- Evidencia/Forma/H2H: {row['coverage']}",
                f"- Accion: {row['action']}",
                f"- Razon: {row['reason']}",
                "",
            ]
        )
    return "\n".join(lines)


def classify_match(
    prediction: dict[str, Any],
    feature_payload: dict[str, Any],
    quality: dict[str, Any],
    ticket: dict[str, Any],
) -> dict[str, str]:
    outcomes = sorted(
        [
            ("L", float(prediction.get("home_probability", 0))),
            ("E", float(prediction.get("draw_probability", 0))),
            ("V", float(prediction.get("away_probability", 0))),
        ],
        key=lambda item: item[1],
        reverse=True,
    )
    readiness = str(prediction.get("competition_readiness", "unclassified"))
    confidence = str(prediction.get("confidence_band", "low"))
    validation = ticket.get("validation", {})
    decisions = ticket.get("decisions", {})
    doubles_pick = decisions.get("doubles") or decisions.get("simple") or {}
    pick = " / ".join(_display_pick(item) for item in doubles_pick.get("picks", [])) or outcomes[0][0]
    evidence_count = int(quality.get("evidence_count", feature_payload.get("evidence_items", 0)) or 0)
    recent_count = int(quality.get("recent_results_count", feature_payload.get("recent_results_count", 0)) or 0)
    h2h_count = int(quality.get("head_to_head_results_count", feature_payload.get("head_to_head_results_count", 0)) or 0)
    missing = quality.get("missing", []) if isinstance(quality.get("missing", []), list) else []
    quality_level = str(quality.get("quality_level", "thin"))
    quality_score = quality.get("quality_score", "sin score")
    validation_level = str(validation.get("level", "medium"))

    if readiness in {"unclassified"} or confidence == "blocked":
        category = "datos insuficientes"
        action = "No tratar como fijo; requiere revision o cobertura."
    elif readiness == "not_ready":
        category = "benchmark bajo"
        action = "Cubrir si entra en presupuesto; no subir confianza sin backtest."
    elif quality_level == "thin" or evidence_count <= 0 or recent_count <= 0:
        category = "datos insuficientes"
        action = "No subir confianza hasta reforzar evidencia/forma reciente."
    elif readiness in {"covered", "context_only"} or validation_level in {"medium", "high"}:
        category = "cautela"
        action = str(validation.get("recommendation") or "Usar cobertura si la jugada lo permite.")
    else:
        category = "listo"
        action = "Fijo defendible si coincide con presupuesto y validacion humana."

    reasons = [
        str(prediction.get("policy_reason", "")),
        str(validation.get("label", "")),
    ]
    if missing:
        reasons.append("Falta: " + ", ".join(str(item) for item in missing))
    reason = " ".join(item for item in reasons if item).strip() or "Sin observaciones adicionales."

    return {
        "position": str(prediction["position"]),
        "match": f"{prediction['home_team_name']} vs {prediction['away_team_name']}",
        "category": category,
        "pick": pick,
        "probabilities": " · ".join(f"{label} {probability:.0%}" for label, probability in outcomes),
        "readiness": f"{readiness}; confianza {confidence}",
        "quality": f"{quality_score}/100 ({quality_level})",
        "coverage": f"evidencia {evidence_count}; forma {recent_count}; H2H {h2h_count}",
        "action": action,
        "reason": reason,
    }


def _display_pick(value: str) -> str:
    return {"1": "L", "X": "E", "2": "V"}.get(str(value), str(value))


if __name__ == "__main__":
    raise SystemExit(main())
