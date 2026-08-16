# Data — collection & processing

Everything in this project starts from the ECHR's [HUDOC](https://hudoc.echr.coe.int/)
database. This directory holds the scripts that pull Art. 6 case metadata and case
text from HUDOC, clean them, and turn the raw HTML into a segmented JSONL corpus.

The pipeline is four stages:

```
HUDOC REST API
      │
      ▼
1. data_collection.py        → raw metadata + per-case HTML
      │
      ▼
2. metadata_processing.py    → cleaned, enriched metadata (judges, countries, limb)
      │
      ▼
3. case_text_processing.py   → sectioned JSONL text corpus
      │
      ▼
4. build_ontocast_test_set.py → small hand-balanced extraction test set
```

Stages 1–3 are the reproducible core. Stage 4 is an optional sampler used for
ontology-extraction experiments.

## Before you start

Run every command **from the repository root**, not from inside `data/`. The
scripts resolve inputs with relative `./data/...` paths and will not find their
inputs otherwise.

```bash
uv sync          # installs deps from pyproject.toml
```

## 1. Collection — `data_collection.py`

Queries the HUDOC REST API for all judgments and decisions citing Article 6,
sorts them by document type, downloads the case text HTML, and deduplicates.

```bash
uv run python data/data_collection.py
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

### Two gotchas in this script

Both are known and intentional-ish, but they will bite on a clean rerun:

1. **Raw metadata is not saved by default.** The `df.to_json(...)` call that
   writes `hudoc_art6_raw_metadata.json` is commented out
   ([`data_collection.py:84`](data_collection.py#L84)), and `__main__` discards
   the return value of `collect_cases()` and reads that file back from disk
   instead. On a first run from scratch, uncomment those two lines.
2. **The appno mapping is written to the wrong place.** `appno_mapping()` writes
   `./data/echr_appno_mapping.csv`, but the version tracked in this repo lives in
   `data/mappings/`. Move it after generating it.

## 2. Metadata processing — `metadata_processing.py`

Cleans the raw metadata and enriches it with judge identities, standardised
country names, legal-concept labels, and a civil/criminal classification.

```bash
# judgments (defaults)
uv run python data/metadata_processing.py

# decisions
uv run python data/metadata_processing.py \
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
- **Judges** — case judge strings are matched against `additional_data/judges.csv`
  with `difflib.SequenceMatcher`. Exact (case-insensitive) matches score 1.0;
  everything else takes the best fuzzy match. Matches at or above the threshold
  populate `judges_id`; every score is kept in `judge_similarity_pct` so you can
  audit the join. Anything below the threshold is reported in
  `additional_data/unmatched_judges.txt`.
- **Countries** — `respondent` ISO codes resolved to names via `pycountry`.
- **Legal concepts** — `kpthesaurus` IDs mapped to labels via
  `mappings/key_labels.json`; unmapped IDs are printed.
- **Art. 6 limb** — `classify_art6_limb()` reads those labels and tags each case
  `Criminal`, `Civil`, `Constitutional`, `Mixed`, or `Unspecified`. This is
  keyword-based, so treat it as a strong heuristic rather than ground truth.
- **Legal system** — respondent countries mapped to `Civil` / `Common` / `Mixed`
  via `mappings/law_system_mapping.json`.

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
| `judges_id` | list | matched IDs into `judges_processed.json` |
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

## 3. Text processing — `case_text_processing.py`

Turns each case HTML into one JSONL record: a markdown rendering of the full
text, a set of named sections, and any extracted tables.

```bash
uv run python data/case_text_processing.py --corpus judgments
uv run python data/case_text_processing.py --corpus decisions
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
  `--skip-empty-text` flag ([`case_text_processing.py:69`](case_text_processing.py#L69)).
  Many historical decisions are conversion shells with no recoverable body text.

## 4. Extraction test set — `build_ontocast_test_set.py`

Builds a small, deliberately balanced set of 10 English cases for ontology
extraction experiments — spanning every court level and covering violation,
non-violation, and inadmissibility outcomes.

```bash
uv run python data/build_ontocast_test_set.py
```

Reads `sample_metadata.parquet`, filters to English cases whose facts section is
2,500–40,000 characters, then fills one slot per `(court level, outcome)` pair,
preferring an unseen respondent state and then the longest facts section.

Only `introduction`, `procedure`, and `facts` are exported. The Court's own
reasoning (`law`, `reasons`) is withheld deliberately so extraction is scored on
what the facts state about the domestic proceedings, not on the Court's
conclusions. Output goes to `ontocast_test_set/art6_domestic_test_set.json`.

## What is tracked, what is generated

Only the scripts and the small reference/mapping files are in git. Every bulk
artifact is gitignored — regenerate it with the pipeline above.

**Tracked**

| Path | What it is |
|---|---|
| `data_collection.py`, `metadata_processing.py`, `case_text_processing.py`, `build_ontocast_test_set.py` | the pipeline |
| `additional_data/judges.csv` | hand-compiled reference of 229 ECtHR judges (name, country, tenure, role) |
| `additional_data/judges_processed.json` | the same, with `judge_id` assigned and countries standardised |
| `mappings/key_labels.json` | HUDOC legal-concept ID → label |
| `mappings/law_system_mapping.json` | country → `Civil` / `Common` / `Mixed` |
| `mappings/echr_appno_mapping.csv` | application number → ECLI + itemid (183k rows) |

**Generated (gitignored)**

| Path | Produced by | Approx. size |
|---|---|---|
| `hudoc_art6_raw_metadata.json` | stage 1 | 101 MB |
| `art_6_judgments_metadata.json` | stage 1 | 22 MB |
| `art_6_decisions_metadata.json` | stage 1 | 37 MB |
| `judgment_text/*.html` | stage 1 | 15,316 files |
| `decision_text/*.html` | stage 1 | 33,603 files |
| `art_6_judgments_metadata_processed.json` | stage 2 | 22 MB |
| `art_6_decisions_metadata_processed.json` | stage 2 | 34 MB |
| `additional_data/unmatched_judges.txt` | stage 2 | judge-matching audit report |
| `processed_json/echr_corpus.jsonl` | stage 3 | 856 MB |
| `processed_json/echr_decisions_corpus.jsonl` | stage 3 | 842 MB |
| `judgments_metadata_full.parquet` | `ontology/old/build_sample_new_parquet.py` | 162 MB |
| `sample_metadata.parquet` | — | 6 MB, retained sample |

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
