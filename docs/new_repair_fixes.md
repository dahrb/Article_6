# `new_repair.py` — what was broken and what fixed it

Working notes on the redesigned repair pipeline, kept because most of these
faults were invisible in the summary output and only showed up when the
per-operation audit trail was read against the resulting graph.

Benchmark throughout: `results/jurix_phase1/o2_low_jsonld/raw/input.L1.facts.ttl`
(*Stanev v. Bulgaria*), 21 SHACL violations on input, gemma-4-31b at
temperature 0. Old pipeline (`repair_facts.py`) on the same file: **21 → 12**.

| stage of work | SHACL after | total findings after |
|---|--:|--:|
| first working version | 13 | — |
| quote-index and unanchored-additions fixes | 8 | 15 |
| full findings + full ontology + extraction brief to review | 6 | 14 |
| review completeness requirement + applied-based stop rule | 2 | 3 |
| quote reuse + referential guard + shape message split | **1** | **1** |

The single remaining finding is `doc:rilaMunicipalCouncil` having no
`echr:hasAuthorityName`.

## Full-arm result

`o2_low_jsonld`, all 10 documents, 957s. **188 -> 6 SHACL violations**, against
the old pipeline's 188 -> 122 on the same inputs. Seven of ten documents reach
zero.

Conformance was NOT bought by deletion -- every content axis grew:

| metric | raw | old repair | new repair |
|---|--:|--:|--:|
| triples | 2794 | 2801 | **3060** |
| events | 125 | 133 | **149** |
| participations | 128 | 154 | **190** |
| quotes | 284 | 284 | **309** |
| followsProceeding | 69 | 75 | **95** |
| persons | 51 | 43 | 37 |
| unverified quotes | 7 | 0 | **0** |

Persons falling is merging: 38 merges applied, 15 refused by the guard, and on
inspection they are correct -- `doc:applicant_1` <- `doc:aleksanderMatko`,
`doc:supremeAdministrativeCourt` <- two further IRIs for the same court.

The 6 survivors are three unanchored events, two shared participations in L10,
and one authority with no `hasAuthorityName`.

### Open: split-then-merge churn

In L10, five of eight merges dropped a node the same run had just minted by
splitting a conflated event. Four are legitimate -- the split produced a node
for an event that already had its own IRI elsewhere, so merging is correct
deduplication. But `proceeding_3_split_1` and `proceeding_3_split_2` BOTH merged
into `doc:proceeding_2`, which reads as the loop splitting a node and then
re-conflating it.

SHACL cannot see this, so it appears in none of the numbers above. Confined to
one document so far, and events rose 125 -> 149 overall, so it is not dominating
-- but it is invisible to the gate and worth watching before these graphs are
judged.

---

## 1. Findings were being truncated to 8 per category

`Findings.as_prompt_payload` cut every list at `MAX_GAP_ENTRIES_PER_PASS`,
inherited from the old pipeline where guided decoding could not finish a long
patch. Unconstrained generation removed that constraint, and the new loop has
three rounds rather than the old two-passes-across-four-stages, so a deferred
finding was likely never seen again.

**Fixed:** the model gets every finding, every round, and decides its own order.

## 2. The loop had no ontology at all

It was given the `RepairPatch` JSON schema and nothing else — no class list, no
closed vocabularies. It was being asked to repair vocabulary violations without
being told what the vocabulary is.

**Fixed:** both loop and review get the whole of `echr.ttl`. Not
`load_ontology_fragment` — the fragment exists for the old pipeline's narrow
per-class stages, and the one pass that may add an event should not be the one
pass that cannot see the full range of event classes.

## 3. The review stage was judging against a brief it had never read

It got a short prompt of its own while the extraction it was reviewing ran under
`facts.txt` — several thousand words of extraction rules.

**Fixed:** the review stage now receives exactly what the extraction received —
`facts.txt` verbatim, the full ontology, the untruncated document — plus the
graph that extraction produced.

## 4. `quote_index` removals were refused on every attempt

`apply_patch` resolves a quote named by index against the `unverified_quotes`
list it is passed. `new_repair.py` never passed it, so the table was empty and
every indexed removal failed with `quote_index 0 does not name a listed
unverified quote`.

Visible on L1 as the model correctly asking to remove `doc:lawyer_1`'s
paraphrased quote in **all three rounds** and being refused all three times.
This read as model failure in the logs and was entirely ours.

**Fixed:** `unverified_quotes=findings.unverified_quotes` in the loop, and
freshly derived for the review stage.

## 5. The review stage manufactured the defects the loop exists to remove

It added `doc:admin_action_occupational_1990` with a class, a label, a date and
a verbatim quote — and no deciding authority, no party. Both are findings. The
review runs last, so every gap it leaves was permanent.

Restating the obligation in the prompt did not fix it; the stage had already
been given `facts.txt`, which spells both out at length.

**Fixed:** `drop_unanchored_additions` now refuses a group minting an event
unless the same group supplies **a supporting quote, a `hasCourt`, and a
complete participation** — a participation node carrying both
`participatingParty` and `hasPartySide`. A bare `hasParticipation` link to an
empty node does not count; that is the party-less-participation defect under a
different name. Once enforced, the model supplied all three unprompted on the
first attempt and nothing was ever refused.

## 6. The stop rule killed rounds that were working

The loop stopped when the finding count failed to fall. That rule was written
for the stalled swap — a round trading one violation for another forever — but
cannot tell it apart from real work. Round 2 applied **27 operations**, netted
12 → 13 findings, and round 3 was killed.

Deep repairs legitimately raise the count on the way down: splitting one
conflated event into two correct ones creates a node that is briefly missing its
court and its parties.

**Fixed:** stop only on a round that applies **zero** operations. That round has
genuinely nothing to say — the next round sees the same graph and the same
findings — and three rounds caps the rest.

## 7. The quote ban was too blunt for the commonest repair

The loop may not mint evidence: it cannot see the document, so any passage it
composes is one it chose for a fact it did not read. That principle is right and
stays.

But splitting a conflated event in two is the single commonest repair the loop
performs, and the new node needs an anchor — the passage already sitting on the
node being split. On L1 the loop minted the new event with a court, a date and a
participation, offered the parent's own quote, was refused, and the node landed
permanently unanchored: a fresh SHACL violation created by the pass meant to
remove them.

**Fixed:** an anchor may be **moved, never minted**. An `add` of
`hasSupportingQuote` is permitted when the text is equal to, or a contiguous span
of, some `echr:hasSupportingQuote` already in the graph (`_quote_in_pool`). The
ban's actual point survives — the objection was never to the characters, it was
that a stage which cannot read the document has no basis for choosing a passage.
Reused text was chosen by extraction, which did read it, and a substring of a
verifying quote verifies too. This can neither fabricate a span nor promote one
that never appeared in the source.

## 8. The referential-integrity guard was destroying the patch's best work

An `add` whose object is a `doc:` node was refused unless that node existed
already or was minted by an `add rdf:type` op in the same patch. The model
creates a participation by giving it `participatingParty` and `hasPartySide` —
fully specifying it — without a separate type op, since the range of
`hasParticipation` already entails the class.

Result: **eight `add ... echr:hasParticipation` ops refused as dangling in a
single round.** The guard was silently discarding exactly the repair it was
meant to protect.

**Fixed:** any `add` op makes its subject a node the patch is building. The guard
exists to catch references to nodes *nobody* creates.

## 9. A SHACL message described the wrong defect

`ParticipationAtomicShape` combined `minCount 1`, `maxCount 1` and
`sh:class echr:Party` under one message: *"a participation must record exactly
one party"*. When the target was an authority never also typed `echr:Party`, the
finding reported a cardinality problem on a node that plainly had exactly one
value — and the model dutifully re-added the link it already had, three rounds
running.

**Fixed:** the class constraint is split into its own `sh:property` block naming
the actual repair — add `echr:Party` as a second `rdf:type` on the same node
rather than creating a duplicate. The model performed it correctly as soon as it
was told what the fix was.

This one is worth generalising: **a finding the model cannot act on is worse
than no finding**, because it consumes a round and produces a no-op that looks
like refusal.

## 10. Nothing inspected the review stage's output

The review is the last stage, so a shape it broke stayed broken.
`drop_unanchored_additions` catches the three gaps visible structurally; SHACL
catches the rest, and there was no pass left to hand them to.

**Fixed:** one further loop round after the review, run only if findings remain.

---

# Sweep: all five gemma jsonld arms

`run_new_repair.sh`, 50 documents, 2,891s total, into `repaired_v2/` so the old
`repaired/` output survives for comparison.

| arm | raw | old repair | **new repair** |
|---|--:|--:|--:|
| o2_low_jsonld | 188 | 122 | **8** |
| o2_med_jsonld | 85 | 51 | **6** |
| o2_cf_low_jsonld | 50 | 34 | **2** |
| o2_cf_med_jsonld | 27 | 5 | **1** |
| o2_large_jsonld | 11 | 4 | **2** |
| **total** | **361** | **216** | **19** |

The old pipeline removed 40% of violations; this removes 95%.

## Content, which the gate cannot see

Conformance is trivially achievable by deletion, so every axis deletion would
move the wrong way is reported beside it. New repair is the larger graph on
every content axis in every arm:

| arm | stage | triples | events | participations | quotes | follows | unverified quotes |
|---|---|--:|--:|--:|--:|--:|--:|
| o2_low | raw | 2794 | 125 | 128 | 284 | 69 | 7 |
| | old | 2801 | 133 | 154 | 284 | 75 | 0 |
| | **new** | **3035** | **147** | **187** | **305** | **93** | **0** |
| o2_med | raw | 1845 | 96 | 80 | 124 | 52 | 3 |
| | old | 1888 | 101 | 91 | 127 | 54 | 0 |
| | **new** | **2128** | **110** | **145** | **142** | **66** | **0** |
| o2_cf_low | raw | 2819 | 174 | 146 | 318 | 106 | 5 |
| | old | 2998 | 173 | 192 | 317 | 106 | 1 |
| | **new** | **3275** | **175** | **201** | **322** | **111** | **0** |
| o2_cf_med | raw | 1622 | 95 | 73 | 142 | 59 | 3 |
| | old | 1737 | 91 | 108 | 142 | 59 | 0 |
| | **new** | **1950** | **103** | **120** | **153** | **66** | **0** |
| o2_large | raw | 1158 | 60 | 38 | 78 | 39 | 0 |
| | old | 1226 | 60 | 52 | 78 | 39 | 0 |
| | **new** | **1424** | **76** | **56** | **94** | **53** | **0** |

Unverified quotes reach zero in all five arms, including `o2_cf_low` where the
old pipeline left one.

`persons` is the one axis that falls (o2_low 51 -> 36, o2_med 27 -> 18). That is
merging, and the merges were read individually: they are duplicate IRIs for the
same applicant (`doc:applicant_1` <- `doc:aleksanderMatko`), the same court
(`doc:supremeAdministrativeCourt` <- two further IRIs), the same organisation
(four IRIs for the European Space Agency in `o2_low` L2). The duplicate-guard
refused 15 further merges in the L1 arm run.

## Caveats

- **`o2_large_jsonld` arrived nearly clean** (11 violations over ten documents),
  so its 11 -> 2 says little. It is the matched-assembly arm and the only one
  that can carry an ontology claim, so it is worth stating plainly that the gate
  had almost nothing to do there.
- **Run-to-run variance is real at temperature 0.** The same arm scored 6 in a
  scratch run and 8 in the sweep; vLLM batching is not bit-deterministic.
  Differences of one or two violations between arms should not be read.
- **Split-then-merge churn is invisible to the gate** -- see above.


---

# Is OntoCast's native SHACL gate compatible with `echr-shapes.ttl`?

Yes -- with two configuration gaps that had to be fixed to prove it, and one
real, pre-existing OntoCast bug this test surfaced independently of shapes.

## Test

`run_native.py` (native OntoCast, in-process), gemma-4-31b, input.L1
(*Stanev*), `o2_low_jsonld`-equivalent settings (jsonld ontology context, no
chunking), `FACTS_SHACL_AUTOFIX=prune` (default), `FACTS_SHAPES_DIR` pointed at
a copy of `ontology/echr-shapes.ttl`.

## Result: exact match

OntoCast's own `input.facts.validation.json` reports **19 SHACL violations**
under `FactsValidationReport`. Diffing OntoCast's finding list against our own
standalone `repair_facts.find_shape_violations()` run on the identical
`input.facts.ttl`: **the 19 (subject, predicate, message) triples are
identical, in both directions.** Not a coincidental count match -- the same
node-level defects, reported the same way. `echr-shapes.ttl` is plain Core
SHACL (no `sh:sparql`), so it needed no `shacl_advanced` support and none of
OntoCast's SPARQL-constraint machinery.

## Two configuration traps, both silent

1. **`ONTOCAST_ONTOLOGY_DIRECTORY` must contain ONLY `echr.ttl`.** Our repo
   points it at `ontology/`, which also holds `echr-shapes.ttl`. OntoCast's
   seed-ontology scanner treats every `.ttl` file there as a candidate
   ontology, and a shapes file with no `owl:Ontology` declaration syncs as a
   null-IRI placeholder ("Cannot add ontology without valid IRI"). Fetching it
   back then fails ("Fetched 1 of 2 ontology graphs; 1 failed. The catalog is
   incomplete"), so the whole assembled ontology context is discarded and the
   validation gate falls back to empty. Fix: point `FACTS_SHAPES_DIR` and
   `ONTOCAST_ONTOLOGY_DIRECTORY` at two separate directories.
2. **A polluted Fuseki `ontologies` dataset does not self-heal.** Once the
   null-IRI graph above lands in Fuseki, it is content-addressed and persists
   across runs regardless of what the seed directory later contains, because
   the fetch step lists every named graph in the dataset, not just the current
   seed set. Recovering an already-polluted project requires deleting the
   specific named graphs (`DELETE .../data?graph=<uri>`, URL-encoded) rather
   than re-running with a fixed config.

## A bug this surfaced, unrelated to shapes

Every document in this project's runs -- confirmed by grepping the ORIGINAL
`o2_low_jsonld/raw/extract.log`, not just this test -- logs:

    Validating facts against an empty ontology context (no ontology context
    was assembled); every extracted term is outside the catalog.

`_facts_aggregation_inputs` (`node_factories.py:664`) only populates the
ontology context handed to the validation gate from
`build_merged_document_ontology_context`, which reads `reduced_ontology_artifacts`
-- populated by a separate "ontology consolidation" stage that never runs in
this project's `fixed_single_ontology` fact-extraction setup (one fixed
catalog, no dynamic ontology authoring). So OntoCast's own documented
rationale for mixing the ontology into validation --  "a facts graph states a
value uses `unit:DAY`; that the individual IS a `qudt:Unit` is stated only in
the catalog" (`facts_invariants.py:1220`) -- has never actually applied to any
run in this project. It happened not to matter for THIS shapes file because
`echr-shapes.ttl` is written defensively: `sh:targetClass` lists all four
`DomesticEvent` subclasses explicitly rather than relying on `rdfs:subClassOf`
entailment, and every closed vocabulary is inlined via `sh:in` rather than
pointing at catalog individuals. A future shape that DID rely on either would
silently under-fire in this pipeline as currently wired, with no warning
beyond the one line above (which nothing in this project's tooling has been
watching for).

## Bottom line

`echr-shapes.ttl` runs correctly and identically inside OntoCast's native gate
once shapes and ontology are separated into distinct directories. It was never
running in any prior experiment (`FACTS_SHAPES_DIR` was unset everywhere), so
adopting it natively is a config change, not a compatibility problem.

`shacl_autofix=prune` (the default) applied **zero** repairs here, and that is
correct, not a gap: autofix only retypes a literal against `sh:datatype`,
resolves a literal to a catalog IRI under `sh:class`/`sh:nodeKind`, or prunes a
node that violates `sh:minCount` while asserting nothing itself. All 19
findings here are `MaxCountConstraintComponent` (18, mostly the duplicate
`rdfs:label` / doubled functional-property pattern) or a `ClassConstraintComponent`
on an EXISTING node that needs a second `rdf:type`, not a literal to resolve.
None of those three mechanisms touch either case -- picking which of two labels
or two courts to discard, or adding a type to a node that already carries real
data, is exactly the judgment call OntoCast's autofix is deliberately built to
leave alone (`facts_invariants.py`: "No repair invents a value ... filling it
in would be fabrication, and dropping it would be data loss"). It is a
LLM-free code repairer for a different, narrower class of defect than
`new_repair.py` targets; the two are not interchangeable, and this document's
own fixes (quote reuse, participation completeness, merge) are precisely the
judgment-requiring repairs OntoCast's autofix is not designed to attempt.

---

# 2026-08-25 (later): the OntoCast SHACL gate, and a measurement correction

## The gate is now wired in

`run_arms.sh` enables OntoCast's own facts gate (`FACTS_SHAPES_DIR`). Three
things had to be right, and each fails silently on its own:

- **Two single-file staging directories.** OntoCast globs `*.ttl` in
  `ONTOCAST_ONTOLOGY_DIRECTORY`; a shapes file has no `owl:Ontology` IRI, so it
  syncs under a null IRI and the next fetch reports "Fetched 1 of 2 ontology
  graphs; 1 failed. The catalog is incomplete" -- discarding the WHOLE ontology
  context. `ontology_seed/` and `shapes/` are now staged separately under
  `EXPERIMENT_DIR`, which also records exactly what each run used.
- **Fuseki purge.** Those null-IRI graphs are content-addressed and survive
  across runs, because the fetch lists every named graph in the dataset rather
  than the current seed set. `fuseki_purge_foreign_ontology_graphs` deletes them
  in preflight. It uses python3, not curl: the URIs contain `#`, which curl
  strips as a fragment, so the DELETE lands elsewhere and 404s while appearing
  to succeed.
- **`pyshacl` in the ONTOCAST venv**, not ours. Without it the gate logs a
  warning and reports nothing -- the same silent "clean" this change removes.

Verified on a fresh L1 extraction: `shacl_evaluated: true` (was `null` in every
prior run, which reads as clean and means never checked).

## The gate does repair -- now that dates are typed

Autofix applied **7 `shacl_retype` repairs**, all `hasGenderCueText` from
`"..."@en` to `"..."^^xsd:string`. My earlier measurement of zero repairs was
taken before the date-precision rule was added to `facts.txt`; `sh:datatype`
retyping is one of the three things autofix can do, so the gate is no longer
inert. It still refuses everything requiring judgment (which of two labels to
drop), which remains `new_repair.py`'s job.

## A real blind spot in BOTH validators

`NaturalPersonAtomicShape` targeted `echr:NaturalPerson` only.
`echr:LegalRepresentative rdfs:subClassOf echr:NaturalPerson` is declared in
`echr.ttl` -- but SHACL only resolves `sh:targetClass` through
`rdfs:subClassOf` when the ontology graph is available, and it is not, in
either validator:

- `repair_facts.find_shape_violations` calls pyshacl with `ont_graph=None`,
  `inference="none"`;
- OntoCast's gate assembles an EMPTY ontology context in this pipeline. The
  warning "Validating facts against an empty ontology context" appears in every
  `extract.log` in `results/`, including runs predating any of this work. Root
  cause is `_facts_aggregation_inputs` (`node_factories.py:664`), which sources
  the context from `reduced_ontology_artifacts` -- populated by an ontology
  consolidation stage that never runs in `fixed_single_ontology` mode.
  Separating the directories did NOT fix this; it is a distinct bug.

Measured on a fresh L1: four lawyer nodes typed only `echr:LegalRepresentative`
carried `hasGenderCueText` as a language-tagged literal and were **invisible**,
while the identical defect on explicitly-typed `NaturalPerson` nodes was caught
and repaired.

Fixed by enumerating the subclass in `sh:targetClass` (one triple, +6
violations caught on that document), NOT by enabling RDFS inference. Turning
inference on fixes the targeting but makes every `sh:class` constraint vacuous:
`rdfs:range` entailment supplies the very types those constraints check for, so
`hasParticipation must point at a node typed echr:Participation` can never
fail. Measured both ways on the same graph: inference off 24 violations,
inference on 22 -- 6 checks silently lost, 9 gained. Neither setting dominates;
enumerating subclasses gets the gain without the loss.

## MEASUREMENT CORRECTION: 361 -> 19 is obsolete

The shapes file has since grown by 215 lines and 9 shapes (`PartialDateShape`,
`ProceedingLinkShape`, `PartyLinkShape`, and five date-order shapes). Re-scoring
the SAME `repaired_v2` outputs:

| arm | HEAD shapes | + LegalRepresentative fix | current shapes |
|---|--:|--:|--:|
| o2_low_jsonld | 8 | 8 | 94 |
| o2_med_jsonld | 6 | 6 | 47 |
| o2_large_jsonld | 2 | 2 | 64 |
| o2_cf_low_jsonld | 2 | 2 | 109 |
| o2_cf_med_jsonld | 1 | 1 | 39 |
| **total** | **19** | **19** | **353** |

Raw inputs under the current shapes total **453**, so the sweep's real headline
is **453 -> 353 (22%)**, not 361 -> 19 (95%).

Both numbers are honest measurements; they score different things. The repair
loop optimised against the shapes that existed when it ran, and has never been
shown the date-precision, link-typing or chronology constraints -- it cannot fix
what it was never told about. The `LegalRepresentative` fix contributes zero
here because these extractions predate the gender-cue prompt entirely and
contain no `hasGenderCueText`.

**Consequence: the sweep must be re-run against the current shapes before any
of these graphs are judged, and 361 -> 19 must not be quoted.**
