from __future__ import annotations

import pytest

from app.services.publication_gate_service import _data_debt
from app.services.publication_gate_service import _next_actions
from app.services.publication_gate_service import _status


def test_publication_gate_blocks_money_when_data_debt_exists() -> None:
    assert _status(
        money_status="JUGAR_BALANCEADO",
        blocked_count=0,
        placeholder_count=1,
        data_blockers=[],
    ) == "DO_NOT_PLAY"
    assert _status(
        money_status="JUGAR_BALANCEADO",
        blocked_count=2,
        placeholder_count=0,
        data_blockers=[],
    ) == "DO_NOT_PLAY"
    assert _status(
        money_status="JUGAR_BALANCEADO",
        blocked_count=0,
        placeholder_count=0,
        data_blockers=["missing_predictions"],
    ) == "DO_NOT_PLAY"


def test_publication_gate_allows_only_clean_money_mode_decisions() -> None:
    assert _status(
        money_status="JUGAR_BALANCEADO",
        blocked_count=0,
        placeholder_count=0,
        data_blockers=[],
    ) == "READY_TO_PLAY"
    assert _status(
        money_status="JUGAR_SOLO_CONSERVADOR",
        blocked_count=0,
        placeholder_count=0,
        data_blockers=[],
    ) == "PLAY_CONSERVATIVE_ONLY"
    assert _status(
        money_status="NO_JUGAR",
        blocked_count=0,
        placeholder_count=0,
        data_blockers=[],
    ) == "DO_NOT_PLAY"


def test_publication_gate_next_actions_keep_data_and_learning_visible() -> None:
    debt = _data_debt(
        blocked=[{"position": 1}],
        warnings=[{"position": 2}],
        placeholders=[{"position": 3}],
        data_blockers=[],
        learning_exclusion="incomplete_results (0/14 canonical, 0 conflicts)",
    )
    actions = _next_actions(
        "DO_NOT_PLAY",
        debt,
        {
            "training_ready": False,
            "recommended_next_data_action": "accumulate more finished slates",
        },
    )

    assert actions[0] == "No jugar esta slate con dinero real."
    assert any("placeholder" in action for action in actions)
    assert any("BLOQUEADO" in action for action in actions)
    assert any("Completar resultados oficiales" in action for action in actions)
    assert any("accumulate more finished slates" in action for action in actions)


@pytest.mark.anyio
async def test_publication_gate_endpoint_uses_read_only_service(client, monkeypatch) -> None:
    from app.services import publication_gate_service

    def fake_gate(session, *, slate_id=None):
        return {
            "mode": "publication_gate",
            "scope": "selected_slate" if slate_id else "active_upcoming",
            "selected_slate_id": slate_id,
            "summary": {"slate_count": 0},
            "slates": [],
            "write_safety": {"read_only": True, "writes_performed": False},
        }

    monkeypatch.setattr(publication_gate_service, "build_publication_gate", fake_gate)

    response = await client.get("/api/operations/publication-gate?slate_id=s1")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "publication_gate"
    assert body["selected_slate_id"] == "s1"
    assert body["write_safety"]["read_only"] is True
