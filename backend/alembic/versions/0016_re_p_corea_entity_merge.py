"""Merge Re P. Corea placeholder into canonical South Korea entity.

Root cause: the Progol Media Semana PDF uses the abbreviated form
"Re P. Corea" for South Korea.  The normalization service lacked an alias
entry for the alias-key "re p corea", so the entity resolver created a
new placeholder TeamModel instead of linking to the canonical South Korea
entity that TheSportsDB ingestion populates.  The feature service then
found zero recent results for the placeholder, triggering
confidence_band=blocked due to insufficient data anchors.

This revision documents the runtime migration in _migrate_to_v16 and the
accompanying alias additions to NormalizationService.TEAM_ALIAS_SLUGS
("re p corea", "rep corea", "korea rep" → "south-korea").

Revision: 0016
"""

revision = "0016"
down_revision = "0015"
