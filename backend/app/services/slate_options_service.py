"""R6.4 — Slate options generator (read-only).

Always proposes actionable ticket OPTIONS for a slate — aggressive, balanced,
conservative, plus a manual reference — *even when Money Mode says NO JUGAR*. It
strictly respects the Money Mode decision: when the slate is NO JUGAR every
option is a non-recommended simulation, nothing is marked recommended, and the
operator action is "no comprar boleto". Only a JUGAR_* decision marks the
matching option as recommended.

Each option carries its real pricing projection (combinations + estimated cost)
from ``progol_pricing``. While the base price is unverified the cost is ``None``
("precio no verificado") — never an invented amount, never $0.

Read-only: reuses ``build_money_mode`` (which itself writes nothing) and the pure
pricing calculator. No DB writes, no ticket activation.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.domain.progol_pricing import compute_cost
from app.models.tables import ProgolSlateModel
from app.services.money_mode_service import build_money_mode

# Money Mode ticket key -> human option name.
_OPTION_NAMES = [
    ("aggressive", "Agresiva"),
    ("balanced", "Balanceada"),
    ("conservative", "Conservadora"),
]

_ACTION_BY_DECISION = {
    "NO_JUGAR": "NO_COMPRAR",
    "JUGAR_BALANCEADO": "COMPRAR_BALANCEADA",
    "JUGAR_SOLO_BALANCEADO": "COMPRAR_BALANCEADA",
    "JUGAR_SOLO_CONSERVADOR": "COMPRAR_CONSERVADORA",
    "JUGAR_SOLO_AGRESIVO": "COMPRAR_AGRESIVA",
    "JUGAR_CON_CAUTELA": "COMPRAR_CON_CAUTELA",
}


def _option(
    *,
    key: str,
    name: str,
    ticket: dict[str, Any],
    week_type: str,
    no_play: bool,
    recommended_key: str | None,
) -> dict[str, Any]:
    doubles = int(ticket.get("double_count", 0))
    triples = int(ticket.get("triple_count", 0))
    pricing = compute_cost(week_type, doubles=doubles, triples=triples)
    recommended = (not no_play) and (recommended_key == key)
    if no_play:
        reason = "Simulación no recomendada · Money Mode bloquea la slate."
    elif recommended:
        reason = "Boleto recomendado por Money Mode."
    else:
        reason = "Alternativa (no recomendada por defecto)."
    return {
        "key": key,
        "name": name,
        "recommended": recommended,
        # Playable for real money only if Money Mode allows play AND the ticket
        # itself covers every NO-SIMPLE position.
        "playable": (not no_play) and bool(ticket.get("playable")),
        "risk_level": ticket.get("risk_level"),
        "reason": reason,
        "simple_count": ticket.get("simple_count", 0),
        "no_simple_count": ticket.get("no_simple_count", 0),
        "double_count": doubles,
        "triple_count": triples,
        "combinations": pricing["combinations"],
        "estimated_cost": pricing["estimated_cost"],
        "price_status": pricing["price_status"],
        "base_price_mxn": pricing["base_price_mxn"],
        "currency": pricing["currency"],
        "pricing_source": pricing["source"],
        "selections": ticket.get("selections", []),
    }


def _manual_option(week_type: str) -> dict[str, Any]:
    pricing = compute_cost(week_type, doubles=0, triples=0)
    return {
        "key": "manual",
        "name": "Manual / no recomendada",
        "recommended": False,
        "playable": False,
        "risk_level": "manual",
        "reason": "Opción manual fuera de la recomendación del sistema; úsala solo como referencia.",
        "simple_count": 0,
        "no_simple_count": 0,
        "double_count": 0,
        "triple_count": 0,
        "combinations": pricing["combinations"],
        "estimated_cost": pricing["estimated_cost"],
        "price_status": pricing["price_status"],
        "base_price_mxn": pricing["base_price_mxn"],
        "currency": pricing["currency"],
        "pricing_source": pricing["source"],
        "selections": [],
    }


def build_slate_options(session: Session, slate: ProgolSlateModel) -> dict[str, Any]:
    """Always-present ticket options for one slate (read-only)."""
    money = build_money_mode(session, slate)
    decision = money["decision"]
    status = decision["status"]
    no_play = status == "NO_JUGAR"
    week_type = slate.week_type
    tickets = money.get("tickets", {})
    recommended_key = decision.get("recommended_ticket")

    options = [
        _option(
            key=key,
            name=name,
            ticket=tickets.get(key, {}),
            week_type=week_type,
            no_play=no_play,
            recommended_key=recommended_key,
        )
        for key, name in _OPTION_NAMES
    ]
    options.append(_manual_option(week_type))

    any_verified = any(o["price_status"] == "verified" for o in options)

    return {
        "mode": "slate_options",
        "draw_code": slate.draw_code,
        "slate_id": slate.id,
        "week_type": week_type,
        "money_mode_decision": status,
        "recommended_action": _ACTION_BY_DECISION.get(status, "REVISAR"),
        "decision_reason": decision["reason"],
        "options": options,
        "pricing_verified": any_verified,
        "pricing_note": (
            "Precio no verificado: el costo estimado no se muestra hasta validar el "
            "precio oficial."
            if not any_verified
            else "Precio verificado en config."
        ),
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def build_active_slates_options(session: Session) -> dict[str, Any]:
    from app.repositories.slate_repository import SlateRepository
    from app.services.active_slate_scope import build_active_slate_scope
    from app.services.slate_service import SlateService

    slate_service = SlateService(SlateRepository(session))
    out: list[dict[str, Any]] = []
    for info in build_active_slate_scope(session):
        slate = slate_service.get_slate(info.slate_id)
        if slate is not None:
            out.append(build_slate_options(session, slate))
    return {
        "mode": "slate_options_active_upcoming",
        "scope": "active_upcoming",
        "slate_count": len(out),
        "slates": out,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def build_slate_options_for_draw_code(session: Session, draw_code: str) -> dict[str, Any] | None:
    from app.repositories.slate_repository import SlateRepository

    slate = SlateRepository(session).find_by_draw_code(draw_code)
    if slate is None:
        return None
    return build_slate_options(session, slate)
