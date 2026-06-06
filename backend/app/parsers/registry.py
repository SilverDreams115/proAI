import logging

from app.parsers.base import SourceParser
from app.parsers.generic import GenericSourceParser
from app.parsers.sports_feed_v1 import SportsFeedV1Parser

logger = logging.getLogger(__name__)


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers = self._build_default_parsers()

    def get(self, profile_name: str) -> SourceParser:
        if profile_name in self._parsers:
            return self._parsers[profile_name]
        # Silent fallback to generic was masking real bugs: a CSV/sports-feed
        # source mis-tagged with an unknown profile would emit `fixtures`
        # payloads that the generic parser drops, so no matches were ever
        # created and historical_results stayed empty. Log loud so the
        # ingest run is debuggable.
        logger.warning(
            "parser_profile %r is not registered (known: %s); falling back to generic. "
            "If the source emits 'fixtures' the matches will NOT be persisted.",
            profile_name,
            sorted(self._parsers.keys()),
        )
        return self._parsers["generic"]

    def has(self, profile_name: str) -> bool:
        return profile_name in self._parsers

    def known_profiles(self) -> list[str]:
        return sorted(self._parsers.keys())

    def reset(self) -> None:
        self._parsers = self._build_default_parsers()

    def _build_default_parsers(self) -> dict[str, SourceParser]:
        generic = GenericSourceParser()
        sports_feed = SportsFeedV1Parser()
        return {
            generic.profile_name: generic,
            sports_feed.profile_name: sports_feed,
        }


parser_registry = ParserRegistry()
