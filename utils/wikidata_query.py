"""Helpers for resolving country names/codes to Wikidata Q-identifiers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pycountry

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_PATH = SCRIPT_DIR / "_country_wikidata_cache.json"

# Canonical replacements for names observed in source metadata.
COUNTRY_ALIASES = {
    "Moldova, Republic of": "Moldova",
    "Republic of Moldova": "Moldova",
    "Russian Federation": "Russia",
    "Türkiye": "Turkey",
    # Historic ECHR respondent codes mapped to canonical names.
    "CSK": "Czechoslovakia",
    "SCG": "Serbia and Montenegro",
}

# Deterministic mappings for countries observed in ECHR metadata.
COUNTRY_QID_OVERRIDES = {
    "Albania": "Q222",
    "Andorra": "Q228",
    "Armenia": "Q399",
    "Austria": "Q40",
    "Azerbaijan": "Q227",
    "Belgium": "Q31",
    "Bosnia and Herzegovina": "Q225",
    "Bulgaria": "Q219",
    "Croatia": "Q224",
    "Cyprus": "Q229",
    "Czechia": "Q213",
    "Czechoslovakia": "Q33946",
    "Denmark": "Q35",
    "Estonia": "Q191",
    "Eswatini": "Q1050",
    "Finland": "Q33",
    "France": "Q142",
    "Georgia": "Q230",
    "Germany": "Q183",
    "Greece": "Q41",
    "Hungary": "Q28",
    "Iceland": "Q189",
    "Ireland": "Q27",
    "Italy": "Q38",
    "Latvia": "Q211",
    "Liechtenstein": "Q347",
    "Lithuania": "Q37",
    "Luxembourg": "Q32",
    "Malta": "Q233",
    "Moldova": "Q217",
    "Monaco": "Q235",
    "Montenegro": "Q236",
    "Netherlands": "Q55",
    "North Macedonia": "Q221",
    "Norway": "Q20",
    "Poland": "Q36",
    "Portugal": "Q45",
    "Romania": "Q218",
    "Russia": "Q159",
    "San Marino": "Q238",
    "Serbia": "Q403",
    "Serbia and Montenegro": "Q37024",
    "Slovakia": "Q214",
    "Slovenia": "Q215",
    "Spain": "Q29",
    "Sweden": "Q34",
    "Switzerland": "Q39",
    "Turkey": "Q43",
    "Ukraine": "Q212",
    "United Kingdom": "Q145",
}


def _load_cache() -> dict[str, str]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=True, indent=2), encoding="utf-8")


def _search_wikidata_country(country_name: str) -> str | None:
    params = urlencode(
        {
            "action": "wbsearchentities",
            "format": "json",
            "language": "en",
            "type": "item",
            "limit": 5,
            "search": country_name,
        }
    )
    url = f"https://www.wikidata.org/w/api.php?{params}"

    with urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))

    for item in payload.get("search", []):
        q_id = item.get("id")
        if isinstance(q_id, str) and q_id.startswith("Q"):
            return q_id
    return None


@lru_cache(maxsize=2048)
def _canonical_country_name_from_text(raw_text: str) -> str | None:
    raw = raw_text.strip()
    if not raw:
        return None

    alias_hit = COUNTRY_ALIASES.get(raw) or COUNTRY_ALIASES.get(raw.upper())
    candidate = alias_hit or raw

    # Prefer ISO alpha-3 normalization because pipeline respondent values are code-based.
    if len(candidate) == 3 and candidate.isalpha():
        resolved = pycountry.countries.get(alpha_3=candidate.upper())
        if resolved is not None:
            candidate = resolved.name

    alias_hit = COUNTRY_ALIASES.get(candidate)
    if alias_hit:
        candidate = alias_hit

    try:
        return pycountry.countries.search_fuzzy(candidate)[0].name
    except (LookupError, AttributeError):
        return candidate


def get_canonical_country_name(country_value: object) -> str | None:
    """Normalize a country label or ISO alpha-3 code to a canonical country name."""
    if country_value is None:
        return None
    return _canonical_country_name_from_text(str(country_value))


@lru_cache(maxsize=1024)
def _country_identifier_from_canonical(normalized: str) -> str | None:
    if normalized in COUNTRY_QID_OVERRIDES:
        return COUNTRY_QID_OVERRIDES[normalized]

    cache = _load_cache()
    if normalized in cache:
        return cache[normalized]

    try:
        q_id = _search_wikidata_country(normalized)
    except Exception:
        return None

    if q_id:
        cache[normalized] = q_id
        _save_cache(cache)
    return q_id


def get_country_identifier(country_name: object) -> str | None:
    """Resolve a country label or code to a Wikidata Q-id (for example, 'FRA' -> 'Q142')."""
    normalized = get_canonical_country_name(country_name)
    if not normalized:
        return None
    return _country_identifier_from_canonical(normalized)
