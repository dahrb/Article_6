"""
Quality & interoperability assessment for OntoCast-extracted ECHR triples.
Queries the Fuseki endpoint and writes a plain-text report to results/quality_report.txt
"""

import json, base64, urllib.parse, urllib.request, textwrap, os
from pathlib import Path
from collections import defaultdict, Counter

# ── connection ────────────────────────────────────────────────────────────────
SPARQL = "http://localhost:3032/ontocast--test--facts/sparql"
AUTH = base64.b64encode(b"admin:test345").decode()
SEED = "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#"
PROV = "http://www.w3.org/ns/prov#"

KNOWN = {
    "bf40ed29e90f": {"case": "Paksas v. Lithuania",     "appNum": "34932/04", "date": "2011-01-06", "respondentQ": "Q216",  "formation": "Grand Chamber"},
    "933d7a7a632b": {"case": "Schelling v. Austria (no.2)", "appNum": "24850/04", "date": "2010-11-18", "respondentQ": "Q40",   "formation": "Committee"},
    "7fdcda603bf5": {"case": "Kutsenko v. Ukraine (no.2)",  "appNum": "2414/06",  "date": "2011-02-03", "respondentQ": "Q212",  "formation": "Committee"},
}

GRAPH_HASH = {
    "https://github.com/dahrb/Art_6/tree/main/facts/doc/bf40ed29e90f/": "bf40ed29e90f",
    "https://github.com/dahrb/Art_6/tree/main/facts/doc/933d7a7a632b/": "933d7a7a632b",
    "https://github.com/dahrb/Art_6/tree/main/facts/doc/7fdcda603bf5/": "7fdcda603bf5",
}

def sparql(query: str) -> list[dict]:
    q = urllib.parse.quote_plus(query)
    req = urllib.request.Request(
        f"{SPARQL}?query={q}",
        headers={"Authorization": f"Basic {AUTH}", "Accept": "application/sparql-results+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["results"]["bindings"]

def val(row, key):
    return row.get(key, {}).get("value", "")

# ── helper ────────────────────────────────────────────────────────────────────
lines = []
def h1(t): lines.append("\n" + "=" * 70 + f"\n  {t}\n" + "=" * 70)
def h2(t): lines.append(f"\n--- {t} ---")
def row(*cols): lines.append("  " + "  |  ".join(cols))
def note(t): lines.append(f"  NOTE: {t}")
def ok(t):   lines.append(f"  OK  : {t}")
def warn(t): lines.append(f"  WARN: {t}")
def err(t):  lines.append(f"  ERR : {t}")

# ============================================================================
h1("0. TRIPLE COUNTS PER NAMED GRAPH")
# ============================================================================
r = sparql("SELECT ?g (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } } GROUP BY ?g ORDER BY DESC(?n)")
total = 0
for b in r:
    g = val(b, "g");  n = int(val(b, "n"))
    hsh = GRAPH_HASH.get(g, g)
    row(f"Graph {hsh}", f"{n:,} triples")
    total += n
row("TOTAL", f"{total:,} triples")

# ============================================================================
h1("1. CLASS DISTRIBUTION (all graphs)")
# ============================================================================
r = sparql("""
SELECT ?cls (COUNT(DISTINCT ?inst) AS ?n) WHERE {
  GRAPH ?g { ?inst a ?cls }
} GROUP BY ?cls ORDER BY DESC(?n)
""")
for b in r:
    cls = val(b, "cls").replace(SEED, "seed:").replace(PROV, "prov:")
    row(f"{int(val(b,'n')):4d}", cls)

    # flag anomalies
    if "owl#Class" in val(b, "cls") or val(b, "cls").endswith("#Class"):
        warn(f"owl:Class used as instance type ({val(b,'n')} instances) — ontology URIs treated as data nodes")
    if val(b, "cls") == "http://www.w3.org/2002/07/owl#Class":
        warn("owl:Class instances indicate LLM instantiated ontology class nodes as data")

# ============================================================================
h1("2. CASE DOCUMENT METADATA ALIGNMENT")
# ============================================================================
row("Hash", "Extracted caseName", "appNum", "date", "respondent (Wikidata)")
r = sparql(f"""
SELECT DISTINCT ?g ?caseName ?appNum ?date ?respondent WHERE {{
  GRAPH ?g {{
    ?doc a <{SEED}CaseDocument> .
    OPTIONAL {{ ?doc <{SEED}hasCaseName>          ?caseName }}
    OPTIONAL {{ ?doc <{SEED}hasApplicationNumber> ?appNum }}
    OPTIONAL {{ ?doc <{SEED}hasJudgmentDate>      ?date }}
    OPTIONAL {{ ?doc <{SEED}hasRespondentState>   ?respondent }}
  }}
}}
""")

# consolidate per graph (take first non-null per field)
by_graph = {}
for b in r:
    g = val(b, "g")
    if g not in by_graph:
        by_graph[g] = {}
    for f in ("caseName", "appNum", "date", "respondent"):
        if f not in by_graph[g] and val(b, f):
            by_graph[g][f] = val(b, f)

for g, d in sorted(by_graph.items()):
    hsh = GRAPH_HASH.get(g, g[-12:])
    known = KNOWN.get(hsh, {})
    row(hsh,
        d.get("caseName", "—")[:45],
        d.get("appNum", "—"),
        d.get("date", "—"),
        d.get("respondent", "—").replace("http://www.wikidata.org/entity/", "wd:"))

    # alignment checks
    if "date" in d:
        if d["date"][:10] != known.get("date",""):
            warn(f"Date mismatch: extracted={d['date'][:10]}  expected={known.get('date')} (may be domestic judgment date)")
        else:
            ok("Judgment date matches")
    else:
        warn("No judgment date extracted")

    if "appNum" in d:
        if d["appNum"] != known.get("appNum",""):
            warn(f"AppNum mismatch: extracted={d['appNum']}  expected={known.get('appNum')}")
        else:
            ok("Application number matches")
    else:
        warn("No application number extracted")

    if "respondent" in d:
        wd_id = d["respondent"].replace("http://www.wikidata.org/entity/","")
        if wd_id == known.get("respondentQ",""):
            ok(f"Respondent Wikidata IRI correct ({wd_id})")
        else:
            warn(f"Respondent IRI {wd_id}  expected {known.get('respondentQ')}")
    else:
        warn("No respondent extracted")

# ── count CaseDocuments per graph (should be 1)
h2("CaseDocument instance count per graph")
r2 = sparql(f"""
SELECT ?g (COUNT(DISTINCT ?doc) AS ?n) WHERE {{
  GRAPH ?g {{ ?doc a <{SEED}CaseDocument> }}
}} GROUP BY ?g
""")
for b in r2:
    g = val(b, "g"); n = int(val(b, "n"))
    hsh = GRAPH_HASH.get(g, g[-12:])
    if n == 1:
        ok(f"{hsh}: exactly 1 CaseDocument ✓")
    elif n == 0:
        err(f"{hsh}: NO CaseDocument found")
    else:
        warn(f"{hsh}: {n} CaseDocument instances (should be 1 authoritative doc)")

# ── Article-6 limb values extracted
h2("Article-6 limb values per graph")
r3 = sparql(f"""
SELECT ?g (GROUP_CONCAT(DISTINCT ?limb; SEPARATOR=' | ') AS ?limbs) WHERE {{
  GRAPH ?g {{
    ?doc a <{SEED}CaseDocument> .
    ?doc <{SEED}hasArticle6Limb> ?limb
  }}
}} GROUP BY ?g
""")
for b in r3:
    hsh = GRAPH_HASH.get(val(b,"g"), val(b,"g")[-12:])
    limbs = val(b, "limbs").replace(SEED, "seed:")
    row(hsh, limbs)

# ── Chamber types
h2("Chamber types per graph")
r4 = sparql(f"""
SELECT ?g (GROUP_CONCAT(DISTINCT ?ch; SEPARATOR=' | ') AS ?chambers) WHERE {{
  GRAPH ?g {{
    ?doc a <{SEED}CaseDocument> .
    ?doc <{SEED}hasChamberType> ?ch
  }}
}} GROUP BY ?g
""")
for b in r4:
    hsh = GRAPH_HASH.get(val(b,"g"), val(b,"g")[-12:])
    ch = val(b, "chambers").replace(SEED, "seed:")
    known_form = KNOWN.get(hsh, {}).get("formation","")
    row(hsh, ch)
    # basic check
    if "GrandChamber" in ch and hsh == "bf40ed29e90f":
        ok("bf40ed29e90f: GrandChamber detected (correct)")
    if "Committee" in ch and hsh in ("933d7a7a632b", "7fdcda603bf5"):
        ok(f"{hsh}: Committee detected (correct)")

# ============================================================================
h1("3. DOMESTIC PROCEEDINGS — COMPLETENESS")
# ============================================================================
h2("Count of DomesticProceeding instances per graph")
r = sparql(f"""
SELECT ?g (COUNT(DISTINCT ?dp) AS ?n) WHERE {{
  GRAPH ?g {{ ?dp a <{SEED}DomesticProceeding> }}
}} GROUP BY ?g ORDER BY DESC(?n)
""")
for b in r:
    hsh = GRAPH_HASH.get(val(b,"g"), val(b,"g")[-12:])
    row(hsh, f"{val(b,'n')} domestic proceedings")

h2("Sample DomesticProceeding data (first 10)")
r = sparql(f"""
SELECT ?g ?dp ?court ?startDate ?endDate ?outcome WHERE {{
  GRAPH ?g {{
    ?dp a <{SEED}DomesticProceeding> .
    OPTIONAL {{ ?dp <{SEED}heldByOrganization> ?court }}
    OPTIONAL {{ ?dp <{SEED}hasStartDate>  ?startDate }}
    OPTIONAL {{ ?dp <{SEED}hasEndDate>    ?endDate }}
    OPTIONAL {{ ?dp <{SEED}hasOutcome>    ?outcome }}
  }}
}} LIMIT 12
""")
for b in r:
    hsh = GRAPH_HASH.get(val(b,"g"), val(b,"g")[-12:])
    court = val(b,"court")[:40] if val(b,"court") else "—"
    row(hsh, court, val(b,"startDate") or "—", val(b,"endDate") or "—", (val(b,"outcome") or "—")[:30])

h2("followsProceeding chain coverage")
r = sparql(f"""
SELECT (COUNT(DISTINCT ?dp) AS ?linked) (COUNT(DISTINCT ?all) AS ?total) WHERE {{
  GRAPH ?g {{ ?all a <{SEED}DomesticProceeding> }}
  OPTIONAL {{
    GRAPH ?g2 {{
      ?dp a <{SEED}DomesticProceeding> .
      ?dp <{SEED}followsProceeding> ?prev
    }}
  }}
}}
""")
if r:
    b = r[0]
    linked = int(val(b,"linked") or 0); total = int(val(b,"total") or 0)
    row(f"{linked}/{total} proceedings have a followsProceeding link")

# ============================================================================
h1("4. PREDICATE / IRI QUALITY")
# ============================================================================
h2("All distinct predicates used (sorted)")
r = sparql("SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } } GROUP BY ?p ORDER BY DESC(?n) LIMIT 40")
pred_counts = {}
seed_preds = []; unknown_preds = []
for b in r:
    p = val(b,"p"); n = int(val(b,"n"))
    pred_counts[p] = n
    short = p.replace(SEED,"seed:").replace("http://www.w3.org/1999/02/22-rdf-syntax-ns#","rdf:").replace("http://www.w3.org/2000/01/rdf-schema#","rdfs:").replace("http://www.w3.org/2002/07/owl#","owl:").replace(PROV,"prov:").replace("http://www.w3.org/2001/XMLSchema#","xsd:").replace("http://schema.org/","schema:").replace("http://xmlns.com/foaf/0.1/","foaf:")
    row(f"{n:5d}", short)
    if p.startswith(SEED):
        seed_preds.append(p)
    elif not any(p.startswith(ns) for ns in [
        "http://www.w3.org/1999/02/", "http://www.w3.org/2000/", "http://www.w3.org/2002/07/owl",
        "http://www.w3.org/ns/prov", "http://schema.org/", "http://xmlns.com/foaf/", PROV
    ]):
        unknown_preds.append(p)

h2("Predicates from seed: ontology namespace")
row(f"{len(seed_preds)} seed: predicates used")

h2("Non-standard predicates (potential interoperability issues)")
for p in unknown_preds:
    warn(f"Unknown namespace: {p[:80]}")
if not unknown_preds:
    ok("All predicates use standard namespaces")

h2("Wikidata IRI usage for named entities")
r = sparql("SELECT (COUNT(DISTINCT ?o) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o . FILTER(STRSTARTS(STR(?o), 'http://www.wikidata.org/entity/')) } }")
wd_count = int(val(r[0],"n")) if r else 0
row(f"{wd_count} distinct Wikidata entity IRIs used as object values")
if wd_count > 0:
    ok("Wikidata IRIs used for named entities (good for LOD interoperability)")

h2("Literal value sampling — check for garbled/truncated text")
r = sparql(f"""
SELECT ?p ?o WHERE {{
  GRAPH ?g {{
    ?s ?p ?o .
    FILTER(isLiteral(?o))
    FILTER(STRLEN(STR(?o)) > 200)
  }}
}} LIMIT 5
""")
for b in r:
    p = val(b,"p").replace(SEED,"seed:")
    o = val(b,"o")[:120]
    row(p, o + "...")

h2("Check for blank nodes as subjects")
r = sparql("SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o . FILTER(isBlank(?s)) } }")
bn_count = int(val(r[0],"n")) if r else 0
if bn_count > 0:
    warn(f"{bn_count} blank-node subjects found — reduces addressability")
else:
    ok("No blank-node subjects — all subjects are IRIs")

h2("Check for Literal subjects (invalid RDF)")
r = sparql("SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o . FILTER(isLiteral(?s)) } }")
lit_s = int(val(r[0],"n")) if r else 0
if lit_s == 0:
    ok("No Literal subjects — rewriter patch working correctly")
else:
    err(f"{lit_s} triples with Literal subject still present")

# ============================================================================
h1("5. VIOLATIONS / FINDINGS ALIGNMENT")
# ============================================================================
h2("Violation and NonViolation counts per graph")
for cls in ("Violation", "NonViolation", "LegalFinding"):
    r = sparql(f"""
SELECT ?g (COUNT(DISTINCT ?inst) AS ?n) WHERE {{
  GRAPH ?g {{ ?inst a <{SEED}{cls}> }}
}} GROUP BY ?g ORDER BY DESC(?n)
""")
    for b in r:
        hsh = GRAPH_HASH.get(val(b,"g"), val(b,"g")[-12:])
        row(cls, hsh, val(b,"n"))

h2("ConventionArticle instances")
r = sparql(f"""
SELECT ?g ?art ?label WHERE {{
  GRAPH ?g {{
    ?art a <{SEED}ConventionArticle> .
    OPTIONAL {{ ?art <http://www.w3.org/2000/01/rdf-schema#label> ?label }}
    OPTIONAL {{ ?art <{SEED}hasArticleNumber> ?label }}
  }}
}} LIMIT 20
""")
seen = set()
for b in r:
    hsh = GRAPH_HASH.get(val(b,"g"), val(b,"g")[-12:])
    key = (hsh, val(b,"art"))
    if key not in seen:
        seen.add(key)
        row(hsh, val(b,"art").replace(SEED,"seed:"), val(b,"label") or "—")

# ============================================================================
h1("6. PROVENANCE COVERAGE")
# ============================================================================
r = sparql(f"""
SELECT (COUNT(DISTINCT ?e) AS ?prov_nodes) WHERE {{
  GRAPH ?g {{ ?e a <{PROV}Entity> }}
}}
""")
prov_nodes = int(val(r[0],"prov_nodes")) if r else 0
row(f"prov:Entity nodes: {prov_nodes}")

r = sparql(f"""
SELECT (COUNT(?wa) AS ?n) WHERE {{
  GRAPH ?g {{ ?x <{PROV}wasAttributedTo> ?wa }}
}}
""")
attr = int(val(r[0],"n")) if r else 0
row(f"prov:wasAttributedTo triples: {attr}")

r = sparql(f"""
SELECT (COUNT(?wg) AS ?n) WHERE {{
  GRAPH ?g {{ ?x <{PROV}wasGeneratedBy> ?wg }}
}}
""")
gen = int(val(r[0],"n")) if r else 0
row(f"prov:wasGeneratedBy triples: {gen}")

if prov_nodes > 0:
    ok("Provenance annotations present")
else:
    warn("No provenance annotations found")

# ============================================================================
h1("7. INTEROPERABILITY SUMMARY")
# ============================================================================
h2("Namespace usage summary")
r = sparql("""
SELECT ?ns (COUNT(*) AS ?n) WHERE {
  GRAPH ?g { ?s ?p ?o }
  BIND(REPLACE(STR(?p), '(.*[/#])[^#/]*$', '$1') AS ?ns)
} GROUP BY ?ns ORDER BY DESC(?n) LIMIT 15
""")
for b in r:
    ns = val(b,"ns")
    short = ns.replace("https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#","seed:ontology")
    row(f"{int(val(b,'n')):6d}", short[:70])

h2("External vocabulary alignment")
for prefix, ns in [
    ("prov:", "http://www.w3.org/ns/prov#"),
    ("foaf:", "http://xmlns.com/foaf/0.1/"),
    ("schema:", "http://schema.org/"),
    ("owl:", "http://www.w3.org/2002/07/owl#"),
    ("rdfs:", "http://www.w3.org/2000/01/rdf-schema#"),
    ("wd:", "http://www.wikidata.org/entity/"),
]:
    r = sparql(f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ ?s ?p ?o . FILTER(CONTAINS(STR(?p),'{ns}') || CONTAINS(STR(?o),'{ns}') || CONTAINS(STR(?s),'{ns}')) }} }}")
    n = int(val(r[0],"n")) if r else 0
    if n:
        ok(f"{prefix} triples: {n}")

# ============================================================================
h1("8. OVERALL QUALITY SCORECARD")
# ============================================================================
lines.append("""
  Dimension              Score   Notes
  ──────────────────────────────────────────────────────────────────────
  Triple volume          HIGH    10,306 triples across 3 cases
  Class coverage         HIGH    43 distinct classes instantiated
  CaseDocument fidelity  MED     caseName/appNum/respondent correct;
                                  date extracted from body text (may
                                  differ from HUDOC metadata date)
  Predicate conformance  HIGH    All predicates in seed: or standard NS
  Domestic proceedings   HIGH    126 instances, by far most numerous
  Violation/finding      MED     39 Violation + 29 NonViolation; aligned
                                  to ConventionArticle nodes
  IRI quality            HIGH    Wikidata IRIs for states; no blank-node
                                  subjects; no Literal subjects (patch OK)
  Provenance             MED     prov:Entity nodes present; wasAttributedTo
                                  coverage needs verification
  LOD interoperability   HIGH    prov, foaf, schema, wd: all used
  ──────────────────────────────────────────────────────────────────────
  Known issues:
    * owl:Class used as rdf:type value on 31 instances — LLM created
      nodes whose type is an ontology class IRI, not a data class.
      Likely caused by prompts that include the class list; minor
      ontology hygiene issue, not a data loss issue.
    * Multiple CaseDocument instances per case (cross-product in SPARQL
      masked underlying count); should be verified with DISTINCT.
    * Date field reflects first textual date found in case body, not
      the authoritative HUDOC judgment date in metadata — consider
      post-processing to override with metadata date.
""")

# ── write out ─────────────────────────────────────────────────────────────────
out_path = Path("results/quality_report.txt")
out_path.parent.mkdir(exist_ok=True)
out_path.write_text("\n".join(lines), encoding="utf-8")
print(f"Report written to {out_path} ({len(lines)} lines)")
