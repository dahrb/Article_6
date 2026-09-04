# Annotation — the reference standard

A human reference for 20 ECtHR judgments, built to calibrate the LLM judge that
scores the ablation. This directory holds the standard, the templates, and the
source texts.

**If you are here to annotate, read [`annotation_guide.txt`](annotation_guide.txt)
and start on a file in [`gold/`](gold/). Nothing else here is needed.**

---

## Why this exists

The evaluation measures precision and recall of extracted domestic proceedings.
Precision can be checked against the output. **Recall cannot** — it needs a list
of what the document actually contains, which exists nowhere in any pipeline
output.

The judge supplies that at scale: its first pass annotates the document itself,
blind to every arm, before its second pass compares that reference against an
arm's output. These 20 documents are what establish whether the judge's
references can be trusted — they are the **calibration set**, not the sample the
results are computed on.

That is why they are annotated **cold**: source text only, no pipeline output, no
model-generated draft. A standard produced by correcting model output is not a
standard, and agreement measured against it would not mean what it claims to.

---

## Files

| | |
|---|---|
| [`annotation_guide.txt`](annotation_guide.txt) | the standard. Grounded in `ontology/echr.ttl` v3.5.0 — every rule traces to a `skos:definition` or `skos:scopeNote` |
| [`annotation_schema.yaml`](annotation_schema.yaml) | commented template with four worked examples; each key mapped to the triple it stands for |
| [`evaluated_triples.yaml`](evaluated_triples.yaml) | what is scored, in which tier, and how borderline events are handled. **Scoring only — annotators do not need it** |
| `gold/` | 20 blank templates, one per case, plus `manifest.json` |
| `gold_text/` | the 20 source texts, byte-identical to what the pipeline was given |

The guide deliberately contains no reference to judges, arms or scoring. It is an
annotation guide, not a judging guide, and knowing how a field is used downstream
would only bias the annotation.

---

## Annotating

1. Read `annotation_guide.txt` once. Keep §4 (which events to record) and §6
   (controlled vocabularies) open while you work.
2. Open a case in `gold_text/<case_id>.txt` and read the **whole** judgment.
3. Fill in `gold/<case_id>.yaml`, one record per domestic event, ordered by
   decision date.

Quotes must be **copied from `gold_text/`**, not retyped. Those files are
byte-identical to the pipeline's input, so a pasted span verifies against exactly
the string the arms were extracting from.

Do not open any pipeline output until the file is finished.

### Two rules that are easy to miss

**When you cannot decide whether something belongs**, record it and set
`borderline: true` with a one-line reason (guide §4.4). Do not invent a rule.
Whether investigative steps and private arrangements count is genuinely
unsettled, and flagging is how that stays visible instead of becoming a silent
decision — every metric is later reported both with and without flagged events.

**`guide_version` is stamped in every template.** If a rule turns out to be wrong
partway through, finish the current document under the existing rule, then change
the guide, bump the version, and re-annotate what it affects. A standard applied
inconsistently across its own documents is not a standard.

---

## The sample

20 judgments, seed 42, drawn from the completed 250-document evaluation sample
and **nested inside it**, so every annotated document already has output under
all five arms and the judge can be calibrated on exactly these documents.

| court level | n |
|---|---:|
| CHAMBER | 7 |
| COMMITTEE | 7 |
| GRANDCHAMBER | 6 |

Even across level — 20 does not divide by 3, so the remainder goes to levels in
alphabetical order, which is arbitrary but fixed and printed. Within a level the
draw is stratified over period. Periods come out 7 / 7 / 6; 13 respondent states;
median 13,464 characters, total 338,565.

Collapsed to one document per `case_group` first: the parent sample deliberately
keeps both members of the two Chamber/Grand Chamber pairs, but annotating both
means reading the same domestic history twice for one document's worth of
reference. `--keep-pairs` disables that.

```bash
uv run python -m art6.data.build_annotation_sample --n 20 --seed 42
```

Re-running skips templates that already exist, so annotations are never
clobbered. It regenerates `gold/` and `gold_text/` together.

**`001-59859` has no C0 output** — the one document in the run where the baseline
produced nothing parseable. It was kept rather than redrawn, because redrawing to
avoid it would be selecting on outcome. C0 contrasts therefore run at n=19 on
judged metrics.

---

## What happens to these files

1. A lifter converts each YAML to a gold `.ttl`, validated against
   `ontology/echr-shapes.ttl`.
2. The judge's first pass runs over the same 20 documents, producing its own
   reference lists under the same guide.
3. Agreement between the two — reported separately for flagged and unflagged
   events — decides whether the judge tier carries inferential weight.

The threshold for that decision is fixed **before** the comparison is run. See
[`docs/eval_phase_checklist.md`](../docs/eval_phase_checklist.md).
