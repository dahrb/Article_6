# Annotation

**To annotate:** read [`annotation_guide.txt`](annotation_guide.txt), then run the
app: `uv run python annotation_tool/server.py --annotator <name>`
([`annotation_tool/README.md`](../annotation_tool/README.md)).

| | |
|---|---|
| [`annotation_guide.txt`](annotation_guide.txt) | the standard: the set model, guide v3.1 (ontology 4.1.0) |
| `test_simple/` | the set-model gold for the 10 development documents (the 10-set), the default gold of `art6.evaluation.score` |
| `test_text/` | the 10-set source texts, byte-identical to what the pipeline is given (untracked; rebuild with `art6.data.build_ontocast_test_set`) |
| `gold/manifest.json` | the 30-document test set (gold30): how it was drawn |
| `gold_text/` | the gold30 source texts (untracked; rebuild with `art6.data.build_annotation_sample --n 20 --seed 42`) |
| `annotators/<name>/` | each annotator's files, written by the app (made on first use) |
| `court_periods.yaml` | the Court's own reasonable-time periods, the label for `art6.evaluation.score_reasonable_time` (untracked, hand-drafted) |

Legacy, to be replaced: `test/*.yaml` and `gold/*.yaml` are the guide-v1
annotations, and `annotation_schema.yaml` their template. `test_simple/` was
derived from `test/` (`mapping.yaml`); gold30 will be re-annotated in the set
model, double and blind, with the app.

Check any set-model files:

```bash
uv run python -m art6.model annotation/test_simple --text-dir annotation/test_text
```
