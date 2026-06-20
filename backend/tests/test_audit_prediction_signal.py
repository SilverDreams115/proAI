from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from scripts.audit_prediction_signal import build_signal_audit

_HASH = "audit-hash-0001"


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'sigaudit.db'}")
    run_migrations(db_mod.engine)
    with Session(db_mod.engine) as session:
        yield session


def _team(session: Session, name: str) -> TeamModel:
    t = TeamModel(name=name, is_placeholder=False)
    session.add(t)
    session.flush()
    return t


def _slate(session: Session, n: int) -> ProgolSlateModel:
    comp = CompetitionModel(name="International Friendlies", is_placeholder=False)
    session.add(comp)
    session.flush()
    slate = ProgolSlateModel(
        label="PG-T", draw_code="PG-T", week_type="weekend",
        slate_version=1, composition_hash=_HASH,
    )
    session.add(slate)
    session.flush()
    for pos in range(1, n + 1):
        h, a = _team(session, f"H{pos}"), _team(session, f"A{pos}")
        m = MatchModel(
            competition_id=comp.id, home_team_id=h.id, away_team_id=a.id,
            kickoff_at=datetime(2026, 6, 25, 7, tzinfo=timezone.utc) + timedelta(hours=pos),
        )
        session.add(m)
        session.flush()
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))
    session.flush()
    return slate


def _audit(*, raw, decision=None, display=None, flags, evidence, status, fallback):
    decision = decision or raw
    display = display or raw
    return json.dumps({
        "raw_probabilities": raw,
        "decision_probabilities": decision,
        "display_probabilities": display,
        "optimizer_probabilities": decision,
        "sanity_flags": flags,
        "risk_level": "high" if evidence == "low" else "low",
        "evidence_level": evidence,
        "visible_confidence": "baja",
        "ticket_strategy": "EVITAR" if status == "BLOQUEADO" else "NO_DEJAR_SIMPLE",
        "final_status": status,
        "sanity_policy_version": "sanity-v1",
        "model_artifact_id": "artifact-x",
        "fallback_used": fallback,
        "is_international_friendly": True,
    })


def _pred(session, slate, position, *, generated_at, outcome, audit_json, anchors, blocked=None):
    link = next(sm for sm in slate.matches if sm.position == position)
    leg = json.loads(audit_json)["decision_probabilities"]
    session.add(PredictionModel(
        match_id=link.match_id, slate_id=slate.id, composition_hash=_HASH, slate_version=1,
        generated_at=generated_at,
        home_probability=leg["L"], draw_probability=leg["E"], away_probability=leg["V"],
        recommended_outcome=outcome, confidence_band="blocked",
        blocked_reason=blocked, competition_readiness="context_only",
        anchors_json=json.dumps(anchors), sanity_audit_json=audit_json,
    ))
    session.flush()


# 1. Latest-only selection: a newer row supersedes the older one.
def test_uses_latest_prediction_only(db) -> None:
    slate = _slate(db, 1)
    old = datetime(2026, 6, 18, tzinfo=timezone.utc)
    new = datetime(2026, 6, 19, tzinfo=timezone.utc)
    anchors = {"home_recent_matches": 3, "away_recent_matches": 3, "head_to_head_matches": 2, "evidence_count": 2}
    _pred(db, slate, 1, generated_at=old, outcome="2",
          audit_json=_audit(raw={"L": 0.2, "E": 0.3, "V": 0.5}, flags=[], evidence="high", status="LISTO", fallback=False),
          anchors=anchors)
    _pred(db, slate, 1, generated_at=new, outcome="1",
          audit_json=_audit(raw={"L": 0.6, "E": 0.25, "V": 0.15}, flags=[], evidence="high", status="FIJO", fallback=False),
          anchors=anchors)
    db.commit()
    report = build_signal_audit(db, draw_code="PG-T")
    row = report["rows"][0]
    assert row["predicted_outcome"] == "1"  # newer row
    assert row["final_status"] == "FIJO"
    assert report["summary"]["latest_generation_count"] == 1
    assert report["summary"]["prediction_generations_seen"] == 2


# 2. fallback_used detection.
def test_detects_fallback(db) -> None:
    slate = _slate(db, 1)
    _pred(db, slate, 1, generated_at=datetime(2026, 6, 19, tzinfo=timezone.utc), outcome="1",
          audit_json=_audit(raw={"L": 0.4, "E": 0.3, "V": 0.3}, flags=["FALLBACK_USED", "LOW_EVIDENCE"],
                            evidence="low", status="REVISAR", fallback=True),
          anchors={"home_recent_matches": 2, "away_recent_matches": 2, "head_to_head_matches": 0, "evidence_count": 0})
    db.commit()
    report = build_signal_audit(db, draw_code="PG-T")
    assert report["rows"][0]["fallback_used"] is True
    assert report["summary"]["fallback_count"] == 1
    assert report["summary"]["fallback_rate"] == 1.0


# 3. raw extreme vs decision capped.
def test_detects_raw_extreme_and_capped(db) -> None:
    slate = _slate(db, 1)
    # raw favourite 0.96 (extreme) but decision/display shrunk to 0.6 (capped).
    _pred(db, slate, 1, generated_at=datetime(2026, 6, 19, tzinfo=timezone.utc), outcome="1",
          audit_json=_audit(
              raw={"L": 0.96, "E": 0.02, "V": 0.02},
              decision={"L": 0.60, "E": 0.20, "V": 0.20},
              display={"L": 0.60, "E": 0.20, "V": 0.20},
              flags=["EXTREME_PROBABILITY_CAPPED", "SUSPICIOUS_CLASS_PROBABILITY", "EXTREME_PROBABILITY_WITHOUT_EVIDENCE"],
              evidence="low", status="REVISAR", fallback=True),
          anchors={"home_recent_matches": 2, "away_recent_matches": 2, "head_to_head_matches": 0, "evidence_count": 0})
    db.commit()
    report = build_signal_audit(db, draw_code="PG-T")
    row = report["rows"][0]
    assert row["raw_extreme"] is True
    assert row["capped"] is True
    assert row["suspicious"] is True
    assert row["raw_probabilities"]["L"] == 0.96
    assert row["decision_probabilities"]["L"] == 0.60  # decision != raw (capped)
    assert report["summary"]["raw_extreme_count"] == 1
    assert report["summary"]["capped_count"] == 1


# 4. low evidence detection.
def test_detects_low_evidence(db) -> None:
    slate = _slate(db, 1)
    _pred(db, slate, 1, generated_at=datetime(2026, 6, 19, tzinfo=timezone.utc), outcome="1",
          audit_json=_audit(raw={"L": 0.4, "E": 0.3, "V": 0.3}, flags=["LOW_EVIDENCE"],
                            evidence="low", status="REVISAR", fallback=False),
          anchors={"home_recent_matches": 1, "away_recent_matches": 1, "head_to_head_matches": 0, "evidence_count": 0})
    db.commit()
    report = build_signal_audit(db, draw_code="PG-T")
    assert report["rows"][0]["evidence_level"] == "low"
    assert report["summary"]["low_evidence_count"] == 1


# 5. No DB writes.
def test_no_db_writes(db) -> None:
    slate = _slate(db, 2)
    for pos in (1, 2):
        _pred(db, slate, pos, generated_at=datetime(2026, 6, 19, tzinfo=timezone.utc), outcome="1",
              audit_json=_audit(raw={"L": 0.4, "E": 0.3, "V": 0.3}, flags=["FALLBACK_USED"],
                                evidence="low", status="BLOQUEADO", fallback=True),
              anchors={"home_recent_matches": 0, "away_recent_matches": 0, "head_to_head_matches": 0, "evidence_count": 0},
              blocked="insufficient_data_anchors")
    db.commit()
    before = (db.query(PredictionModel).count(), db.query(MatchModel).count())
    build_signal_audit(db, draw_code="PG-T")
    after = (db.query(PredictionModel).count(), db.query(MatchModel).count())
    assert before == after


# 6. Summary counts.
def test_summary_counts(db) -> None:
    slate = _slate(db, 3)
    gen = datetime(2026, 6, 19, tzinfo=timezone.utc)
    _pred(db, slate, 1, generated_at=gen, outcome="1",
          audit_json=_audit(raw={"L": 0.4, "E": 0.3, "V": 0.3}, flags=["FALLBACK_USED", "LOW_EVIDENCE", "BLOCKED_INSUFFICIENT_DATA"],
                            evidence="low", status="BLOQUEADO", fallback=True),
          anchors={"home_recent_matches": 0, "away_recent_matches": 0, "head_to_head_matches": 0, "evidence_count": 0},
          blocked="insufficient_data_anchors")
    _pred(db, slate, 2, generated_at=gen, outcome="2",
          audit_json=_audit(raw={"L": 0.02, "E": 0.3, "V": 0.68}, flags=["FALLBACK_USED", "LOW_EVIDENCE", "SUSPICIOUS_CLASS_PROBABILITY", "EXTREME_PROBABILITY_CAPPED"],
                            evidence="low", status="REVISAR", fallback=True),
          anchors={"home_recent_matches": 2, "away_recent_matches": 2, "head_to_head_matches": 0, "evidence_count": 0})
    _pred(db, slate, 3, generated_at=gen, outcome="1",
          audit_json=_audit(raw={"L": 0.5, "E": 0.25, "V": 0.25}, flags=["FALLBACK_USED", "LOW_EVIDENCE"],
                            evidence="low", status="REVISAR", fallback=True),
          anchors={"home_recent_matches": 2, "away_recent_matches": 3, "head_to_head_matches": 0, "evidence_count": 0})
    db.commit()
    s = build_signal_audit(db, draw_code="PG-T")["summary"]
    assert s["total_predictions"] == 3
    assert s["fallback_count"] == 3
    assert s["low_evidence_count"] == 3
    assert s["blocked_count"] == 1
    assert s["review_count"] == 2
    assert s["suspicious_count"] == 1
    assert s["model_artifact_ids_seen"] == ["artifact-x"]


# 7. Classification per match.
def test_classification(db) -> None:
    slate = _slate(db, 3)
    gen = datetime(2026, 6, 19, tzinfo=timezone.utc)
    # pos1 blocked
    _pred(db, slate, 1, generated_at=gen, outcome="1",
          audit_json=_audit(raw={"L": 0.4, "E": 0.3, "V": 0.3}, flags=["FALLBACK_USED", "BLOCKED_INSUFFICIENT_DATA"],
                            evidence="low", status="BLOQUEADO", fallback=True),
          anchors={"home_recent_matches": 0, "away_recent_matches": 0, "head_to_head_matches": 0, "evidence_count": 0})
    # pos2 suspicious (REVISAR + near-zero L)
    _pred(db, slate, 2, generated_at=gen, outcome="2",
          audit_json=_audit(raw={"L": 0.02, "E": 0.3, "V": 0.68}, flags=["FALLBACK_USED", "SUSPICIOUS_CLASS_PROBABILITY", "EXTREME_PROBABILITY_CAPPED"],
                            evidence="low", status="REVISAR", fallback=True),
          anchors={"home_recent_matches": 2, "away_recent_matches": 2, "head_to_head_matches": 0, "evidence_count": 0})
    # pos3 fallback_only (REVISAR, no suspicious, no anchors)
    _pred(db, slate, 3, generated_at=gen, outcome="1",
          audit_json=_audit(raw={"L": 0.45, "E": 0.3, "V": 0.25}, flags=["FALLBACK_USED", "LOW_EVIDENCE"],
                            evidence="low", status="REVISAR", fallback=True),
          anchors={"home_recent_matches": 2, "away_recent_matches": 3, "head_to_head_matches": 0, "evidence_count": 0})
    db.commit()
    rows = {r["position"]: r["classification"] for r in build_signal_audit(db, draw_code="PG-T")["rows"]}
    assert rows[1] == "blocked_by_sanity"
    assert rows[2] == "suspicious_raw"
    assert rows[3] == "fallback_only"
