# Extraction quality evaluation — echr_2.ttl, one chunk per document

Assessment of the OntoCast facts-extraction output against `ontology/echr_2.ttl` and the
source case texts, for the run of 2026-08-19.

Prepared 2026-08-19. Companion to `ontology/extraction_quality_report.md` (the four-model,
two-format comparison of 2026-08-18) and `ontology/extraction_fixes_evaluation.md`.

## What was compared

| | this run | reference run |
|---|---|---|
| directory | `results/experiment_echr2_20260819_122533/` | `results/experiment_ttl_20260818_161537/` |
| ontology | **`echr_2.ttl` @ 3.0.0** (535 triples) | `echr.ttl` @ 2.2.0 (929 triples) |
| chunking | **`CHUNK_MIN_SIZE=20000` / `MAX=50000` → 1 unit/document** | `MIN=5000` / `MAX=15000` → 41 units over 10 docs |
| models | gemma-4-31b (local vLLM), gpt-5-mini (API) | gpt5mini, gpt54nano, gemma4, qwen3 |
| cases | 10 (L1–L10), identical texts | identical |
| graph format | turtle | turtle |
| section classifier | off | off |
| Fuseki projects | `art6_{model}_echr2` (fresh) | `art6_{model}_ttl` |

Two variables moved at once — the ontology **and** the chunking — so this run does not
isolate either. It was not meant to: both changes were adopted on the evidence in
`extraction_fixes_evaluation.md`, and the question here is whether the combined pipeline is
now good enough to scale, not which half deserves the credit. Where a defect class can be
attributed to one change rather than the other, the report says so and says why.

Method: mechanical metrics over all 20 graphs via `art6/ontology/quality_metrics.py`, which
reads the class list, property list, closed vocabularies and `owl:FunctionalProperty` set
**live from whichever ontology file is in play** — so the old run is scored against
`echr.ttl` and this one against `echr_2.ttl`, and neither is penalised for the other's
vocabulary. Plus the three validators (`validate_source_paragraphs`,
`validate_source_quotes`, `validate_shapes`) and close reading of each graph against the
source text.

---

## Ranking

| # | model | score | one-line summary |
|---|---|---|---|
| **1** | **gemma4** | **7/10** | Near-perfect hygiene and the cleanest graph in either run; pays for it in recall, and one bad false merge. |
| **2** | **gpt5mini** | **6/10** | Better recall and evidence density, but looser outcome coverage, three functional collisions, and it cannot reliably finish inside the default timeout. |

Both are a large step up from anything in the 2026-08-18 run, where the top three models
tied at 4/10. The gap between them here is narrower than the gap between either of them and
their own previous selves.

---

## Mechanical metrics

Aggregates across all 10 cases per model. Bold marks the better value where one direction is
clearly better.

### Volume and structure

| metric | gemma4 | gpt5mini |
|---|---:|---:|
| triples | 959 | **987** |
| typed nodes | 116 | **122** |
| DomesticProceeding | 60 | **66** |
| DomesticAuthority | 36 | **45** |
| CaseDocument | **10** | 0 |

Raw volume is *not* comparable to the previous run (1,792 and 1,724 triples for the same two
models). `echr_2.ttl` deleted `Article6Issue`, `Party`, `NaturalPerson`, `Participation`,
`PreTrialDetention`, `JudicialOfficer` and the whole gender module. Roughly half the old
triple count was schema surface that no longer exists. **The previous report's single
largest complaint — "gpt5mini emitted zero `Article6Issue` nodes across all 20 of its
files" — is now moot by construction, not solved.** If the Article 6 grievance layer still
matters analytically, it has to come back into the schema before it can be scored.

`CaseDocument` is the one live class gpt5mini ignores entirely: gemma4 mints one per
document (case name, application number, respondent state, and a `hasDomesticProceeding`
link), gpt5mini mints none. That is a real coverage gap, not a stylistic difference.

### Delay module — unused by both

| metric | gemma4 | gpt5mini |
|---|---:|---:|
| Adjournment | 0 | 0 |
| InactivityPeriod | 0 | **1** |
| DelayAttribution | 0 | 0 |

One `InactivityPeriod` across 20 documents. Several of these cases are
length-of-proceedings complaints, so this is the same finding the previous report made
("the delay module is nearly unused") and it survives the schema change untouched. The
module is defined and essentially never instantiated. Either the prompt has to ask for it
explicitly or it should be dropped.

### Link density and evidence anchoring

| metric | gemma4 | gpt5mini |
|---|---:|---:|
| hasCourt | 60 | **65** |
| followsProceeding | 37 | **49** |
| hasOutcome | **57** | 46 |
| hasInstanceLevel | 60 | **66** |
| hasOutcomeDirection | **56** | 32 |
| supporting quotes | 60 | **79** |
| source paragraphs | 60 | **79** |
| proceedings w/o court | **0** | 1 |
| proceedings w/o outcome | **3** | 20 |
| proceedings w/o date | **2** | 7 |
| proceedings w/o quote | **0** | **0** |

The split is consistent: gpt5mini finds *more* proceedings and anchors them with more
evidence; gemma4 describes the proceedings it finds *more completely*. gpt5mini leaves 20 of
66 proceedings (30%) with no outcome and only assigns an outcome direction to half of them;
gemma4 leaves 3 of 60 (5%) and directs 93%. Neither leaves a proceeding unquoted.

`proceedings w/o court` collapsing to 0 and 1 (from 7 and 15) is the clearest single
consequence of one-chunk extraction: a court named in chunk 1 is no longer invisible to the
proceeding extracted in chunk 2.

### Conformance and hygiene

| metric | gemma4 | gpt5mini |
|---|---:|---:|
| invented `echr:` terms | **0** | **0** |
| functional-property violations | **0** | 3 |
| malformed typed literals | **0** | **0** |
| duplicate authority names | **0** | **0** |
| multi-label nodes (false merge) | **1** | 2 |
| authorities w/o `hasAuthorityName` | 1 | **0** |
| followsProceeding self-loops | **0** | **0** |
| followsProceeding 2-cycles | **0** | **0** |
| blank nodes | **0** | **0** |
| static SHACL (`echr-shapes.ttl`) | **10/10 conform** | 8/10 conform |

Closed-vocabulary discipline is now perfect for both models, as it was for three of four in
the previous run. No invented terms, no malformed literals, no blank nodes, no asymmetry
violations.

gpt5mini's 3 functional-property violations are all `hasAuthorityName` on a single node,
and they are the same defect as its 2 multi-label nodes — see *Entity identity* below.

### Network topology

| metric | gemma4 | gpt5mini |
|---|---:|---:|
| components per case | 2.3 | **2.2** |
| singleton nodes | 12 | **10** |
| largest component share | **0.86** | 0.84 |

---

## Against the previous run

Same two models, same ten cases, each scored against its own ontology.

| metric | gemma4 old → new | gpt5mini old → new |
|---|---|---|
| **multi-label nodes (false merge)** | 26 → **1** | 11 → **2** |
| **functional violations** | 26 → **0** | 0 → 3 |
| proceedings w/o court | 7 → **0** | 15 → **1** |
| components per case | 5.9 → **2.3** | 4.0 → **2.2** |
| singleton nodes | 29 → **12** | 15 → **10** |
| largest component share | 0.62 → **0.86** | 0.69 → **0.84** |
| duplicate authority names | 0 → 0 | 0 → 0 |
| invented terms | 0 → 0 | 0 → 0 |

**The identity-collapse failure class is essentially gone.** gemma4's multi-label nodes fall
26 → 1 and its functional violations 26 → 0. The previous report attributed those defects
specifically to chunk-local IRI minting — `proceeding_1` in chunk 2 colliding with
`proceeding_1` in chunk 1 — and predicted that fixing the minting scheme would eliminate
them. Removing chunking eliminated them instead, which is the cheaper of the two fixes and
required no code change at all.

Topology improves for both on every measure. gemma4's largest component now covers 86% of
its typed nodes against 62% before; components per case more than halve for both.

The one regression is gpt5mini's functional violations, 0 → 3. All three are
`hasAuthorityName` collisions on one L3 node. Given the previous run's gpt5mini had 11
multi-label nodes doing the same damage through a channel the functional check could not
see, this is better characterised as the same defect becoming *visible* than as a new one
appearing.

---

## The five criteria

### 1. Hallucinations

**Scope leakage — the dominant failure mode of the previous run — is gone.** That report
opened with "material from the Court's own §§40–65 reasoning, or from the RELEVANT LAW
section, materialised as domestic proceedings… L2 (Beer and Regan) triggers it in every
model". I checked L2 against the source. The real domestic history is:

- §13 proceedings instituted Oct/Nov 1993 before the Darmstadt Labour Court against ESA;
- §15 21 March 1995, those actions declared inadmissible;
- §16 the applicants did not pursue the matter further;
- §17 a separate settlement of 6 Sept 1994 in the same court over Mr Regan's dismissal.

gemma4 extracts exactly two proceedings, both real, both correctly dated and outcomed.
gpt5mini extracts three. **Neither hallucinates the *Waite and Kennedy* Federal Labour Court
decision** — a different case cited only as precedent, which gpt5mini and gemma4 both
invented as a proceeding in the previous run.

Nor does either mint statutes as authorities. L2 §§19–22 are the Provision of Labour
(Temporary Staff) Act, the German Courts Act and the ESA Convention — the exact material
qwen3 previously turned into ten `DomesticAuthority` nodes chained with
`followsProceeding`. Both models ignore all of it.

Across all 20 graphs, **every `DomesticAuthority` is a genuine court, tribunal, prosecutor's
office or administrative body.** Full sweep, no exceptions. That is the single most
important result in this report.

Attribution: this is the ontology change and the one-chunk change working together, plus
the sharpened `facts.txt` instruction ("DO NOT model legal precedent or other legal cases
which did not DIRECTLY affect the applicant"). The prompt edit is the most likely primary
cause and this run cannot separate it from the other two.

**Fabricated evidence anchors persist, and are now the leading hallucination class.**

| model | anchors | malformed | unnumbered | out of range | **hard** | gaps |
|---|---:|---:|---:|---:|---:|---:|
| gemma4 | 60 | 6 | 4 | 0 | **10 (16.7%)** | 8 |
| gpt5mini | 79 | 0 | 7 | 5 | **12 (15.2%)** | 11 |

Two documents print no paragraph numbers at all in the supplied text, and both models
assert anchors on them anyway — L8 (4 fabricated from gemma4, 7 from gpt5mini). On L7
(Sawoniuk), which prints only `**1.**` and `**2.**` (two certified questions, not
paragraphs), gpt5mini asserts §14, §23, §27, §35 — none of which exist — and gemma4 instead
writes the literal string `"THE FACTS"` into `hasSourceParagraph` four times.

The rate is essentially unchanged from the previous run (gemma4 11.8%, gpt5mini 19.0%), so
neither the schema nor the chunking touched it. It is a prompt/validation problem, and the
validator now catches it for free.

**Quote fidelity** is high but no longer near-perfect: gemma4 58/60 verbatim (3.3%
unverified), gpt5mini 71/79 (10.1%). gpt5mini's higher quote volume comes with a
proportionally higher unverified rate.

### 2. Missing triples

- **The delay module is unused.** One `InactivityPeriod` across 20 graphs, zero
  `Adjournment`, zero `DelayAttribution`, in a corpus that includes several
  length-of-proceedings complaints.
- **gpt5mini ignores `CaseDocument` entirely** — 0 nodes against gemma4's 10. Case name,
  application number and respondent state are simply absent from its graphs.
- **gpt5mini's outcome layer is thin**: 20 of 66 proceedings carry no `hasOutcome` and only
  32 carry a direction, against gemma4's 3 and 56.
- **gemma4's recall is lower across the board**: 60 proceedings to gpt5mini's 66, 36
  authorities to 45, and markedly fewer per case on the richer documents. Its L1 (Stanev)
  has 3 authorities where gpt5mini has 8 — and the missing ones (Ruse Municipal Council, the
  Mayor of Rila, the regional and appellate prosecutors) are all real actors in that case's
  procedural history.
- **Recovered since the previous run:** L7's authority layer. The old report noted gpt5mini's
  L7 had *zero* `DomesticAuthority` nodes — "House of Lords, Court of Appeal and trial court
  simply absent". Both models now extract all of them.

### 3. Adherence to echr_2.ttl

Perfect on the closed vocabularies for both models: **0 invented `echr:` terms across all 20
files**, every enumeration member drawn from the ontology's own `owl:oneOf` lists. No
stringified object properties, no competing namespaces, no `ns1:` corruption of the kind
gpt54nano produced in the previous run.

gemma4 conforms to the static SHACL gatekeeper on **10/10** files. gpt5mini fails 2: L3 and
L6, both `an authority may have at most one name`.

One defect the shapes do **not** catch, and neither does the functional-property check:
gemma4's L3 has a single authority node carrying five `rdfs:label` values —

```
doc:authAdministrativeCourt a echr:DomesticAuthority ;
    rdfs:label "13th Chamber of the Supreme Administrative Court",
        "6th Chamber of the İstanbul Administrative Court",
        "Constitutional Court",
        "General Assembly of the Administrative Proceedings Divisions ...",
        "administrative court" ;
```

— and no `hasAuthorityName` at all. `rdfs:label` is not `owl:FunctionalProperty`, and the
`at most one name` shape keys on `hasAuthorityName`, which is absent, so the node passes
every check while asserting that the Constitutional Court and the İstanbul Administrative
Court are one entity. All seven of L3's proceedings point at it. **This is the worst single
defect in gemma4's output and it is currently invisible to every automated check.**

gpt5mini gets L3 right, separating the three courts into three nodes.

### 4. Quality of triples extracted

Evidence anchoring is good and near-universal: every proceeding in both models carries a
supporting quote, and quotes are 90–97% verbatim.

**Entity identity is much improved but not solved.** The cross-chunk collisions that
dominated the previous report are gone — 0 duplicate authority names for both models,
against 16 for qwen3 and 5 for gemma4 previously. What remains is *within-document* false
merging, where a model folds a chamber into its parent court:

- gemma4 L3, five courts in one node (above);
- gpt5mini L3, `supremeAdminCourt` carrying "13th Chamber of the Supreme Administrative
  Court", "General Assembly of the Administrative Proceedings Divisions…" and "Supreme
  Administrative Court";
- gpt5mini L6, `authorityCommercialCourt` carrying both "Commercial Court" and "three-judge
  bench of the Commercial Court".

The last is arguably correct — a bench of a court is that court — which is exactly why this
class is hard to adjudicate mechanically and why the L3 cases (a constitutional court is
*not* an administrative court) matter more.

**The repair pass had almost nothing to do**, which is itself the result. gemma4: 8 of 10
files "nothing flagged", 0 operations and 0 merges applied. gpt5mini: 8 of 10 clean, 1 merge
on L5. Compare the previous run, where the same pass applied 68 removals across the two
models to undo chunk-collision damage. **With one chunk per document the defect it existed
to fix largely does not arise.**

The one merge it did perform is a genuine duplicate, correctly handled: two nodes for the
Kyiv District Administrative Court decision of 8 April 2021, folded into the better-evidenced
one — 1 inbound edge re-pointed, 2 properties moved, 7 survivor values kept, duplicate
deleted outright.

### 5. Ease of formatting into a network

Both outputs are directly buildable, and both are better on this criterion than the best
graph in the previous run (`ttl/gpt5mini`, 4.0 components per case, 0.69 largest-component
share).

1. **gemma4** — 2.3 components per case, largest component 86% of typed nodes, 12
   singletons, 0 orphan proceedings, 0 asymmetry violations, 10/10 SHACL-conformant.
   Needs the L3 authority node split by hand; nothing else.
2. **gpt5mini** — 2.2 components per case, 0.84 largest-component share, 10 singletons.
   Marginally better connected, but 30% of its proceedings have no outcome and 2 files fail
   the shapes, so more downstream cleanup.

Zero blank nodes, stable `doc:`-prefixed IRIs and object properties used as real links
throughout, for both.

---

## Operational finding: the default timeout is now the binding constraint

**gpt5mini failed to produce L4 and L10 on the first attempt**, both with
`LLM request exceeded 180.0s`. This is a direct consequence of removing chunking and it is
the one cost of that change.

Per-document render times, this run:

| model | min | median | max | timeouts |
|---|---:|---:|---:|---:|
| gemma4 (local vLLM) | 9.1s | ~18s | 45.0s | 0/10 |
| gpt5mini (API) | 92.0s | ~148s | >180s | 2/10 |

gemma4 has roughly 4× headroom against the 180s default. **gpt5mini has about 30 seconds of
median headroom, on every document.** Re-running the two failures with
`LLM_REQUEST_TIMEOUT_SECONDS=900` succeeded at 101.8s and 122.2s — *both under the original
180s limit*. So this is not a hard size ceiling that long documents cross; it is
**response-time variance**, and at a 148s median any document can lose the coin flip. A
20% failure rate on a 10-document run is consistent with that.

Raise `LLM_REQUEST_TIMEOUT_SECONDS` to 600–900 for any API model before a corpus-scale run.
At 48,000 documents a 20% silent-failure rate is not a nuisance, it is a corrupted corpus —
and the failure is quiet: `run_experiment.sh` reported `raw=8` and exited 0.

Wall-clock, 10 documents, extraction + repair + validation:

| model | total | note |
|---|---|---|
| gemma4 | **3m 49s** | local vLLM, no API cost |
| gpt5mini | 24m 47s | plus 4m for the two retries |

Roughly **6.5× faster** for the local model, before considering API cost.

---

## Two bugs found and fixed during this evaluation

Both were in `repair_facts.py`, both introduced by the merge work added earlier the same
day, and both were caught by measuring this run rather than by the tests written alongside
the code.

**1. The repair pass could inject invented vocabulary.** On L5, gpt5mini's repair patch
"corrected" `echr:OutcomeQuashedAndRemitted` to `echr:OutcomeQuashed` — a term that does not
exist in `echr_2.ttl` — and `apply_patch` applied it. The two-namespace guard only ever
checked that *subjects* were `doc:`-namespaced; it never checked that an `echr:` predicate or
object was a term the ontology defines. Net effect: **extraction produced 0 invented terms
and the repair pass introduced 1.** Fixed by adding `ontology_terms()` /
`unknown_echr_terms()` and validating both predicate and object on every `add`.

*Residual issue, not yet fixed:* the paired `remove` still applied, so blocking the `add`
leaves the proceeding with no outcome at all. A `RepairGroup` should be atomic — if any
operation in it is rejected, the whole group should be skipped. Worth doing before the next
run.

**2. `merge_nodes` created a `followsProceeding` self-loop.** L5's raw graph contained
`drop → keep`. The merge's outbound loop copied it onto the survivor as `keep → keep`,
violating `owl:IrreflexiveProperty`. The self-loop guard covered only the *inbound*
direction (`s == keep`), and the edge-case test written with it exercised only that same
direction. Fixed by guarding `o == keep` in the outbound loop; the test now covers both.

Both fixes are in place and verified. The delivered `repaired/` graphs were regenerated
afterwards and carry neither defect.

---

## What to fix before a corpus-scale run

1. **Raise `LLM_REQUEST_TIMEOUT_SECONDS` to 600–900 for API models.** Highest priority: at
   the default, gpt5mini silently loses ~20% of documents and the run still exits 0. Also
   make `run_experiment.sh` fail loudly when `raw` < input record count.
2. **Constrain `rdfs:label` in `echr-shapes.ttl`** — `sh:maxCount 1` on every typed node,
   and require `hasAuthorityName` on `DomesticAuthority`. gemma4's five-courts-in-one-node
   L3 defect passes every current check; this closes it for free.
3. **Make `RepairGroup` atomic** so a rejected `add` cannot leave the paired `remove`
   applied (see bug 1 above).
4. **Fix the paragraph-anchor layer.** 15–17% of anchors are unanchorable, unchanged across
   both runs. Two cheap deterministic steps: pass the document's actual paragraph-number set
   into the extraction prompt, and reject non-integer values at render time (`"THE FACTS"`,
   `"13, 15"`). Documents that print no paragraph numbers should suppress the property
   entirely rather than invite invention.
5. **Decide the delay module's fate.** One instantiation across 20 graphs. Either ask for it
   explicitly in `facts.txt` or drop `Adjournment` / `InactivityPeriod` / `DelayAttribution`
   from the schema.
6. **Decide whether the Article 6 grievance layer comes back.** `echr_2.ttl` removed it, so
   the previous report's largest gap is no longer measurable. If the analysis needs it, it
   has to be re-added and re-scored; if not, the removal is a genuine simplification.
7. **Prompt gpt5mini for `CaseDocument`**, which it never instantiates.

## Caveats

- n = 10 cases, English-only, weighted toward long judgments — `build_ontocast_test_set.py`
  selects `max(len(facts))` per slot, so this sample is *not* representative of the corpus,
  whose median document is ~6,000 characters against this set's ~24,000. The timeout finding
  in particular will be milder at corpus scale; the recall findings may not transfer.
- **Two variables changed together** (ontology and chunking), plus a third: `facts.txt` was
  edited between the runs to forbid modelling legal precedent. The disappearance of scope
  leakage is most plausibly the prompt edit, but this run cannot prove it. A clean
  attribution needs one variable at a time.
- gpt5mini's L4 and L10 were produced on a retry with `LLM_REQUEST_TIMEOUT_SECONDS=900`
  while the other eight ran at the 180s default. The timeout governs only whether a request
  completes, not what the model extracts, so the graphs are comparable — but the run is not
  byte-for-byte homogeneous. The original 8-file output is preserved at
  `gpt5mini/repaired_partial_8files/`.
- gpt5mini's L5 was repaired twice: once with the buggy guards, once after the fixes. The
  delivered graph is the second. gemma4 was not re-repaired, as its repair applied no
  operations on any file.
- Hallucination and missing-triple findings come from close reading of L1, L2, L3, L5, L6
  and L7 against source text, plus mechanical sweeps (authority-label enumeration, anchor
  validation, quote verification) across all ten. They are indicative, not exhaustive.
- All mechanical figures come from `art6/ontology/quality_metrics.py`, which reads each
  run's own ontology. Where these differ from figures in `extraction_fixes_evaluation.md`
  (which used a hardcoded functional-property list in `repair_impact.py`), the numbers here
  supersede them.
