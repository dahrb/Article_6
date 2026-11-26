# Set annotator

A local web app for annotating a judgment's domestic proceedings in the set
model ([`annotation/annotation_guide.txt`](../annotation/annotation_guide.txt) v3.1)
without writing YAML by hand. It writes the guide's file format (section 8),
one file per judgment.

```bash
uv run python annotation_tool/server.py --annotator sacha
# then open http://localhost:8765 (opens by itself)

# another text folder, another port (two annotators on one machine)
uv run python annotation_tool/server.py --annotator david --texts annotation/test_text --port 8766
```

| option | default | |
|---|---|---|
| `--annotator` | required | your name, lower case; files go to `OUT/<annotator>/` |
| `--texts` | `annotation/gold_text` | a folder of `<case_id>.txt` judgments |
| `--out` | `annotation/annotators` | where each annotator's folder is made |
| `--port` | 8765 | |

Python 3.12 and the repository's environment (`uv sync`); no other installs.
Nothing is sent anywhere: the server listens on `127.0.0.1` only.

## How it works

- **Left:** the judgment. Every quote you have recorded is highlighted in its
  set's colour; click one to open its event.
- **Right:** your sets of proceedings, each with its start act and its decisions
  in derived order (the start, then by date).
- Select the sentence that narrates an event (or <kbd>Alt</kbd>+click anywhere
  in it), then press <kbd>S</kbd> for the active set's start act or <kbd>D</kbd>
  for a decision. The quote is filled in from the selection and the date from
  the first date in it; check both.
- With an event open, select text and press <kbd>B</kbd> (body), <kbd>P</kbd>
  (add a party), <kbd>T</kbd> (date) or <kbd>Q</kbd> (replace the quote).
- Closed lists are drop-downs with the guide's glosses as tooltips; matter is a
  row of toggles; `appealed_by` and the appeal date appear once `reviews` is set.
- **Guide** opens the full guide beside the text; **Keys** lists the shortcuts.

Every change saves within a second to `OUT/<annotator>/<case_id>.yaml`. The
**Checks** box shows what `art6/model.py` flags: the test suite's schema
checks, plus every quote found verbatim in the text. It never blocks a save, so
a half-done draft is fine. Tick **Done** when a document is finished.

`OUT/<annotator>/_status.yaml` records, per document, the done flag, the number
of events, the open problems and the **active annotation time** (the clock
pauses when the tab is hidden or after two idle minutes). That is the per-document
time cost for the paper.

## Blind double annotation

One server serves one annotator, and it shows only the text and that
annotator's own files: no other annotator, no model output, no old gold. Each
annotator runs their own copy (or their own `--port`) and does not open the
other's folder until both are done. Agreement is then:

```bash
uv run python -m art6.evaluation.agreement sacha=annotation/annotators/sacha david=annotation/annotators/david
```

and any file can be checked on its own with

```bash
uv run python -m art6.model annotation/annotators/sacha --text-dir annotation/gold_text
```

## Files

```
server.py          the local server: texts in, YAML out, checks via art6/model.py
static/index.html  the page
static/app.js      the editor (no framework, no network beyond the local server)
static/style.css
```
