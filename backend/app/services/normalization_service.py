import re
import unicodedata


class NormalizationService:
    # "femenil"/"femenino" are deliberately NOT stopwords. Dropping them
    # collapsed every women's side onto the men's club of the same name:
    # "Cruz Azul Femenil" and "Cruz Azul" both normalized to `cruz-azul`,
    # so a Liga MX Femenil fixture resolved to the men's team and its
    # document linked to the men's match — orientation included, which
    # produced links like "Cruz Azul Femenil vs Pumas Femenil" onto
    # "Pumas vs Cruz Azul". The competition layer already keeps
    # `international-friendlies-women` separate for exactly this reason;
    # the team layer has to agree.
    TEAM_STOPWORDS = {"fc", "cf", "club", "de", "ac", "sc", "cd", "unam"}
    COMPETITION_STOPWORDS = {"liga", "division", "primera", "torneo", "league"}
    TEAM_ALIAS_SLUGS = {
        "ath bilbao": "athletic-bilbao",
        "atletico madrid": "atletico-madrid",
        "atletico de madrid": "atletico-madrid",
        "b munich": "bayern-munich",
        "c azul": "cruz-azul",
        "inter p a": "internacional-porto-alegre",
        "internacional porto alegre": "internacional-porto-alegre",
        "paris sg": "paris-saint-germain",
        "r sociedad": "real-sociedad",
        "rayo vallec": "rayo-vallecano",
        "u catolica": "universidad-catolica",
        "universidad catolica": "universidad-catolica",
        # Four different clubs are called some form of "Nacional", and
        # `_normalize` strips "club"/"cd"/"de" as stopwords, so three of them
        # collapse onto the bare slug `nacional`. That is how 104 Portuguese
        # matches and 8 Copa Libertadores ones ended up on one row.
        # `TEAM_ALIAS_SLUGS` is consulted BEFORE stopword stripping, so it is
        # the only place the distinction survives. Each live source spells its
        # club differently, which is what makes the mapping unambiguous:
        # TheSportsDB says "Club Nacional" for Asuncion, football-data.org
        # sends full names ("Club Nacional de Football", "Club Nacional
        # Potosi") because the connector reads `name` and never `shortName`,
        # and the two Portuguese feeds say "CD Nacional" / "Nacional" — those
        # keep the bare slug.
        "club nacional": "nacional-asuncion",
        "nacional asuncion": "nacional-asuncion",
        # National team aliases: Progol PDF uses Spanish names, TheSportsDB
        # uses English. Map both inputs to the same slug so the Progol
        # fixture resolver matches the friendlies ingested under
        # International Friendlies (league id 4562).
        "eua": "usa",
        "e u a": "usa",
        "estados unidos": "usa",
        "noruega": "norway",
        "suecia": "sweden",
        "dinamarca": "denmark",
        "alemania": "germany",
        "francia": "france",
        "italia": "italy",
        "espana": "spain",
        "paises bajos": "netherlands",
        "holanda": "netherlands",
        "polonia": "poland",
        "belgica": "belgium",
        "inglaterra": "england",
        "escocia": "scotland",
        "republica de irlanda": "republic-of-ireland",
        "republica checa": "czech-republic",
        "ucrania": "ukraine",
        "rusia": "russia",
        "turquia": "turkey",
        "japon": "japan",
        "corea del sur": "south-korea",
        "arabia saudita": "saudi-arabia",
        "catar": "qatar",
        "emiratos arabes unidos": "united-arab-emirates",
        "marruecos": "morocco",
        "egipto": "egypt",
        "argelia": "algeria",
        "cabo verde": "cape-verde",
        "costa de marfil": "ivory-coast",
        "republica democratica del congo": "democratic-republic-of-congo",
        "sudafrica": "south-africa",
        "brasil": "brazil",
        # Phase 8 — national team coverage for PG-2336.
        # The Progol PDF and some Indigo guides use short Spanish forms that
        # don't appear in the existing alias table. Without these entries
        # the fixture resolver creates a new placeholder entity instead of
        # linking to the real TheSportsDB-ingested one, leaving the team
        # with 0 result history and triggering insufficient_data_anchors.
        "croacia": "croatia",
        "tunez": "tunisia",
        "nueva zelanda": "new-zealand",
        "bosnia": "bosnia-herzegovina",
        "bosnia-herzegovina": "bosnia-herzegovina",
        "bosnia y herzegovina": "bosnia-herzegovina",
        # "republica de corea" captures "República De Corea" (Progol) and
        # "Korea Republic" abbreviations; "corea del sur" already existed.
        "republica de corea": "south-korea",
        "korea republic": "south-korea",
        # Progol Media Semana PDF uses abbreviated forms like "Re P. Corea",
        # "Rep. Corea", and TSDB sometimes uses "Korea Rep" — all are South Korea.
        "re p corea": "south-korea",
        "rep corea": "south-korea",
        "korea rep": "south-korea",
        "corea del norte": "north-korea",
        "corea": "south-korea",       # bare "Corea" in some PDF editions
        # "chequia" is the modern Spanish name for Czech Republic;
        # "republica checa" is already mapped above.
        "chequia": "czech-republic",
        # Canada (same in Spanish/English) – explicit to avoid tokenizer
        # stripping "canada" as a stopword substring.
        "canada": "canada",
        # South Africa: "sudafrica" already mapped above; add regional variant
        "africa del sur": "south-africa",
        # Ivory Coast: "costa de marfil" already mapped above; add French variants
        "cote d ivoire": "ivory-coast",
        "costa marfil": "ivory-coast",
        # Ecuador (same in Spanish) – explicit entry for safety
        "ecuador": "ecuador",
        # panamá with accent stripped → "panama" is already the slug
        # (no stopword collision); this entry documents the canonical form.
        "panama": "panama",
        # Netherlands: "paises bajos" and "holanda" already mapped above
        "holland": "netherlands",
        # USA: "estados unidos" and "eua" already mapped above
        "united states": "usa",
        "usa": "usa",
        # Belgium: "belgica" already mapped above
        "belgie": "belgium",
        # Liga Expansión MX team aliases (PDF uses short names)
        "tampico": "tampico-madero",
        "tepatitlan": "tepatitlan-fc",
        # Current Progol PDF abbreviations/truncations observed in active
        # slates. Keep these only for unambiguous aliases; single-letter
        # fragments such as "G" remain unresolved and must be surfaced for
        # manual review.
        "s laguna": "santos-laguna",
        "vasco da ga": "vasco-da-gama",
        "g argentina": "argentina",
        "aguilas": "america",
        "chicago": "chicago-fire",
        "vancouver": "vancouver-whitecaps",
        "vitoria ba": "vitoria",
        "vitoria bahia": "vitoria",
        "st louis": "st-louis-city",
        "kansas city": "sporting-kansas-city",
        "aucas": "sd-aucas",
        "sport recife": "sport-do-recife",
        "sport club recife": "sport-do-recife",
        "operario": "operario-ferroviario",
        "operario ferroviario": "operario-ferroviario",
        "sarpsborg": "sarpsborg-08",
        "sarpsborg 08": "sarpsborg-08",
        "sarpsborg 08 ff": "sarpsborg-08",
        "kristiansund bk": "kristiansund",
        "racing club de montevideo": "racing-montevideo",
        # Allsvenskan team aliases — PDF and TSDB sometimes use slightly
        # different stems for the long Swedish names.
        "brommapojkarna": "brommapojkarna",
        "degerfors": "degerfors-if",
        "kalmar": "kalmar-ff",
        "malmo": "malmo-ff",
        "malmo ff": "malmo-ff",
        # Phase 9 — national-team canonicalization for the API-Football
        # sports-score audit. API-Football uses English forms (Czechia,
        # Switzerland, Bosnia & Herzegovina) while the Progol slate stores
        # Spanish/long forms; both inputs must collapse to one slug so the
        # matcher scores them as the same team. Several of these slugs are
        # already produced by _normalize(); the explicit entries pin the
        # cross-language pairs that otherwise diverge.
        "mexico": "mexico",
        "south korea": "south-korea",
        "korea": "south-korea",
        "czechia": "czech-republic",
        "czech republic": "czech-republic",
        "suiza": "switzerland",
        "switzerland": "switzerland",
        "swiss": "switzerland",
        "bosnia and herzegovina": "bosnia-herzegovina",
        "bosnia herzegovina": "bosnia-herzegovina",
        "bosnia & herzegovina": "bosnia-herzegovina",
        "ivory coast": "ivory-coast",
        "netherlands": "netherlands",
        "qatar": "qatar",
        "cape verde": "cape-verde",
        "turkiye": "turkey",
        "turkey": "turkey",
        "japan": "japan",
        "tunisia": "tunisia",
        "norway": "norway",
        "morocco": "morocco",
        "croatia": "croatia",
        # Club short/long-name pins (2026-07-17). football-data emits the
        # long form ("Kalmar FF", "Malmo FF", "Sarpsborg 08") while TheSportsDB
        # emits the short form ("Kalmar", "Malmö", "Sarpsborg"); without these
        # both forms produced different slugs and the TSDB ingest created a
        # duplicate team, splitting each side's recent form across two rows.
        # The short forms and "malmo ff"/"sarpsborg 08" are already pinned
        # above; only the "Kalmar FF" long form was still missing.
        "kalmar ff": "kalmar-ff",
        # Uruguayan CA Cerro (Montevideo). TSDB writes bare "Cerro"; the
        # canonical row is "Ca Cerro". Paraguayan Cerro Porteño normalizes
        # to "cerro-porteno" and is unaffected.
        "cerro": "ca-cerro",
        "ca cerro": "ca-cerro",
        "club atletico cerro": "ca-cerro",
        # Serie A short forms (football-data.co.uk I1.csv). The canonical
        # rows came from the UCL feed under their long names, so without
        # these pins the CSV ingest would mint a second row per club and
        # split each side's form — the defect v30/v31 had to fold by hand.
        # Only these two collide: "Juventus FC" and "AC Milan" already
        # normalize onto the CSV's "Juventus"/"Milan" because `fc` and `ac`
        # are team stopwords, and the remaining Italian clubs have no row yet.
        #
        # Bare "inter" is pinned to Internazionale, not to Internacional
        # (Porto Alegre): no feed we ingest emits "Inter" for the Brazilian
        # club — TSDB Brasileirao writes "Internacional", and "Inter P.A." /
        # "Internacional Porto Alegre" are pinned separately above.
        "inter": "internazionale-milano",
        "atalanta": "atalanta-bc",
        # Argentine Primera short forms (football-data.co.uk ARG.csv). We
        # ingest that file because TheSportsDB's free tier caps eventsday.php
        # at three events per call and drops most of a matchday. Only the
        # abbreviations below fail to reach their canonical row on their own;
        # "Boca Juniors", "Atl. Tucuman", "Lanus", "Rosario Central",
        # "Platense" and "Newells Old Boys" already resolve through alias rows
        # the earlier TSDB ingests created.
        #
        # "Union de Santa Fe" pins to the bare "union" row rather than the
        # other way round: that is the row the TSDB feed writes and the one
        # carrying the club's 20 matches. The Chilean Unións (La Calera,
        # Española) keep their own slugs and are unaffected.
        "dep riestra": "deportivo-riestra",
        "estudiantes l p": "estudiantes-la-plata",
        "gimnasia l p": "gimnasia-y-esgrima-la-plata",
        "gimnasia mendoza": "gimnasia-y-esgrima-mendoza",
        "ind rivadavia": "cs-independiente-rivadavia",
        "san martin s j": "san-martin-san-juan",
        "sarmiento junin": "sarmiento",
        "union de santa fe": "union",
        # Manchester City arrives under two names from two feeds:
        # football-data.co.uk's E0 writes "Man City" (the row holding the
        # club's league history), the Champions League feed writes
        # "Manchester City FC". `fc` is a stopword, so the long form lands on
        # "manchester-city" and never meets "man-city" — the split v35 folds.
        # Pinning both spellings keeps the next UCL ingest from reopening it.
        # Women's sides carry their gender marker and keep separate slugs.
        "manchester city": "man-city",
        "manchester city fc": "man-city",
    }
    COMPETITION_ALIAS_SLUGS = {
        "copa de alemania": "german-cup",
        "copa de rusia": "russian-cup",
        "j1 league": "j1-league",
        "la liga": "la-liga",
        "laliga": "la-liga",
        "liga mx": "liga-mx",
        "premier league": "premier-league",
        "primera division chile": "primera-division-chile",
        "russian cup": "russian-cup",
        "serie a brasil": "serie-a-brazil",
        "serie a brazil": "serie-a-brazil",
        "brasileirao serie b": "serie-b-brazil",
        "brasileirao serie b brazil": "serie-b-brazil",
        "campeonato brasileiro serie b": "serie-b-brazil",
        "brazilian serie b": "serie-b-brazil",
        "brazil serie b": "serie-b-brazil",
        "serie b brazil": "serie-b-brazil",
        "club friendlies": "club-friendlies",
        "club friendly": "club-friendlies",
        "amistosos de clubes": "club-friendlies",
        "amistoso de clubes": "club-friendlies",
        "norwegian eliteserien": "norwegian-eliteserien",
        "eliteserien": "norwegian-eliteserien",
        "liga auf uruguaya": "uruguayan-primera-division",
        "uruguayan primera division": "uruguayan-primera-division",
        "primera division uruguay": "uruguayan-primera-division",
        "ligapro serie a": "ecuador-serie-a",
        "ecuador serie a": "ecuador-serie-a",
        # ESPN labels the CONMEBOL cups with the confederation in front. Left
        # unaliased they would open a second competition next to the one
        # football-data.org already fills, and the same match would exist
        # twice under two fixture identities.
        "conmebol libertadores": "copa-libertadores",
        "copa libertadores": "copa-libertadores",
        "conmebol sudamericana": "copa-sudamericana",
        "copa sudamericana": "copa-sudamericana",
        # football-data.co.uk's ARG.csv labels the Argentine top flight by its
        # sponsor-era name, "Liga Profesional". Both COMPETITION_STOPWORDS
        # entries in it ("liga") strip away, leaving the slug "profesional",
        # which would stand up a second competition row beside the
        # "Argentinian Primera Division" the TheSportsDB ingests built.
        #
        # That split costs more than tidiness: the recent-form window is sized
        # from the competition's own median match gap, competition_profiles
        # drives the knockout draw calibration, and the published walk-forward
        # verdict is keyed per competition — all three would see half a league.
        # "Copa De La Liga Profesional" is a genuinely different tournament and
        # keeps its own slug.
        "liga profesional": "argentinian",
        "argentina primera division": "argentinian",
        # New leagues (Fase 6.6) — without these explicit aliases the
        # generic _normalize() strips "liga"/"la" as stopwords and the
        # resulting slug ("de-expansion-mx", "spanish-la-2") no longer
        # matches the policy table in model_training_service.
        "liga de expansion mx": "liga-expansion-mx",
        "mexican liga de expansion mx": "liga-expansion-mx",
        "spanish la liga 2": "spanish-la-liga-2",
        "spanish la liga": "spanish-la-liga",
        # Phase 8 — International Friendlies name variants.
        # TSDB stores "International Friendlies"; some feeds write the
        # Spanish form or short form. All map to the same slug so the
        # competition_operating_policy lookup finds the ready policy.
        "international friendlies": "international-friendlies",
        "amistosos internacionales": "international-friendlies",
        "amistoso internacional": "international-friendlies",
        "international friendly": "international-friendlies",
        "fifa international friendlies": "international-friendlies",
        # UEFA Nations League (treated as international-friendlies for
        # policy purposes until a dedicated benchmark is built).
        "uefa nations league": "international-friendlies",
        "nations league": "international-friendlies",
        "uefa nations league a": "international-friendlies",
        "uefa nations league b": "international-friendlies",
        "uefa nations league c": "international-friendlies",
        "liga de naciones": "international-friendlies",
        "liga de naciones uefa": "international-friendlies",
        # World Cup Qualifying confederation variants.
        # TSDB stores these under separate league IDs (5513-5518); they
        # follow the operating policy for international-friendlies until a
        # dedicated WCQ benchmark is built.
        "world cup qualifying": "international-friendlies",
        "world cup qualifying uefa": "international-friendlies",
        "world cup qualifying conmebol": "international-friendlies",
        "world cup qualifying caf": "international-friendlies",
        "world cup qualifying afc": "international-friendlies",
        "world cup qualifying concacaf": "international-friendlies",
        "world cup qualifying ofc": "international-friendlies",
        "eliminatorias mundialistas": "international-friendlies",
        "clasificatorio mundial": "international-friendlies",
        "eliminatorias sudamericanas": "international-friendlies",
        "eliminatorias conmebol": "international-friendlies",
        "wcq": "international-friendlies",
        # Phase 9 — short friendlies labels API-Football emits. These ADD
        # to the competition score (one weighted term), they never force a
        # match. Women's friendlies stay a SEPARATE slug so a women's
        # fixture can't be scored against a men's national-team slate.
        "friendlies": "international-friendlies",
        "friendly": "international-friendlies",
        "world friendlies": "international-friendlies",
        "friendlies women": "international-friendlies-women",
        "women friendlies": "international-friendlies-women",
    }

    def normalize_team_name(self, value: str) -> str:
        alias_key = self._alias_key(value)
        if alias_key in self.TEAM_ALIAS_SLUGS:
            return self.TEAM_ALIAS_SLUGS[alias_key]
        return self._normalize(value, self.TEAM_STOPWORDS)

    def normalize_competition_name(self, value: str) -> str:
        alias_key = self._alias_key(value)
        if alias_key in self.COMPETITION_ALIAS_SLUGS:
            return self.COMPETITION_ALIAS_SLUGS[alias_key]
        return self._normalize(value, self.COMPETITION_STOPWORDS)

    def _alias_key(self, value: str) -> str:
        ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        lowered = ascii_value.lower()
        cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
        return " ".join(cleaned.split())

    def _normalize(self, value: str, stopwords: set[str]) -> str:
        ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        lowered = ascii_value.lower()
        cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
        tokens = [token for token in cleaned.split() if token and token not in stopwords]
        return "-".join(tokens)
