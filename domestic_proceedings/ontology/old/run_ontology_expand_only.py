"""
run_ontology_expand_only.py
---------------------------
Grow ontology from a fixed seed using N English documents, then emit a
Turtle delta (additions / deletions / changes) against the original seed.

This script intentionally focuses on ontology expansion only.
Facts extraction can be run later with the stabilized ontology.

Status:
Unstable

Last Updated:
26.05.2026
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from dotenv import load_dotenv
from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef

# Ensure UTF-8 output on Windows.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent

# -------------------------
# Top-level hyperparameters
# -------------------------
DEFAULT_N_DOCS = 5
DEFAULT_START_IDX = 0
DEFAULT_RUN_TAG = "ontology-expand"
DEFAULT_TENANT = "ontoexpand"
DEFAULT_DRY_RUN = False

CANONICAL_SEED = SCRIPT_DIR / "ontologies" / "seed.ttl"
METADATA_PARQUET = REPO_ROOT / "data" / "sample_metadata.parquet"
RESULTS_BASE = REPO_ROOT / "results" / "ontology_expansion"


@dataclass
class Settings:
    n_docs: int
    start_idx: int
    dry_run: bool
    run_tag: str


@dataclass
class RunPaths:
    run_root: Path
    ontology_dir: Path
    working_dir: Path
    cache_dir: Path
    seed_copy: Path
    manifest_json: Path
    metrics_json: Path
    final_ontology_ttl: Path
    delta_ttl: Path
    additions_ttl: Path
    deletions_ttl: Path


@dataclass
class CaseResult:
    case_key: str
    case_name: str
    elapsed_s: float
    status: str
    ontology_file: str | None


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-") or "run"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_args() -> Settings:
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}

    n_docs = int(args[0]) if len(args) > 0 else DEFAULT_N_DOCS
    start_idx = int(args[1]) if len(args) > 1 else DEFAULT_START_IDX
    dry_run = DEFAULT_DRY_RUN or ("--dry-run" in flags)

    run_tag = DEFAULT_RUN_TAG
    if "--run-tag" in argv:
        i = argv.index("--run-tag")
        if i + 1 >= len(argv):
            raise ValueError("--run-tag requires a value")
        run_tag = argv[i + 1]

    if n_docs <= 0:
        raise ValueError("n_docs must be > 0")
    if start_idx < 0:
        raise ValueError("start_idx must be >= 0")

    return Settings(n_docs=n_docs, start_idx=start_idx, dry_run=dry_run, run_tag=run_tag)


def _load_env() -> None:
    # Force ontology-focused mode (without editing ontology.env).
    os.environ["RENDER_MODE"] = "ontology"
    os.environ["SKIP_ONTOLOGY_DEVELOPMENT"] = "false"
    os.environ["LLM_GRAPH_FORMAT"] = "jsonld"

    for env_path in (REPO_ROOT / "keys.env", SCRIPT_DIR / "ontology.env"):
        if not env_path.exists():
            raise FileNotFoundError(f"Missing env file: {env_path}")
        load_dotenv(env_path, override=True)

    # Re-apply forced values after dotenv override.
    os.environ["RENDER_MODE"] = "ontology"
    os.environ["SKIP_ONTOLOGY_DEVELOPMENT"] = "false"
    os.environ["LLM_GRAPH_FORMAT"] = "jsonld"


def _build_paths(settings: Settings, run_id: str) -> RunPaths:
    run_root = RESULTS_BASE / run_id
    ontology_dir = run_root / "ontologies"
    working_dir = run_root / "work"
    cache_dir = run_root / ".cache"

    return RunPaths(
        run_root=run_root,
        ontology_dir=ontology_dir,
        working_dir=working_dir,
        cache_dir=cache_dir,
        seed_copy=ontology_dir / "seed.ttl",
        manifest_json=run_root / "run_manifest.json",
        metrics_json=run_root / "run_metrics.json",
        final_ontology_ttl=run_root / "final_ontology.ttl",
        delta_ttl=run_root / "ontology_delta.ttl",
        additions_ttl=run_root / "ontology_additions.ttl",
        deletions_ttl=run_root / "ontology_deletions.ttl",
    )


def _prepare_paths(paths: RunPaths) -> None:
    paths.run_root.mkdir(parents=True, exist_ok=True)
    paths.ontology_dir.mkdir(parents=True, exist_ok=True)
    paths.working_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)


def _set_runtime_paths(paths: RunPaths) -> None:
    os.environ["ONTOCAST_WORKING_DIRECTORY"] = str(paths.working_dir).replace("\\", "/")
    os.environ["ONTOCAST_ONTOLOGY_DIRECTORY"] = str(paths.ontology_dir).replace("\\", "/")
    os.environ["ONTOCAST_CACHE_DIR"] = str(paths.cache_dir).replace("\\", "/")


def _copy_seed(paths: RunPaths) -> None:
    if not CANONICAL_SEED.exists():
        raise FileNotFoundError(f"Canonical seed not found: {CANONICAL_SEED}")
    shutil.copyfile(CANONICAL_SEED, paths.seed_copy)


def _load_english_cases(settings: Settings) -> tuple[list[dict[str, Any]], int, int, int]:
    if not METADATA_PARQUET.exists():
        raise FileNotFoundError(f"Parquet not found: {METADATA_PARQUET}")

    records: list[dict[str, Any]]

    try:
        import polars as pl  # type: ignore[import-not-found]

        df = pl.read_parquet(METADATA_PARQUET)
        eng = df.filter(
            pl.col("full_text").is_not_null()
            & (pl.col("full_text").cast(pl.Utf8).str.len_chars() > 0)
            & (pl.col("languageisocode").cast(pl.Utf8).str.to_uppercase() == "ENG")
        )
        records = [eng.row(i, named=True) for i in range(len(eng))]
    except ModuleNotFoundError:
        try:
            import pandas as pd  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "Parquet reader dependency missing. Install polars or pandas, "
                "or run with the project environment via: uv run python ontology/run_ontology_expand_only.py ..."
            ) from exc

        pdf = pd.read_parquet(METADATA_PARQUET)
        mask = (
            pdf["full_text"].notna()
            & (pdf["full_text"].astype(str).str.len() > 0)
            & (pdf["languageisocode"].astype(str).str.upper() == "ENG")
        )
        records = pdf.loc[mask].to_dict(orient="records")

    total_available = len(records)
    end_idx = min(settings.start_idx + settings.n_docs, total_available)
    selected = records[settings.start_idx:end_idx]
    return selected, total_available, settings.start_idx, end_idx


def _write_manifest(
    paths: RunPaths,
    settings: Settings,
    run_id: str,
    tenant: str,
    project: str,
    cases: list[dict[str, Any]],
    total_available: int,
    start_idx: int,
    end_idx: int,
) -> None:
    manifest = {
        "run_id": run_id,
        "run_tag": settings.run_tag,
        "timestamp_utc": _utc_stamp(),
        "n_docs_requested": settings.n_docs,
        "start_idx": settings.start_idx,
        "selected_count": len(cases),
        "total_english_available": total_available,
        "selected_range": [start_idx, max(start_idx, end_idx - 1)],
        "dry_run": settings.dry_run,
        "tenant": tenant,
        "project": project,
        "mode": os.environ.get("RENDER_MODE"),
        "seed": str(paths.seed_copy),
        "paths": {
            "run_root": str(paths.run_root),
            "ontology_dir": str(paths.ontology_dir),
            "working_dir": str(paths.working_dir),
            "cache_dir": str(paths.cache_dir),
        },
        "cases": [
            {
                "index": start_idx + i,
                "case_key": str(row.get("itemid") or row.get("ecli") or "unknown"),
                "case_name": row.get("case_name", ""),
            }
            for i, row in enumerate(cases)
        ],
    }
    paths.manifest_json.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _latest_seed_variant(ontology_dir: Path) -> Path | None:
    """Return best-available final ontology artifact from a run directory.

    Preferred output is versioned ``ontology_seed_*.ttl`` when present.
    Some execution paths may only persist updates into ``seed.ttl``.
    """
    candidates = sorted(
        ontology_dir.glob("ontology_seed_*.ttl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]

    seed_path = ontology_dir / "seed.ttl"
    if seed_path.exists() and seed_path.is_file():
        return seed_path

    return None


def _rdflib_graph(path: Path) -> Graph:
    g = Graph()
    g.parse(path, format="turtle")
    return g


def _write_plain_graph(triples: set[tuple[Any, Any, Any]], out_path: Path) -> None:
    g = Graph()
    for s, p, o in triples:
        g.add((s, p, o))
    g.serialize(destination=out_path, format="longturtle")


def _write_delta_ttl(
    base_graph: Graph,
    final_graph: Graph,
    out_path: Path,
    run_id: str,
) -> tuple[int, int, int]:
    delta = Graph()
    D = Namespace("https://example.org/ontocast/delta#")
    delta.bind("delta", D)

    root = URIRef(f"urn:ontocast:delta:{run_id}")
    delta.add((root, RDF.type, D.OntologyDelta))
    delta.add((root, D.generatedAt, Literal(_utc_stamp())))

    additions = set(final_graph) - set(base_graph)
    deletions = set(base_graph) - set(final_graph)

    add_by_sp: dict[tuple[Any, Any], set[Any]] = {}
    del_by_sp: dict[tuple[Any, Any], set[Any]] = {}

    for s, p, o in additions:
        add_by_sp.setdefault((s, p), set()).add(o)
    for s, p, o in deletions:
        del_by_sp.setdefault((s, p), set()).add(o)

    changed_sp = set(add_by_sp.keys()).intersection(del_by_sp.keys())

    change_count = 0
    for s, p in sorted(changed_sp, key=lambda x: (str(x[0]), str(x[1]))):
        old_objects = del_by_sp[(s, p)]
        new_objects = add_by_sp[(s, p)]
        for old_o in sorted(old_objects, key=str):
            for new_o in sorted(new_objects, key=str):
                bn = BNode()
                delta.add((root, D.change, bn))
                delta.add((bn, RDF.type, D.ChangeStatement))
                delta.add((bn, RDF.subject, s))
                delta.add((bn, RDF.predicate, p))
                delta.add((bn, D.oldObject, old_o))
                delta.add((bn, D.newObject, new_o))
                change_count += 1

    residual_additions = {
        t for t in additions if (t[0], t[1]) not in changed_sp
    }
    residual_deletions = {
        t for t in deletions if (t[0], t[1]) not in changed_sp
    }

    for s, p, o in sorted(residual_additions, key=lambda t: (str(t[0]), str(t[1]), str(t[2]))):
        bn = BNode()
        delta.add((root, D.addition, bn))
        delta.add((bn, RDF.type, D.AdditionStatement))
        delta.add((bn, RDF.subject, s))
        delta.add((bn, RDF.predicate, p))
        delta.add((bn, RDF.object, o))

    for s, p, o in sorted(residual_deletions, key=lambda t: (str(t[0]), str(t[1]), str(t[2]))):
        bn = BNode()
        delta.add((root, D.deletion, bn))
        delta.add((bn, RDF.type, D.DeletionStatement))
        delta.add((bn, RDF.subject, s))
        delta.add((bn, RDF.predicate, p))
        delta.add((bn, RDF.object, o))

    delta.add((root, D.additionsCount, Literal(len(residual_additions))))
    delta.add((root, D.deletionsCount, Literal(len(residual_deletions))))
    delta.add((root, D.changesCount, Literal(change_count)))

    delta.serialize(destination=out_path, format="longturtle")
    return len(residual_additions), len(residual_deletions), change_count


async def _run_case(
    row: dict[str, Any],
    idx: int,
    total: int,
    graph: Any,
) -> CaseResult:
    from ontocast.onto.enum import LLMGraphFormat, OntologyContextMode, RenderMode
    from ontocast.onto.state import AgentState

    case_key = str(row.get("itemid") or row.get("ecli") or "unknown")
    case_name = str(row.get("case_name") or "")
    input_text = str(row.get("full_text") or "")

    print(f"\n[{idx}/{total}] {case_key} | {case_name}")
    print(f"  Text chars: {len(input_text):,}")

    state = AgentState(
        # convert_document() expects .txt payload as JSON-encoded string
        # (mirrors existing run_ontology_dev.py behavior).
        raw_input={f"{case_key}.txt": json.dumps(input_text).encode("utf-8")},
        render_mode=RenderMode.ONTOLOGY,
        llm_graph_format=LLMGraphFormat.JSONLD,
        ontology_context_mode=OntologyContextMode.FIXED_SINGLE_ONTOLOGY,
        ontology_context_fixed_ontology_id="seed",
        ontology_user_instruction=(
            "Extend seed.ttl with any concepts missing from the text which are pertinent to a legal understanding. Focus in particular on aspcts relevant to appellant demographics, legal outcomes, conclusions, domestic proceedings. "
            "Only create ontology terms (owl:Class, owl:ObjectProperty, owl:DatatypeProperty). "
            "Do not create instances. Do not redefine existing terms. English labels/comments/entities/relations only."
        ),
    )
    
            #"IRI namespace: <https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#> — no other namespace. "

    t0 = perf_counter()
    try:
        await graph.ainvoke(state)
        elapsed = perf_counter() - t0
        ontology_file = _latest_seed_variant(Path(os.environ["ONTOCAST_ONTOLOGY_DIRECTORY"]))
        return CaseResult(
            case_key=case_key,
            case_name=case_name,
            elapsed_s=elapsed,
            status="ok",
            ontology_file=str(ontology_file) if ontology_file else None,
        )
    except Exception as exc:
        elapsed = perf_counter() - t0
        return CaseResult(
            case_key=case_key,
            case_name=case_name,
            elapsed_s=elapsed,
            status=f"error: {exc}",
            ontology_file=None,
        )


async def _run_all(
    tools: Any,
    cases: list[dict[str, Any]],
    tenant: str,
    project: str,
) -> list[CaseResult]:
    from ontocast.stategraph import create_agent_graph

    # IMPORTANT: set tenancy before initialize(). initialize() synchronizes
    # filesystem/triple-store ontologies and would otherwise read defaults.
    await tools.update_tenancy(tenant, project)
    await tools.clean_tenancy_data(tenant, project)
    await tools.initialize()

    graph = create_agent_graph(tools)
    results: list[CaseResult] = []
    total = len(cases)

    for i, row in enumerate(cases, 1):
        res = await _run_case(row, i, total, graph)
        print(f"  Status: {res.status} ({res.elapsed_s:.1f}s)")
        results.append(res)

    return results


def main() -> None:
    settings = _parse_args()
    _load_env()

    run_id = f"{_utc_stamp()}_{_sanitize(settings.run_tag)}"
    tenant = _sanitize(DEFAULT_TENANT)
    project = _sanitize(run_id)

    if tenant == "ontocast" and project == "test":
        raise RuntimeError("Refusing legacy tenancy ontocast--test")

    paths = _build_paths(settings, run_id)
    _prepare_paths(paths)
    _set_runtime_paths(paths)
    _copy_seed(paths)

    cases, total_available, start_idx, end_idx = _load_english_cases(settings)
    _write_manifest(
        paths=paths,
        settings=settings,
        run_id=run_id,
        tenant=tenant,
        project=project,
        cases=cases,
        total_available=total_available,
        start_idx=start_idx,
        end_idx=end_idx,
    )

    print("\n" + "=" * 64)
    print(" Ontology Expansion (Seed-anchored, Fresh Fuseki Tenancy)")
    print("=" * 64)
    print(f"Run ID        : {run_id}")
    print(f"Tenant/Project: {tenant} / {project}")
    print(f"Mode          : {os.environ.get('RENDER_MODE')}")
    print(f"Seed          : {paths.seed_copy}")
    print(f"Cases         : {len(cases)} (index {start_idx}..{max(start_idx, end_idx - 1)} of {total_available})")
    print(f"Dry run       : {settings.dry_run}")

    for i, row in enumerate(cases, 1):
        case_key = str(row.get("itemid") or row.get("ecli") or "unknown")
        print(f"  {i:>2}. {case_key} -- {row.get('case_name', '')}")

    if settings.dry_run:
        print("\nDry run complete. No OntoCast calls were made.")
        return

    from ontocast.config import Config
    from ontocast.toolbox import ToolBox

    config = Config()
    try:
        tools = ToolBox(config)
    except Exception as exc:
        raise RuntimeError(
            "Failed to initialize OntoCast ToolBox. "
            "Check project environment dependencies (notably docling/bs4 stack) and retry with uv environment."
        ) from exc

    t0 = perf_counter()
    results = asyncio.run(_run_all(tools, cases, tenant=tenant, project=project))
    elapsed_total = perf_counter() - t0

    latest = _latest_seed_variant(paths.ontology_dir)
    if latest is None:
        raise RuntimeError(
            f"No ontology_seed_*.ttl produced in {paths.ontology_dir}. "
            "Cannot compute delta."
        )

    shutil.copyfile(latest, paths.final_ontology_ttl)

    base_graph = _rdflib_graph(paths.seed_copy)
    final_graph = _rdflib_graph(paths.final_ontology_ttl)

    additions = set(final_graph) - set(base_graph)
    deletions = set(base_graph) - set(final_graph)
    _write_plain_graph(additions, paths.additions_ttl)
    _write_plain_graph(deletions, paths.deletions_ttl)

    add_count, del_count, chg_count = _write_delta_ttl(
        base_graph=base_graph,
        final_graph=final_graph,
        out_path=paths.delta_ttl,
        run_id=run_id,
    )

    ok_count = sum(1 for r in results if r.status == "ok")
    metrics = {
        "run_id": run_id,
        "elapsed_total_s": round(elapsed_total, 3),
        "cases_total": len(results),
        "cases_ok": ok_count,
        "cases_error": len(results) - ok_count,
        "delta": {
            "additions": add_count,
            "deletions": del_count,
            "changes": chg_count,
        },
        "outputs": {
            "final_ontology_ttl": str(paths.final_ontology_ttl),
            "delta_ttl": str(paths.delta_ttl),
            "additions_ttl": str(paths.additions_ttl),
            "deletions_ttl": str(paths.deletions_ttl),
            "manifest_json": str(paths.manifest_json),
        },
        "cases": [
            {
                "case_key": r.case_key,
                "case_name": r.case_name,
                "elapsed_s": round(r.elapsed_s, 3),
                "status": r.status,
                "ontology_file": r.ontology_file,
            }
            for r in results
        ],
    }
    paths.metrics_json.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print("\n" + "-" * 64)
    print("Run completed")
    print(f"  Success: {ok_count}/{len(results)}")
    print(f"  Total:   {elapsed_total:.1f}s")
    print(f"  Final ontology: {paths.final_ontology_ttl}")
    print(f"  Delta TTL:      {paths.delta_ttl}")
    print(f"  Metrics JSON:   {paths.metrics_json}")


if __name__ == "__main__":
    main()
