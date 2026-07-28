"""Unlink women's documents from men's matches and drop their evidence.

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-28

The last of the femenil contamination. While the normalizer stripped
"femenil" from team names, "Cruz Azul Femenil" resolved to the men's Cruz
Azul, and women's fixture documents were linked onto men's Liga MX
matches. v23-v26 split the team rows and moved the fixtures; the
``gender_mismatch`` blocker in ``sports_score_matching`` stops new bad
links. Neither went back for the links already written.

25 documents remained linked to 21 men's matches, each contributing an
evidence item to that match's recent-form window — a men's Pumas fixture
carrying a Pumas Femenil result as if it were its own.

Scope, verified before writing this revision:

* ``match_results`` are untouched and were never wrong. Every result on
  the 21 fixtures came from the men's own source ("TSDB Liga MX"), so the
  scores are genuine men's scores. Only evidence was polluted.
* The documents are unlinked, not deleted. They are real documents about
  real women's matches; a later ingestion run can link them to the right
  fixture now that the blocker is in place.

Idempotent: a re-run finds nothing left to unlink.
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_MARKERS = ("femenil", "femenino", "femenina", "women")

_BAD_LINKS = """
    SELECT d.id AS document_id, d.linked_evidence_id AS evidence_id
    FROM source_documents d
    JOIN matches m ON m.id = d.matched_match_id
    JOIN teams ht ON ht.id = m.home_team_id
    JOIN teams at ON at.id = m.away_team_id
    WHERE ({title_clause}) AND ({team_clause})
""".format(
    title_clause=" OR ".join(f"LOWER(d.title) LIKE '%{m}%'" for m in _MARKERS),
    team_clause=" AND ".join(
        f"LOWER(ht.name) NOT LIKE '%{m}%' AND LOWER(at.name) NOT LIKE '%{m}%'"
        for m in _MARKERS
    ),
)


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.exec_driver_sql(_BAD_LINKS).fetchall()
    if not rows:
        return
    document_ids = tuple(row[0] for row in rows)
    evidence_ids = tuple(row[1] for row in rows if row[1])

    connection.exec_driver_sql(
        "UPDATE source_documents "
        "SET matched_match_id = NULL, linked_evidence_id = NULL "
        "WHERE id = ANY(%(ids)s)",
        {"ids": list(document_ids)},
    )
    if evidence_ids:
        connection.exec_driver_sql(
            "DELETE FROM evidence_items WHERE id = ANY(%(ids)s)",
            {"ids": list(evidence_ids)},
        )


def downgrade() -> None:
    # The links were wrong; re-creating them is not a recovery.
    pass
