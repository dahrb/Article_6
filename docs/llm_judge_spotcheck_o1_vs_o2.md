# LLM-as-judge spot check: O1 vs the gemma O2 arms

Rough, unblinded, single-judge (Claude Opus 5) assessment of **3 of the 10 pilot
documents**, scoring the **repaired** output of six O2 arms against the **O1 schema-light**
baseline. Run 2026-08-24 against `results/jurix_phase1/`.

Written in two passes. The first covered the three arms that rank highest on the automated
eval; the second extended it to **every remaining jsonld arm**, which completes the jsonld
side of the gemma sweep. Read §"Extension" and the full seven-condition aggregate for the
combined picture — the early tables cover the first three arms only.

This is a spot check to sanity-test what the automated numbers in
`docs/phase1_pilot_report.md` are measuring. It is not the Tier-2 judge pass from
`docs/jurix_plan.md` §5 — that one is blinded, on the normalised common form, two judges,
240 documents. Nothing here should be reported as a result.

## What was compared

| condition | what it is |
|---|---|
| `o1_gemma` | schema-light baseline, flat JSON, one call per document, no repair stage |
| `o2_cf_low_jsonld` | carry-forward, 3k/6k, jsonld — **automated eval rank 1** (174 raw events) |
| `o2_cf_low_ttl` | carry-forward, 3k/6k, turtle — **rank 2** (171) |
| `o2_low_jsonld` | fan-out, 3k/6k, jsonld — **rank 3** (125) |

Cases, chosen for length spread rather than difficulty:

| id | case | chars | shape |
|---|---|--:|---|
| L6 | *Scholz AG v. Armenia* | 17,298 | one commercial dispute, ~7 jurisdictional instances, near-linear |
| L1 | *Stanev v. Bulgaria* | 23,996 | ~22 events across 6 parallel tracks; judicial, administrative, prosecutorial and enforcement all present |
| L10 | *M.N. and Others v. Belgium* | 38,275 | ~26 events, 3 heavily cross-referencing tracks, several same-date pairs |

I read each source document and built a reference event list before opening any output.
Automated structural checks (vocabulary conformance, quote-verbatim, isolated nodes,
participation defects, multi-court/multi-date violations) ran alongside the manual read;
the counts quoted below come from those checks, the judgements from the read.

---

## The rubric

Four axes, the ones asked for. Each scored **0–5, higher is better** (5 = essentially no
errors of that kind; 0 = the axis has failed). Scores are per document, then averaged.

### 1. Hallucinated triples
Assertions the source does not support. Weighted by how misleading they are:

| weight | what counts |
|---|---|
| **severe** | a fact asserted that contradicts the text (wrong court on a decision, an act attributed to a body the text says did not do it), or a fabricated proper name |
| **moderate** | out-of-scope entities modelled as domestic events — legal precedent, other cases, statutory provisions, private contracts — which the task instruction explicitly excludes; a date transcribed wrong |
| **light** | wrong class on a real entity (a doctor typed `LegalRepresentative`); an over-confident closed-vocabulary code (`OutcomeMeritsDecided` on a purely procedural ruling) |

Non-verbatim `hasSupportingQuote` is counted here when the quote stitches two passages
with an ellipsis, because the resulting string is presented as a span that does not exist.

### 2. Missed connections
Nodes that are present but not correctly joined:

| weight | what counts |
|---|---|
| **severe** | a `followsProceeding` edge that is wrong (links steps that do not review or continue each other), or an edge whose target is a conflated node and therefore means nothing |
| **moderate** | an event isolated from the chain when the text plainly states what it follows |
| **light** | a defensible track-start where a link was arguable |

Isolated-node counts are reported but not scored mechanically: a genuine track start
*should* be isolated on the in-side.

### 3. Missed triples
Content the source supports and the output does not assert: whole events absent, and
fields left empty (date, court, instance level, outcome, custodial measure, final-decision
flag). **O1 is scored against what its own schema can hold.** It cannot be marked down for
lacking `hasInstanceLevel` vocabulary it was never asked for — but it gets no credit for
structure it cannot express either.

### 4. Other errors
Everything else that would damage the artefact downstream: duplicate IRIs for one
real-world entity, duplicate nodes for one event, granularity violations (splitting one
jurisdictional instance across two proceedings, or merging two into one), and
participation defects — shared, orphaned, party-less or side-less `Participation` nodes,
all of which the extraction prompt forbids by name.

---

## Scores

**O1 = `o1_gemma`; **CFJ** = `o2_cf_low_jsonld`; **CFT** = `o2_cf_low_ttl`; **FOJ** = `o2_low_jsonld`.

### L6 — *Scholz AG v. Armenia*

| axis | O1 | CFJ | CFT | FOJ |
|---|--:|--:|--:|--:|
| Hallucinated triples | **4.5** | 4.0 | 3.0 | 2.0 |
| Missed connections | **5.0** | 4.5 | 2.0 | 2.0 |
| Missed triples | **4.0** | 3.0 | 3.0 | 2.0 |
| Other errors | **5.0** | 2.5 | 4.0 | 1.5 |
| **mean** | **4.63** | 3.50 | 3.00 | 1.88 |

*O1* recovered exactly the seven jurisdictional instances, all quotes verbatim, chain
correct throughout. Its one real error is a field misuse: the freezing injunction over the
LLC's assets recorded as `custodial_measure`, which is defined as deprivation of liberty
or legal-capacity restriction.

*CFJ* has the best chain of the three O2 arms — 15 events, 14 edges, nothing isolated — but
systematically splits each instance into a "claim lodged" node and a "decision" node,
which is the granularity rule inverted, and points **all 15 events at just 3 shared
`Participation` nodes**, the failure mode the prompt spends a paragraph warning against.
It also carries the LLC twice (`safaryanAssociatesLlc` and `llcDebtor`).

*CFT* gets granularity right where CFJ does not (start date and decision date on one
node), and its 19 participations are clean. But **6 of 11 events are isolated** — the whole
Commercial Court track has no edges at all — and it types the two private commercial
contracts of 28 October 2005 as `AdministrativeAction`, which they are not: no state body
is involved. It also collapses the two named lawyers into one "Representative of Scholz AG".

*FOJ* asserts a false triple: `proceeding_2` is labelled "District Court proceeding",
carries `hasCourt` → Kentron and Nork-Marash District Court, and is evidenced by a quote
about the **Commercial Court's** three-judge bench on 10 December 2007, with a second
decision date attached. Applicant, LLC and case document are each duplicated.

### L1 — *Stanev v. Bulgaria*

| axis | O1 | CFJ | CFT | FOJ | PRIOR |
|---|--:|--:|--:|--:|--:|
| Hallucinated triples | **5.0** | 2.5 | 2.0 | 1.5 | 1.5 |
| Missed connections | **4.0** | 4.0 | 2.5 | 1.5 | 1.0 |
| Missed triples | 2.0 | **3.5** | 3.0 | 2.5 | 0.5 |
| Other errors | **4.0** | 4.0 | 2.0 | 1.5 | 2.0 |
| **mean** | **3.75** | 3.50 | 2.38 | 1.75 | 1.25 |

**PRIOR** = `prior_results/art6_domestic_test_set.L1.facts(2).ttl`, scored on the same
rubric but **not comparable to the other four** — see the addendum below for why. It is
excluded from the three-document aggregate.

This is the case where the conditions genuinely diverge.

*O1* fabricates nothing and its chain is sound, but it returns **10 entries against ~22
recoverable events** and the omissions are not random: the entire enforcement layer is gone —
the welfare-placement agreement of 10 December 2002, the ambulance transfer to Pastra the
same day, the address registration of 14 December 2002 — as is the whole social-allowance
track and the October 2006 police-and-return sequence. `custodial_measure` is **null on all
ten entries** in a case about deprivation of liberty and legal-capacity restriction, where
the 20 November 2000 partial-incapacity declaration is a textbook instance of the field's
own definition. Two quotes are ellipsis-stitched.

*CFJ* is the best output on this document: 20 events, the placement layer present, all
three prosecutorial levels correctly chained, the Dupnitsa judgment correctly flagged
`isFinalDomesticDecision`. Its hallucinations are real though. The prosecutor's office is
named **"regional prosecutor of Rila"** and **"appellate prosecutor of Rila"** — the text says
*Ruse*, and never associates a prosecutor with Rila. The allowance increase is dated
`2009-03-03` where the quote says 3 February 2009. And `enforcementReturnToHome` carries
`hasCourt` → Ruse municipal police, when the text states the police **refused** to transfer
him and he was driven back "apparently by staff of the home" — an assertion that inverts
the source. Dr V.S. (psychiatrist) and Ms I.A. (psychologist) are typed
`LegalRepresentative`.

*CFT* recovers the most events (24) including the 25 November 2004 request to the
prosecutor that everything else misses, but pays for it: **7 of 24 events are isolated**,
including the entire placement sequence, and **9 carry no date at all**. Two nodes are
extracted not from the facts but from the ECtHR's summary of the complaint in paragraph 3
— `administrativeActionPlacement` ("his placement in a social care home for people with
mental disorders") and `domesticProceedingGuardianship` ("seek release from partial
guardianship") — with no date, no court and no outcome. **Six `Participation` nodes have no
`participatingParty` and no `hasPartySide`**, which the prompt calls a defect and says never
to emit.

*FOJ* fails structurally. `admin_action_1` merges the Ruse Municipal Council's appointment
of a guardian (23 May 2002) with the mayor of Rila's refusal (16 September 2005) into one
node with two labels, two decision dates and two quotes — and the Dupnitsa judicial review
then declares `followsProceeding` on it. The mayor's refusal also exists a second time as
`admin_action_mayor_refusal`, typed `DomesticProceeding` and pointed at the 2001 Veliko
Tarnovo appeal, an edge with no basis in the text. The placement is attributed twice to the
Ministry of Labour and Social Policy, which the document names only as the body responsible
for the home, never as decision-maker. The applicant, the case document and the guardian
are each duplicated.

### L10 — *M.N. and Others v. Belgium*

| axis | O1 | CFJ | CFT | FOJ |
|---|--:|--:|--:|--:|
| Hallucinated triples | **4.0** | 1.5 | 3.0 | 1.0 |
| Missed connections | **5.0** | 3.0 | 4.0 | 1.0 |
| Missed triples | 3.5 | 2.5 | **4.0** | 2.5 |
| Other errors | **3.5** | 2.5 | 3.0 | 1.0 |
| **mean** | **4.00** | 2.38 | 3.50 | 1.38 |

The hardest document, and the clearest separation.

*O1* is the standout on chain structure and it is not close. Twenty-two entries, and the
multi-parent edges are **correct**: the Aliens Appeals Board's 24 March 2017 judgment
follows both the 10 October refusal and the 14 October stay; the 6 March 2017 lifting
follows both the 13 September refusal and the 7 October stay; the Court of Appeal's 30 June
2017 judgment follows the 20 October judgment, the 7 December judgment and the State's
enforcement action; the 20 December 2017 enforcement ruling follows both the applicants'
action and the 30 June judgment. It also correctly distinguishes the **two different
Conseil d'État judgments of 8 February 2018** and attaches each to the right parent. Its
weakness here is quote fidelity — **5 of 22 quotes are ellipsis-stitched**, the highest
proportion of any condition on any document in this sample.

*CFJ* has by far the highest raw count (42 events) and by far the worst scope discipline.
**Nine of the 42 are not domestic events at all**: seven EU legislative provisions (Article 25
of the Visa Code, Articles 4 and 6 of the Schengen Borders Code, Article 3 of the Dublin
Regulation, and so on) typed as `echr:AdministrativeAction`, plus the CJEU's preliminary
ruling in *X and X v. Belgium* and the 8 December 2016 reference that produced it — a
different case, which the task instruction excludes in terms. Five further pairs are
duplicate nodes for one event (the 13 September refusal, the 7 December judgment and the
30 June judgment each appear twice; the 24 March 2017 judgment appears twice with the same
parent). Fourteen events are isolated. Against that: the core Aliens Office ↔ Appeals Board
ping-pong is chained correctly and the two Conseil d'État judgments are correctly
distinguished, which is genuinely hard.

*CFT* is the best O2 output in this sample. Twenty-eight events, only 2 out-of-scope (the
CJEU pair), EU instruments parked as non-event nodes rather than injected into the event
set, `hasOutcome` and `hasInstanceLevel` on nearly every proceeding, `isFinalDomesticDecision`
set, and the applicants' enforcement action modelled correctly as **one** instance carrying
both a start date (15 December 2016) and a decision date (20 December 2017) — the
granularity rule applied as written. Three duplicate event pairs remain, one edge has a
spurious extra parent, and quote fidelity is the weakest of the four conditions here at
**25 of 129 non-verbatim**.

*FOJ* is not usable on this document. Four nodes — `proceeding_1` through `proceeding_4` —
each carry **two or three decision dates, two or three `hasCourt` links and up to five
labels**. `proceeding_3` alone merges the Appeals Board judgment of 20 October 2016, the
Court of Appeal judgment of 7 December 2016, the 8 December preliminary reference and the
Appeals Board proceedings on the 17 October decisions. Six `followsProceeding` edges
terminate on these nodes, so the 18-edge count is misleading: the chain cannot be read
back. The Appeals Board exists as three separate IRIs, the applicants as four.

---

## Aggregate

Mean across the three documents.

| axis | O1 | `o2_cf_low_jsonld` | `o2_cf_low_ttl` | `o2_low_jsonld` |
|---|--:|--:|--:|--:|
| Hallucinated triples | **4.50** | 2.67 | 2.67 | 1.50 |
| Missed connections | **4.67** | 3.83 | 2.83 | 1.50 |
| Missed triples | 3.17 | 3.00 | **3.33** | 2.33 |
| Other errors | **4.17** | 3.00 | 3.00 | 1.33 |
| **overall** | **4.13** | 3.13 | 2.96 | 1.67 |

Supporting counts from the automated pass, over the same three documents:

| | O1 | CFJ | CFT | FOJ |
|---|--:|--:|--:|--:|
| events / entries | 39 | 77 | 63 | 50 |
| chain edges | 39 | 52 | 35 | 34 |
| isolated events | — | 17 | 18 | 3 |
| non-verbatim quotes | 7 / 39 | 11 / 113 | 30 / 216 | 7 / 88 |
| multi-court or multi-date violations | n/a | 0 | 0 | **7** |
| shared / party-less / orphan participations | n/a | 5 | 8 | 3 |
| closed-vocabulary violations | n/a | 0 | 0 | 0 |

---

## What I take from this

**1. O1 wins these four axes, and part of that win is the axes.** The criteria scored here
— hallucinated triples, missed connections, missed triples, entity discipline — are ones
where a flat JSON list has structurally fewer ways to fail. O1 has no classes to misassign,
no IRIs to duplicate, no `Participation` nodes to construct wrongly, and no closed
vocabularies to over-apply. It cannot commit a multi-court violation because it has no
`hasCourt`. So the gap on hallucination and "other errors" is real but partly an artefact
of surface area, and it should not be reported without that qualification. The axis where
the comparison is cleanest is **missed connections**, because both conditions are asked for
the same thing in the same terms — and O1 wins that one too, 4.67 to 3.83, mainly on L10,
where its multi-parent review edges are correct and O2's are not.

**2. O1's failure mode is recall of the non-judicial layer, and it is severe on L1.** Ten
entries against ~22 events, with the entire enforcement and administrative layer missing in
the one case in the sample where that layer is the substance of the complaint. The plan
predicted this in the O1 prompt ("these are the easiest steps to miss") and the prompt did
not prevent it. `custodial_measure` null across all ten entries of *Stanev* is the single
most consequential miss anywhere in this sample.

**3. The automated ranking and the judged ranking disagree, and specifically on rank 3.**
`o2_low_jsonld` ranks third on raw recall (125 events) and last here by a wide margin
(1.67). Its event count is inflated by nodes that are conflations of two or three real
events — 7 multi-court/multi-date violations across three documents, against 0 for both
carry-forward arms. Counting a node that merges three proceedings as three proceedings'
worth of recall is exactly backwards. This is the granularity confound the pilot report
flags for O1, appearing on the O2 side and in a more damaging form.

**4. The two carry-forward arms are close overall but fail differently, and turtle is not
obviously behind.** CFJ scores 3.13 to CFT's 2.96, but the split is not uniform: CFJ has the
better chain (3.83 vs 2.83, and 0 isolated events on L6 against CFT's 6), CFT the better
field completeness and the better granularity discipline. On the hardest document CFT is
ahead, 3.50 to 2.38, almost entirely because CFJ injected nine out-of-scope nodes into the
event set. The pilot report chose jsonld on body coverage and quote fidelity; quote
fidelity in this sample runs the other way at the span level, with CFT stitching 30 of 216
quotes against CFJ's 11 of 113 — but CFT extracts roughly twice as many quotes, so the rate
is 14% against 10%, a narrower gap than the raw counts suggest.

**5. Two error classes are shared across all three O2 arms and look like extraction-prompt
problems rather than assembly problems.** Out-of-scope modelling — EU legislation, other
cases, private contracts typed as domestic events — appears in every arm at some rate,
despite an explicit instruction against it. And the ellipsis-stitched quote appears in every
condition including O1, despite explicit instructions in both prompts. Neither is something
a different chunk size will fix.

**6. Raw recall is close to useless as a quality proxy on this sample.** Across the six O2
arms the automated rank and the judged rank barely relate (table above). `o2_cf_med_jsonld`
is 5th on recall and 1st on quality; `o2_low_jsonld` is 3rd on recall and last. The reason is
consistent: the high-recall arms inflate their counts with duplicate and conflated nodes,
and the metric charges nothing for either. Any selection rule built on raw `n` will keep
picking the arms that fail hardest here.

**7. The matched-assembly arm is the one that most needs re-examining, because it is the one
carrying the ontology claim.** `o2_large_jsonld` produced the single best output in the study
on L6 (4.75) and the second-worst on L10 (1.75), extracting 8 events from a 26-event
document and losing the entire cassation layer. That variance is a problem for the O1-vs-
O2-large contrast specifically: on L10 the comparison reads O1 4.00 against O2-large 1.75,
and essentially all of that gap is recall on a long document, not anything about
formalisation. Whole-document assembly appears to degrade sharply with length — which is a
plausible finding, but it means the ontology contrast cannot be read off this arm without
stratifying by document length first.

**8. Participation modelling is the one axis where an O2 arm is cleanly best.**
`o2_large_jsonld` produced 22 participation nodes across three documents with **zero**
defects — no sharing, no orphans, no missing party or side. Every other O2 arm has between 3
and 8. O1 has no equivalent structure at all, so there is nothing to compare, which is
precisely the Layer 3/4 argument: this is capability the flat baseline cannot express rather
than a contest it loses.

## Extension: the remaining jsonld arms

Added in a second pass, same rubric, same three cases, same `repaired/` checkpoint. This
completes the jsonld side of the gemma sweep — **LRG** = `o2_large_jsonld` (whole document,
single call — the **matched-assembly** arm), **CFM** = `o2_cf_med_jsonld` (carry-forward,
8k/16k), **MED** = `o2_med_jsonld` (fan-out, 8k/16k).

`o2_large_jsonld` matters more than its recall rank suggests: per `docs/jurix_plan.md` §3
it is the only O2 arm whose call structure matches O1's, and therefore **the only one that
can carry an ontology claim**.

### L6 — *Scholz AG v. Armenia*

| axis | LRG | CFM | MED |
|---|--:|--:|--:|
| Hallucinated triples | **5.0** | 4.5 | 2.5 |
| Missed connections | **5.0** | 4.5 | 2.5 |
| Missed triples | **4.0** | 3.5 | 3.0 |
| Other errors | **5.0** | 2.5 | 2.0 |
| **mean** | **4.75** | 3.75 | 2.50 |

**LRG is the best single output anywhere in this study, on any document.** Seven events —
exactly the seven jurisdictional instances — each carrying its start date *and* its decision
date on one node, which is the granularity rule applied precisely as written. Full chain,
nothing isolated, every quote verbatim, `isFinalDomesticDecision` on the cassation ruling,
nine participations with no defects, and one clean node per real-world entity. I could not
find a hallucinated triple in it. Its only gaps are omissions: the two named lawyers, and
`hasProceedingType` throughout.

*CFM* splits the Commercial Court appeal into a "lodged" node and a "decided" node, and
points all eight events at **two shared `Participation` nodes**. Otherwise sound: chain
complete, final flagged, both lawyers present.

*MED* leaves the 10 December 2007 appeal-bench decision isolated, labels it
`LevelFirstInstance` when it is the appeal, and marks the District Court's leave-unexamined
as `OutcomeMeritsDecided`. Applicant and LLC are each duplicated, and the Commercial Court
and Arbitration Tribunal are spuriously typed `echr:Party` alongside `DomesticAuthority`.

### L1 — *Stanev v. Bulgaria*

| axis | LRG | CFM | MED |
|---|--:|--:|--:|
| Hallucinated triples | **3.0** | 2.5 | 1.5 |
| Missed connections | **3.5** | **3.5** | 2.5 |
| Missed triples | 2.5 | **3.0** | 2.0 |
| Other errors | **3.0** | **3.0** | 1.5 |
| **mean** | **3.00** | **3.00** | 1.88 |

Both carry-forward-adjacent arms commit the same specific error: `councilRilaMunicipal`
carries **two `rdfs:label` values — "Rila Municipal Council" and "Ruse Municipal Council"** —
two councils 400 km apart, in different municipalities, doing different things at different
times, merged into one node. This is the exact false-merge pattern the shapes file's own
comment describes. In *LRG* it is partly survivable because a separate `councilRuseMunicipal`
node also exists; in *CFM* there is no separate Ruse node, so **the Ruse council's
appointment of R.P. is attributed to a node half-labelled Rila**.

*LRG* has 10 events to O1's 10, chains them correctly, and flags the Dupnitsa judgment
final — but four events carry no `hasCourt` at all, including two of the three prosecutorial
reviews, and the allowance, police-and-return and address-registration layers are all
absent.

*CFM* recovers the 2007 allowance grant that *LRG* misses, and models the Dupnitsa review
correctly as one instance with both a start and a decision date — but then **fails to flag
it final**, which *LRG* gets right. Its three named lawyers are collapsed into one node
labelled "Lawyer of Mr Rusi Kosev Stanev".

*MED* is the weakest. Three nodes — `actionGuardianAppointment`, `actionHomePlacement`,
`administrative_action_1` — are typed **both `echr:AdministrativeAction` and
`echr:DomesticProceeding`**, which the ontology declares disjoint in
`owl:AllDisjointClasses`. That is not a style problem: it makes the graph logically
inconsistent, and it is the only arm in the whole study that does it. On top of that
`prosecutorial_review_2` carries two decision dates (11 October and 29 November 2005) while
`prosecutorial_review_chief` separately holds the 29 November one, so the Chief Prosecutor's
decision is asserted twice — once inside a conflated node. Applicant, case document and
lawyer are each duplicated.

### L10 — *M.N. and Others v. Belgium*

| axis | LRG | CFM | MED |
|---|--:|--:|--:|
| Hallucinated triples | 1.5 | **3.0** | 1.5 |
| Missed connections | 1.5 | **4.0** | 1.0 |
| Missed triples | 1.0 | **2.5** | **2.5** |
| Other errors | **3.0** | **3.0** | 1.5 |
| **mean** | 1.75 | **3.13** | 1.63 |

**LRG collapses here — 8 events against ~26.** All four Conseil d'État judgments are
missing, though a `conseilEtat` authority node is minted and left pointing at nothing. Three
things are outright wrong. `proceeding_court_appeal_1` declares
`followsProceeding` on `proceeding_tpi_inter_partes_1`, **a node that does not exist in the
graph** — a dangling edge, and the only one in the study. The 14 October and 20 October
stays both follow the 13 September refusal, when they follow the 10 October and 17 October
refusals respectively — neither of which was extracted, so the edges were forced onto the
wrong parent. And the Court of Appeal's 30 June 2017 judgment is flagged
`isFinalDomesticDecision` when the case ran on to February and May 2018. Quote fidelity is
**5 of 9 non-verbatim (56%)**, the worst rate of any condition on any document here.

*CFM* is the standout of these three and the second-best O2 output on this document. Nineteen
events, and critically **each Aliens Appeals Board stay is chained to the correct Aliens
Office refusal** — the pairing *LRG* got wrong and *CFJ* got right. It also captures the
Belgian State's 27 February 2017 cassation appeal, which almost nothing else does, though it
files it under the Brussels Court of Appeal rather than the Court of Cassation. Its real
weakness is flattening: the **two** Conseil d'État judgments of 8 February 2018 become one
node with two parents, and the **two** Aliens Appeals Board judgments of 24 March 2017
likewise — distinctions O1 and `o2_cf_low_jsonld` both preserved. And it asserts
`hasOutcome` on **zero of 19** events and no final-decision flag at all.

*MED* contains a **cycle**: `proceeding_3` → `proceeding_5` → `proceeding_4` →
`proceeding_3`, which has the 25 October 2016 TPI order following the 7 December 2016 Court
of Appeal judgment. `echr:followsProceeding` is declared `owl:AsymmetricProperty` and
`owl:IrreflexiveProperty`; a cycle is both a logical violation and a chain that cannot be
read back in either direction. It is the only cycle in the study. `proceeding_1` also holds
two decision dates while `proceeding_board_20oct` separately duplicates one of them, and the
30 June 2017 judgment is flagged final while the same graph contains two 2018 judgments.

### Structural defects unique to these arms

Each of the three anomalies below appears in exactly one arm, across all seven conditions
and three documents:

| defect | arm | where |
|---|---|---|
| `followsProceeding` to a node not in the graph | `o2_large_jsonld` | L10 |
| chain cycle (3 nodes) | `o2_med_jsonld` | L10 |
| nodes typed into two `owl:AllDisjointClasses` members | `o2_med_jsonld` | L1 (×3) |

---

## Full aggregate, all seven conditions

Mean across L1, L6 and L10. `o2_cf_low_ttl` is included from the first pass for
completeness; every other row is jsonld or O1.

| axis | O1 | CFM | LRG | CFJ | CFT | MED | FOJ |
|---|--:|--:|--:|--:|--:|--:|--:|
| Hallucinated triples | **4.50** | 3.33 | 3.17 | 2.67 | 2.67 | 1.83 | 1.50 |
| Missed connections | **4.67** | 4.00 | 3.33 | 3.83 | 2.83 | 2.00 | 1.50 |
| Missed triples | 3.17 | 3.00 | 2.50 | 3.00 | **3.33** | 2.50 | 2.33 |
| Other errors | **4.17** | 2.83 | 3.67 | 3.00 | 3.00 | 1.67 | 1.33 |
| **overall** | **4.13** | **3.29** | 3.17 | 3.13 | 2.96 | 2.00 | 1.67 |

Key: **CFM** `o2_cf_med_jsonld` · **LRG** `o2_large_jsonld` · **CFJ** `o2_cf_low_jsonld` ·
**CFT** `o2_cf_low_ttl` · **MED** `o2_med_jsonld` · **FOJ** `o2_low_jsonld`.

Supporting counts, three documents combined:

| | O1 | CFM | LRG | CFJ | CFT | MED | FOJ |
|---|--:|--:|--:|--:|--:|--:|--:|
| events / entries | 39 | 38 | 25 | 77 | 63 | 37 | 50 |
| chain edges | 39 | 28 | 18 | 52 | 35 | 20 | 34 |
| isolated events | — | 3 | 2 | 17 | 18 | 7 | 3 |
| non-verbatim quotes | 7/39 | 6/45 | 6/29 | 11/113 | 30/216 | 6/41 | 7/88 |
| multi-court/date violations | n/a | 0 | 0 | 0 | 0 | 2 | 7 |
| participation defects | n/a | 6 | **0** | 5 | 8 | 6 | 3 |
| dangling edges / cycles / disjoint double-types | n/a | 0 | 1 | 0 | 0 | 4 | 0 |

### Automated rank against judged rank

| arm | raw events (10 docs) | automated rank | judged rank (of 6 O2 arms) |
|---|--:|--:|--:|
| `o2_cf_low_jsonld` | 174 | 1 | 3 |
| `o2_cf_low_ttl` | 171 | 2 | 4 |
| `o2_low_jsonld` | 125 | 3 | **6** |
| `o2_med_jsonld` | 96 | 4 | 5 |
| `o2_cf_med_jsonld` | 95 | 5 | **1** |
| `o2_large_jsonld` | 60 | 6 | 2 |

The two rankings are close to uncorrelated on this sample, and where they disagree they
disagree hard: the arm ranked 3rd on recall is last on quality, and the arm ranked 5th is
first.

## Limits

One judge, one model family, three documents, unblinded — I knew which arm I was reading.
The reference event lists are my own reading of the source, not an annotated gold standard,
and reasonable readers would differ on how many distinct events *Stanev* contains. The
0–5 scores are calibrated within this sample only and should not be compared to anything
outside it. Three documents cannot separate 3.13 from 2.96.

---

## Addendum: `prior_results/art6_domestic_test_set.L1.facts(2).ttl`

Scored on the same rubric at the user's request. **It is not a fourth O2 arm**, and the
1.25 should not be read as a model comparison.

| | |
|---|---|
| extractor | **gpt-5-mini**, temperature 1.0 (all four arms above are gemma-4-31b at 0.4) |
| pipeline | OntoCast **0.6.1**, `render_mode: facts`, 4 anchor units, 8 LLM calls |
| run date | 2026-08-16 — eight days before the phase-1 sweep |
| repair | deterministic only; `facts_llm_repair_renders_total: 0` — no LLM repair pass |
| prompt | evidently an earlier one: it emits **no `hasSupportingQuote` at all** and uses schema.org freely, neither of which the current facts prompt permits |

Different model, different pipeline version, different prompt, no repair stage. Any of
those alone would break comparability.

### Scores

| axis | score | why |
|---|--:|---|
| Hallucinated triples | 1.5 | schema-level, not fact-level — see below |
| Missed connections | 1.0 | 1 edge in the whole file |
| Missed triples | 0.5 | 3 events of ~22; zero quotes; zero participations |
| Other errors | 2.0 | duplicate case and applicant nodes; two core events exiled off-ontology |
| **mean** | **1.25** | |

### What happened

**It stops at May 2002.** Three domestic events survive: the Ruse Regional Court judgment
of 20 November 2000, the Veliko Tarnovo appeal of 12 April 2001, and the guardian
appointment of 23 May 2002 — roughly paragraph 12 of 41. Everything after is gone: the
placement, the address registration, the Rila guardian, all three prosecutorial levels, the
mayor's refusal, and the Dupnitsa District Court judgment of 10 March 2006 — **the final
domestic decision, which is the operative Article 6 event in this case**.

This looks like an aggregation failure rather than a capability limit. The run reports
`facts_rejected_merges: 86` and `facts_triples: 744` against 243 triples actually in the
file. The surviving content maps almost exactly to the first of four chunks. Worth checking
against the `dump_classes` / merge fixes that landed later in the week before drawing any
conclusion about gpt-5-mini from it.

**Zero supporting quotes.** Not one `hasSupportingQuote` triple. The evidence anchor the
whole study rests on is absent, so nothing in the file is verifiable against the source and
it could not be scored on the quote-fidelity axis at all — the same reason O0 was dropped.

**Eight invented vocabulary terms**, against zero for all four gemma arms:
`echr:JudicialOfficer` (used 16×), `echr:TypeGuardianship`, `echr:GenderCueHonorific`,
`echr:hasGenderCue`, `echr:hasGenderCueText`, `echr:hasHonorific`, `echr:hasGivenName`,
`echr:hasBirthYear`. `TypeGuardianship` breaks a closed `owl:oneOf` enumeration outright.

**Eighteen foreign predicates carrying load-bearing structure** — `schema:agent`,
`schema:member`, `schema:attendee`, `schema:participant`, `schema:worksFor`,
`schema:jobTitle`, `schema:location`, `schema:provider`, `schema:patient`, plus
`schema:Agreement`, `schema:Event`, `schema:MedicalCondition`, `schema:Organization` and
`prov:Activity` as classes. The consequence is concrete: the welfare-placement agreement of
10 December 2002 is a `schema:Agreement` and the ambulance transfer the same day is a
`schema:Event`, so **neither is a `DomesticEvent` and no SPARQL projection over the
ontology would return them**. Two of the most important events in *Stanev* are present in
the file and invisible to the graph.

**About half the file is Strasbourg, not domestic.** The Grand Chamber's 18 judges, the
deliberations of 9 February and 7 December 2011, and the hearing of 9 February 2011 with
its attendee list are all modelled, against an instruction to extract only events prior to
submission to the ECHR.

**Duplicates**: two `CaseDocument` nodes for one case (`case_36760_06` and
`case_stanev_v_bulgaria_36760_06`), and the applicant twice (`applicant_1`, "the
applicant", carrying the birth year; and `rusiKosevStanev`, carrying the party typing and
the representatives). `deliberation_2011_02_09` is minted and never referenced.
`appointment_2002_guardian` is typed `DomesticProceeding` where a municipal council
appointing a guardian is the prompt's own example of an `AdministrativeAction`.

**No `Participation` nodes at all**, so no party-side structure anywhere — Layers 3 and 4
have nothing to work with.

### What it does better than anything else in this sample

Worth recording, because two of these are ideas rather than accidents:

- **`hasJurisdictionState` on all four authorities.** It is the only output anywhere in
  this comparison that populates the field at all; both carry-forward arms omit it
  entirely across all three documents.
- **Gender provenance.** It records not just `hasGender` but *why* — `hasGenderCue`
  → `GenderCueHonorific` and `hasGenderCueText` → the literal span that triggered it. The
  facts prompt spends a long paragraph insisting gender is documentary marking decided by
  an explicit cue and nothing else, and this is the only output that makes the cue
  auditable. It is a schema violation because the terms do not exist — but the schema is
  arguably the thing that is wrong here, and **adding `hasGenderCue` / `hasGenderCueText`
  to the ontology is worth considering on its own merits.**
- `hasBirthYear` 1956, captured by nothing else.
- Correct dates on all three events, a correct chain edge, clean consistent labelling, and
  a `hasExtractionNote` that correctly reconciles the deliberation, adoption and delivery
  dates.

### Read

On the four axes asked for, this file is the weakest thing in the sample — but the reason
is not that gpt-5-mini extracts badly. It is that an earlier pipeline lost three quarters
of the document at the merge step, and an earlier prompt neither demanded verbatim
evidence nor closed the vocabulary. The current facts prompt and repair stage appear to
have fixed exactly these failures: quote fidelity ran 84–100% across the four current
arms, and closed-vocabulary violations were **zero in all four**. That is a reasonable
before/after datapoint for the methodology section, and a poor basis for any claim about
the model.

---

# Re-judge after `new_repair.py` (2026-08-25)

Same rubric, same three documents, same single unblinded judge. What changed is the
input: every O2 arm was re-repaired with `new_repair.py` (see
`docs/new_repair_fixes.md`) instead of the staged `repair_facts.py` output the tables
above scored. Run on scratch copies in `/tmp/spotcheck_repair/`; `results/` untouched.

**O1 is unchanged and uncopied** — it has no repair stage, so its 4.13 carries over as the
same baseline. Only the six O2 arms were re-scored.

Automated structural counts, three documents combined, repaired output:

| | CFM | CFJ | CFT | FOJ | LRG | MED |
|---|--:|--:|--:|--:|--:|--:|
| events | 43 | 76 | 58 | 54 | 38 | 40 |
| chain edges | 32 | 54 | 38 | 43 | 25 | 26 |
| isolated events | 4 | 15 | 11 | 3 | 8 | 4 |
| **non-verbatim quotes** | **0/50** | **0/119** | **0/218** | **0/92** | **0/35** | **0/43** |
| multi-court/date violations | **0** | **0** | **0** | **0** | **0** | **0** |
| duplicate-label nodes | **0** | **0** | **0** | **0** | **0** | **0** |
| party-less participations | **0** | **0** | **0** | **0** | **0** | **0** |
| shared / orphan participations | 0/7 | 3/5 | 1/2 | 2/14 | 0/1 | 0/5 |
| side-less participations | 0 | 0 | **7** | 1 | 0 | 0 |
| **disjoint double-typed events** | **0** | **0** | **14** | **1** | **8** | **4** |
| events with no supporting quote | 0 | 1 | 4 | 2 | **7** | 1 |
| dangling edges / cycles | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |

## Per-document scores

### L6 — *Scholz AG v. Armenia*

| axis | LRG | CFM | CFT | CFJ | MED | FOJ |
|---|--:|--:|--:|--:|--:|--:|
| Hallucinated triples | **5.0** | 4.5 | 4.0 | 4.0 | 3.5 | 3.0 |
| Missed connections | **5.0** | **5.0** | 3.0 | 4.5 | 4.0 | 3.5 |
| Missed triples | 4.5 | 4.0 | 3.5 | 3.5 | 3.5 | 2.5 |
| Other errors | 4.5 | 3.0 | 4.0 | 2.0 | 3.0 | 3.0 |
| **mean** | **4.75** | 4.13 | 3.63 | 3.50 | 3.50 | 3.00 |

*LRG* remains the best output in the study: eight events, one per jurisdictional instance,
every one carrying a court and a date, chain complete. Repair added the Yerevan Civil Court
injunction, correctly.

*CFJ scores worst on Other errors and repair made it worse.* Eighteen events for a case
with roughly seven jurisdictional instances, because it models filing and decision as
separate proceedings (`proceeding_1` "Claim for recovery of debt" and `proceeding_2`
"Decision to leave claim and counterclaim unexamined" are one instance; so are 3/4, 5/6,
7/8/9, 10/11). The review stage then **added three more** — `proceeding_counterclaim`,
`proceeding_response`, `proceeding_request_unexamined` — which are party filings, not
jurisdictional instances. The stage is doing what it was told (find events with no node)
and the rubric's granularity rule is invisible to it.

### L1 — *Stanev v. Bulgaria*

| axis | CFJ | CFM | FOJ | LRG | CFT | MED |
|---|--:|--:|--:|--:|--:|--:|
| Hallucinated triples | **3.5** | 2.5 | 3.0 | 2.0 | 2.0 | 2.0 |
| Missed connections | **4.0** | 3.5 | 3.0 | 3.5 | 2.5 | 2.5 |
| Missed triples | 3.5 | 3.0 | 3.0 | 2.5 | **3.5** | 2.0 |
| Other errors | **4.0** | 3.5 | 3.0 | 1.5 | 1.0 | 1.5 |
| **mean** | **3.75** | 3.13 | 3.00 | 2.38 | 2.25 | 2.00 |

**FOJ's defining failure is fixed.** The first pass scored it 1.75 here because
`admin_action_1` merged the Ruse Municipal Council's 2002 guardian appointment with the
mayor of Rila's 2005 refusal. Repair split them: `admin_action_1` now holds the Ruse
appointment (23 May 2002, `councilRuse`) and `admin_action_mayor_refusal` the refusal
(16 September 2005, `mayorOfRila`). One duplicate pair survives — `admin_action_placement_1`
and `administrative_action_1` are the same December 2002 placement.

**CFM's severe error survives repair untouched.** `actionGuardianAppointment`, the 23 May
2002 appointment, still carries `hasCourt councilRilaMunicipal` — the Ruse council made
that decision. No shape can see a wrong-but-well-formed authority link, so nothing flagged
it and nothing fixed it. This is the clearest case in the study of SHACL conformance and
factual accuracy coming apart.

### L10 — *M.N. and Others v. Belgium*

| axis | CFT | CFM | MED | FOJ | CFJ | LRG |
|---|--:|--:|--:|--:|--:|--:|
| Hallucinated triples | **3.5** | **3.5** | 3.0 | 2.5 | 2.0 | 1.5 |
| Missed connections | **4.0** | **4.0** | 3.5 | 3.5 | 2.5 | 1.5 |
| Missed triples | **4.0** | 3.0 | 3.0 | 3.0 | 3.0 | 2.0 |
| Other errors | 3.0 | 3.0 | 3.0 | 2.0 | 2.5 | 1.5 |
| **mean** | **3.63** | 3.38 | 3.13 | 2.75 | 2.50 | 1.63 |

*MED's chain cycle is gone* and its 3-node disjoint double-typing on this document is gone.

**CFJ's nine out-of-scope nodes survive verbatim.** `visa_code_article_25`,
`visa_code_article_32`, `visaCodeReg`, `dublin_regulation_article_1`,
`dublin_regulation_article_3`, `schengen_borders_code_article_4`,
`schengen_borders_code_article_6`, `asylum_procedures_directive_article_3` and
`cjeuPreliminaryRulingCaseX` are all still typed as domestic events. Repair took this
document from 13 SHACL violations to a handful while leaving every one of them in place —
no shape forbids typing a statute as a proceeding.

**LRG regressed badly and repair caused it.** Seven of its eighteen events are stubs the
loop minted: `rdf:type`, `hasCourt` and a participation, with **no label, no date and no
quote**. `proceeding_set_aside_2`, `proceeding_set_aside_3`,
`proceeding_enforcement_dutch_1`, `proceeding_enforcement_final_1`,
`proceeding_enforcement_french_1`, `proceeding_court_appeal_stay_1` and
`proceeding_tpi_inter_partes_1` assert that proceedings exist without saying what they are
or pointing at any text. The apparent recall gain from 11 events to 18 is not real.

## Aggregate, six O2 arms, after re-repair

| axis | O1 | CFM | CFJ | CFT | FOJ | LRG | MED |
|---|--:|--:|--:|--:|--:|--:|--:|
| Hallucinated triples | **4.50** | 3.50 | 3.17 | 3.17 | 2.83 | 2.83 | 2.83 |
| Missed connections | **4.67** | 4.17 | 3.67 | 3.17 | 3.33 | 3.33 | 3.33 |
| Missed triples | 3.17 | 3.33 | 3.33 | **3.67** | 2.83 | 3.00 | 2.83 |
| Other errors | **4.17** | 3.17 | 2.83 | 2.67 | 2.67 | 2.50 | 2.50 |
| **overall** | **4.13** | **3.54** | 3.25 | 3.17 | 2.92 | 2.92 | 2.88 |

## Change against the first pass

| arm | before | after | Δ |
|---|--:|--:|--:|
| `o2_low_jsonld` (FOJ) | 1.67 | 2.92 | **+1.25** |
| `o2_med_jsonld` (MED) | 2.00 | 2.88 | **+0.88** |
| `o2_cf_low_ttl` (CFT) | 2.96 | 3.17 | +0.21 |
| `o2_cf_med_jsonld` (CFM) | 3.29 | 3.54 | +0.25 |
| `o2_cf_low_jsonld` (CFJ) | 3.13 | 3.25 | +0.12 |
| `o2_large_jsonld` (LRG) | 3.17 | 2.92 | **−0.25** |
| `o1_gemma` (O1) | 4.13 | 4.13 | — (not repaired) |

**Ranking, six O2 arms:**

| | before | after |
|---|---|---|
| 1 | CFM 3.29 | CFM 3.54 |
| 2 | LRG 3.17 | CFJ 3.25 |
| 3 | CFJ 3.13 | CFT 3.17 |
| 4 | CFT 2.96 | FOJ 2.92 |
| 5 | MED 2.00 | LRG 2.92 |
| 6 | FOJ 1.67 | MED 2.88 |

## What I take from the re-judge

**1. Repair compresses the field far more than it raises it.** The spread across six arms
fell from 1.62 points (1.67–3.29) to 0.66 (2.88–3.54). The two worst arms gained +1.25 and
+0.88; the best gained +0.25 and one regressed. Repair is mostly buying back floor, not
ceiling — which is what a defect-driven loop should do, but it means **arm choice matters
much less after repair than the first pass implied.** Three of the six now sit within 0.1
of each other, which three documents cannot separate.

**2. O1 still wins, by a wider relative margin than the headline suggests.** 4.13 against
CFM's 3.54. The caveat from the first pass stands and now matters more: O1 is not scored on
half of what O2 is scored on, and its lead is largest on the axes where the rubric is
common to both.

**3. Everything mechanically checkable went to zero. Nothing judgment-dependent moved.**
Non-verbatim quotes, multi-court/date violations, duplicate labels and party-less
participations are now zero in every arm — 449 non-verbatim quotes across the sample
eliminated. But CFJ's nine statutory nodes, CFM's wrong council and CFJ's filing/decision
over-segmentation all survived untouched. **The repair loop fixes exactly what SHACL can
name, and the residual error is now almost entirely the part SHACL cannot name.** That is
the strongest argument in this whole study for the formalisation claim, and simultaneously
the clearest statement of its limit.

**4. Repair introduced a new defect class that SHACL cannot see: 27 disjoint-class
contradictions.** Zero exist in any raw extraction; 14 in CFT, 8 in LRG, 4 in MED and 1 in
FOJ exist after repair. `echr.ttl` declares `DomesticProceeding`, `AdministrativeAction`,
`EnforcementAction` and `ProsecutorialReview` mutually disjoint in `owl:AllDisjointClasses`;
these nodes carry two of them and are therefore logically inconsistent.

The cause is traceable and is a shape problem, not a model problem. `CaseDocumentShape`
requires `hasDomesticProceeding` to point at a `DomesticProceeding` (matching
`rdfs:range` in `echr.ttl`), but the extractions link the case document to *every* domestic
event through that property — 13 such violations in CFT L1 alone. The model repaired them
the only way that satisfies the shape, by adding the missing type, and its own rationale
says so: *"These events are linked as domestic proceedings but lack the
echr:DomesticProceeding type."* The correct repair was to remove the mis-typed links. **A
shape that can be satisfied two ways will be satisfied the cheap way**, and nothing in the
gate scores the difference — SHACL does not evaluate `owl:AllDisjointClasses`.

**5. The gate's own number is now actively misleading for LRG.** LRG L10 finished at a low
SHACL count while containing seven content-free stub events. Conformance rose and the
artefact got worse. This is the same pattern `run_native.py`'s header comment records for
unit loss — "shape conformance measures whether what you extracted is well-formed, never
whether you extracted it" — reproduced here by the repair stage rather than the extractor.

**6. Automated recall rank and judged rank remain close to uncorrelated**, and re-repair did
not fix that. `o2_cf_low_jsonld` is still rank 1 on raw events and 2nd here;
`o2_cf_med_jsonld` is still 5th on recall and 1st on quality; `o2_large_jsonld` is 6th on
recall and has now fallen to 5th on quality.

## Limits, unchanged and one new

Everything in the earlier Limits section still applies — one judge, three documents,
unblinded, no gold standard, and three documents cannot separate 3.25 from 3.17.

New: I scored these knowing which defects `new_repair.py` was built to fix, having built it.
That is a real bias risk in the direction of crediting repair, and the two findings that cut
against it — the 27 disjointness contradictions and LRG's seven stubs — were found by
automated checks I wrote for this pass, not by the judgement. Readers should weight the
mechanical counts above the scores accordingly.

---

# Pilot: does compression change where the ontology should enter? (2026-08-27)

**This is a pilot on the 10-case set and must not appear in the paper.** Same rubric,
same three documents, four conditions crossing compression with repair.

| id | stage 1 | stage 2 | repair |
|---|---|---|---|
| **A** | — | ontology direct from judgment | no |
| **B** | — | ontology direct from judgment | yes |
| **C** | evidence selection (spans) | ontology from bundles | no |
| **D** | evidence selection (spans) | ontology from bundles | yes |

**B is excluded from the scores below.** It was repaired under the older
`CaseDocumentAtomicShape`, whose `sh:class echr:DomesticProceeding` constraint has since
been corrected to `sh:or` over the four DomesticEvent subclasses. That constraint is what
manufactured B's 8 disjoint-class contradictions, so B and D were repaired against
different rules and are not comparable. B must be re-run before any B/D claim is made.

## Scores

### L6 — *Scholz AG v. Armenia*

| axis | A | C | D |
|---|--:|--:|--:|
| Hallucinated triples | **4.0** | 3.5 | 3.0 |
| Missed connections | **4.0** | 3.5 | **4.0** |
| Missed triples | **3.5** | 3.0 | 3.0 |
| Other errors | **4.0** | 2.5 | 2.0 |
| **mean** | **3.88** | 3.13 | 3.00 |

**A wins this document and the reason is granularity.** It recovers exactly seven
instances for a seven-instance case. C splits the Yerevan Civil Court instance into an
"examination" node and a "transfer" node, and D additionally splits the arbitration
correspondence (23 July / 1 August 2008) from the arbitration refusal. C and D also model
the Yerevan Civil Court itself as a participating party with no side, and D gives the
District Court proceeding two `SideInitiating` parties.

C and D code "left unexamined" as `OutcomeMeritsDecided`; A codes it `OutcomeInadmissible`,
which is right.

### L1 — *Stanev v. Bulgaria*

| axis | A | C | D |
|---|--:|--:|--:|
| Hallucinated triples | 2.0 | **3.5** | **3.5** |
| Missed connections | 3.0 | **4.0** | **4.0** |
| Missed triples | 2.5 | **4.0** | **4.0** |
| Other errors | 2.5 | **3.0** | 2.5 |
| **mean** | 2.50 | **3.63** | 3.50 |

**A carries a severe internal contradiction**: the node labelled "Guardian appointment by
Ruse Municipal Council" has `hasCourt` pointing at **Rila** Municipal Council. Its own
label contradicts its own authority link — the same Ruse/Rila error seen in `o2_cf_med`.
Seven of A's ten events carry no parties at all, and its single person has `hasGender`
with no cue.

C and D recover the welfare track A misses entirely (allowance grant, allowance increase,
social assessment) and carry two persons with verbatim gender cues.

Both C and D violate the extraction rule that a participation names the party, never their
representative: `the applicant's lawyer` appears as `participatingParty` on two
prosecutorial reviews.

### L10 — *M.N. and Others v. Belgium*

| axis | A | C | D |
|---|--:|--:|--:|
| Hallucinated triples | 3.0 | **3.5** | 3.0 |
| Missed connections | 2.5 | **4.5** | 4.0 |
| Missed triples | 1.5 | **4.0** | **4.0** |
| Other errors | 2.0 | **3.5** | 3.0 |
| **mean** | 2.25 | **3.88** | 3.50 |

**A fails on the hardest document**: 8 events against a ~26-event reference, one dangling
`followsProceeding`, six of eight events with no parties, and two persons carrying
`hasGender` with no cue. The entire Conseil d'État track is absent.

C recovers 20 events, every one quote-anchored, nearly all with parties, no dangling
references. D adds two more, one of which has no supporting quote.

## Aggregate

| axis | A direct | C compressed | D compressed+repair |
|---|--:|--:|--:|
| Hallucinated triples | 3.00 | **3.50** | 3.17 |
| Missed connections | 3.17 | **4.00** | **4.00** |
| Missed triples | 2.50 | **3.67** | **3.67** |
| Other errors | 2.83 | **3.00** | 2.50 |
| **overall** | **2.88** | **3.54** | 3.33 |

## Against O1

O1 was re-scored in this same sitting rather than carried over, because the earlier 4.13
was calibrated against different comparators. It came out **4.17**, close enough to the
original to show the scale is stable.

| axis | **O1** | C compressed | D compr+repair | A direct |
|---|--:|--:|--:|--:|
| Hallucinated triples | **4.50** | 3.50 | 3.17 | 3.00 |
| Missed connections | **4.50** | 4.00 | 4.00 | 3.17 |
| Missed triples | **3.67** | **3.67** | **3.67** | 2.50 |
| Other errors | **4.00** | 3.00 | 2.50 | 2.83 |
| **overall** | **4.17** | 3.54 | 3.33 | 2.88 |

Per document: O1 4.38 (L6), 3.88 (L1), 4.25 (L10).

**Compression halves the gap but does not close it.** O1 led direct ontology extraction by
1.29 (4.17 vs 2.88); it leads the compressed pipeline by 0.63 (4.17 vs 3.54).

**Recall is now level — the remaining gap is entirely structural.** Missed triples is a tie
at 3.67. On L10 O1 recovers 22 events and C recovers 20. What separates them is "other
errors" (4.00 vs 3.00) and hallucination (4.50 vs 3.50), and those come from defects the
flat format cannot commit:

- O1 cannot split one instance across two nodes, because an entry is an entry. C split the
  Yerevan Civil Court instance; D split the arbitration correspondence.
- O1 cannot produce a dangling reference or a participation with no side, because `follows`
  is an index into its own array and roles are inline strings.
- O1 cannot type a court as a party. C models the Yerevan Civil Court as a participating
  party with no side.

O1 also does something none of the ontology arms manage: `follows: [6, 9, 13]` expresses a
genuine DAG with multiple parents, which is what a case with three interleaving tracks
actually looks like.

**Where O1 is weaker, and the rubric does not see it.** Its `instance_level` values across
these three documents alone include "arbitration", "administrative review", "supervisory
review", "first instance (extremely urgent procedure)" and "cassation (appeal on points of
law)" — the 21-form dispersion measured earlier. It models the deciding body as a *party*
in seven of ten L1 entries, which is a category error the ontology forbids by construction.
It carries no persons, no gender, no entity identity. None of that is penalised here,
because the rubric scores per-document fidelity and O1 is scored against what its own
schema can hold.

**So the comparison stands where it stood.** O1 wins per-document extraction quality;
compression narrows that lead substantially but does not overturn it. The ontology's case
remains aggregability, not fidelity — and this pilot is evidence that the pipeline can be
made competitive on fidelity while keeping the closed vocabularies, not that it can win on
fidelity outright.

## What this pilot suggests

**1. Compression helps, and it helps most where the ontology pipeline was weakest.** The
gain is concentrated in recall (missed triples 2.50 -> 3.67) and chain structure (3.17 ->
4.00), which are exactly the axes the matched-assembly arm lost on in the earlier study.
Reading and instantiating in one pass was costing coverage.

**2. Repair is net-NEGATIVE on compressed input (3.54 -> 3.33).** This inverts the
expectation. Repair's additions are mostly noise once the input is already well-formed: it
split one instance into two on L6 and on L1, and added an unanchored event on L10. Its
"Other errors" score falls on all three documents. On direct extraction repair had real
defects to fix; here it mostly manufactures granularity violations.

If this holds at scale it is a genuine finding — **the value of a repair stage is a
function of how bad its input is**, and a pipeline that fixes the input upstream should
consider dropping it rather than tuning it.

**3. Gender provenance only exists in the compressed pipeline.** Across all ten documents,
direct extraction produced 23 `hasGender` triples and **zero** `hasGenderCueText`: gender
asserted with no evidence, unverifiable by construction. Compression produced 25 and 25.

**4. Two defects belong to stage 2, not to compression.** Stage 1 extracted 53 persons
with 42 gender cues; only 16 persons and 25 cues survive into the graph — roughly 70% loss
in the mapping stage. And `the applicant's lawyer` appears as a participating party
despite an explicit prohibition. Both are fixable in the stage-2 prompt.

## Limits

One judge, three documents, and I built the pipeline being judged. **I was not blind**:
outputs were rendered into a common format that strips IRI naming conventions, but I knew
which directory each came from. The mechanical counts are trustworthy; the 0-5 scores
carry my authorship bias and should be weighted accordingly. Note that the scores go
against the pipeline I built on L6 and against the repair stage I built overall, which is
weak evidence that the bias did not dominate — but it is not a substitute for blinding.
