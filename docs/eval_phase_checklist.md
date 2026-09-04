# Evaluation phase — checklist and status

Companion to `docs/JURIX_2.md` (conditions, sample, contrasts). This file covers
the evaluation only: what is measured, by what, and what is done so far.

**Status: design complete, annotation not started.**
**Next action: annotate the 20.** Nothing else is blocked on anything but that.

---

## The protocol in three lines

Precision and recall need different instruments.

- **Mechanical** measures structure — free, all 250 × 5 arms, no model in the loop.
- **The judge** measures precision *and* recall, because pass 1 has it produce its
  own reference list before it ever sees pipeline output.
- **You** measure the judge — 20 cold annotations are the standard the judge is
  calibrated against, not the sample the results are computed on.

The judge scales to the full sample; your 20 are what license it to.

---

## Artefacts

| file | what it is |
|---|---|
| `annotation/annotation_guide.txt` | the annotation standard, grounded in `ontology/echr.ttl` v3.5.0 |
| `annotation/annotation_schema.yaml` | commented template, four worked examples, every key mapped to its triple |
| `annotation/evaluated_triples.yaml` | what is scored, in which tier, and how borderline events are handled |
| `annotation/gold/` | 20 blank templates + `manifest.json` |
| `annotation/gold_text/` | the 20 source texts, byte-identical to pipeline input |
| `art6/data/build_annotation_sample.py` | the draw — seed 42, reproducible |
| `art6/ontology/diagnostics/ablation_auto_eval.py` | tier 1, already implemented |

---

## Design decisions — settled

- [x] Three tiers: mechanical / judge / human, with nothing mechanically checkable sent to a judge
- [x] **Two-pass judge.** Pass 1: source + guide → its own reference list, structurally blind to every arm. Pass 2: source + reference + one arm's output → alignment and residual adjudication. Six calls per document (1 + 5 arms).
- [x] **The judge aligns; a script counts.** The model emits correspondences, never a metric.
- [x] **No bundling of arms into one pass-2 call.** ~£24 cheaper across the whole study, and it converts independent alignment into side-by-side comparison in the direction of the headline contrast.
- [x] **Evaluated triple set** — the event, the authority that decided it, the parties to it. Tier A comparable across all five arms; tier B graph arms only; tier C mechanical. See `evaluated_triples.yaml`.
- [x] `echr:isFinalDomesticDecision` scored, tier B, **per chain not per document**
- [x] **Borderline events are flagged, not legislated.** Every metric reported twice — `core` (excluded) and `full` (included). If a conclusion flips between them, that range *is* the finding.
- [x] Guide contains no evaluation framing — it is an annotation guide, not a judging guide

---

## Runs

- [x] 250 judgments × 5 arms complete — `results/ablation_250_mv1/.done`
- [ ] **Tier 1 auto-eval** — started, but `auto_eval.json` is not in the run directory. Check whether it finished or failed.

```
uv run python -m art6.ontology.diagnostics.ablation_auto_eval \
    --run-dir results/ablation_250_mv1 \
    --source-json data/art6_eval_sample_judgments_flat.json
```

---

## Yours

- [ ] **Annotate 20 cold** — `annotation/gold/`, sources in `annotation/gold_text/`
      0/20 done · 338,565 chars · ~12–15h
      Shortest `001-194305` (1,164 chars); longest `001-206582` (84,407 — a quarter
      of the set alone). Working up from the short Committee judgments is easier.
- [ ] **Fix the κ threshold in writing**, before the pilot runs. Deciding it after
      seeing κ is the thing to avoid.
- [ ] Decide the paper framing (see below)

---

## Mine — starts once ~5 annotations exist

- [ ] Lifter: annotation YAML → gold `.ttl`, validated against `ontology/echr-shapes.ttl`
- [ ] Judge harness, both passes, borderline instruction carried into pass 1
- [ ] Pilot against your first 5 — agreement, and a **measured** cost per document
- [ ] Choose judged sample size on that measurement, not on my estimate
- [ ] Full judge run
- [ ] Calibration report, paired contrasts, statistics

### Cost, estimated from real token counts

Mean document 3,644 tokens; guide 5,678; arm outputs 966–2,135. Per document per
judge ≈ 46k in / 5.5k out ≈ **$0.11**. Verify current rates before committing.

| judged sample | one judge | two judges |
|---|---:|---:|
| 50 | £4 | £9 |
| 60 (20 per court level) | £5 | £11 |
| all 250 | £22 | £44 |

**Sample size is not the cost problem — uncapped reasoning is.** Output is half
the cost, and uncapped reasoning can multiply it fivefold. Cap it, and cache both
the guide (identical across all pass-1 calls) and the document (reused six times
per document).

If subsampling: **nest the 20 annotated documents inside it**, or you pay to run
the judge on them separately for the calibration.

---

## Statistics

- Unit of analysis is the **document**; every metric is a per-document rate
- Paired differences per contrast, win/tie/loss, Wilcoxon signed-rank, Holm-corrected across the four contrasts of `JURIX_2.md` §1
- C0 vs C1 reported separately — it sits outside the 2×2
- The 2×2 used as a 2×2: both main effects estimated twice, interaction reported either way
- Design weights on any population statement (Grand Chamber is 57% sampled against Chamber's 1.4%)
- Everything reported at both checkpoints — recall is a property of `raw/`, precision of `repaired/`

---

## Open, not blocking

**Decisions have no gold.** The 20 are all judgments, so the decisions bonus
experiment would be judged by an instrument calibrated only on judgments — and
decisions have a different section structure (`text_processing.py` routes
`PROCEDURE` into `facts` for decisions but not judgments). Either annotate ~5
decisions later, or state plainly that the decisions result rests on an
uncalibrated transfer. The second is defensible if said out loud.

**Second judge** — staged. Decide after the pilot gives you κ. With a single
gemma extractor, self-preference cancels within-document, so the second judge
buys reliability rather than bias control.

**`001-59859` has no C0 output** — the one document where the baseline produced
nothing parseable. Kept rather than redrawn, because redrawing to avoid it would
be selecting on outcome. C0 contrasts run at n=19 on judged metrics. Footnote it.

**Paper framing.** Content is long-paper shaped; a short paper is a defensible
risk call given solo annotation. If short, pick one claim — C1 vs C4 plus
evidence integrity — and let compression/repair support it rather than frame it.
The alternative is to make the *protocol* the paper, with the ablation as its
demonstration; more original, riskier as a contribution type.

---

## Sample

20 judgments, seed 42, drawn from the completed 250 and nested inside it.

| court level | n | design |
|---|---:|---|
| CHAMBER | 7 | stratified over period |
| COMMITTEE | 7 | stratified over period |
| GRANDCHAMBER | 6 | stratified over period |

Even across level (20 does not divide by 3; the remainder goes alphabetically,
fixed and printed). Periods 7 / 7 / 6, 13 respondent states, median 13,464 chars.
Collapsed to one document per `case_group` first — the parent sample keeps both
members of the two Chamber/Grand Chamber pairs, but annotating both means reading
the same domestic history twice. `--keep-pairs` disables it.

```
uv run python -m art6.data.build_annotation_sample --n 20 --seed 42
```

Re-running skips existing templates, so annotations are never clobbered.
