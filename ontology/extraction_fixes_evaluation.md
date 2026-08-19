# Acting on the extraction quality report — what the evidence supports

Follow-up to `ontology/extraction_quality_report.md`. Four questions were put to the
pipeline: what the repair pass is actually buying, whether the chunk section classifier
fences the Court's reasoning, whether `hasSourceParagraph` can be validated, and whether
chunking can be removed or improved.

Prepared 2026-08-19. Every number below is measured, not estimated; the scripts are in
`art6/ontology/` and named at each section.

**Headline:** three of the report's six recommendations survive contact with the data,
one is wrong, and the single highest-value change is smaller than anyone expected — two
environment variables.

| report's fix | verdict |
|---|---|
| 1. collision-proof chunk-local IRIs | **superseded** — delete the chunks instead (§4) |
| 2. make the grievance layer explicit in the prompt | untested here, still stands |
| 3. turn the section classifier on to fence §§40–65 | **wrong** — it makes things worse (§2) |
| 4. SHACL-check `hasSourceParagraph` | **done** — implemented, finds 125 fabrications (§3) |
| 5. enforce the gender-cue rule | untested here, still stands |
| 6. re-run the format comparison cleanly | still stands |
| — | **new:** the §§40–65 leak is mostly a data-prep bug (§2.3) |

---

## 1. What `repair_facts.py` actually does

`art6/ontology/repair_impact.py` diffs each model's `raw/` against its `repaired/` graph
and cross-references the `<stem>.facts.repairs.json` audit logs. Scoped to gemma4 and
gpt5mini, both runs.

| | js/mini | js/gemma | ttl/mini | ttl/gemma |
|---|---:|---:|---:|---:|
| files touched (of 10) | 4 | 6 | 5 | 4 |
| triples | 1480 → 1467 (−0.9%) | 1779 → 1769 (−0.6%) | 1734 → 1724 (−0.6%) | 1821 → 1792 (−1.6%) |
| **functional violations** | 13 → 13 | 18 → **13** | 15 → 15 | 36 → **19** |
| `Article6Issue` | 0 → 0 | 2 → 2 | 0 → 0 | 8 → 8 |
| proceedings w/o court | 53 → 50 | 19 → 17 | 15 → 15 | 7 → 7 |
| supporting quotes | 204 → 204 | 146 → 146 | 182 → 182 | 171 → 171 |
| source paragraphs | 191 → 191 | 151 → 151 | 184 → 184 | 187 → 187 |
| **components** | 42 → 42 | 55 → 55 | 40 → 40 | 51 → **59** |
| **singletons** | 13 → **15** | 28 → 27 | 15 → 15 | 25 → **29** |

### It is a deletion pass, not a repair pass

Across all four run/model combinations the model issued **6 applied `add` operations
against 68 applied `remove` operations**. Of the 6 adds, 2 were `followsProceeding` and 4
`isFinalDomesticDecision`. Every single `add echr:hasCourt` the model proposed — 5 of
them — was **skipped, because the authority node it wanted to link to does not exist in
the graph**. The pass cannot create the missing entity, only re-point at existing ones,
so the largest gap in the report (36% of gpt5mini's proceedings have no court) is
structurally out of its reach.

### Its one genuine win is functional-property collisions

gemma4-turtle: 36 → 19 (−47%). gemma4-jsonld: 18 → 13. That is the report's most
damaging defect class — a node asserting three court names at once — and the repair pass
roughly halves it on gemma. On gpt5mini it changes nothing at all (13 → 13, 15 → 15),
because gpt5mini barely had the defect to begin with.

### The win was paid for in fragmentation — now fixed

gemma4-turtle's components went 51 → 59 and singletons 25 → 29. The pass removed 6
`hasCourt`, 5 `hasInstanceLevel` and 3 `followsProceeding` edges from L10 alone. It
resolved a contradiction by cutting an edge, which left an orphan: **it traded a
contradictory graph for a disconnected one** — better for correctness, worse for
buildability, and the report's own §5 ranking is on buildability.

**Resolved (2026-08-19):** `repair_facts.py` now has a real merge operation. Duplicates
are no longer re-pointed-around-and-abandoned; they are folded into the survivor and
deleted. See §1a.

### 20% of operations are rejected — and half of those rejections are a bug

19 of 93 proposed operations (20%) never reach the graph. Splitting the 14
`skipped: triple not present` rejections by checking each against the raw graph:

| cause | count | whose fault |
|---|---:|---|
| model named a genuinely different object (e.g. the wrong court) | 7 | model — guard correct |
| **lexical/datatype encoding mismatch on a triple that IS present** | **7** | **guard — the fix was valid** |
| subject+predicate genuinely absent | 0 | — |

The second row is a defect in `resolve_term()`, not in the model. Every one of the seven is
an `isFinalDomesticDecision` removal, the exact defect this pass exists to fix, and the
graph really does hold the triple:

```
graph holds:  doc:proceeding_4 echr:isFinalDomesticDecision true       # xsd:boolean
model sent:   object="true "   object_is_literal=true  datatype="xsd:boolean"
                     ^ trailing space -> Literal("true ") != Literal("true", xsd:boolean)
```

Three variants recur: a trailing space in `object`, `datatype=None` on a value the graph
types as `xsd:boolean` (so a plain `Literal("true")` is built and matches nothing), and
double-encoding as `object="true^^xsd:boolean"` alongside `datatype="xsd:boolean"`.

Across all four models the same split is 10 guard-bug / 18 wrong-object / 16 genuinely
absent, so on the noisier models the model's own errors do dominate — but on gemma4 and
gpt5mini **half of all rejections are the pipeline discarding a correct fix**.

**Fix:** normalise before matching in `resolve_term()` — strip the object, strip any
`^^suffix` the model double-encoded, and when an exact match fails, retry against the
existing objects for that `(subject, predicate)` comparing on lexical value rather than
on `(lexical, datatype)`. That converts 7 silent no-ops into 7 applied fixes on the two
models measured here, and materially improves the `isFinalDomesticDecision` conflict
resolution that is one of only four things the pass is asked to do.

The remaining 7 wrong-object rejections plus the 5 dead `add hasCourt` ops are genuine
model errors, correctly caught.

### 1a. Duplicate merging (added 2026-08-19)

The Belgian L10 case is the archetype: `doc:aliens_appeals_board_20oct2016` (10 triples,
full quote) and `doc:aliensAppealsBoardProceeding` (thin, quote is just a cross-reference)
are the *same* Aliens Appeals Board judgment of 20 October 2016, and the Court of Appeal's
`followsProceeding` pointed at the thin one. The old pass emitted a remove + an add and
left the duplicate stranded in the graph.

Three parts, added to `repair_facts.py`:

1. **`find_duplicate_candidates`** — deterministic surfacing, no LLM. `DomesticProceeding`
   nodes sharing both `hasCourt` and `hasDecisionDate` (one court cannot decide the same
   case twice in one day, so a collision is a duplicate rather than a coincidence), and
   `DomesticAuthority` nodes sharing a normalised `hasAuthorityName`. Over the turtle run
   it finds 7 / 4 / 12 / 6 candidate groups for mini / gemma / qwen / nano.
2. **A `merges` list in the patch schema** — the model names only the pair and which node
   survives, never the triples. Asking a model to hand-write a merge is how you get
   half-merged nodes and dangling references.
3. **`merge_nodes`** — deterministic execution. Re-points every inbound edge onto the
   survivor (dropping any rewrite that would create a self-loop, since
   `followsProceeding` is `owl:AsymmetricProperty` *and* `owl:IrreflexiveProperty`), moves
   across every property the survivor lacks, and **deletes every triple mentioning the
   duplicate**. For the 22 predicates the ontology declares `owl:FunctionalProperty` the
   survivor's own value wins, so a merge can never manufacture the multi-value
   contradiction the pass exists to remove. Read from `echr_2.ttl` at runtime, so a schema
   edit cannot leave the merge logic on stale cardinalities.

Replayed on the real L10 pair: the `followsProceeding` edge re-points itself with no
add/remove op at all, the duplicate vanishes completely, `hasDecisionDate` and `hasCourt`
stay single-valued, and the two source-paragraph anchors (§25 and §75) both survive as
accumulated evidence.

A narrow **`sweep_stub_orphans`** then deletes typed nodes carrying nothing but
`rdf:type`/`rdfs:label` with no inbound reference. It is deliberately conservative: an
unreferenced node that has real properties is *content*, not litter, and is kept for the
graph build to connect.

Upper bound if every detected candidate is confirmed, over the turtle run:

| model | triples | typed nodes | components |
|---|---|---|---|
| gpt5mini | 1734 → 1682 | 229 → **221** | 40 → 40 |
| gemma4 | 1821 → 1780 | 265 → **259** | 51 → **50** |
| qwen3 | 3273 → 3149 | 377 → **361** | 77 → **76** |
| gpt54nano | 1602 → 1528 | 239 → **224** | 97 → **89** |

Nodes leave the graph and component counts do not rise — the fragmentation regression is
gone.

### Verdict

Keep it — it is cheap, audited, reversible, and halves gemma's worst defect class. But
stop expecting it to fill gaps: it never touches quotes, anchors, or the grievance layer,
and it cannot mint the entities the extraction missed. **It is a consistency pass, and
should be renamed and re-scoped as one.** Two concrete changes:

1. Forbid `remove` on an edge whose removal would orphan a node, unless the patch also
   supplies a replacement edge. This directly prevents the components 51 → 59 regression.
2. Feed it the source text, not just the graph. The dead `add hasCourt` operations exist
   because the model knows which court belongs there and has no way to mint the node.
   Allowing new `doc:` authority nodes *with a supporting quote* would convert five
   skipped operations into five real fixes.

---

## 2. The chunk section classifier does not fence the Court's reasoning

Tested with `art6/ontology/chunk_probe.py`, which drives OntoCast's real
`prepare_content_units` over the test set with no LLM calls. Four configurations, all at
`CHUNK_MIN_SIZE=5000 / MAX=15000`:

| config | chunks over 10 docs | labels assigned |
|---|---:|---|
| A `classifier=off` (the 2026-08-18 runs) | **41** | none |
| C `classifier=heading`, `schema_id=legal` | 76 | **zero** — every segment `outline_unresolved` |
| D `classifier=heuristic`, auto-detect schema | 65 | 4 per doc, all wrong |
| E `heuristic` + no size chunking | 46 | same |

### 2.1 It classifies ECHR judgments as academic papers

Schema auto-detection abstains to the manifest default, which is `academic`. So:

```
[0]    743 chars  label=abstract        src=front_matter     conf=0.5    (the title block)
[1]   3249 chars  label=methods         src=heading_pattern  conf=0.95   (## PROCEDURE)
[2]     12 chars  label=None            src=outline_unresolved
[3]   5775 chars  label=None            src=outline_unresolved           (THE FACTS)
```

`PROCEDURE` is confidently labelled `methods` at 0.95. Every substantive segment — all of
THE FACTS, the whole domestic procedural history — is `outline_unresolved`. Forcing
`schema_id=legal` is worse: that schema describes *contracts* (definitions, recitals,
indemnity, governing law), matches nothing in a judgment, and produces **zero labels on
all ten documents**.

### 2.2 It increases the chunk count by 60–85%

41 → 65 chunks under auto-detection (76 with the `legal` schema), because it emits 11-, 12-, 16- and 19-character fragments from the title
block as separate content units. Each becomes its own extraction call, against the full
ontology context, over a fragment reading `## JUDGMENT`. This is the mechanism behind the
run script's existing note that "the section cascade was dropping title/PROCEDURE front
matter" — it does not drop it, it *shatters* it. Even with size chunking removed entirely
(config E) the classifier still produces 3–9 units per document instead of 1.

**Turning the classifier on, as configured today, is strictly harmful.** The report's fix
#3 should be withdrawn.

### 2.3 The leak is upstream of chunking anyway

The classifier could not fence THE LAW even with a correct schema, because *the heading is
not in the text*. The markdown OntoCast receives carries `##` headings only down to
`THE FACTS` / `I. THE CIRCUMSTANCES OF THE CASE`; nothing marks where the Court's
reasoning begins.

The real cause is the HUDOC section splitter in the data-prep stage:

| case | `facts` chars | `law` chars |
|---|---:|---:|
| L2 001-58299 (Beer and Regan) | 28,418 | **0** |
| L5 001-248395 | 25,363 | **0** |

Both are the report's worst leakage cases. Their `law` field is empty because the splitter
failed, so the entire "AS TO THE LAW" / "THE COURT'S ASSESSMENT" section was swept into
`facts` and handed to the model as fact. L2's headings confirm it: `AS TO THE LAW`,
`ALLEGED VIOLATION OF ARTICLE 6 § 1 OF THE CONVENTION`. That is exactly where the
*Waite and Kennedy* precedent leak comes from.

Corpus-wide, of 317 sampled cases with a non-empty `facts` field:

- **23 (7.8%) have an empty `law` field** — splitter failure,
- **10 (3.4%) have Court-reasoning headings inside `facts`**.

So it is systematic but modest — roughly 1 case in 13. The test set happened to draw 2 of
10, which is why the report saw it in every model.

### Recommended fix — cheap and deterministic

Add a guard to the test-set / corpus build (`build_ontocast_test_set.py`,
`build_text()`): after assembling `introduction + procedure + facts`, truncate at the
first line matching

```
^(AS TO THE LAW|THE LAW|THE COURT.S ASSESSMENT|ALLEGED VIOLATION OF ARTICLE|FOR THESE REASONS)
```

and log the truncation. This costs nothing, is auditable, and fixes the 3.4% where the
heading survives. For the residual cases where the splitter failed *and* left no heading,
a `RELEVANT LAW` / `LEGAL FRAMEWORK` denylist on the same pass covers the statute-as-
authority hallucinations (qwen3 typing the "Provision of Labour (Temporary Staff) Act" as
a `DomesticAuthority`).

A bespoke `echr` section-label schema in the OntoCast checkout is the "proper" fix, but it
is a cross-repo change that only pays off once the headings are reliably present — and
they are not. Do the truncation guard first.

---

## 3. SHACL check on `hasSourceParagraph` — implemented

`art6/ontology/validate_source_paragraphs.py`. Added `pyshacl` to the dependencies.

The ontology cannot state this constraint, because the legal set of paragraph numbers is a
property of the *document*, not of the schema. So the shapes graph is **generated per
document**: read every paragraph number the text prints, emit a `sh:NodeShape` with
`sh:targetSubjectsOf echr:hasSourceParagraph` and an `sh:in` list of exactly those values,
plus an `sh:pattern "^[0-9]+$"` shape, then run pyshacl over the facts graph.

Paragraph markers appear in two forms, both line-anchored — `**12.** On 3 May…` (bold, most
documents) and `12. On 3 May…` (plain, some) — and both are collected. Sub-headings reuse
the bold form, which makes the allowed set slightly permissive; that is deliberate, since
a validator that invents violations is worse than none.

Violations are reported in four buckets, weakest last:

- `unnumbered` — the document prints no paragraph numbers at all, so every anchor on it is
  invented outright;
- `out_of_range` — outside the document's `[min, max]`;
- `malformed` — not a bare integer;
- `gap` — inside the range but absent from this text, usually because that paragraph
  landed in the `law`/`legal_framework` field the build drops. Reported but never counted
  as a hard violation.

### What it finds, on the turtle run's `repaired/` output

| model | anchors | malformed | unnumbered | out of range | **hard** | gaps |
|---|---:|---:|---:|---:|---:|---:|
| gpt5mini | 184 | 3 | 26 | 6 | **35 (19.0%)** | 15 |
| gemma4 | 187 | 10 | 5 | 7 | **22 (11.8%)** | 16 |
| gpt54nano | 182 | 13 | 2 | 3 | **18 (9.9%)** | 21 |
| qwen3 | 438 | 6 | 35 | 9 | **50 (11.4%)** | 39 |

Two documents carry no numbering at all in the supplied text. **L8 (001-22669): every
anchor asserted on it is fabricated** — 26 of 26 from gpt5mini, 35 of 35 from qwen3. On
**L7 (Sawoniuk)**, which prints only `**1.**` and `**2.**` (the two certified questions,
not paragraphs), gemma4 asserts 11 anchors of which 10 are bad. The report's suspicion is
confirmed and quantified.

The `malformed` bucket exposes a failure the report did not name: gemma4 puts entire
sentences into the field —

```
echr:hasSourceParagraph "The facts of the case, as submitted by the applicant,
                         may be summarised as follows. Arrest and trial"
```

Note this is stronger evidence than "hallucination" alone: **quote fidelity is checked by
substring match and scores 95–99%, while paragraph anchors were checked by nothing and
score 81–90%.** The gap is a direct measure of what an unchecked field costs.

### Usage

```bash
# every model in an experiment directory
uv run python -m art6.ontology.validate_source_paragraphs \
    --experiment-dir results/experiment_ttl_20260818_161537

# one directory, gate a CI run
uv run python -m art6.ontology.validate_source_paragraphs \
    --facts-dir results/.../gemma4/repaired --fail-on 0

# keep the generated shapes for inspection
... --write-shapes
```

It writes `<stem>.facts.paragraphs.json` beside each graph and prints a per-model table.
**Wire it into `run_experiment.sh` as a phase 3**, after repair — it costs no LLM calls.

---

## 4. Chunking: yes, remove it

This is the largest and cheapest win available, and it needs no code change at all.

### 4.1 `CHUNK_MAX_SIZE` was never the parameter that mattered

The runs were configured `MIN=5000 / MAX=15000`, and produced chunks of 5,010–10,771
characters — never near the maximum. `merge_small_parts` in
`ontocast/tool/chunk/sizing.py` merges only while the accumulator is **below `min_size`**,
so once a chunk passes 5,000 characters it stops growing. **Effective chunk size tracks
`CHUNK_MIN_SIZE`, and `MAX_SIZE` is nearly inert.** Anyone tuning `MAX_SIZE` to control
granularity has been adjusting a parameter with no effect.

### 4.2 One chunk per document, with two environment variables

```
CHUNK_MIN_SIZE=40000
CHUNK_MAX_SIZE=45000
```

Measured over the test set: **41 chunks → 10, one per document, all ten documents intact**
(23,996 chars in, 23,996 out on L1). No code change, no new dependency, no cross-repo edit.

### 4.3 It fits comfortably in every model's context

Longest document is 38,275 chars ≈ 10k tokens. Plus the turtle ontology context at 16,676
tokens, that is a ~27k-token prompt against gemma-4-31b's 98,304 and Qwen-3-80B's 90,000
— the measurements already recorded in `run_experiment.sh`. gpt-5-mini has 400k. There is
no context obstacle for any of the four models.

### 4.4 It deletes the report's #1 defect class outright

The report's top recommendation was to make chunk-local IRI minting collision-proof,
because `proceeding_1` in chunk 2 collides with `proceeding_1` in chunk 1 — the cause of
qwen3's 204 functional-property violations, of authorities carrying three names, and of
proceedings carrying four decision dates. **With one chunk per document there are no
cross-chunk collisions to prevent.** The chunk-prefixed-IRI change becomes unnecessary
rather than merely deferred, and qwen3 — the best-recall model, currently unusable —
becomes the leading candidate.

It also removes the second-order damage. Look at the baseline paragraph spans:

```
L1 [1] §1-24     L2 [2] §1-30
L1 [2] §5-35     L2 [3] §2-41
```

Chunk boundaries fall mid-narrative, so a single appeal chain is split across two
independent extraction calls that never see each other. That is the mechanism behind
gpt5mini's L7 holding three separate nodes for one 1999 trial.

### 4.5 The real corpus barely chunks today anyway

From `cost_report.md`: mean document is 8,967 chars (judgments) and 5,886 (decisions);
overall 6,862. From the 317-case metadata sample, `introduction + procedure + facts`:

| percentile | chars |
|---|---:|
| p50 | 5,945 |
| p75 | 11,330 |
| p90 | 23,323 |
| p95 | 34,895 |
| p99 | 91,867 |
| max | 140,546 |

Only **10 of 317 (3.2%) exceed 40,000 characters**; 3 exceed 100,000. So at
`MIN_SIZE=40000` roughly 97% of the corpus becomes exactly one chunk. The change is
transformative for long judgments — which is precisely the population the test set
over-sampled, since `build_ontocast_test_set.py` picks `max(len(facts))` per slot — and a
no-op for the median case, which is already a single chunk at `MIN_SIZE=5000`.

That last point cuts both ways and should be said plainly: **the corpus-scale cost saving
is modest** (fewer redundant ontology-context prefills only on the long tail), and the
corpus-scale *quality* saving is concentrated in the ~10% of documents long enough to
chunk. It is still worth doing, because those are the procedurally richest cases.

### 4.6 If you want to chunk, chunk on paragraphs — not semantics

For the residual long tail, the current semantic segmenter is the wrong tool. ECHR
judgments carry an explicit, machine-readable unit of meaning that it ignores: the
numbered paragraph. A paragraph-aware splitter would:

- never cut mid-paragraph, so no quote is ever truncated across a boundary;
- give each chunk a **declared paragraph range**, which can be injected into the prompt
  (`this excerpt covers §§33–41`) — this alone would prevent most `out_of_range` anchors
  found in §3, and gives the SHACL check a per-chunk shape rather than a per-document one;
- prefer boundaries at the lettered sub-headings the text already carries
  (`A. The applicant's placement under partial guardianship`,
  `D. The applicant's attempts to obtain release…`), which are exactly the boundaries
  between distinct procedural strands — the thing the schema is trying to model.

The extraction regex is 3 lines and already written, in
`validate_source_paragraphs.py:PARAGRAPH_PATTERNS`. But this is only worth building *after*
the no-chunking run, and only for the ~3% that still need splitting.

### Recommended settings for the next run

```
CHUNK_MIN_SIZE=40000
CHUNK_MAX_SIZE=45000
CHUNK_SECTION_CLASSIFIER=off      # unchanged — §2 shows 'on' is worse
LLM_GRAPH_FORMAT=turtle
```

Two documents in the wider corpus exceed 100k characters and will still split. Log which
documents produce more than one content unit, so the residual population is known rather
than assumed.

---

## 5. What to do next, in order

1. **Set `CHUNK_MIN_SIZE=40000 / CHUNK_MAX_SIZE=45000`** and re-run. Free, no code, and it
   removes the report's #1 defect class. Do this before anything else, because it changes
   what every other metric means.
2. **Add the Court's-reasoning truncation guard** to `build_text()` (§2.3). Deterministic,
   ~10 lines, fixes the leak the classifier cannot.
3. **Wire `validate_source_paragraphs` into `run_experiment.sh` as phase 3.** Already
   written, costs nothing to run.
4. **Leave `CHUNK_SECTION_CLASSIFIER=off`.** Withdraw the report's fix #3.
5. **Fix the literal-matching bug in `repair_facts.py:resolve_term()`** (§1) — it is
   currently discarding half the valid fixes on gemma4 and gpt5mini. Then re-scope the
   pass: block orphan-creating removals, and let it mint authority nodes with a
   supporting quote.
6. Then revisit the report's fixes #2 (grievance layer in the prompt) and #5 (gender-cue
   enforcement), which this round did not test.

Note for the next run: it should use `ontology/echr_2.ttl`. `run_experiment.sh` still
copies `ontology/echr.ttl`, which **no longer exists in the working tree** — the run will
fail at the snapshot step until that line is updated.

## Caveats

- §1 and §3 measure the 2026-08-18 runs as they exist on disk; no new extraction was run.
- §2 and §4 use OntoCast's real chunking code with no LLM in the loop, so they predict
  *how documents are split*, not what a model then extracts from them. The quality
  consequence of one-chunk extraction is an inference from the report's own diagnosis of
  cross-chunk collision, and needs the re-run to confirm.
- Corpus percentiles in §4.5 are from the 317-case `sample_metadata.parquet`, not the full
  48k-record corpora; the mean figures cited alongside them are from `cost_report.md`,
  which did cover both corpora in full.
