# Extraction quality evaluation — chunking strategy: none vs. stock parallel vs. sequential carry-forward

Assessment of three extraction strategies over the same ten cases and the same model
(gemma-4-31b, local vLLM), for the runs of 2026-08-19.

Prepared 2026-08-19. Companion to `ontology/extraction_quality_report_echr2.md` (the
one-chunk-per-document run of the same morning) and
`ontology/extraction_quality_report.md` / `ontology/extraction_fixes_evaluation.md`
(2026-08-18).

## What was compared

| | **nochunk** | **stock8k** | **cf3k** |
|---|---|---|---|
| directory | `results/experiment_echr2_20260819_122533/gemma4/` | `results/experiment_cfcmp_20260819_165630/stock8k/` | `results/experiment_cfcmp_20260819_165630/cf3k/` |
| strategy | one unit per document | OntoCast stock parallel fan-out | **sequential carry-forward** (`art6/ontology/carry_forward.py`) |
| chunk size | `MIN=20000 / MAX=50000` → 1 unit/doc | `MIN=8000 / MAX=16000` → 52 units | `MIN=3000 / MAX=6000` → **70 units** |
| ontology | `echr_2.ttl` **@ 3.0.0** | `echr_2.ttl` @ 3.3.0 | `echr_2.ttl` @ 3.3.0 |
| model | gemma-4-31b | gemma-4-31b | gemma-4-31b |
| cases | 10 (L1–L10) | identical | identical |
| graph format | turtle | turtle | turtle |
| section classifier | off | off | off |

`cf3k` runs each chunk sequentially and seeds chunk *N*'s graph with everything extracted
from chunks 1…*N−1*, so the model is asked to *continue* a graph rather than build a fresh
one, and is told in `CARRY_FORWARD_INSTRUCTION` to reuse existing IRIs. `stock8k` is the
matched control: same model, same day, same ontology, same corpus — chunking strategy is
the only variable. **That pair is the clean experiment.**

`nochunk` is the third reference point and is **not** cleanly comparable: it ran against
`echr_2.ttl` **3.0.0**, before the schema gained `DomesticEvent`/`AdministrativeAction`/
`ProsecutorialReview`, the `Party`/`NaturalPerson`/`Participation`/`LegalRepresentative`
module and the tightened `echr-shapes.ttl`, and before `hasSourceParagraph` was removed.
Every figure below scores it against the *current* 3.3.0 schema so the columns line up, and
that costs it fairly (its one "invented term" is `hasSourceParagraph`, a property that was
legal when it ran) and unfairly flatters it in places (shapes that police classes its
ontology did not define cannot fire). Where the confound matters, the text says so.

Method: mechanical metrics over all 30 graphs via `art6/ontology/quality_metrics.py`
against the live `echr_2.ttl` @ 3.3.0, plus `validate_shapes.py` (against
`ontology/echr-shapes.ttl`), `validate_source_quotes.py` against the source text, and close
reading of L1, L2, L3, L6 and L10.

Input texts are byte-identical across all three arms (verified: same ten records, same
character counts).

---

## Ranking

| # | arm | score | one-line summary |
|---|---|---|---|
| **1** | **nochunk** | **8/10** | Still the cleanest graph by a wide margin — 9/10 shape-conformant, near-zero false merges, tightest topology. Lowest recall, and its lead is partly an artefact of a smaller schema. |
| **2** | **cf3k** | **6/10**\* | Carry-forward does exactly what it was built to do: the cross-chunk collision class collapses and recall roughly doubles. It pays for it with the return of scope leakage and a total loss of evidence anchoring on 3 of 10 documents. |
| **3** | **stock8k** | **3/10** | Confirms that stock parallel chunking is not viable at this schema: 0/10 conformant, the entity layer is systematically collapsed, and four repair passes barely moved it. |

\* **`cf3k` was run with a bug**: `carry_forward.py` replaced the entire `facts.txt` domain
prompt with `CARRY_FORWARD_INSTRUCTION` on every chunk after the first, so chunks 2…N were
never told to anchor evidence, never told to avoid precedent, and never told not to merge
entities. This was found and confirmed by A/B test after the run — see *Root cause* below —
and it accounts for the evidence-anchoring collapse and most of the false merging, though
not the scope leakage. **Its 6/10 is a floor, not a verdict**, and the arm should be
re-measured with the prompt restored before carry-forward is judged.

**Carry-forward is a large, real improvement over stock chunking and is still worse than
not chunking at all.** If a document fits in one request, send it in one request.

---

## Mechanical metrics

Aggregates across all 10 cases per arm, `repaired` stage. Bold marks the best value where
one direction is clearly better.

### Volume and structure

| metric | nochunk | stock8k | cf3k |
|---|---:|---:|---:|
| triples | 959 | 1676 | **2514** |
| typed nodes | 116 | 253 | **421** |
| DomesticProceeding | 60 | 65 | **104** |
| DomesticAuthority | 36 | 35 | **71** |
| CaseDocument | 10 | 21 | 10 |
| Participation | — | 36 | 65 |
| Party | — | 26 | 57 |
| NaturalPerson | — | 19 | 54 |
| AdministrativeAction | — | 11 | 37 |

Volume is not directly comparable across the ontology boundary: `Participation`, `Party`,
`NaturalPerson`, `AdministrativeAction`, `LegalRepresentative` and `ProsecutorialReview` did
not exist in 3.0.0, so `nochunk`'s 116 typed nodes are drawn from a three-class universe
(`DomesticProceeding`, `DomesticAuthority`, `CaseDocument`) while the other two draw from
thirteen. Roughly half of `cf3k`'s extra typed nodes are in classes `nochunk` could not have
emitted.

`stock8k`'s 21 `CaseDocument` nodes for 10 documents is a defect, not coverage: the
aggregator failed to unify per-chunk document nodes. Both other arms mint exactly 10.

### Delay module — still nearly unused

| metric | nochunk | stock8k | cf3k |
|---|---:|---:|---:|
| Adjournment | 0 | 1 | **5** |
| InactivityPeriod | 0 | 1 | **2** |
| DelayAttribution | 0 | 0 | 0 |

Small chunks do coax a few delay facts out of the model — 7 instantiations for `cf3k`
against 0 for `nochunk` — but `DelayAttribution` is still never used, across 30 graphs and
three strategies. The finding from both previous reports stands: **the delay module has to
be asked for explicitly in `facts.txt` or dropped.**

### Link density and evidence anchoring

| metric | nochunk | stock8k | cf3k |
|---|---:|---:|---:|
| hasCourt | 60 | 74 | **145** |
| followsProceeding | 37 | 52 | **73** |
| hasOutcome | 57 | 71 | **104** |
| hasInstanceLevel | 60 | 63 | **69** |
| hasOutcomeDirection | 56 | 59 | **89** |
| supporting quotes | 60 | 124 | 124 |
| proceedings w/o court | **0** | 10 | 5 |
| proceedings w/o outcome | **3** | 7 | 32 |
| proceedings w/o date | **2** | 6 | 34 |
| **proceedings w/o quote** | **0** | 3 | **40** |

The raw link counts favour `cf3k` everywhere, but the *completeness* columns reverse the
verdict. 31% of `cf3k`'s proceedings carry no outcome, 33% no date and **38% no supporting
quote** — against 0%, 3% and 0% for `nochunk`. See *Evidence anchoring collapses on three
documents* below: this is not a uniform thinning, it is three documents losing their entire
quote layer.

### Conformance and hygiene

| metric | nochunk | stock8k | cf3k |
|---|---:|---:|---:|
| invented `echr:` terms | 1\* | **0** | **0** |
| functional-property violations | **0** | 51 | 17 |
| malformed typed literals | **0** | **0** | **0** |
| duplicate authority names | **0** | **0** | **0** |
| **multi-label nodes (false merge)** | **1** | 31 | 23 |
| authorities w/o `hasAuthorityName` | 1 | 6 | **0** |
| `followsProceeding` self-loops | **0** | **0** | **0** |
| `followsProceeding` 2-cycles | **0** | **0** | **0** |
| blank nodes | **0** | **0** | **0** |
| **static SHACL — files conformant** | **9/10** | 0/10 | 1/10 |
| SHACL violations | **2** | 79 | 29 |
| SHACL warnings | **0** | 3 | 60 |

\* `nochunk`'s single "invented term" is `echr:hasSourceParagraph`, which 3.0.0 defined and
3.3.0 deleted. It is a scoring artefact of the cross-version comparison, not a defect.

`followsProceeding` asymmetry, blank nodes, malformed literals and duplicate authority names
are clean everywhere. Closed-vocabulary discipline holds for all three arms. **What
chunking breaks is entity identity, and nothing else.**

### Network topology

| metric | nochunk | stock8k | cf3k |
|---|---:|---:|---:|
| components per case | **2.3** | 6.4 | 11.0 |
| singleton nodes | **12** | 46 | 91 |
| largest component share | **0.86** | 0.71 | 0.75 |

`cf3k` is the most fragmented arm by component count, but that partly reflects its
`Party`/`NaturalPerson` layer, which attaches through `Participation` and is easy to leave
dangling. Its largest-component *share* is better than `stock8k`'s despite 66% more typed
nodes.

---

## The clean experiment: carry-forward vs. stock chunking

Same model, same day, same ontology, same texts. Only the chunking strategy differs.

| metric | stock8k | cf3k | change |
|---|---:|---:|---|
| SHACL violations | 79 | **29** | **−63%** |
| false-merge (`rdfs:label` maxCount) violations | 30 | **12** | **−60%** |
| `hasAuthorityName` shape violations | 11 | **5** | −55% |
| closed-vocabulary multi-value violations | 18 | **3** | **−83%** |
| functional-property violations | 51 | **17** | −67% |
| authorities w/o `hasAuthorityName` | 6 | **0** | −100% |
| `CaseDocument` nodes (10 expected) | 21 | **10** | fixed |
| repair operations needed | 52 over 4 passes | **10 over 2 passes** | −81% |
| DomesticProceeding | 65 | **104** | +60% |
| DomesticAuthority | 35 | **71** | +103% |

**Carry-forward works on the failure it was designed for.** The signature of chunk-local IRI
minting — the same court, named in two chunks, becoming one node with two names, or two
nodes the aggregator then speculatively merges — drops by 60–83% on every measure that
detects it. The commit message's L6 smoke test reproduces at full scale:

| | stock8k L6 | cf3k L6 |
|---|---|---|
| authority nodes | **1** | **6** |
| distinct names on them | 6 | 6 |
| the node(s) | `districtCourt` labelled *Arbitration Tribunal*, *Civil Court of Appeal*, *Commercial Court*, *Court of Cassation*, *Kentron and Nork-Marash District Court of Yerevan*, *Yerevan Civil Court* | six separate, correctly-named nodes |
| SHACL violations | 5 | **0** |

A single node asserting that a commercial court, an arbitration tribunal, a court of appeal
and a court of cassation are the same entity destroys the instance hierarchy the whole
schema exists to capture. Carry-forward eliminates it here entirely.

An *entity-resolution ratio* — authority nodes divided by distinct authority names, where
1.0 means every distinct name got its own node — summarises it:

| arm | authority nodes | distinct names | ratio |
|---|---:|---:|---:|
| nochunk | 36 | 42 | 0.86 |
| stock8k | 35 | 66 | **0.53** |
| cf3k | 71 | 103 | 0.69 |
| cf3k excluding L10 | 67 | 86 | **0.78** |

`stock8k` collapses nearly half its named authorities out of existence. `cf3k` recovers most
of that, and its residual is dominated by one document (L10, below).

---

## The five criteria

### 1. Hallucinations

**Scope leakage returns with small chunks, and it is `cf3k`'s worst defect.**

The previous report's headline result was that scope leakage had disappeared: "across all 20
graphs, every `DomesticAuthority` is a genuine court, tribunal, prosecutor's office or
administrative body. Full sweep, no exceptions." I re-ran that sweep over all three arms.

`nochunk` and `stock8k` both hold the line — every authority in both is a real domestic
body. **`cf3k` does not.** L2 (*Beer and Regan v. Germany*), the canary case both previous
reports used, is the clearest failure:

| arm | L2 `DomesticAuthority` nodes |
|---|---|
| nochunk | Darmstadt Labour Court |
| stock8k | one node, labelled *Darmstadt Labour Court* / *German courts* / *The Labour Court* |
| **cf3k** | Darmstadt Labour Court, **Federal Labour Court**, **European Commission of Human Rights**, **European Space Agency**, **ESA Council**, **European Space Operations Centre**, **Appeals Board of the Agency**, **`section 20(2) of the Courts Act`** |

Five of `cf3k`'s eight L2 authorities are not domestic authorities, and each reproduces a
failure a previous report recorded as fixed:

- **`section 20(2) of the Courts Act`** — a statute modelled as an authority. This is
  precisely the qwen3 defect of 2026-08-18, where §§19–22 of the RELEVANT LAW section became
  ten `DomesticAuthority` nodes.
- **Federal Labour Court** — the *Waite and Kennedy* decision, cited only as precedent. The
  previous report checked for exactly this node and confirmed both models had stopped
  inventing it.
- **European Commission of Human Rights**, and the proceedings `proceedingCommissionAdmissibility`
  and `proceedingCommissionOpinion` — Strasbourg procedure modelled as domestic history.
- **ESA / ESA Council / ESOC / Appeals Board of the Agency** — the respondent international
  organisation and its internal organs, which are the *subject* of the immunity dispute, not
  domestic authorities.

Two of `cf3k`'s L2 proceedings (`esaAppealsBoardJurisdiction`, `proceedingEsaAppealsBoard`)
are explicitly counterfactual — "*potential* recourse to ESA Appeals Board" — a proceeding
that never happened.

The mechanism is straightforward: `facts.txt` says "DO NOT model legal precedent or other
legal cases which did not DIRECTLY affect the applicant", and at 3,000 characters the model
frequently cannot tell *which section it is in*. The signal that separates §13–17 (the
applicants' own Darmstadt proceedings) from §§19–22 (RELEVANT LAW) and from the Court's own
reasoning is document-level structure, and 3k chunks destroy it. `nochunk` sees the whole
document and never makes this mistake.

Sweeping all 71 `cf3k` authorities, roughly **14–16 (20–23%) are not domestic authorities**:
the L2 set above, plus `Court of Justice of the European Union` (L10), `Maribor General
Hospital` / `Slovenj Gradec General Hospital` / `Special Unit` (L4), `Social care home in
Pastra` (L1), `Register of Advocates` (L5), `Industrial Development Bank of Türkiye` (L3),
and `investigator` / `the investigating authorities` (L9, a role and a collective, not a
body). The comparable figure for both other arms is **zero**.

**Quote fidelity**, where quotes exist, is acceptable everywhere and is not the problem:

| arm | quotes | verified | unverified |
|---|---:|---:|---:|
| nochunk | 60 | 58 | 2 (3.3%) |
| stock8k | 124 | 116 | 8 (6.5%) |
| cf3k | 124 | 118 | 6 (4.8%) |

`stock8k`'s L10 accounts for 5 of its 8 unverified quotes.

### 2. Missing triples

**Evidence anchoring collapses on three documents in `cf3k`.** L1, L6 and L10 contain
**zero** `hasSupportingQuote` triples — not thinned, absent. That is 29 of the arm's 40
unquoted proceedings, and it is the source of its 60 SHACL warnings.

| case | cf3k quotes | cf3k proceedings | unquoted | failed chunk? |
|---|---:|---:|---:|---|
| L1 | **0** | 3 | 3 | **yes — chunk 3/6** |
| L2 | 4 | 9 | 6 | no |
| L3 | 26 | 10 | 0 | no |
| L4 | 25 | 19 | 0 | no |
| L5 | 12 | 10 | 4 | no |
| L6 | **0** | 6 | 6 | **yes — chunk 3/5** |
| L7 | 15 | 10 | 0 | no |
| L8 | 20 | 11 | 1 | no |
| L9 | 12 | 6 | 0 | no |
| L10 | **0** | 20 | 20 | **yes — chunk 7/11** |

The correlation is exact: **the three documents with zero quotes are the three documents
where a chunk failed.** That correlation turned out to be a coincidence of a third variable.
The actual cause was found and confirmed experimentally — see
*Root cause: carry-forward discards the domain prompt* below. In short:
`carry_forward.py` replaced the entire `facts.txt` instruction with
`CARRY_FORWARD_INSTRUCTION` on every chunk after the first, and `facts.txt` is the only
place `hasSupportingQuote` is ever requested. Restoring it takes these four documents from
4 quotes to 115.


Other gaps:

- **`cf3k` outcome and date coverage is thin**: 32 of 104 proceedings carry no
  `hasOutcome` and 34 no date, against 3 and 2 for `nochunk`. Carry-forward extracts more
  proceedings but describes each less completely — later chunks add entities and rely on
  earlier chunks for detail that was never there.
- **`stock8k` loses 10 proceedings' deciding authority** (`proceedings w/o court`), against
  5 for `cf3k` and 0 for `nochunk`.
- **`nochunk`'s recall is genuinely low**: 3 authorities on L1 against `cf3k`'s 11, 1 on L2
  against 8. On L1 the difference is real coverage, not leakage — `cf3k` correctly finds
  Ruse Municipal Council, the Mayor of Rila, the regional and appellate prosecutors and
  Ruse municipal police, all real actors in *Stanev* and all named in the previous report as
  gemma4's specific misses. **Carry-forward fixes the recall complaint the previous report
  raised against `nochunk`.**
- **`DelayAttribution` is unused in all 30 graphs.**

### 3. Adherence to `echr_2.ttl` @ 3.3.0

Closed-vocabulary discipline is perfect for `stock8k` and `cf3k`: **0 invented `echr:`
terms** across 20 files, every enumeration member drawn from the ontology's own `owl:oneOf`
lists. `nochunk`'s single flagged term is the retired `hasSourceParagraph`.

Static SHACL against `ontology/echr-shapes.ttl` (v3.3.0, with the new `SingleLabelShape` and
`hasAuthorityName minCount 1`):

| arm | conformant | violations | warnings |
|---|---|---:|---:|
| nochunk | **9/10** | **2** | **0** |
| stock8k | 0/10 | 79 | 3 |
| cf3k | 1/10 | 29 | 60 |

Violations by shape:

| shape | nochunk | stock8k | cf3k |
|---|---:|---:|---:|
| `rdfs:label maxCount 1` (false merge) | 1 | **30** | 12 |
| `hasAuthorityName` exactly 1 | 1 | 11 | 5 |
| `hasCourt maxCount 1` | 0 | 8 | 1 |
| closed-vocabulary `maxCount 1` (outcome, level, type, direction, kind, side) | 0 | **18** | 3 |
| date `maxCount 1` | 0 | 8 | 1 |
| `participatingParty` exactly 1 | — | 3 | 7 |
| `hasSupportingQuote minCount 1` *(warning)* | 0 | 3 | **60** |

**The new `SingleLabelShape` earns its place immediately.** It was added specifically because
the previous report found gemma4's five-courts-in-one-node L3 defect passing every automated
check; it now catches 30 instances in `stock8k` and 12 in `cf3k` that nothing else detects.

The closed-vocabulary `maxCount` row is the aggregator's signature: `stock8k` has 18 nodes
carrying two conflicting enumeration values (two outcomes, two instance levels) because two
chunks characterised the same proceeding differently and the aggregator merged them without
resolving the conflict. Carry-forward reduces this to 3 — the model resolves the conflict
itself, at extraction time, because it can see what it already asserted.

### 4. Quality of triples extracted

**The character of the residual false merges differs between the two chunked arms, and this
matters more than the counts.**

`stock8k`'s merges destroy the entity layer:

```
input.L6  districtCourt        6 labels: Arbitration Tribunal, Civil Court of Appeal,
                                         Commercial Court, Court of Cassation, ...
input.L5  courtSupreme         5 labels: Kyiv Administrative Court of Appeal,
                                         Kyiv District Administrative Court,
                                         Podilskyy District Court, ...
input.L4  mariborHigherCourt   3 labels: Domestic Court, Maribor Higher Court,
                                         Slovenj Gradec District Court
input.L3  courtSupremeAdmin    4 labels: 13th Chamber of the SAC, General Assembly of the
                                         SAC, Supreme Administrative Court, ...
input.L10 proceeding_1         3 labels: Action for stay of execution (13 Dec 2016),
                                         Aliens Appeals Board proceeding,
                                         Aliens Office visa refusal decisions of 10 Oct 2016
```

Distinct courts at different instance levels folded into one node, and — uniquely to
`stock8k` — *proceedings* merged with each other (L10 has five such nodes). A graph in which
the first-instance court and the court of cassation are the same node cannot answer any
question the project exists to ask.

`cf3k`'s merges are mostly a different, milder defect — over-eager reuse of
`Participation` nodes:

```
input.L6  participation_1      4 labels: Scholz AG Participation in Appeal / Arbitration /
                                         Cassation / proceeding_1
input.L5  participationApplicantAppeal  5 labels: Participation of Mr Martynovskiy in
                                         Bar appeals / HQDCB appeal / Kyiv Admin Court of
                                         Appeal proceeding / Kyiv District Admin Court ...
input.L3  participationAksoyStanding    8 labels: Participation of Erol Aksoy in
                                         Constitutional Court / SAC 13th Chamber / SAC
                                         General Assembly / enforcement proceedings ...
```

The same party genuinely participates in all of those proceedings; the model has correctly
identified one party and one role and then attached them to several proceedings through a
single reified `Participation` node instead of one node per proceeding. That is a
**modelling error that is mechanically repairable** — split by `participatesIn` object —
where `stock8k`'s court collapses are not, because the information needed to un-merge them
was discarded. Of `cf3k`'s 23 multi-label nodes, 11 are `Participation`, 5 are authority collapses
(three of them near-synonyms of the same prosecutor's office), and the rest are
persons and parties.

Note that the carry-forward instruction *invites* this and nothing counterbalances it:
"REUSE EXISTING IRIs… Do not mint a second IRI for something already present" is the only
identity guidance chunks 2…N receive, because the rule that would have bounded it — "a party
who is the responding party at trial and the initiating party on appeal gets TWO
participations, not one with two sides" — is dropped along with the rest of `facts.txt`
(and in this run was not in the embedded prompt at all; see *Also found* below).

**The one genuinely bad `cf3k` node is L10's `aliensOffice`, carrying 10 labels** — Aliens
Office, Aliens Appeals Board, Brussels Court of Appeal, Brussels Dutch-speaking TPI and six
more. It is worse than anything in `stock8k`, it is in one of the three failed-chunk
documents, and it single-handedly drags the arm's entity-resolution ratio from 0.78 to 0.69.

**The repair pass is close to useless on chunked output.** It reduced `stock8k` from 82 to
79 violations across *four* passes and 52 operations, and `cf3k` from 34 to 29 across two
passes and 10 operations. The `stock8k` driver log shows the gate reverting: *"repair pass 1
did not reduce merge-signature errors (8 → 8); reverting to the pre-repair graph"*. Repair
cannot recover identity information that chunking already discarded — consistent with the
previous report's finding that repair had almost nothing to do once chunking was removed.
**Repair is not a mitigation for a bad chunking strategy.**

### 5. Ease of formatting into a network

1. **nochunk** — 2.3 components per case, 0.86 largest-component share, 12 singletons,
   9/10 SHACL-conformant, one node to split by hand. Directly buildable.
2. **cf3k** — 11.0 components per case and 91 singletons, but a correct entity layer under
   the fragmentation. The `Participation` splits are scriptable; the scope leakage needs a
   filter (or a bigger chunk); the three quote-less documents need re-extraction. Buildable
   after work.
3. **stock8k** — 6.4 components per case looks better than `cf3k`, but the connectivity is
   an illusion produced by false merges: collapsing six courts into one node *raises* the
   largest-component share while destroying the graph's meaning. **Not buildable** without
   re-extraction.

Zero blank nodes, stable `doc:`-prefixed IRIs and object properties used as real links in
all three.

---

## Root cause: carry-forward discards the domain prompt

Confirmed by direct experiment against the same gemma-4-31b server, 2026-08-19 evening.

`art6/ontology/carry_forward.py:300` builds each chunk's state like this:

```python
facts_user_instruction=(
    CARRY_FORWARD_INSTRUCTION
    if carried
    else state.facts_user_instruction
),
```

`state.facts_user_instruction` is the `facts.txt` domain prompt. `CARRY_FORWARD_INSTRUCTION`
**replaces** it rather than supplementing it, so chunk 1 gets the domain rules and
**chunks 2…N get none of them**. In OntoCast, `facts_user_instruction` is the only channel
that carries domain instructions into the facts prompt
(`ontocast/agent/render_facts.py:212`); the other slot, `facts_operational_guidelines`, is
generic namespace/format guidance that never mentions evidence, precedent, labels or
parties. Confirmed by grep: no string matching `supporting.?quote` exists anywhere in
ontocast's prompt or agent modules.

Everything `facts.txt` says is therefore absent from chunks 2…N:

| rule lost from chunk 2 onward | defect it maps to |
|---|---|
| "Anchor All Evidence… populate `echr:hasSupportingQuote` with the exact verbatim substring" | 40 unquoted proceedings, 60 SHACL warnings, three documents with no evidence layer |
| "DO NOT model legal precedent or other legal cases which did not DIRECTLY affect the applicant" | scope leakage (statutes, ESA, Strasbourg organs as authorities) |
| "It is strictly forbidden for a proceeding to have more than one `echr:hasCourt`… mint a completely NEW IRI" | `hasCourt maxCount` violations, proceeding-level merges |
| the closed-vocabulary and no-hallucinated-terms constraints | — (held anyway) |

And `CARRY_FORWARD_INSTRUCTION` actively pushes the other way: "REUSE EXISTING IRIs… Do not
mint a second IRI for something already present" is the *only* identity guidance chunks 2…N
receive, with nothing left to bound it.

### Why some documents kept their quotes and three lost them entirely

Chunk 1 is the only chunk that sees the quote requirement. Later chunks emit quotes only by
**imitating the graph they are seeded with**. So quote behaviour for a whole document is
decided by whether chunk 1 happened to produce a quote-bearing exemplar — and chunk 1 is
usually the judgment's title page and PROCEDURE section, which often yields almost nothing.

Chunk-1 output size predicts the document's entire quote layer, with clean separation:

| record | chunk-1 triples | final quotes |
|---|---:|---:|
| L6 | 10 | **0** |
| L1 | 14 | **0** |
| L2 | 15 | 4 |
| L10 | 17 | **0** |
| L3 | 24 | 26 |
| L4 | 25 | 25 |
| L7 | 30 | 15 |
| L9 | 43 | 12 |
| L5 | 49 | 12 |
| L8 | 73 | 20 |

The four thinnest chunk-1 outputs are exactly the four worst quote outcomes. The apparent
"failed chunk causes zero quotes" correlation is this variable in disguise: the same
documents that give the model a thin start also give it thin, ambiguous later chunks.

### The A/B test

`CARRY_FORWARD_INSTRUCTION` was changed from replacing `facts.txt` to being appended to it.
Nothing else changed — same model, same env file, same chunk sizes, same input.

| | L1 | L2 | L6 | L10 | total |
|---|---:|---:|---:|---:|---:|
| quotes, as run | 0 | 4 | 0 | 0 | **4** |
| quotes, prompt restored | 37 | 21 | 11 | 46 | **115** |
| quotes verified verbatim | 36 | 20 | 11 | 44 | 111 (96.5%) |
| SHACL warnings (unquoted entity) | 15 | 6 | 6 | 26 | **53 → 0** |
| false-merge (`rdfs:label`) violations | | | | | **6 → 1** |

L10's catastrophic 10-label `aliensOffice` node does not reappear. **Evidence anchoring and
false merging are both fixed by restoring the prompt**, and neither is intrinsic to
carry-forward.

### What the fix does *not* fix

**Scope leakage is independent of the prompt drop and survives the fix.** With `facts.txt`
restored, L2 still yields `European Space Agency`, `ESA Appeals Board` and `Federal Labour
Court` (precedent) as domestic authorities, and L10 gets *worse* — it now mints four EU
legal instruments as `DomesticAuthority` nodes:

```
'Asylum Procedures Directive'  'Dublin Regulation'
'Schengen Borders Code'        'Visa Code'
'Court of Justice of the European Union'
```

The rule forbidding precedent is present in the prompt and the model still breaks it,
because at 3,000 characters it cannot tell which section of the judgment it is reading.
**This one is intrinsic to small chunks and only a larger window will fix it.**

The restored prompt also introduces a new defect: **invented enumeration members**.
`echr:TypeGuardianship` and `echr:TypeSocialSecurity` appear on L1 proceedings; neither is in
the closed `ProceedingType` vocabulary. Extraction ambition went up and vocabulary
discipline went down (13 violations across the four files).

### A separate, reproducible bug: failed chunks silently drop their text

`facts_loop` failures are deterministic enough to reproduce: L1 chunk 3 and L6 chunk 3
failed in *both* the original and the patched run. Two distinct signatures:

1. **Malformed Turtle from the model** — a stray caret where a datatype tag should be:
   `rdfs:label "European Space Agency"^ .` Also seen on L6 chunk 3.
2. **Unparseable response** — `1 validation error for GraphUpdateRenderReport: Input should
   be a valid dictionary…, input_value=None` after all three inner parse retries.

No output token cap is configured and the model has a 98,304-token window, so this is not
truncation; the model is simply emitting invalid payloads.

The consequence is the real problem: **a failed chunk contributes nothing and its text is
never revisited.** L1 chunk 3 is 4,491 characters — 19% of the document — silently absent
from the graph, and `carry_forward.py` writes the output file and reports the record anyway.
Restoring the prompt makes this *more* frequent (5 failed chunks in 31 against 3 in 70),
presumably because longer, quote-bearing payloads have more chances to go malformed.

---

## Also found: every run so far used a stale `facts.txt`

The prompt is embedded into `input.jsonl` at build time by `build_ontocast_test_set.py`. All
three arms carry the **same 1,783-byte prompt**, which is the pre-3.3.0 version. The current
`art6/ontology/prompts/facts.txt` is 3,532 bytes. Missing from every run to date:

- the `DomesticEvent` subclass guidance (`AdministrativeAction`, `EnforcementAction`,
  `ProsecutorialReview`) — the old text says only "Instantiate these as
  `echr:DomesticProceeding`";
- **"No entity should have more than one label, ensure different IRIs for different
  courts"** — the rule the `SingleLabelShape` was written to enforce has never been asked
  for;
- the entire Party / `Participation` / `LegalRepresentative` section, including "a party who
  is the responding party at trial and the initiating party on appeal gets TWO
  participations" — the exact rule whose absence produced `cf3k`'s 11 over-reused
  `Participation` nodes;
- and it still requires `echr:hasSourceParagraph`, deleted from the ontology in 3.3.0 —
  which is why the patched A/B output emits that retired property.

This does not invalidate the three-arm comparison (the prompt is constant across all of
them), but **no run has yet tested the ontology and the prompt that are actually in the
repo.** `input.jsonl` must be rebuilt before the next experiment.

---

## Operational notes

**The timing comparison in this run is not usable.** `stock8k` (16:56–17:04) and `cf3k`
(16:56–17:15) ran **concurrently against the same local vLLM server**, so both arms
contended for the same GPU and neither wall-clock figure reflects what it would do alone.
What can be said:

| arm | LLM units | LLM work | note |
|---|---:|---|---|
| nochunk | 10 | — | 3m 49s wall clock, uncontended, per the previous report |
| stock8k | 52 chunks, 52 doc-level calls | 593s of `llm/provider` time | parallel fan-out, `PARALLEL_WORKERS=16` |
| cf3k | 70 chunks | 1,109s total, strictly sequential | no parallelism available by construction |

The structural point survives the contention: **carry-forward is sequential by design and
cannot be parallelised within a document.** `stock8k` issues 52 requests it can overlap;
`cf3k` issues 70 it cannot. At 48,000 documents that is the dominant cost consideration,
and it argues for the same conclusion as the quality metrics — send whole documents.

**3 of 70 `cf3k` chunks (4.3%) failed outright**, each with
`Failed to generate graph update for facts: 1 validation error for GraphUpdateRenderReport`,
and the run continued past all three. Given the quote correlation above, a failed chunk
should be treated as a failed *document* until the mechanism is understood.

**Fuseki was rejecting dataset creation throughout both arms** (`403 Access denied : only
localhost access allowed`, hundreds of occurrences in both driver logs). This did not affect
the delivered TTL files, which are written from the in-process graph, but it means neither
arm was loaded into the triple store and the `art6_gemma4_stock8k` / `art6_gemma4_cf3k`
projects do not exist. See the standing note that the Fuseki catalog still needs reloading.

---

## What to do

1. **Do not adopt stock parallel chunking.** It is the worst arm on every quality measure
   that matters, four repair passes could not rescue it, and its apparent connectivity is an
   artefact of false merges.
2. **Default to no chunking** for documents that fit in one request. It remains the best
   graph by a wide margin and the cheapest to run. The corpus median document is ~6,000
   characters against this test set's ~24,000, so the great majority of the corpus will fit
   comfortably.
3. **Keep carry-forward for the documents that do not fit**, but raise the chunk size well
   above 3k first. Almost every `cf3k`-specific defect — scope leakage, thin outcome/date
   coverage — traces to chunks too small to carry document structure. A carry-forward run at
   `MIN=8000 / MAX=16000` would isolate that variable against `stock8k` at the same size and
   is the obvious next experiment.
4. **Fix the prompt drop in `carry_forward.py:300`** — append `CARRY_FORWARD_INSTRUCTION`
   to `state.facts_user_instruction` instead of replacing it. One line. It takes the four
   worst documents from 4 supporting quotes to 115 and cuts false merges from 6 to 1, and
   no other finding in this report should be acted on before it lands, because every
   `cf3k` number above was measured without the domain prompt.
5. **Rebuild `input.jsonl`.** Every run to date embedded a stale 1,783-byte `facts.txt`;
   the current one is 3,532 bytes and contains the label rule, the `DomesticEvent` subclass
   guidance and the whole `Participation` section. No run has yet tested the prompt in the
   repo.
6. **Treat a failed chunk as a failed document** in `carry_forward.py`. It currently logs
   the error, continues, and writes an output file; L1 chunk 3 is 4,491 characters — 19% of
   the document — silently missing. Either abort the record or re-queue the chunk.
7. **Re-measure carry-forward after 4–6**, then decide. The `cf3k` column in this report
   describes a configuration that should not have been run, and its 6/10 is a floor rather
   than a verdict.
8. **Scope leakage still needs a bigger window.** It survives the prompt fix and gets worse
   on L10 (four EU legal instruments minted as `DomesticAuthority`). No prompt wording will
   fix a model that cannot see which section of the judgment it is in.
9. **Watch for invented enumeration members** once the prompt is restored:
   `echr:TypeGuardianship` and `echr:TypeSocialSecurity` appeared in the A/B output and are
   not in the closed `ProceedingType` vocabulary.
10. **Fix the Fuseki 403** (`Access denied : only localhost access allowed`) before the next
   run, or accept that runs are file-only — neither cfcmp arm was loaded into the store.
11. **Resolve the delay module.** `DelayAttribution` has now gone unused across 30 graphs and
   three chunking strategies.

## Caveats

- **The `nochunk` comparison is confounded by the ontology version** (3.0.0 vs 3.3.0). Its
  hygiene lead is real — 2 violations against 29 and 79 — but part of it is that six of the
  thirteen classes the shapes police did not exist when it ran, and `Participation`, the
  class responsible for half of `cf3k`'s residual false merges, is one of them. A clean
  reading needs a no-chunking re-run against 3.3.0. That run is cheap (3m 49s for ten
  documents) and should be done before the recommendation in point 2 is treated as settled.
- `stock8k` vs `cf3k` **is** clean: same model, same ontology, same texts, same day, one
  variable.
- The two chunked arms ran concurrently on one GPU; no timing conclusion should be drawn
  from their wall clocks.
- n = 10 cases, English-only, selected by `build_ontocast_test_set.py` as `max(len(facts))`
  per slot, so this sample is deliberately weighted toward long judgments and is not
  representative of the corpus.
- Hallucination findings come from a full mechanical sweep of every `DomesticAuthority`
  label in all 30 graphs, plus close reading of L1, L2, L3, L6 and L10 against source text.
  The proceeding-level leakage figures are indicative, not exhaustive — only L2's
  proceedings were read individually.
- All mechanical figures come from `art6/ontology/quality_metrics.py`,
  `art6/ontology/validate_shapes.py` and `art6/ontology/validate_source_quotes.py` run
  against the live `ontology/echr_2.ttl` @ 3.3.0 and `ontology/echr-shapes.ttl`, and are
  reproducible from the delivered graphs.
