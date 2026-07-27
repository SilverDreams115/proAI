"""Re-normalize women's team aliases so they stop colliding with men's clubs.

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-27

``NormalizationService.TEAM_STOPWORDS`` used to contain "femenil" and
"femenino", so the marker was stripped before a team name became a slug.
Every women's side therefore shared a namespace with the men's club of the
same name: "Cruz Azul Femenil" and "Cruz Azul" both normalized to
`cruz-azul`, "Tigres UANL Femenil" landed as `tigres-uanl`.

Two failures came out of that, and only the first is obvious:

* a women's fixture resolved to the men's team, so its source document
  linked to the men's match — orientation included, which produced links
  like "Cruz Azul Femenil vs Pumas Femenil" onto "Pumas vs Cruz Azul";
* a men's name could resolve to a women's row, because the stripped alias
  sits in the same lookup namespace and ``find_team_by_alias`` orders a
  ``normalized_alias`` hit ahead of a ``TeamModel.name`` hit. Verified
  against the live database before this revision: resolving "Barcelona"
  returned "Barcelona Femenino", not the men's club that owns 58 results
  in SP1.

``uq_team_alias_normalized`` makes the slug unique, so a row whose
recomputed slug another team already holds is skipped rather than moved.

Nothing was written to ``match_results`` from this — the fixtures that
exposed it had no scores yet — but the next scored women's round would
have attached a women's result to a men's match with the sign inverted.

Dropping the stopword fixes new lookups; this revision re-derives the
aliases already stored under the old rule. Data migration only — no schema
DDL. ``_migrate_to_v23`` is the apply path; ``SCHEMA_VERSION`` is bumped
to 23.
"""
from alembic import op  # noqa: F401  (imported for parity with the other revisions)

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data migration only — the runtime migration (_migrate_to_v23) applies it.
    pass


def downgrade() -> None:
    # Intentionally a no-op: the pre-migration slugs are recoverable from the
    # alias text itself, and reverting would restore the collision. Mirrors 0022.
    pass
