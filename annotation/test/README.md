# The 10-document test set — pre-filled drafts

Annotation structure for the 10 pilot documents the ablation arms were developed
on (`data/art6_domestic_test_set.json`, selected by
[`art6/data/build_ontocast_test_set.py`](../../art6/data/build_ontocast_test_set.py)).

**These are drafts to correct, not a standard.** Each `<case_id>.yaml` was
pre-filled from the source text against
[`annotation_guide.txt`](../annotation_guide.txt) v1.1. Read the source, then
delete what does not belong, add what is missing, and fix what is wrong.

| | |
|---|---|
| `<case_id>.yaml` | one pre-filled draft per case, `mode: assisted` |
| `../test_text/<case_id>.txt` | the source text, byte-identical to the pipeline's input |
| `manifest.json` | the 10 documents, with the `L1`..`L10` line ids used in `results/ablation_test/` |

## How this differs from `gold/`

`gold/` is the **calibration set**: 20 documents annotated cold, source text
only, because a standard produced by correcting model output is not a standard
(see [`../README.md`](../README.md)). This set is not that. It is pre-filled, so
it inherits whatever the drafting got wrong and cannot be used to measure
agreement with a model. Its purpose is to give the 10 pilot documents a
human-checked reference at a fraction of the reading cost.

If any of these documents ever needs to carry inferential weight, re-annotate it
cold under a fresh filename rather than correcting the draft.

## What to check first

Every draft's `document_note` ends with the calls most likely to be wrong, named
explicitly. Those are the fastest place to start. Across the set the recurring
ones are:

- **`final_domestic_decision`.** §5.1 says a chain still pending when the
  application was lodged gets no flag. Applying that literally leaves
  `001-248395` with none at all despite an express "final decision of the Supreme
  Court", and leaves the four 2018 Conseil d'État judgments in `001-202468`
  unflagged. Both are argued in the file.
- **Private agreements.** §4 says a thing that types as none of the four
  subclasses is not an event; §4.4 says record signed private agreements and flag
  them. The line drawn here is that an agreement which *terminates a proceeding*
  is recorded (`001-58299` id 6) and one that *creates the underlying legal
  relationship* is not (the seven contracts in `001-189419`). Reject the line and
  `001-189419` gains seven records.
- **Interlocutory rulings.** Rulings on how a proceeding is run are steps;
  rulings that create or remove a right — bail, detention, recusal, refusal of a
  lawyer — are events. See `001-5878`, `001-22669` and `001-188177`.
- **Same court, same day, two decisions.** §8's identity rule merges these
  wrongly. Four pairs in the set: `001-248393` ids 19/20, `001-202468` ids 19/20
  and 25/26, `001-58299` ids 4/5.
- **Undated acts that cannot be recorded.** `001-5878` loses its indictment, its
  directed acquittal on two counts and its abuse-of-process application this way;
  `001-248395` loses the complaint that started the case.

## Checking a file

```bash
uv run python -m art6.data.check_annotations --dir annotation/test --text-dir annotation/test_text
```

Verifies that every quote is verbatim against the source, that closed
vocabularies hold, that `follows` points at real ids, that ids run 1..n in order
and that every event carries a date. All 10 drafts pass. Run it after editing.
