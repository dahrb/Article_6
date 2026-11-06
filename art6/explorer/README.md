# Domestic proceedings explorer

Local Streamlit demo for [issue #7](https://github.com/dahrb/Article_6/issues/7).
No extraction services, credentials or model calls are needed.

From the repository root:

```bash
uv sync --extra viz
uv run --extra viz streamlit run art6/explorer/app.py
```

Search by case name or ID, choose a respondent country, and filter the events by
significance, court/authority, outcome, direction, instance level or track.
Read courts, dates, instance levels, proceeding types and outcomes directly on
the event cards. Select a card to highlight its direct connections and inspect
its quotation, parties and extracted fields in the side panel. Use the track
selector, drag/scroll, zoom, or overview navigator to explore longer chains.
Download the diagram as SVG, or the filtered event table as JSON.

The viewer uses plain SVG, with no WebGL, external scripts or animation loop.
Automatic file watching is disabled in `.streamlit/config.toml` because the
repository includes a large corpus and experiment tree. Restart Streamlit after
source edits. The setting avoids recursive watching; it does not establish the
cause of any previous machine crash.

## Data and selection

`data/validation_demo.json` contains payloads from two saved stage-1 runs on the
same ten validation cases. It includes the original artifact paths and SHA-256
hashes. It preserves the extracted values, including generic authority names and
unknown outcomes. It contains no gold annotations, model credentials or run logs.

On 2026-11-06, compared all 14 complete configurations under
`results/exp_stage1/full` using the current working scorer, the ten
`annotation/test` cases and the experiment's **major-only** view:

```bash
CORE=1 PYTHONPATH=. uv run python results/exp_stage1/scripts/review.py summary \
  vote9_v9_luna=results/exp_stage1/full/vote9_v9_luna \
  vote9_v9b_luna=results/exp_stage1/full/vote9_v9b_luna \
  vote_luna4=results/exp_stage1/full/vote_luna4 \
  v9_luna=results/exp_stage1/full/v9_luna/s1
```

The experiment script is local and untracked; this command documents the source
of the selection, and is not needed to run the packaged demo. The scorer itself
has local changes at selection time; these figures describe that working state.

| Configuration | Major events | Event alignment F1 | Medium-match F1 | Follows F1 (rounded) | Part-of F1 (rounded) |
|---|---:|---:|---:|---:|---:|
| **vote9_v9_luna (default)** | 212 | **89.1** | **78.9** | 64 | 71 |
| vote9_v9b_luna | 210 | 88.0 | 77.7 | 66 | 72 |
| vote_luna4 | 195 | 88.3 | 73.4 | 62 | 74 |
| v9_luna, original | 222 | 87.3 | 77.4 | 58 | 65 |

The other complete configurations compared were deepseek, luna, v7_deepseek,
v7_luna, v8_deepseek, v8_luna, v9_deepseek, v9b_deepseek, v9b_luna and
v9c_deepseek. Event alignment F1 ranged from 79.8 to 85.6; medium-match F1
from 66.0 to 75.4. Partial per-case reruns were excluded.

The default is a single consistent run, chosen by highest event alignment F1
(also highest medium-match F1), rather than a per-case mixture. Some variants
score higher on links. These are development-set scores, not held-out results.
The voting exporter explicitly marks retained events as major; it does not
contain minor events. The original run is separately selectable and has
222 major + 43 minor events. Switching runs replaces the entire graph; it does
not mix voted links with original-run minor events.

Repackage from the existing local results, without extraction:

```bash
uv run python -m art6.explorer.prepare_demo
```

## Reading the timeline

- Rows are extracted track labels. Events without membership may inherit it
  through `part_of`; otherwise they remain unassigned. Larger tracks appear first.
  Cards read chronologically within each row, with separate slots for events on
  the same date. Horizontal spacing is a **sequence view, not a duration scale**;
  columns in different tracks do not imply the same date.
- The coloured level badge follows the static figures' visual language: I, AR,
  FI, AP, CA, CR and RO, with the full level name alongside it. Cards show the
  proceeding type and major/minor label; minor cards also have dashed borders.
- Every card shows its outcome in text. Direction uses a symbol and text:
  green ▲ favourable, red ▼ unfavourable, amber ◆ mixed, relative to the
  applicant. Missing outcomes/directions are explicitly labelled as unrecorded.
- Each event's relation fields reference another event. Arrows point from the
  referenced event toward the referring event. Follows, part-of and occurs-during
  have distinct line styles. Only the original links with both endpoints visible
  are drawn; filtering never invents replacement links.
- Partial dates retain their year/month precision on the card. Decision date
  takes precedence over start date. Events without usable dates remain in their
  track, labelled as undated and placed after dated events; their position does
  not infer a date. No events are discarded to make the diagram fit.
- Selecting a card highlights only its direct neighbours. The side panel lists
  incoming and outgoing relations as navigable links, and identifies unavailable
  endpoints. Following a connection outside the focused track opens all tracks.
  Keyboard: Tab to an event, Enter to inspect, left/right to move within a track,
  Escape to clear. Overview fits the whole diagram; Read restores full-size cards.
- Descriptions are extraction summaries; supporting quotations are separate.
  Neither is generated by the explorer. These extractions can contain errors.

## Verification and next iteration

```bash
uv run --extra viz python -m unittest discover -s tests -p 'test_explorer*.py'
```

Next iteration: country → court → case links and richer cross-case navigation,
including an explicit policy for resolving variant/generic court names. Country
currently means the extracted **respondent state**, not an inferred jurisdiction
for every event. The first demo only filters cases by it. Reasonable-time overlays,
maps and cross-case graph views are outside this first iteration.
