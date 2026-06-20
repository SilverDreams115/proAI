from __future__ import annotations

from datetime import datetime, timezone

from app.domain.team_rating import (
    ConfidenceBucket,
    TeamRatingCalculator,
    TeamRatingConfig,
    TeamRatingInputMatch,
    confidence_bucket,
    config_checksum,
    input_checksum,
)


def _m(mid, h, a, hs, as_, *, day=1, comp="Liga", ns="club", conflict=False, sign_only=False):
    return TeamRatingInputMatch(
        match_id=mid,
        played_at=datetime(2026, 1, day, tzinfo=timezone.utc),
        home_team_id=h, away_team_id=a, home_score=hs, away_score=as_,
        competition=comp, namespace=ns, is_conflict=conflict, is_sign_only=sign_only,
    )


def _snap(snaps, team_id, ns="club"):
    return snaps[(team_id, ns)]


def _ratings(snaps):
    return {k: round(s.rating, 4) for k, s in snaps.items()}


# 1. Determinism with same input.
def test_deterministic_same_input() -> None:
    matches = [_m("m1", "A", "B", 2, 0, day=1), _m("m2", "B", "C", 1, 1, day=2)]
    calc = TeamRatingCalculator()
    s1, _ = calc.compute(matches)
    s2, _ = calc.compute(matches)
    assert _ratings(s1) == _ratings(s2)


# 2. Input ordering does not change output.
def test_input_order_independent() -> None:
    matches = [_m("m1", "A", "B", 2, 0, day=1), _m("m2", "B", "C", 1, 1, day=2),
               _m("m3", "A", "C", 0, 2, day=3)]
    calc = TeamRatingCalculator()
    s1, sum1 = calc.compute(matches)
    s2, sum2 = calc.compute(list(reversed(matches)))
    assert _ratings(s1) == _ratings(s2)
    assert sum1.output_checksum == sum2.output_checksum


# 3. Win raises winner, lowers loser.
def test_win_raises_loser_lowers() -> None:
    snaps, _ = TeamRatingCalculator().compute([_m("m1", "A", "B", 3, 0)])
    assert _snap(snaps, "A").rating > 1500.0
    assert _snap(snaps, "B").rating < 1500.0
    assert _snap(snaps, "A").wins == 1 and _snap(snaps, "B").losses == 1


# 4. Draw moves rating per expectation (equal teams: no move; favourite drops).
def test_draw_moves_by_expectation() -> None:
    eq, _ = TeamRatingCalculator().compute([_m("m1", "A", "B", 1, 1)])
    assert _snap(eq, "A").rating == 1500.0 and _snap(eq, "B").rating == 1500.0
    assert _snap(eq, "A").draws == 1

    # A becomes the favourite (beats X 5-0), then draws B (1500) -> A drops.
    fav, _ = TeamRatingCalculator().compute(
        [_m("m1", "A", "X", 1, 0, day=1), _m("m2", "A", "B", 0, 0, day=2)]
    )
    assert _snap(fav, "A").rating < _snap(fav, "A").rating + 1  # sanity
    # After a win A>1500; the subsequent draw vs a 1500 team must pull A down.
    win_only, _ = TeamRatingCalculator().compute([_m("m1", "A", "X", 1, 0)])
    assert _snap(fav, "A").rating < _snap(win_only, "A").rating


# 5. Zero-sum per match.
def test_zero_sum() -> None:
    snaps, _ = TeamRatingCalculator().compute([_m("m1", "A", "B", 2, 1)])
    up = _snap(snaps, "A").rating - 1500.0
    down = 1500.0 - _snap(snaps, "B").rating
    assert round(up, 6) == round(down, 6)


# 6. Goal diff disabled by default.
def test_goal_diff_disabled_by_default() -> None:
    a, _ = TeamRatingCalculator().compute([_m("m", "A", "B", 5, 0)])
    b, _ = TeamRatingCalculator().compute([_m("m", "C", "D", 1, 0)])
    assert round(_snap(a, "A").rating, 6) == round(_snap(b, "C").rating, 6)


# 7. Goal diff enabled respects the 1.75 cap.
def test_goal_diff_capped_when_enabled() -> None:
    cfg = TeamRatingConfig(goal_diff_enabled=True, goal_diff_cap=1.75)
    calc = TeamRatingCalculator(cfg)
    big, _ = calc.compute([_m("m", "A", "B", 50, 0)])
    small, _ = calc.compute([_m("m", "C", "D", 1, 0)])
    # Big win moves more than small, but never beyond k_base * 1.75 from 1500.
    assert _snap(big, "A").rating > _snap(small, "C").rating
    assert _snap(big, "A").rating - 1500.0 <= 32.0 * 1.75 + 1e-9


# 8. Match without a valid score is ignored.
def test_missing_score_ignored() -> None:
    snaps, summary = TeamRatingCalculator().compute([_m("m1", "A", "B", None, None)])
    assert summary.rated_match_count == 0
    assert summary.excluded_reasons.get("missing_score") == 1
    assert snaps == {}


# 9. Conflicting match is ignored.
def test_conflict_ignored() -> None:
    _, summary = TeamRatingCalculator().compute([_m("m1", "A", "B", 1, 0, conflict=True)])
    assert summary.rated_match_count == 0
    assert summary.excluded_reasons.get("conflict") == 1


# 10. Sign-only ignored by default; included when configured.
def test_sign_only_ignored_by_default() -> None:
    _, summary = TeamRatingCalculator().compute([_m("m1", "A", "B", 1, 0, sign_only=True)])
    assert summary.excluded_reasons.get("sign_only") == 1
    cfg = TeamRatingConfig(include_sign_only=True)
    _, s2 = TeamRatingCalculator(cfg).compute([_m("m1", "A", "B", 1, 0, sign_only=True)])
    assert s2.rated_match_count == 1


# 11. Confidence buckets.
def test_confidence_buckets() -> None:
    assert confidence_bucket(0) is ConfidenceBucket.NO_RATING
    assert confidence_bucket(3) is ConfidenceBucket.WEAK
    assert confidence_bucket(4) is ConfidenceBucket.MEDIUM
    assert confidence_bucket(9) is ConfidenceBucket.MEDIUM
    assert confidence_bucket(10) is ConfidenceBucket.STRONG


# 12. Namespaces separated do not mix.
def test_namespaces_separated() -> None:
    matches = [
        _m("m1", "A", "B", 3, 0, ns="club", day=1),
        _m("m2", "A", "C", 3, 0, ns="national", day=2),
    ]
    snaps, _ = TeamRatingCalculator().compute(matches)
    club_a = snaps[("A", "club")]
    nat_a = snaps[("A", "national")]
    # Two independent ratings for A; each reflects only its own namespace.
    assert club_a.matches_count == 1 and nat_a.matches_count == 1
    assert club_a.rating > 1500.0 and nat_a.rating > 1500.0
    # Club win does not bleed into the national rating (single win each).
    assert round(club_a.rating, 6) == round(nat_a.rating, 6)


def test_namespaces_merged_when_disabled() -> None:
    cfg = TeamRatingConfig(namespaces_separated=False)
    matches = [_m("m1", "A", "B", 3, 0, ns="club"), _m("m2", "A", "C", 3, 0, ns="national")]
    snaps, _ = TeamRatingCalculator(cfg).compute(matches)
    # One merged rating for A under the global namespace.
    assert ("A", "global") in snaps
    assert snaps[("A", "global")].matches_count == 2


# 13. Checksums change when input changes.
def test_checksum_changes_on_input_change() -> None:
    base = [_m("m1", "A", "B", 2, 0)]
    changed = [_m("m1", "A", "B", 0, 2)]  # flipped score
    assert input_checksum(base) != input_checksum(changed)
    s_base, _ = TeamRatingCalculator().compute(base)
    s_chg, _ = TeamRatingCalculator().compute(changed)
    from app.domain.team_rating import output_checksum
    assert output_checksum(s_base.values()) != output_checksum(s_chg.values())


# 14. Checksums stable under input reordering.
def test_checksum_stable_under_reorder() -> None:
    matches = [_m("m1", "A", "B", 2, 0, day=1), _m("m2", "B", "C", 1, 1, day=2)]
    assert input_checksum(matches) == input_checksum(list(reversed(matches)))


def test_config_checksum_changes_with_config() -> None:
    assert config_checksum(TeamRatingConfig()) != config_checksum(
        TeamRatingConfig(k_base=40.0)
    )


# 15. Team with no matches: absent without universe, no_rating with universe.
def test_team_universe_emits_no_rating() -> None:
    matches = [_m("m1", "A", "B", 1, 0)]
    calc = TeamRatingCalculator()
    no_universe, _ = calc.compute(matches)
    assert ("Z", "club") not in no_universe
    assert all(team_id != "Z" for team_id, _ in no_universe)

    with_universe, summary = calc.compute(matches, team_universe={"A", "B", "Z"})
    z = with_universe[("Z", "unknown")]
    assert z.confidence_bucket is ConfidenceBucket.NO_RATING
    assert z.matches_count == 0
    assert z.rating == 1500.0
    assert summary.team_count == 3
