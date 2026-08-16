# Data — collection & processing

Everything in this project starts from the ECHR's [HUDOC](https://hudoc.echr.coe.int/)
database. This directory holds the data itself; the code that produces it lives in
the `art6` package at the repo root, which pulls Art. 6 case metadata and case text
from HUDOC, cleans them, and turns the raw HTML into a segmented JSONL corpus.

The pipeline is four stages:

```
HUDOC REST API
      │
      ▼
1. art6.data.collection          → raw metadata + per-case HTML
      │
      ▼
2. art6.data.metadata_processing → cleaned, enriched metadata (judges, countries, limb)
      │
      ▼
3. art6.data.text_processing     → sectioned JSONL text corpus
      │
      ▼
4. art6.ontology.build_ontocast_test_set → small hand-balanced extraction test set
```

Stages 1–3 are the reproducible core. Stage 4 is an optional sampler used for
ontology-extraction experiments.

## Before you start

```bash
uv sync          # installs deps and the art6 package itself, in editable mode
```

Every stage is a module, runnable from any working directory — all paths are
anchored on the repo layout, defined once in
[`art6/paths.py`](../art6/paths.py). Explicit path flags (`--input-json`,
`--input-dir`, …) are still interpreted relative to your current directory.

To run the whole pipeline, or a subset of stages, in order:

```bash
uv run python main.py                # all four stages
uv run python main.py --stages 2 3   # just metadata + text processing
uv run python main.py --list         # what each stage does
```

## 1. Collection — `art6/data/collection.py`

Queries the HUDOC REST API for all judgments and decisions citing Article 6,
sorts them by document type, downloads the case text HTML, and deduplicates.

```bash
uv run python -m art6.data.collection
```

What it does, in order:

| Step | Function | Result |
|---|---|---|
| Query metadata | `collect_cases()` | one record per case per language |
| Map application numbers | `appno_mapping()` | `individual_appno → ecli, itemid` lookup |
| Split by document type | `process_cases()` | judgments / decisions / screening-panel |
| Filter language | `sort_language()` | keeps `ENG` and `FRE` only, English first |
| Download case text | `retrieve_text()` | one `.html` per case, skips existing files |
| Verify coverage | `check_retrieval()` | reports ECLIs with no retrieved text |
| Deduplicate | `data_no_dupe()` | one row per ECLI, prefers English, adds `case_text_path` |

**The date range is deliberately frozen.** `collect_cases()` caps the final
query at `2026-02-16`, the date the dataset was originally collected, so reruns
reproduce the same corpus rather than silently growing. Edit `collect_cases()`
if you want more recent cases.

**Be patient and be polite.** The API is paged 1000 records at a time, in
two-year windows (HUDOC hard-limits any single query to 10,000 results). The
script sleeps 1s between metadata pages and 0.5s between HTML downloads. A full
run downloads ~49,000 HTML files and takes many hours. It is resumable — files
that already exist on disk are skipped.

## 2. Metadata processing — `art6/data/metadata_processing.py`

Cleans the raw metadata and enriches it with judge identities, standardised
country names, legal-concept labels, and a civil/criminal classification.

```bash
# judgments (defaults)
uv run python -m art6.data.metadata_processing

# decisions
uv run python -m art6.data.metadata_processing \
  --input-json  data/art_6_decisions_metadata.json \
  --output-json data/art_6_decisions_metadata_processed.json
```

| Flag | Default | Meaning |
|---|---|---|
| `--input-json` | `data/art_6_judgments_metadata.json` | raw metadata JSONL |
| `--output-json` | `data/art_6_judgments_metadata_processed.json` | processed JSONL |
| `--similarity-threshold` | `0.7` | fuzzy judge-name match cutoff (0–1) |

The main transformations:

- **Column pruning** — drops 16 low-value/low-coverage HUDOC fields (`DROP_COLUMNS`).
- **Application numbers** — splits the semicolon-delimited fields into lists and
  separates the case's own numbers (`case_appno`) from referenced ones
  (`secondary_appno`) and cited ones (`cited_appno`).
- **Code mapping** — `originatingbody` and `typedescription` numeric codes are
  resolved to human labels via the HUDOC mappings at the top of the script.
  Unmatched codes are printed, not silently dropped.
- **Judges** — case judge strings are matched against `data/additional_data/judges.csv`
  with `difflib.SequenceMatcher`. Exact (case-insensitive) matches score 1.0;
  everything else takes the best fuzzy match. Matches at or above the threshold
  populate `judges_id`; every score is kept in `judge_similarity_pct` so you can
  audit the join. Anything below the threshold is reported in
  `data/additional_data/unmatched_judges.txt`.
- **Countries** — `respondent` ISO codes resolved to names via `pycountry`.
- **Legal concepts** — `kpthesaurus` IDs mapped to labels via
  `data/mappings/key_labels.json`; unmapped IDs are printed.
- **Art. 6 limb** — `classify_art6_limb()` reads those labels and tags each case
  `Criminal`, `Civil`, `Constitutional`, `Mixed`, or `Unspecified`. This is
  keyword-based, so treat it as a strong heuristic rather than ground truth.
- **Legal system** — respondent countries mapped to `Civil` / `Common` / `Mixed`
  via `data/mappings/law_system_mapping.json`.

### Processed schema (28 columns)

| Field | Type | Notes |
|---|---|---|
| `itemid` | str | HUDOC document ID; joins to case text and the corpus |
| `ecli` | str | European Case Law Identifier; unique per case |
| `case_name` | str | full HUDOC document name |
| `appellant` | str | applicant name parsed from `case_name` (EN and FR forms) |
| `respondent` | str | respondent state ISO code(s) |
| `country_name` | list | respondent state name(s) |
| `law_system` | str | `Civil` / `Common` / `Mixed` |
| `judgementdate` | datetime | |
| `court_level` | str | HUDOC `doctypebranch` (`GRANDCHAMBER`, `CHAMBER`, …) |
| `originatingbody` | str | resolved chamber/section label |
| `judgment_type` | str | resolved `typedescription` label |
| `importance` | Int64 | HUDOC importance level (1 = highest) |
| `languageisocode` | str | `ENG` or `FRE` |
| `judges` | list | judge names as they appear in the case |
| `judges_id` | list | matched IDs into `data/additional_data/judges_processed.json` |
| `judge_similarity_pct` | list | match score per judge, parallel to `judges` |
| `separateopinion` | str | |
| `article` | list | ECHR articles engaged |
| `violation` | list | articles found violated |
| `nonviolation` | list | articles found not violated |
| `conclusion` | list | HUDOC conclusion strings |
| `kpthesaurus` | str | raw legal-concept IDs |
| `kpthesaurus_labels` | list | resolved concept labels |
| `article_6_limb` | str | `Criminal` / `Civil` / `Constitutional` / `Mixed` / `Unspecified` |
| `case_appno` | list | this case's application numbers |
| `secondary_appno` | list | other extracted numbers, excluding the case's own |
| `cited_appno` | list | cited-case numbers, excluding the case's own |
| `case_text_path` | str | relative path to the case HTML |

## 3. Text processing — `art6/data/text_processing.py`

Turns each case HTML into one JSONL record: a markdown rendering of the full
text, a set of named sections, and any extracted tables.

```bash
uv run python -m art6.data.text_processing --corpus judgments
uv run python -m art6.data.text_processing --corpus decisions
```

| Flag | Default | Meaning |
|---|---|---|
| `--corpus` | *required* | `judgments` or `decisions` |
| `--input-dir` | by corpus | source HTML folder |
| `--output-jsonl` | by corpus | output path |
| `--skip-empty-text` | off | drop cases with empty extracted text |
| `--limit` | none | process only the first N files (useful for smoke tests) |

Each output record is:

```json
{
  "itemid": "001-100002",
  "full_text": "markdown rendering of the whole document",
  "text_chunks": {
    "introduction": "...", "procedure": "...", "facts": "...",
    "legal_framework": "...", "law": "...", "reasons": "...", "appendix": "..."
  },
  "tables": [{"table_1": [{"col": "value"}]}]
}
```

**How sectioning works.** The HTML is flattened to markdown first — bold/all-caps
short lines become headings, numbered paragraphs keep their numbers — then a
single large regex matches section headers in both English and French
(`THE FACTS` / `EN FAIT`, `THE LAW` / `EN DROIT`, `FOR THESE REASONS` /
`PAR CES MOTIFS`, and many historical Commission-era variants). Text accumulates
into whichever section was last matched. `QUESTIONS TO THE PARTIES` sections are
matched and discarded.

Two behaviours worth knowing:

- For **decisions**, `PROCEDURE` content is folded into `facts` rather than kept
  separate, because decisions do not have the judgment's procedural structure.
- For **decisions**, empty-text cases are always skipped regardless of the
  `--skip-empty-text` flag ([`text_processing.py:74`](../art6/data/text_processing.py#L74)).
  Many historical decisions are conversion shells with no recoverable body text.

## 4. Extraction test set — `art6/ontology/build_ontocast_test_set.py`

Builds a small, deliberately balanced set of 10 English cases for ontology
extraction experiments — spanning every court level and covering violation,
non-violation, and inadmissibility outcomes.

```bash
uv run python -m art6.ontology.build_ontocast_test_set
```

Reads `sample_metadata.parquet`, filters to English cases whose facts section is
2,500–40,000 characters, then fills one slot per `(court level, outcome)` pair,
preferring an unseen respondent state and then the longest facts section.

Only `introduction`, `procedure`, and `facts` are exported. The Court's own
reasoning (`law`, `reasons`) is withheld deliberately so extraction is scored on
what the facts state about the domestic proceedings, not on the Court's
conclusions. Output goes to `data/art6_domestic_test_set.json`.

## Dataset at a glance

Figures below are from the frozen 2026-02-16 collection.

| | Judgments | Decisions |
|---|---|---|
| Processed metadata rows | 15,316 | 33,603 |
| Case text HTML files | 15,316 | 33,603 |
| Corpus JSONL records | 15,305 | 33,019 |

Judgment records lost between HTML and corpus are extraction failures (reported
per-file at stage 3). The larger decision gap is dominated by the empty-shell
cases that stage 3 skips by design.
