"""R6.4 — completed-slate results validation dry-run (read-only, no writes)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.tables import (
    MatchResultModel,
    PredictionModel,
    ProgolSlateModel,
    SourceModel,
)
from app.services.completed_slate_results_validation_service import (
    build_completed_slate_validation,
)
from backend.tests.test_ticket_canary_dry_run_service import (
    DRAW,
    db,  # noqa: F401 — pytest fixture
    seed_canary_slate,
)


def _slate(session):
    return session.query(ProgolSlateModel).filter_by(draw_code=DRAW).one()


def _result_count(session_factory):
    with session_factory() as s:
        return int(s.scalar(select(func.count()).select_from(MatchResultModel)) or 0)


def test_validation_reports_missing_results_and_no_writes(db):  # noqa: F811
    """9/10 + 11 + 13 — validation responds, flags missing results, writes nothing."""
    from app.db import session as db_mod

    seed_canary_slate(db)

    before = _result_count(db_mod.SessionLocal)
    report = build_completed_slate_validation(db, _slate(db))
    after = _result_count(db_mod.SessionLocal)

    assert after == before  # no match_results written
    assert report["mode"] == "completed_slate_results_validation"
    assert report["predictions_count"] >= 1
    assert report["local_results_count"] == 0
    assert report["provider_results_count"] == 0
    assert report["coverage"] == 0.0
    assert report["ready_to_apply"] is False
    assert "missing_provider_results" in report["blockers"]
    assert "missing_local_results" in report["blockers"]
    for m in report["matches"]:
        assert m["status"] == "missing"


def test_validation_compares_hits_when_results_present(db):  # noqa: F811
    """14 — with complete local results, coverage=100% and hits are compared."""
    seed_canary_slate(db)
    slate = _slate(db)

    source = SourceModel(name="test-results", base_url="http://x", kind="manual")
    db.add(source)
    db.flush()
    # Insert one local result per match, matching the prediction sign for the
    # first match so we get at least one hit.
    for idx, link in enumerate(sorted(slate.matches, key=lambda i: i.position)):
        pred = db.execute(
            select(PredictionModel.recommended_outcome)
            .where(PredictionModel.slate_id == slate.id, PredictionModel.match_id == link.match_id)
            .limit(1)
        ).scalar()
        code = pred or "1"
        goals = (2, 0) if code == "1" else (0, 2) if code == "2" else (1, 1)
        db.add(
            MatchResultModel(
                match_id=link.match_id,
                source_id=source.id,
                played_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
                home_goals=goals[0],
                away_goals=goals[1],
                result_code=code,
            )
        )
    db.commit()

    report = build_completed_slate_validation(db, _slate(db))
    assert report["local_results_count"] == report["match_count"]
    assert report["coverage"] == 1.0
    assert report["hits"] >= 1
    # local-only coverage still needs provider confirmation to be apply-ready.
    assert report["ready_to_apply"] is False
    assert all(m["status"] in ("resolved", "conflict") for m in report["matches"])


# --- R7.0: manual official results (load / validate / guarded apply) ---------
import json  # noqa: E402

from app.services.completed_slate_manual_results import (  # noqa: E402
    apply_manual_results,
    evaluate_manual_apply,
    load_manual_results,
)
from backend.tests._learning_seed import (  # noqa: E402, F401
    learn_db,
    manual_payload,
    seed_official_slate,
)


def _drawslate(session, draw):
    return session.query(ProgolSlateModel).filter_by(draw_code=draw).one()


def test_manual_dryrun_writes_nothing(learn_db):  # noqa: F811
    """3 — evaluating a manual file is read-only (no match_results written)."""
    from app.db import session as db_mod

    seed_official_slate(learn_db, draw="PG-MAN", n=4, with_results=False)
    manual = load_manual_results(manual_payload("PG-MAN", 4, complete=True))
    before = _result_count(db_mod.SessionLocal)
    report = evaluate_manual_apply(learn_db, _drawslate(learn_db, "PG-MAN"), manual)
    after = _result_count(db_mod.SessionLocal)
    assert after == before
    assert report["write_safety"]["writes_performed"] is False


def test_manual_incomplete_blocks_apply(learn_db):  # noqa: F811
    """4 — an incomplete manual file is not ready_to_apply."""
    seed_official_slate(learn_db, draw="PG-MAN2", n=4, with_results=False)
    manual = load_manual_results(manual_payload("PG-MAN2", 4, complete=False))
    report = evaluate_manual_apply(learn_db, _drawslate(learn_db, "PG-MAN2"), manual)
    assert report["ready_to_apply"] is False
    assert "incomplete_positions" in report["blockers"]


def test_manual_complete_is_ready_to_apply(learn_db):  # noqa: F811
    """5 — a complete, conflict-free, high-confidence file is ready_to_apply."""
    seed_official_slate(learn_db, draw="PG-MAN3", n=4, with_results=False)
    manual = load_manual_results(manual_payload("PG-MAN3", 4, complete=True))
    report = evaluate_manual_apply(learn_db, _drawslate(learn_db, "PG-MAN3"), manual)
    assert report["ready_to_apply"] is True
    assert report["blockers"] == []


def test_manual_apply_requires_confirmation_token(learn_db, tmp_path):  # noqa: F811
    """6 — the apply CLI refuses without the exact confirmation token."""
    from app.db import session as db_mod
    from scripts.validate_completed_slate_results import main

    seed_official_slate(learn_db, draw="PG-MAN4", n=4, with_results=False)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manual_payload("PG-MAN4", 4, complete=True)), encoding="utf-8")

    before = _result_count(db_mod.SessionLocal)
    rc = main(["--manual-file", str(path), "--apply", "--confirm", "WRONG-TOKEN"])
    after = _result_count(db_mod.SessionLocal)
    assert rc == 0  # falls back to a read-only dry-run
    assert after == before  # nothing written without the exact token


def test_manual_apply_writes_when_ready(learn_db):  # noqa: F811
    """Guarded apply path: a ready file inserts exactly n results."""
    from app.db import session as db_mod

    seed_official_slate(learn_db, draw="PG-MAN5", n=4, with_results=False)
    manual = load_manual_results(manual_payload("PG-MAN5", 4, complete=True))
    before = _result_count(db_mod.SessionLocal)
    outcome = apply_manual_results(learn_db, _drawslate(learn_db, "PG-MAN5"), manual)
    learn_db.commit()
    after = _result_count(db_mod.SessionLocal)
    assert outcome["applied"] is True
    assert outcome["inserted"] == 4
    assert after - before == 4
