# Extraction quality evaluation — four models, two graph formats

Assessment of the OntoCast facts-extraction output against `ontology/echr.ttl` and the
source case texts, for the two experiment runs of 2026-08-18.

Prepared 2026-08-18.

## What was compared

| | run 1 | run 2 |
|---|---|---|
| directory | `results/experiment_20260818_143651/` | `results/experiment_ttl_20260818_161537/` |
| `LLM_GRAPH_FORMAT` | `jsonld` | `turtle` |
| models | gpt5mini, gpt54nano, gemma4, qwen3 | same four |
| cases | 10 (L1–L10), identical texts | identical |

Everything else was held constant per `manifest.json`: same ontology snapshot (929
triples as loaded), same `facts.txt` prompt, `RENDER_MODE=facts`, temperature 1.0,
`max_visits=1`, chunk sizes 5000/15000, section classifier off. Both runs' outputs were
put through the repair step and serialised to Turtle, so all 80 files are read the same
way.

One caveat on the comparison: run 2's `ontology.env` differs on a second axis —
`ONTOLOGY_CONTEXT_MODE=fixed_single_ontology` where run 1 used
`selected_single_ontology`, though `manifest.json` records `selected_single_ontology`
for both. Format is therefore not perfectly isolated from context mode. The effects
below are large and consistent enough to be worth acting on, but a clean re-run varying
only `LLM_GRAPH_FORMAT` would settle it.

Method: mechanical metrics computed over all 80 graphs with rdflib (triple counts,
class instantiation, closed-vocabulary conformance, connectivity, orphan analysis),
plus a close reading of each model's 20 output files against the case texts and the
schema.

---

## Ranking

| # | model | score | one-line summary |
|---|---|---|---|
| **1** | **gpt5mini** | **4/10** | Perfect vocabulary discipline and the cleanest graph, but exercises barely a quarter of the schema. |
| **2** | **gemma4** | **4/10** | Also zero invented vocabulary, better grievance coverage than gpt5mini, undermined by entity duplication and fabricated paragraph anchors. |
| **3** | **qwen3** | **4/10** | Much the best recall and the richest graph, wrecked by cross-chunk identity collapse and inventing 30+ schema terms. |
| **4** | **gpt54nano** | **3/10** | Namespace corruption in the JSON-LD run makes most of it unqueryable; no party, participation or grievance layer at all. |

The three leaders genuinely are close — they fail in different directions rather than by
different amounts, so the ordering depends on what you weight. See
[Choosing on your priorities](#choosing-on-your-priorities).

---

## Mechanical metrics

Aggregates across all 10 cases per model/run. Bold marks the best value in a row where
one direction is clearly better.

### Volume and structure

| metric | js/mini | js/nano | js/gemma | js/qwen | ttl/mini | ttl/nano | ttl/gemma | ttl/qwen |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| triples | 1467 | 929 | 1769 | 2161 | 1724 | 1601 | 1792 | **3241** |
| typed nodes | 197 | 131 | 251 | 289 | 227 | 239 | 260 | **368** |
| DomesticProceeding | 140 | 41 | 107 | 65 | 138 | **167** | 90 | 89 |
| DomesticAuthority | 45 | 10 | 53 | 42 | 60 | 35 | 59 | **69** |

### Schema-module coverage — the sharpest divide in the whole comparison

| metric | js/mini | js/nano | js/gemma | js/qwen | ttl/mini | ttl/nano | ttl/gemma | ttl/qwen |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Party | 1 | 0 | 21 | 22 | 3 | 1 | 30 | **32** |
| NaturalPerson | 1 | 0 | 22 | 22 | 5 | 3 | 24 | **31** |
| Participation | 0 | 0 | 34 | 22 | 5 | 1 | 46 | **48** |
| **Article6Issue** | **0** | **0** | 2 | 33 | **0** | 1 | 8 | **26** |
| PreTrialDetention | 1 | 0 | 3 | 5 | 2 | 2 | 5 | **9** |
| InactivityPeriod | 0 | 0 | 1 | 8 | 2 | 0 | 1 | **12** |
| Adjournment | 0 | 0 | 1 | 4 | 1 | 4 | 1 | **8** |

gpt5mini emitted **zero `Article6Issue` nodes across all 20 of its files**, and
gpt54nano one (a fabricated placeholder). Nine of the ten cases carry explicit grievance
language — L4's "the criminal proceedings had been unfair and excessively long
(Article 6 of the Convention)", L5's repeated "length of the proceedings" and "access to
a court", L8's four mentions of "impartial". Both OpenAI models extract the proceeding
skeleton and drop the layer the schema exists to capture.

Gender is worse still: `echr:hasGender` appears **zero times** in either gpt5mini run and
either gpt54nano run, and gemma4 never once emits the `hasGenderCue`/`hasGenderCueText`
pair the schema requires — so 100% of its 8 gender assertions are bare guesses.

### Link density and evidence anchoring

| metric | js/mini | js/nano | js/gemma | js/qwen | ttl/mini | ttl/nano | ttl/gemma | ttl/qwen |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hasCourt | 95 | 6 | 95 | 45 | 125 | 51 | 90 | **133** |
| followsProceeding | **92** | 37 | 55 | 32 | 80 | 91 | 50 | 58 |
| hasParticipation | 0 | 0 | 35 | 29 | 5 | 1 | 41 | **50** |
| raisesArticle6Issue | 0 | 0 | 0 | 23 | 0 | 0 | 1 | **49** |
| supporting quotes | 204 | 54 | 146 | 420 | 182 | 190 | 171 | **426** |
| source paragraphs | 191 | 54 | 151 | 306 | 184 | 182 | 187 | **438** |
| proceedings w/o court | 50 | 38 | 17 | 24 | 15 | 119 | 7 | **2** |
| proceedings w/o outcome | 87 | 32 | 20 | 25 | 71 | 95 | 19 | **8** |

### Conformance and hygiene

| metric | js/mini | js/nano | js/gemma | js/qwen | ttl/mini | ttl/nano | ttl/gemma | ttl/qwen |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **invented `echr:` terms** | **0** | **0** | **0** | **30+** | **0** | **0** | **0** | **25+** |
| validation errors | 2 | 4 | 10 | 3 | 2 | **0** | 21 | 45 |
| malformed duration literals | **0** | **0** | 1 | 23 | **0** | **0** | **0** | 32 |
| duplicate authority names | 3 | **0** | 5 | 5 | **0** | 1 | **0** | 16 |
| gender asserted w/o cue | – | – | 3/3 | 0/8 | – | – | 5/5 | 0/13 |
| blank nodes | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** |

qwen3 is the **only** model that invents schema terms, and it does so in both runs.
JSON-LD run: `echr:OutcomeDismissed`, `echr:OutcomeAnnulled`,
`echr:OutcomeDirectionFavorable`, `echr:ProceedingTypeAnnulment`,
`echr:InstanceLevelSecondInstance`, `echr:hasDistance`, plus authority IRIs minted
straight into the ontology namespace (`echr:YerevanCivilCourt`,
`echr:CommercialCourt`). Turtle run: `echr:hasShareholdingPercentage`,
`echr:hasVoteInFavour`, `echr:hasVoteAgainst`, `echr:AdministrativeAction`,
`echr:DomesticAuthorityHouseOfLords`. Every other model stayed inside the `owl:oneOf`
enumerations in all 20 files — the closed vocabularies are working as designed for
three of four models.

### Network topology

| metric | js/mini | js/nano | js/gemma | js/qwen | ttl/mini | ttl/nano | ttl/gemma | ttl/qwen |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| components per case | 4.2 | **3.7** | 5.5 | 16.3 | 4.0 | 9.7 | 5.9 | 8.2 |
| singleton nodes | 15 | 16 | 27 | 145 | **15** | 55 | 29 | 69 |
| largest component share | 0.56 | 0.60 | 0.68 | 0.42 | 0.69 | 0.38 | 0.62 | **0.77** |

No model emits blank nodes and all use stable `doc:`-prefixed IRIs — mechanically, every
output is graph-shaped. The problem is always connectivity and identity, never syntax.

---

## The five criteria

### 1. Hallucinations

**Scope leakage is the dominant failure mode across all four models** — material from
the Court's own §§40–65 reasoning, or from the RELEVANT LAW section, materialised as
domestic proceedings. L2 (Beer and Regan) triggers it in every model: the real domestic
history is one Darmstadt Labour Court action, yet gpt5mini (both runs) and gemma4 (run 1)
hallucinate the *Waite and Kennedy* Federal Labour Court decision of 10 Nov 1993 — a
**different case cited as precedent** — as a proceeding in this chain. qwen3's turtle run
goes furthest, manufacturing ten proceedings out of statutes:
`doc:authority_1 echr:hasAuthorityName "Provision of Labour (Temporary Staff) Act"` and
`doc:authority_3 … "ESA Convention"` typed as `echr:DomesticAuthority` and chained
together with `followsProceeding`. gpt54nano's turtle run types five Strasbourg-level
events as domestic proceedings in the same case, including the Commission's admissibility
decision of 24 Feb 1997.

**Fabricated evidence anchors.** gemma4 asserts `hasSourceParagraph` values in L7
(Sawoniuk) — a decision with no numbered paragraphs at all — 9 times in run 1 and 18 in
run 2. qwen3's JSON-LD run twice asserts `echr:hasSupportingQuote "the appeal took six
years"`, a string that appears nowhere in L7: it is the `skos:definition` of
`echr:Article6Issue` in `echr.ttl`, i.e. the ontology text leaking into the evidence
layer. Quote fidelity is otherwise the strongest part of every model's output (95–99%
verbatim; most residual failures are curly-quote normalisation, not invention).

**Non-proceedings typed as proceedings.** gpt54nano run 1 is worst — L4 gives
`ns1:domestic_proceeding_27 … rdfs:label "medical aid sought next day at Maribor General
Hospital and x-ray findings"`, and types officers' witness testimony as proceedings.
qwen3 turtle types a hospital admission as `echr:PreTrialDetention` and three police
officers as `echr:JudicialOfficer`. gemma4 run 1 types a roadside stop as a proceeding.

**Fabricated chain edges.** All models use `followsProceeding` for mere chronological
adjacency, which the schema's scope note explicitly forbids. gpt5mini L1 strings four
unrelated administrative steps together; gpt54nano inverts the property six times in run 2
(one proceeding dated the 25th "following" one dated the 26th) and creates a 2-cycle in
L4, violating `owl:AsymmetricProperty`.

### 2. Missing triples

Counting only domestic-proceedings material, as requested:

- **The grievance layer is the single largest gap.** Zero `Article6Issue` from gpt5mini
  across 20 files; one fabricated placeholder from gpt54nano — and that one is candid
  about itself: `echr:hasExtractionNote "Placeholder node created to satisfy numeric
  coverage validation…"`. gemma4 manages 2 (run 1) and 8 (run 2); only qwen3 works this
  layer seriously, at 33 and 26.
- **Missing courts.** gpt5mini run 1 leaves 50 of 140 proceedings (36%) with no
  `hasCourt`; its L7 has **zero** `DomesticAuthority` nodes — House of Lords, Court of
  Appeal and trial court simply absent. Its L4 creates 2 authorities for 20 proceedings.
  gpt54nano run 2 is worse in absolute terms: 119 courtless proceedings.
- **Dropped chain segments.** gpt54nano run 2's L7 omits the Court of Appeal judgment of
  10 Feb 2000, the House of Lords refusal of 15 June 2000, and both the conviction and the
  directed acquittal. qwen3's turtle L4 extracts 3 proceedings and misses roughly six
  narrated instances — though its JSON-LD run got that same chain completely right, the
  best single output either run produced.
- **The delay module is nearly unused** by everything except qwen3: gemma4 manages one
  `InactivityPeriod` and one `Adjournment` per run across ten cases, several of which are
  length-of-proceedings cases.

### 3. Adherence to echr.ttl

**Closed vocabularies: three of four models are perfect.** gpt5mini, gpt54nano (run 2)
and gemma4 never once mint an IRI outside an `owl:oneOf` list. qwen3 breaks this in 5 of
10 JSON-LD files and again in the turtle run. gpt54nano run 2 has a subtler variant — it
respects the enumerations but creates *new individuals* of the vocabulary classes rather
than reusing members: `doc:outcome_upheld_on_appeal_1 a echr:ProceedingOutcome ;
rdfs:label "upheld the decision of 17 April 2013"`, when `echr:OutcomeUpheldOnAppeal`
already exists.

**Namespace integrity is gpt54nano run 1's catastrophic failure.** L7 declares three
competing namespaces — `@prefix eCHR: <https://example.org/echr/>` (a wholly invented
external vocabulary), `@prefix echr: <https://growgraph.dev/echr/>` (missing the `#`),
and the correct one — so that file's 22 proceedings split across three disjoint
vocabularies. L9 emits `@prefix ns1: <echr:>`. 165 predicates across 7 of 10 cases land
outside the real namespace, and `followsProceeding` is degraded to a string literal:
`doc:echrFollowsProceeding "cd:domestic_proceeding_4"^^xsd:string`. gemma4 run 1 has a
milder version (stray `https://growgraph.dev/ontology/echr` namespace, plus `qudt:` and
`schema:` classes).

**Functional-property violations** are the turtle run's characteristic fault, and they
are the most damaging kind because they assert contradictions on a merged node: 204 in
qwen3 turtle vs 64 in its JSON-LD run, 26 vs 8 for gemma4.

**The Participation pattern** is used correctly by gemma4 and qwen3 and effectively
ignored by both OpenAI models (0 and 5 instances). Where qwen3 skips it, it commits
exactly the error the schema warns against — `doc:domestic_proceeding_3
echr:hasAuthorityKind …; echr:hasPartySide echr:SideInitiating` — stuffing roles onto the
proceeding.

### 4. Quality of triples extracted

Quotes are uniformly the best part of the output: near-universal coverage, well-chosen,
and 95–99% verbatim. gemma4's anchoring is exemplary where it isn't fabricated —
`"the Maribor Higher Court quashed the judgment and remitted the case to a new panel for
retrial"` sits exactly on the `OutcomeQuashedAndRemitted` node.

**Entity identity is where everything falls down.** Two distinct failures, and the
over-merges are far more damaging than the duplicates because they assert falsehoods:

- *Duplication:* gpt5mini's L7 holds three nodes for the single 1999 trial, with the
  appeal, the bail review and the indictment each pointing at a **different copy**.
  gemma4's L4 has four separate nodes for the Slovenj Gradec Police.
- *False merging:* gpt5mini merges "Darmstadt Labour Court" and "Federal Labour Court"
  into one IRI, then attributes the Federal court's decision to it; it merges three
  prosecutor levels into `doc:ruseRegionalProsecutor`, so the appellate prosecutor appears
  to uphold its own refusal. gemma4 collapses Rila and Ruse Municipal Councils into one
  node that then decides both guardianship appointments.

**qwen3's cross-chunk collision is the most severe case of this in the whole
comparison**, and it is a mechanical artefact rather than a reasoning error: the
per-chunk renderer re-uses slot names (`proceeding_1`, `authority_1`), so unrelated
entities from different chunks fuse. `doc:domestic_authority_1` ends up carrying
`hasAuthorityName "Ruse Regional Court", "Veliko Tarnovo Court of Appeal", "public
prosecutor's office"` simultaneously; `doc:domestic_proceeding_1` carries three
mutually exclusive decision dates. This is why qwen3 has the best raw recall and is
still not the top-ranked model.

### 5. Ease of formatting into a network

Good news first, and it applies to all four: **zero blank nodes anywhere**, stable
`doc:`-prefixed IRIs throughout, and object properties used as real links rather than
literals (the one exception being gpt54nano run 1's stringified `followsProceeding`).
Authority IRIs are generally reused correctly as both `hasCourt` targets and separately
typed `DomesticAuthority` subjects.

Ranked on actual buildability:

1. **ttl/gpt5mini** — 4.0 components per case, largest component covers 69% of nodes, 15
   singletons, 0 orphan authorities, no asymmetry cycles. L5 is a single 30-node
   component, L6 a single 17-node component. Needs a dedupe pass, nothing more.
2. **ttl/qwen3** — best largest-component share at 0.77 and reliably links
   proceeding→authority, but 204 functional collisions mean the well-connected nodes are
   internally contradictory, and L10 splits the same Belgian chain into two parallel
   unconnected families. Needs a de-collision pass keyed on
   `hasAuthorityName`/`hasDecisionDate` before it is worth anything.
3. **ttl/gemma4** and **js/gemma4** — moderately fragmented (5.5–5.9 components per case,
   L10 run 1 has 14 components over 32 proceedings), some orphan `JudicialOfficer` and
   `PreTrialDetention` nodes never linked to a proceeding.
4. **js/qwen3** — 16.3 components per case, 145 singletons; L1 has 26 of 30 typed nodes
   fully orphaned. Not usable.
5. **js/gpt54nano** — the worst. Three namespaces mean three unrelated edge types over
   three disjoint subgraphs, `followsProceeding` is a string, and five
   `DomesticProceedingChain` nodes reify edges as nodes. Unusable without a hand-written
   remapping layer.

---

## JSON-LD vs Turtle

**Turtle-prompting wins for three of the four models**, and for gpt54nano it is the
difference between usable and unusable.

| model | better run | why |
|---|---|---|
| gpt5mini | **turtle** | 11% vs 36% of proceedings missing `hasCourt`; 60 vs 45 authorities; 2 vs 8 untyped nodes; 0 vs 1 asymmetry cycle; and it is the only run to use `Participation`, `hasBirthYear`, `hasInactivityPeriod`, `hasAdjournment` and `hasCustodialMeasure` at all. `hasOutcomeDirection` 38× vs 2×. |
| gpt54nano | **turtle**, decisively | 100% correct namespaces vs 165 corrupted predicates in 7/10 cases. Schema content: 51 `hasCourt`, 77 `hasOutcome`, 80 `hasInstanceLevel` vs 6, 21, 29. |
| qwen3 | **turtle**, on balance | 0.5% vs 4.3% unsupported quotes, 91% vs 62% outcome coverage, 5% vs 30% stub nodes, far fewer orphans. But cross-chunk merging is *worse* (204 vs 64 collisions), so it needs post-processing either way. |
| gemma4 | **JSON-LD**, narrowly | 8 vs 26 functional violations, half the fabricated anchors, more distinctive IRIs so fewer cross-chunk collisions. Its turtle run has better semantic coverage (4× the `Article6Issue` nodes, correct outcome *directions* where run 1 inverts them) but fuses proceedings in L3 and L10 badly enough that the repair step needed 20 operations on L10 alone. |

The general pattern: **turtle-prompting buys schema coverage and connectivity, and costs
cross-chunk identity discipline.** Models emit more nodes per chunk into a colliding
name space, so functional-property violations and duplicate authority names rise even as
the graph gets richer. That trade is worth taking, because missing triples can be
re-extracted while contradictory merged nodes silently corrupt downstream analysis — but
it argues for fixing the IRI-minting scheme rather than the prompt format.

This also lines up with the cost report's expectation that `LLM_GRAPH_FORMAT=turtle`
roughly halves prompt tokens per triple "with no change to extraction semantics". The
semantics did change, and mostly for the better.

---

## Choosing on your priorities

The top three scored identically at 4/10 because they fail in orthogonal ways. Pick on
what you need:

- **Want a trustworthy graph you can build on with light cleanup?** → **ttl/gpt5mini**.
  Perfect vocabulary discipline, cleanest topology, best-anchored quotes. Accept that you
  get proceedings, courts, dates and outcomes and essentially nothing else — no
  grievances, no parties, no gender.
- **Want the Article 6 grievance and delay layers populated?** → **ttl/qwen3** or
  **ttl/gemma4**. qwen3 gives 26 `Article6Issue` and 49 `raisesArticle6Issue` links
  against gpt5mini's zero; gemma4 gives 8 with far better vocabulary hygiene. Both need a
  dedupe pass first.
- **Cost-constrained corpus run?** gpt54nano is the cheapest option in the cost report at
  ~$246 for both corpora, but this evaluation does not support running it: its JSON-LD
  output is unqueryable, and its turtle output has no party, participation or grievance
  layer at all. The 80% saving buys a graph missing most of what the schema was written
  to capture.

## What to fix before a corpus-scale run

1. **Make chunk-local IRI minting collision-proof.** This is the highest-value fix and it
   is a pipeline problem, not a model problem. qwen3's and gemma4-turtle's worst defects —
   courts carrying three names, proceedings carrying four decision dates — come entirely
   from `proceeding_1` in chunk 2 colliding with `proceeding_1` in chunk 1. Prefixing
   minted IRIs with a chunk identifier would eliminate most of the 204 functional-property
   violations at a stroke, and would convert qwen3 from unusable-but-rich to the best
   recall option available.
2. **Make the grievance layer explicit in the prompt.** Two of four models emit zero
   `Article6Issue` nodes despite nine of ten cases carrying flagged grievances. The
   ontology defines the class well; the facts prompt evidently does not ask for it hard
   enough.
3. **Fence the Facts/Procedure sections from the Court's reasoning.** Every model leaks
   §§40–65 material and cited precedent into `DomesticProceeding`. The chunk section
   classifier is currently off — turning it on is the obvious lever.
4. **Add a SHACL check for `hasSourceParagraph` against paragraphs that actually exist**
   in the document. gemma4's 27 fabricated anchors on L7 would have been caught for free.
5. **Enforce the gender-cue rule.** The schema states that a gender value with no cue "is
   a guess and scores as one"; nothing currently enforces it, and gemma4 asserts 8
   genders with 0 cues.
6. **Re-run the format comparison with only `LLM_GRAPH_FORMAT` varying**, to separate the
   format effect from the `ONTOLOGY_CONTEXT_MODE` difference noted above.

## Caveats

- n = 10 cases, English-only, weighted toward long judgments. The decisions corpus is
  simpler per character and may behave differently.
- Hallucination and missing-triple counts are the product of close reading against source
  text on roughly five cases per model per run, with a mechanical conformance pass across
  all ten. They are indicative rates, not exhaustive audits.
- Both runs record `git_dirty: true` at `7acf921`.
- The repair step was applied to both runs before this evaluation; some defects visible in
  `raw/` will have been fixed, and the `repaired/` figures here are what you would
  actually consume downstream.
