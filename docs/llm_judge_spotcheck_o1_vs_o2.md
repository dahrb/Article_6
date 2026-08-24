# LLM-as-judge spot check: O1 vs the top three O2 arms

Rough, unblinded, single-judge (Claude Opus 5) assessment of **3 of the 10 pilot
documents**, scoring the **repaired** output of the three best-performing O2 arms on the
automated eval against the **O1 schema-light** baseline. Run 2026-08-24 against
`results/jurix_phase1/`.

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
