# proAI — Data Quality

## Canonical results

A canonical result is the confirmed final result of a match, stored in `canonical_results`. It is the source of truth for scoring and the adaptive dataset. Without a confirmed canonical result, scoring must not be computed.

### Sources and priority

`result_source_priority` on `SourceModel` determines which source prevails when multiple connectors report different results for the same match. Higher-priority sources override lower-priority ones.

### Result conflicts

A conflict occurs when two sources with equal or higher priority report different results for the same `(home_team_id, away_team_id, match_date)`. Conflicts are tracked in the adaptive dataset via `conflict_rate`.

**Do not use conflicting results in scoring or retraining.** The `max_conflict_rate=0.05` gate in `AdaptiveRetrainingService` rejects datasets with more than 5% conflicting rows.

---

## Legacy slate decisions (PG-2334 → PG-2336)

### PG-2334
First slate produced. A decoupling pattern was detected: the slate received multiple prediction snapshots under different versions. The `composition_hash` changed during the process, generating `v1`, `v2`, etc. versions. Result: potentially inconsistent scoring metrics across versions.

**Lesson:** the `composition_hash` guarantees that each score is compared against the exact composition that generated the prediction. Do not mix scores from different versions.

### PG-2335
Second slate analyzed. A post-mortem review identified three positions where the model had biases from insufficient calibration. The model artifacts were adjusted (`model_training_artifacts.py` line 57). Slate marked as `PARTIAL_ONLY` — data partially useful for the adaptive dataset but not representative of the current pipeline.

### PG-2336
First clean slate with the complete pipeline:
- `composition_hash` from the start
- calibrated model
- documented evidence quality
- active anchor gap diagnostic
- 0 `blocked` matches

It is the first valid slate to feed the adaptive dataset without restrictions. **Do not manually modify any PG-2336 data.**

### Why not do arbitrary legacy backfill

Backfilling PG-2334 or PG-2335 requires:
1. Knowing exactly which model was active at each moment
2. Confirming that the features used are the same as the current ones
3. Resolving documented result conflicts
4. Avoiding contaminating the adaptive dataset with predictions from incompatible model versions

Without those guarantees, the backfill introduces noise into retraining and distorts the walk-forward metrics.

---

## Evidence quality

`evidence_count` is the number of text-evidence items (news, injuries, rotations, etc.) associated with a match. It is currently **0 in almost all matches** because there is no active news scraper.

This is intentional and declared in the system. `_confidence_band()` accepts H2H or recent form as an equivalent anchor (`evidence_count >= 1 OR h2h >= 2 OR (home_recent >= 3 AND away_recent >= 3)`). Do not inflate `evidence_count` artificially or create synthetic evidence to raise confidence bands.

---

## Anchor gap

The anchor gap is the diagnostic that explains why a match is in the `low` band. It is reported when `_anchored = False`:

```
anchored = (evidence_count >= 1) OR (h2h >= 2) OR (home_recent >= 3 AND away_recent >= 3)
```

If `anchored = False`, the rationale includes exactly what is missing:

| Condition | Message |
|---|---|
| `home_recent < 3` | "Local tiene N resultado(s) reciente(s) en ventana activa — necesita 3" |
| `away_recent < 3` | "Visitante tiene N resultado(s) reciente(s) en ventana activa — necesita 3" |
| `h2h < 2` | "Historial directo insuficiente (N enfrentamiento(s), necesita 2)" |

### Active window

The recent-form window is **3 × median days between matches** of the competition. For "International Friendlies" (which WCQ qualifiers are mapped to): ≈211 days.

Matches outside the window do not contribute to `home_recent` or `away_recent`. This is correct: a match from 8 months ago does not reflect the team's current form.

### Why lows must not be inflated artificially

A `low` match means the model does not have enough recent evidence to back its choice. Turning it into `medium` or `high` via an artificial rule (e.g. "if the competition is a World Cup, give +1 bonus") violates the system's semantics and produces non-auditable picks.

The UI shows the anchor gap diagnostic explicitly so the operator understands the limitation, not to hide it.

---

## WCQ (World Cup Qualifying) data

The world-cup qualifier sources are registered in the system:

| Confederation | League ID TSDB | State (Jun 2026) |
|---|---|---|
| CONMEBOL | 5515 | Ended Sep 2025 — outside active window |
| CAF | 5514 | Ended Oct 2025 — outside active window |
| AFC | 5513 | Ended Jun 2025 — outside active window |
| CONCACAF | 5516 | Ended Mar 2026 — inside window |
| UEFA | 5518 | Active 2026 |

The normalization aliases (`eliminatorias mundialistas`, `wcq`, etc.) map to `international-friendlies` to share that competition group's form window.

---

## Missing data vs. conflicting data

| Situation | Correct behavior |
|---|---|
| No recent data (`home_recent < 3`) | `low` — show anchor gap |
| No H2H (`h2h < 2`) | `low` — show anchor gap |
| Competition not classified | `blocked` — do not present a pick |
| Conflicting results in DB | Do not use in scoring or retraining |
| Evidence count = 0 | Normal — do not inflate artificially |
