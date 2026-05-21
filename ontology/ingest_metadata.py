"""
Creates the A-box for the schema T-box created in an earlier script for the HUDOC metadata

Last Updated:
21.05.26

Status:
Done

History:
v1_0 - maps the dataframes to the appropriate parts of the schema 
v2_0 - pushes this to Fuseki
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD
from rdflib.namespace import DCTERMS


SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ontology.create_schema import ONTOLOGY_BASE_IRI as SCHEMA_BASE_IRI
from ontology.create_schema import ONTOLOGY_IRI as SCHEMA_ONTOLOGY_IRI


DATA_ONTOLOGY_IRI = "https://github.com/dahrb/Art_6/tree/main/ontology/metadata.ttl"
DATA_BASE_IRI = f"{DATA_ONTOLOGY_IRI}#"

DEFAULT_SCHEMA_TTL = SCRIPT_DIR / "seed.ttl"
DEFAULT_METADATA_TTL = SCRIPT_DIR / "metadata.ttl"
DEFAULT_SAMPLE_PARQUET = REPO_ROOT / "data" / "sample_metadata.parquet"
DEFAULT_JUDGES_JSON = REPO_ROOT / "data" / "additional_data" / "judges_processed.json"

ARTICLE6_LIMB_MAP = {
    "civil": "Limb_Civil",
    "criminal": "Limb_Criminal",
    "mixed": "Limb_Mixed",
    "constitutional": "Limb_Constitutional",
    "unspecified": "Limb_Unspecified",
}

IMPORTANCE_MAP = {
    "1": "Importance_1",
    "2": "Importance_2",
    "3": "Importance_3",
    "4": "Importance_4",
}

LAW_SYSTEM_MAP = {
    "civil": "LawSystem_Civil",
    "common": "LawSystem_Common",
    "mixed": "LawSystem_Mixed",
}


@dataclass
class CaseAudit:
    itemid: str
    case_uri: URIRef
    mapped_fields: dict[str, list[str]] = field(default_factory=dict)
    related_nodes: set[URIRef] = field(default_factory=set)


@dataclass
class IngestionContext:
    graph: Graph
    schema_ns: Namespace
    data_ns: Namespace
    judge_authority_by_id: dict[int, dict[str, Any]]
    judge_authority_by_name: dict[str, dict[str, Any]]
    audits: dict[str, CaseAudit] = field(default_factory=dict)
    case_nodes: dict[str, URIRef] = field(default_factory=dict)
    application_nodes: dict[str, URIRef] = field(default_factory=dict)
    article_nodes: dict[str, URIRef] = field(default_factory=dict)
    country_nodes: dict[str, URIRef] = field(default_factory=dict)
    judge_nodes: dict[str, URIRef] = field(default_factory=dict)
    formation_nodes: dict[str, URIRef] = field(default_factory=dict)
    judgment_type_nodes: dict[str, URIRef] = field(default_factory=dict)
    keyword_nodes: dict[str, URIRef] = field(default_factory=dict)
    application_to_cases: dict[str, set[URIRef]] = field(default_factory=dict)
    cited_applications_by_case: dict[URIRef, list[str]] = field(default_factory=dict)
    finding_counter: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest parquet metadata into a Turtle graph aligned to ontology/create_schema.py."
    )
    parser.add_argument("--schema-ttl", type=Path, default=DEFAULT_SCHEMA_TTL)
    parser.add_argument("--input-parquet", type=Path, default=DEFAULT_SAMPLE_PARQUET)
    parser.add_argument("--judges-json", type=Path, default=DEFAULT_JUDGES_JSON)
    parser.add_argument("--output-ttl", type=Path, default=DEFAULT_METADATA_TTL)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except Exception:
        return False
    return bool(missing) if isinstance(missing, bool) else False

def normalize_text(value: Any) -> str | None:
    if is_missing(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text

def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in (normalize_text(v) for v in value) if item]
    if isinstance(value, tuple):
        return [item for item in (normalize_text(v) for v in value) if item]
    if hasattr(value, "tolist"):
        try:
            converted = value.tolist()
        except Exception:
            converted = None
        if isinstance(converted, list):
            return [item for item in (normalize_text(v) for v in converted) if item]

    text = normalize_text(value)
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return [item for item in (normalize_text(v) for v in parsed) if item]
    return [item for item in (normalize_text(part) for part in re.split(r"[;|]+", text)) if item]

def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def normalize_article_code(code: str) -> str:
    code = code.strip()
    if re.match(r"^p\d", code, flags=re.IGNORECASE):
        return f"P{code[1:]}"
    return code

def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").lower()
    return slug or "unknown"

def parse_optional_date(value: Any):
    text = normalize_text(value)
    if not text:
        return None
    candidate = text.split("T", 1)[0]
    try:
        return datetime.strptime(candidate, "%Y-%m-%d").date()
    except ValueError:
        return None

def parse_year_range(value: Any) -> tuple[str | None, str | None]:
    text = normalize_text(value)
    if not text:
        return None, None
    match = re.fullmatch(r"(\d{4})?(?:-(\d{4})?)?", text)
    if not match:
        return None, None
    return match.group(1), match.group(2)

def string_literal(value: str) -> Literal:
    return Literal(value, datatype=XSD.string)

def add_audit_value(audit: CaseAudit, field_name: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, list):
        normalized_values = [str(item) for item in value if str(item).strip()]
    else:
        normalized_values = [str(value)]
    slot = audit.mapped_fields.setdefault(field_name, [])
    for item in normalized_values:
        if item not in slot:
            slot.append(item)

def load_judge_authority(path: Path) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[int, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}

    if not path.exists():
        return by_id, by_name

    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        judge_id = row.get("judge_id")
        if isinstance(judge_id, int):
            by_id[judge_id] = row

        raw_name = normalize_text(row.get("Judge Name"))
        if raw_name:
            by_name[slugify(raw_name)] = row

    return by_id, by_name

def create_graph() -> Graph:
    graph = Graph()
    graph.bind("echr", Namespace(SCHEMA_BASE_IRI))
    graph.bind("echrmeta", Namespace(DATA_BASE_IRI))
    graph.bind("dcterms", DCTERMS)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)

    ontology_ref = URIRef(DATA_ONTOLOGY_IRI)
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    graph.add((ontology_ref, RDF.type, OWL.Ontology))
    graph.add((ontology_ref, DCTERMS.title, Literal("ECHR Art. 6 Metadata Graph", lang="en")))
    graph.add((
        ontology_ref,
        DCTERMS.description,
        Literal("A-box metadata graph aligned to the ECHR Art. 6 schema.", lang="en"),
    ))
    graph.add((ontology_ref, DCTERMS.created, Literal(created_at, datatype=XSD.dateTime)))
    graph.add((ontology_ref, OWL.imports, URIRef(SCHEMA_ONTOLOGY_IRI)))
    return graph

_COUNTRY_TOOLS: tuple[Any, Any] | None = None

def get_country_tools() -> tuple[Any, Any]:
    global _COUNTRY_TOOLS
    if _COUNTRY_TOOLS is None:
        from utils.wikidata_query import get_canonical_country_name, get_country_identifier

        _COUNTRY_TOOLS = (get_canonical_country_name, get_country_identifier)
    return _COUNTRY_TOOLS

def ensure_application(ctx: IngestionContext, app_number: str) -> URIRef:
    app_number = app_number.strip()
    existing = ctx.application_nodes.get(app_number)
    if existing is not None:
        return existing

    uri = ctx.data_ns[f"application_{slugify(app_number.replace('/', '_'))}"]
    ctx.application_nodes[app_number] = uri
    ctx.graph.add((uri, RDF.type, ctx.schema_ns.Application))
    ctx.graph.add((uri, ctx.schema_ns.hasApplicationNumber, string_literal(app_number)))
    ctx.graph.add((uri, RDFS.label, Literal(app_number)))
    return uri

def ensure_convention_article(ctx: IngestionContext, article_code: str) -> URIRef:
    article_code = article_code.strip()
    existing = ctx.article_nodes.get(article_code)
    if existing is not None:
        return existing

    uri = ctx.data_ns[f"convention_article_{slugify(article_code)}"]
    ctx.article_nodes[article_code] = uri
    ctx.graph.add((uri, RDF.type, ctx.schema_ns.ConventionArticle))
    ctx.graph.add((uri, RDFS.label, Literal(f"Article {article_code}")))
    return uri

def ensure_country(ctx: IngestionContext, raw_country: str) -> URIRef | None:
    get_canonical_country_name, get_country_identifier = get_country_tools()
    canonical = get_canonical_country_name(raw_country)
    if not canonical:
        return None

    existing = ctx.country_nodes.get(canonical)
    if existing is not None:
        return existing

    uri = ctx.data_ns[f"country_{slugify(canonical)}"]
    ctx.country_nodes[canonical] = uri
    ctx.graph.add((uri, RDF.type, ctx.schema_ns.Country))
    ctx.graph.add((uri, RDFS.label, Literal(canonical, lang="en")))

    q_id = get_country_identifier(canonical)
    if q_id:
        ctx.graph.add((uri, OWL.sameAs, URIRef(f"http://www.wikidata.org/entity/{q_id}")))
    return uri


def ensure_court_formation(ctx: IngestionContext, label: str) -> URIRef:
    existing = ctx.formation_nodes.get(label)
    if existing is not None:
        return existing

    uri = ctx.data_ns[f"court_formation_{slugify(label)}"]
    ctx.formation_nodes[label] = uri
    ctx.graph.add((uri, RDF.type, ctx.schema_ns.CourtFormation))
    ctx.graph.add((uri, RDFS.label, Literal(label)))
    return uri


def ensure_judgment_type(ctx: IngestionContext, label: str) -> URIRef:
    existing = ctx.judgment_type_nodes.get(label)
    if existing is not None:
        return existing

    uri = ctx.data_ns[f"judgment_type_{slugify(label)}"]
    ctx.judgment_type_nodes[label] = uri
    ctx.graph.add((uri, RDF.type, ctx.schema_ns.JudgmentType))
    ctx.graph.add((uri, RDFS.label, Literal(label)))
    return uri


def ensure_keyword(ctx: IngestionContext, label: str, code: str | None = None) -> URIRef:
    key = slugify(label)
    existing = ctx.keyword_nodes.get(key)
    if existing is not None:
        return existing

    uri = ctx.data_ns[f"keyword_{key}"]
    ctx.keyword_nodes[key] = uri
    ctx.graph.add((uri, RDF.type, ctx.schema_ns.Keyword))
    ctx.graph.add((uri, RDFS.label, Literal(label)))
    if code is not None:
        ctx.graph.add((uri, ctx.schema_ns.hasKeywordCode, string_literal(code)))
    return uri


def resolve_judge_authority(
    ctx: IngestionContext,
    judge_name: str | None,
    judge_id: int | None,
) -> dict[str, Any] | None:
    if judge_id is not None:
        authority = ctx.judge_authority_by_id.get(judge_id)
        if authority is not None:
            return authority
    if judge_name:
        return ctx.judge_authority_by_name.get(slugify(judge_name))
    return None


def ensure_judge(
    ctx: IngestionContext,
    judge_name: str | None,
    judge_id: int | None,
) -> URIRef | None:
    authority = resolve_judge_authority(ctx, judge_name, judge_id)

    resolved_name = judge_name
    resolved_id = judge_id
    country_name = None
    tenure_begin, tenure_end = None, None
    role_begin, role_end = None, None
    is_president = False

    if authority is not None:
        resolved_name = normalize_text(authority.get("Judge Name")) or resolved_name
        authority_id = authority.get("judge_id")
        if isinstance(authority_id, int):
            resolved_id = authority_id
        country_name = normalize_text(authority.get("Country"))
        tenure_begin, tenure_end = parse_year_range(authority.get("Tenure"))
        role_begin, role_end = parse_year_range(authority.get("Role Length"))
        is_president = normalize_text(authority.get("Role")) == "President"

    if resolved_id is not None:
        cache_key = f"id:{resolved_id}"
    elif resolved_name:
        cache_key = f"name:{slugify(resolved_name)}"
    else:
        return None

    existing = ctx.judge_nodes.get(cache_key)
    if existing is not None:
        return existing

    uri = ctx.data_ns[
        f"judge_{slugify(str(resolved_id) if resolved_id is not None else resolved_name or 'unknown')}"
    ]
    ctx.judge_nodes[cache_key] = uri
    ctx.graph.add((uri, RDF.type, ctx.schema_ns.Judge))

    if resolved_name:
        ctx.graph.add((uri, RDFS.label, Literal(resolved_name)))
    if resolved_id is not None:
        ctx.graph.add((uri, ctx.schema_ns.hasJudgeId, Literal(resolved_id, datatype=XSD.integer)))
    if tenure_begin:
        ctx.graph.add((uri, ctx.schema_ns.hasTenureBeginYear, Literal(tenure_begin, datatype=XSD.gYear)))
    if tenure_end:
        ctx.graph.add((uri, ctx.schema_ns.hasTenureEndYear, Literal(tenure_end, datatype=XSD.gYear)))
    if is_president:
        ctx.graph.add((uri, ctx.schema_ns.isSectionPresident, Literal(True, datatype=XSD.boolean)))
    if role_begin:
        ctx.graph.add((uri, ctx.schema_ns.hasPresidencyBeginYear, Literal(role_begin, datatype=XSD.gYear)))
    if role_end:
        ctx.graph.add((uri, ctx.schema_ns.hasPresidencyEndYear, Literal(role_end, datatype=XSD.gYear)))
    if country_name:
        country_uri = ensure_country(ctx, country_name)
        if country_uri is not None:
            ctx.graph.add((uri, ctx.schema_ns.hasJudgeCountry, country_uri))

    return uri


def create_case_node(ctx: IngestionContext, itemid: str) -> URIRef:
    existing = ctx.case_nodes.get(itemid)
    if existing is not None:
        return existing

    uri = ctx.data_ns[f"case_{slugify(itemid)}"]
    ctx.case_nodes[itemid] = uri
    ctx.graph.add((uri, RDF.type, ctx.schema_ns.CaseDocument))
    return uri


def create_case_audit(ctx: IngestionContext, itemid: str, case_uri: URIRef) -> CaseAudit:
    audit = ctx.audits.get(itemid)
    if audit is None:
        audit = CaseAudit(itemid=itemid, case_uri=case_uri)
        ctx.audits[itemid] = audit
    audit.related_nodes.add(case_uri)
    return audit


def infer_case_subclass(record: dict[str, Any], schema_ns: Namespace) -> URIRef:
    judgment_type = normalize_text(record.get("judgment_type")) or ""
    source = normalize_text(record.get("source")) or ""
    combined = f"{judgment_type} {source}".lower()
    if "decision" in combined:
        return schema_ns.Decision
    return schema_ns.Judgment


def infer_chamber_type_iri(record: dict[str, Any], schema_ns: Namespace) -> URIRef | None:
    text = " ".join(
        part for part in [normalize_text(record.get("originatingbody")), normalize_text(record.get("court_level"))] if part
    ).lower()
    if "grand chamber" in text:
        return schema_ns.GrandChamber
    if "commission" in text:
        return schema_ns.Commission
    if "committee" in text:
        return schema_ns.Committee
    if "section" in text or "chamber" in text:
        return schema_ns.Chamber
    return None


def infer_separate_opinion_iri(value: Any, schema_ns: Namespace) -> URIRef | None:
    text = (normalize_text(value) or "").strip().lower()
    if not text or text in {"0", "false", "no", "none", "absent"}:
        return schema_ns.SeparateOpinion_Absent
    if text in {"1", "true", "yes", "present"}:
        return schema_ns.SeparateOpinion_Present
    return schema_ns.SeparateOpinion_Present


def extract_article_references(text_values: list[str]) -> list[str]:
    extracted: list[str] = []
    for text in text_values:
        normalized = normalize_text(text)
        if not normalized:
            continue
        if re.fullmatch(r"P?\d+(?:-\d+)*(?:-[A-Za-z])?(?:\+P?\d+(?:-\d+)*(?:-[A-Za-z])?)*", normalized):
            extracted.extend(normalized.split("+"))
            continue
        if re.search(r"\bart(?:icle)?\b", normalized, flags=re.IGNORECASE):
            extracted.extend(
                re.findall(r"P?\d+(?:-\d+)*(?:-[A-Za-z])?(?:\+P?\d+(?:-\d+)*(?:-[A-Za-z])?)*", normalized)
            )
    return expand_article_codes(extracted)


def expand_article_codes(raw_codes: list[str]) -> list[str]:
    """Split compound codes like '6+6-3-c' into individual article codes and deduplicate."""
    expanded: list[str] = []
    for code in raw_codes:
        for part in code.split("+"):
            part = part.strip()
            if part:
                expanded.append(normalize_article_code(part))
    return unique_preserving_order(expanded)


def map_case_scalars(ctx: IngestionContext, record: dict[str, Any], case_uri: URIRef, audit: CaseAudit) -> None:
    case_name = normalize_text(record.get("case_name"))
    if case_name:
        ctx.graph.add((case_uri, ctx.schema_ns.hasCaseName, string_literal(case_name)))
        ctx.graph.add((case_uri, RDFS.label, Literal(case_name)))
        add_audit_value(audit, "case_name", case_name)

    ctx.graph.add((case_uri, ctx.schema_ns.hasItemId, string_literal(audit.itemid)))
    add_audit_value(audit, "itemid", audit.itemid)

    ecli = normalize_text(record.get("ecli"))
    if ecli:
        ctx.graph.add((case_uri, ctx.schema_ns.hasEcli, string_literal(ecli)))
        add_audit_value(audit, "ecli", ecli)

    language_code = normalize_text(record.get("languageisocode"))
    if language_code:
        ctx.graph.add((case_uri, ctx.schema_ns.hasLanguageCode, string_literal(language_code)))
        add_audit_value(audit, "languageisocode", language_code)

    judgment_date = parse_optional_date(record.get("judgementdate"))
    if judgment_date:
        ctx.graph.add((case_uri, ctx.schema_ns.hasJudgmentDate, Literal(judgment_date.isoformat(), datatype=XSD.date)))
        add_audit_value(audit, "judgementdate", judgment_date.isoformat())

    year_value = normalize_text(record.get("year"))
    if year_value and re.fullmatch(r"\d{4}", year_value):
        ctx.graph.add((case_uri, ctx.schema_ns.hasYear, Literal(year_value, datatype=XSD.gYear)))
        add_audit_value(audit, "year", year_value)
    elif judgment_date:
        year_text = str(judgment_date.year)
        ctx.graph.add((case_uri, ctx.schema_ns.hasYear, Literal(year_text, datatype=XSD.gYear)))
        add_audit_value(audit, "year", year_text)

    case_text_path = normalize_text(record.get("case_text_path"))
    if case_text_path:
        ctx.graph.add((case_uri, ctx.schema_ns.hasCaseTextPath, string_literal(case_text_path)))
        add_audit_value(audit, "case_text_path", case_text_path)

    applicant_name = normalize_text(record.get("appellant"))
    if applicant_name:
        ctx.graph.add((case_uri, ctx.schema_ns.hasApplicantName, string_literal(applicant_name)))
        add_audit_value(audit, "appellant", applicant_name)

    kp_codes_raw = normalize_text(record.get("kpthesaurus"))
    kp_codes = [c.strip() for c in kp_codes_raw.split(";") if c.strip()] if kp_codes_raw else []
    keyword_labels = normalize_list(record.get("kpthesaurus_labels"))
    for i, label in enumerate(keyword_labels):
        code = kp_codes[i] if i < len(kp_codes) else None
        kw_uri = ensure_keyword(ctx, label, code)
        ctx.graph.add((case_uri, ctx.schema_ns.hasKeyword, kw_uri))
        audit.related_nodes.add(kw_uri)
    if keyword_labels:
        add_audit_value(audit, "kpthesaurus_labels->hasKeyword", keyword_labels)


def map_case_controlled_vocab(ctx: IngestionContext, record: dict[str, Any], case_uri: URIRef, audit: CaseAudit) -> None:
    subclass_iri = infer_case_subclass(record, ctx.schema_ns)
    ctx.graph.add((case_uri, RDF.type, subclass_iri))
    add_audit_value(audit, "judgment_type/source->rdf:type", subclass_iri.split("#")[-1])

    article6_limb = normalize_text(record.get("article_6_limb"))
    if article6_limb:
        iri_name = ARTICLE6_LIMB_MAP.get(article6_limb.lower())
        if iri_name:
            ctx.graph.add((case_uri, ctx.schema_ns.hasArticle6Limb, ctx.schema_ns[iri_name]))
            add_audit_value(audit, "article_6_limb", article6_limb)

    importance = normalize_text(record.get("importance"))
    if importance:
        iri_name = IMPORTANCE_MAP.get(importance)
        if iri_name:
            ctx.graph.add((case_uri, ctx.schema_ns.hasImportanceLevel, ctx.schema_ns[iri_name]))
            add_audit_value(audit, "importance", importance)

    law_system = normalize_text(record.get("law_system"))
    if law_system:
        iri_name = LAW_SYSTEM_MAP.get(law_system.lower())
        if iri_name:
            ctx.graph.add((case_uri, ctx.schema_ns.hasLawSystem, ctx.schema_ns[iri_name]))
            add_audit_value(audit, "law_system", law_system)

    separate_opinion_iri = infer_separate_opinion_iri(record.get("separateopinion"), ctx.schema_ns)
    if separate_opinion_iri is not None:
        ctx.graph.add((case_uri, ctx.schema_ns.hasSeparateOpinion, separate_opinion_iri))
        add_audit_value(audit, "separateopinion", normalize_text(record.get("separateopinion")) or "")

    chamber_type_iri = infer_chamber_type_iri(record, ctx.schema_ns)
    if chamber_type_iri is not None:
        ctx.graph.add((case_uri, ctx.schema_ns.hasChamberType, chamber_type_iri))
        add_audit_value(audit, "court_level/originatingbody->hasChamberType", chamber_type_iri.split("#")[-1])

    originatingbody = normalize_text(record.get("originatingbody"))
    if originatingbody:
        formation_uri = ensure_court_formation(ctx, originatingbody)
        ctx.graph.add((case_uri, ctx.schema_ns.hasCourtFormation, formation_uri))
        audit.related_nodes.add(formation_uri)
        add_audit_value(audit, "originatingbody", originatingbody)

    judgment_type = normalize_text(record.get("judgment_type"))
    if judgment_type:
        judgment_type_uri = ensure_judgment_type(ctx, judgment_type)
        ctx.graph.add((case_uri, ctx.schema_ns.hasJudgmentType, judgment_type_uri))
        audit.related_nodes.add(judgment_type_uri)
        add_audit_value(audit, "judgment_type", judgment_type)


def map_case_applications(ctx: IngestionContext, record: dict[str, Any], case_uri: URIRef, audit: CaseAudit) -> None:
    case_app_numbers = unique_preserving_order(normalize_list(record.get("case_appno")))
    cited_app_numbers = unique_preserving_order(normalize_list(record.get("cited_appno")))
    secondary_app_numbers = unique_preserving_order(normalize_list(record.get("secondary_appno")))

    for app_number in case_app_numbers:
        app_uri = ensure_application(ctx, app_number)
        ctx.graph.add((case_uri, ctx.schema_ns.hasApplication, app_uri))
        ctx.application_to_cases.setdefault(app_number, set()).add(case_uri)
        audit.related_nodes.add(app_uri)
    if case_app_numbers:
        add_audit_value(audit, "case_appno", case_app_numbers)

    for app_number in cited_app_numbers:
        app_uri = ensure_application(ctx, app_number)
        ctx.graph.add((case_uri, ctx.schema_ns.citesApplication, app_uri))
        ctx.cited_applications_by_case.setdefault(case_uri, []).append(app_number)
        audit.related_nodes.add(app_uri)
    if cited_app_numbers:
        add_audit_value(audit, "cited_appno", cited_app_numbers)

    for app_number in secondary_app_numbers:
        app_uri = ensure_application(ctx, app_number)
        ctx.graph.add((case_uri, ctx.schema_ns.referencesSecondaryApplication, app_uri))
        audit.related_nodes.add(app_uri)
    if secondary_app_numbers:
        add_audit_value(audit, "secondary_appno", secondary_app_numbers)


def map_case_articles_and_findings(ctx: IngestionContext, record: dict[str, Any], case_uri: URIRef, audit: CaseAudit) -> None:
    article_codes = expand_article_codes(unique_preserving_order(normalize_list(record.get("article"))))
    for article_code in article_codes:
        article_uri = ensure_convention_article(ctx, article_code)
        ctx.graph.add((case_uri, ctx.schema_ns.concernsArticle, article_uri))
        audit.related_nodes.add(article_uri)
    if article_codes:
        add_audit_value(audit, "article", article_codes)

    conclusion_refs = extract_article_references(normalize_list(record.get("conclusion")))
    for article_code in conclusion_refs:
        # Conclusion references are now modeled as datatype values.
        ctx.graph.add((case_uri, ctx.schema_ns.hasConclusionReference, string_literal(article_code)))
    if conclusion_refs:
        add_audit_value(audit, "conclusion->hasConclusionReference", conclusion_refs)

    for finding_class_name, field_name in (("Violation", "violation"), ("NonViolation", "nonviolation")):
        article_list = expand_article_codes(unique_preserving_order(normalize_list(record.get(field_name))))
        if article_list:
            add_audit_value(audit, field_name, article_list)
        for article_code in article_list:
            article_uri = ensure_convention_article(ctx, article_code)
            finding_uri = ctx.data_ns[f"finding_{ctx.finding_counter:06d}"]
            ctx.finding_counter += 1

            ctx.graph.add((finding_uri, RDF.type, ctx.schema_ns[finding_class_name]))
            ctx.graph.add((finding_uri, RDFS.label, Literal(f"{finding_class_name} {article_code}")))
            ctx.graph.add((finding_uri, ctx.schema_ns.findingRefersToArticle, article_uri))
            ctx.graph.add((case_uri, ctx.schema_ns.hasFinding, finding_uri))
            audit.related_nodes.add(article_uri)
            audit.related_nodes.add(finding_uri)


def map_case_respondent_states(ctx: IngestionContext, record: dict[str, Any], case_uri: URIRef, audit: CaseAudit) -> None:
    raw_candidates = normalize_list(record.get("country_name")) + normalize_list(record.get("respondent"))
    candidates = unique_preserving_order(raw_candidates)
    mapped_countries: list[str] = []

    for candidate in candidates:
        country_uri = ensure_country(ctx, candidate)
        if country_uri is None:
            continue
        ctx.graph.add((case_uri, ctx.schema_ns.hasRespondentState, country_uri))
        audit.related_nodes.add(country_uri)
        label_values = [obj for obj in ctx.graph.objects(country_uri, RDFS.label)]
        if label_values:
            mapped_countries.append(str(label_values[0]))

    respondent_value = normalize_text(record.get("respondent"))
    if respondent_value:
        add_audit_value(audit, "respondent", respondent_value)
    if mapped_countries:
        add_audit_value(audit, "country_name/respondent->hasRespondentState", unique_preserving_order(mapped_countries))


def map_case_judges(ctx: IngestionContext, record: dict[str, Any], case_uri: URIRef, audit: CaseAudit) -> None:
    judge_names = normalize_list(record.get("judges"))
    judge_ids_raw_list = normalize_list(record.get("judges_id"))
    judge_ids: list[int | None] = []
    for value in judge_ids_raw_list:
        try:
            judge_ids.append(int(value))
        except ValueError:
            judge_ids.append(None)

    slot_count = max(len(judge_names), len(judge_ids))
    linked_judges: list[str] = []
    for index in range(slot_count):
        judge_name = judge_names[index] if index < len(judge_names) else None
        judge_id = judge_ids[index] if index < len(judge_ids) else None
        judge_uri = ensure_judge(ctx, judge_name, judge_id)
        if judge_uri is None:
            continue
        ctx.graph.add((case_uri, ctx.schema_ns.hasJudge, judge_uri))
        audit.related_nodes.add(judge_uri)
        labels = [obj for obj in ctx.graph.objects(judge_uri, RDFS.label)]
        if labels:
            linked_judges.append(str(labels[0]))

    if judge_names:
        add_audit_value(audit, "judges", judge_names)
    if judge_ids_raw_list:
        add_audit_value(audit, "judges_id", judge_ids_raw_list)
    if linked_judges:
        add_audit_value(audit, "judges->hasJudge", unique_preserving_order(linked_judges))


def ingest_record(ctx: IngestionContext, record: dict[str, Any]) -> None:
    itemid = normalize_text(record.get("itemid"))
    if not itemid:
        return

    case_uri = create_case_node(ctx, itemid)
    audit = create_case_audit(ctx, itemid, case_uri)

    map_case_scalars(ctx, record, case_uri, audit)
    map_case_controlled_vocab(ctx, record, case_uri, audit)
    map_case_applications(ctx, record, case_uri, audit)
    map_case_articles_and_findings(ctx, record, case_uri, audit)
    map_case_respondent_states(ctx, record, case_uri, audit)
    map_case_judges(ctx, record, case_uri, audit)


def add_case_citation_links(ctx: IngestionContext) -> None:
    for case_uri, app_numbers in ctx.cited_applications_by_case.items():
        for app_number in unique_preserving_order(app_numbers):
            for cited_case_uri in sorted(ctx.application_to_cases.get(app_number, set()), key=str):
                if cited_case_uri != case_uri:
                    ctx.graph.add((case_uri, ctx.schema_ns.citesCase, cited_case_uri))
                    for audit in ctx.audits.values():
                        if audit.case_uri == case_uri:
                            audit.related_nodes.add(cited_case_uri)
                            break


def build_metadata_graph(args: argparse.Namespace) -> tuple[Graph, list[CaseAudit]]:
    if not args.schema_ttl.exists():
        raise FileNotFoundError(f"Schema file not found: {args.schema_ttl}")
    if not args.input_parquet.exists():
        raise FileNotFoundError(f"Input parquet not found: {args.input_parquet}")

    frame = pd.read_parquet(args.input_parquet)
    if args.limit is not None:
        frame = frame.head(args.limit)

    judge_authority_by_id, judge_authority_by_name = load_judge_authority(args.judges_json)
    graph = create_graph()
    ctx = IngestionContext(
        graph=graph,
        schema_ns=Namespace(SCHEMA_BASE_IRI),
        data_ns=Namespace(DATA_BASE_IRI),
        judge_authority_by_id=judge_authority_by_id,
        judge_authority_by_name=judge_authority_by_name,
    )

    for record in frame.to_dict(orient="records"):
        ingest_record(ctx, record)

    add_case_citation_links(ctx)
    audits = sorted(ctx.audits.values(), key=lambda audit: audit.itemid)
    return graph, audits


def write_graph(graph: Graph, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=output_path, format="turtle")


def main() -> None:
    args = parse_args()
    graph, _audits = build_metadata_graph(args)
    write_graph(graph, args.output_ttl)

    case_count = sum(1 for _ in graph.subjects(RDF.type, Namespace(SCHEMA_BASE_IRI).CaseDocument))
    print(f"Wrote metadata Turtle to {args.output_ttl}")
    print(f"Cases ingested: {case_count}")


if __name__ == "__main__":
    main()
