"""
run_facts_extract.py
--------------------
Facts-only batch runner for OntoCast with a minimal CLI.

Design constraints implemented here:
1. CLI exposes only --n-cases and --env-file.
2. Parallel processing is always enabled with an internal hard cap of 10.
3. Case execution reuses native OntoCast workflow graph execution.
4. Processing mirrors REST /process behavior, including chunking and in-graph serialization.

Status:
In Progress

Last Updated:
26.05.2026
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import polars as pl
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows.
if hasattr(sys.stdout, "reconfigure"):
	sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
	sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent  # ontology/old/ -> repo root

DEFAULT_ENV_FILE = "ontology"
MAX_PARALLEL = 10

RESULTS_BASE = REPO_ROOT / "results" / "facts_extract"
METADATA_PARQUET = REPO_ROOT / "data" / "sample_metadata.parquet"


@dataclass
class Settings:
	n_cases: int
	env_file: str


@dataclass
class CaseExecution:
	case_key: str
	case_name: str
	status: str
	elapsed_s: float
	error: str | None
	error_traceback: str | None
	facts_triples: int


def _utc_stamp() -> str:
	return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sanitize(value: str) -> str:
	return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "run"


def _strip_json_comments(raw_text: str) -> str:
	"""Remove JS-style comments outside JSON strings."""
	out: list[str] = []
	in_string = False
	escaped = False
	i = 0
	length = len(raw_text)

	while i < length:
		char = raw_text[i]
		nxt = raw_text[i + 1] if i + 1 < length else ""

		if in_string:
			out.append(char)
			if escaped:
				escaped = False
			elif char == "\\":
				escaped = True
			elif char == '"':
				in_string = False
			i += 1
			continue

		if char == '"':
			in_string = True
			out.append(char)
			i += 1
			continue

		if char == "/" and nxt == "/":
			i += 2
			while i < length and raw_text[i] not in "\r\n":
				i += 1
			continue

		if char == "/" and nxt == "*":
			i += 2
			while i + 1 < length and not (raw_text[i] == "*" and raw_text[i + 1] == "/"):
				i += 1
			i += 2 if i + 1 < length else 0
			continue

		out.append(char)
		i += 1

	return "".join(out)


def _install_lenient_json_parser() -> None:
	"""Patch LangChain JSON parsing for this process only."""
	import langchain_core.output_parsers.json as lc_output_json
	import langchain_core.utils.json as lc_utils_json

	original_parse_json_markdown = lc_utils_json.parse_json_markdown

	def _patched_parse_json_markdown(json_string: str, *, parser=lc_utils_json.parse_partial_json):
		try:
			return original_parse_json_markdown(json_string, parser=parser)
		except json.JSONDecodeError:
			cleaned = _strip_json_comments(json_string)
			cleaned = re.sub(r"```(?:json)?", "```", cleaned, flags=re.IGNORECASE)
			return original_parse_json_markdown(cleaned, parser=parser)

	def _patched_parse_and_check_json_markdown(text: str, expected_keys: list[str]) -> dict:
		json_obj = _patched_parse_json_markdown(text)
		if not isinstance(json_obj, dict):
			from langchain_core.exceptions import OutputParserException

			error_message = (
				f"Expected JSON object (dict), but got: {type(json_obj).__name__}. "
			)
			raise OutputParserException(error_message, llm_output=text)

		for key in expected_keys:
			if key not in json_obj:
				from langchain_core.exceptions import OutputParserException

				msg = (
					f"Got invalid return object. Expected key `{key}` "
					f"to be present, but got {json_obj}"
				)
				raise OutputParserException(msg)
		return json_obj

	lc_utils_json.parse_json_markdown = _patched_parse_json_markdown
	lc_utils_json.parse_and_check_json_markdown = _patched_parse_and_check_json_markdown
	lc_output_json.parse_json_markdown = _patched_parse_json_markdown
def _parse_args() -> Settings:
	parser = argparse.ArgumentParser(
		description="Run OntoCast facts extraction in batch mode with capped parallelism."
	)
	parser.add_argument(
		"--n-cases",
		type=int,
		required=True,
		help="Number of English cases to process from metadata source.",
	)
	parser.add_argument(
		"--env-file",
		choices=["ontology", "ontology_local"],
		default=DEFAULT_ENV_FILE,
		help="Environment profile loaded from the selected env file.",
	)
	args = parser.parse_args()

	if args.n_cases <= 0:
		raise ValueError("--n-cases must be > 0")

	return Settings(n_cases=args.n_cases, env_file=args.env_file)


def _load_env(settings: Settings) -> Path:
	env_map = {
		"ontology": SCRIPT_DIR / "ontology.env",
		"ontology_local": SCRIPT_DIR / "ontology_local.env",
	}

	selected_env = env_map[settings.env_file]
	keys_env = REPO_ROOT / "keys.env"
	for env_path in (keys_env, selected_env):
		if not env_path.exists():
			raise FileNotFoundError(f"Missing env file: {env_path}")
		load_dotenv(env_path, override=True)

	results_dir = os.getenv("FACTS_RESULTS_DIR")
	if results_dir:
		global RESULTS_BASE
		RESULTS_BASE = Path(results_dir)

	return selected_env


def _load_metadata_records(n_cases: int) -> list[dict[str, Any]]:
	frame = pl.read_parquet(METADATA_PARQUET)
	selected = frame.filter(
		pl.col("full_text").is_not_null()
		& (pl.col("full_text").cast(pl.Utf8).str.len_chars() > 0)
		& (pl.col("languageisocode").cast(pl.Utf8).str.to_uppercase() == "ENG")
	).sort(["itemid", "ecli", "judgementdate"]).head(n_cases)
	return [selected.row(i, named=True) for i in range(selected.height)]


def _case_key(row: dict[str, Any], index: int) -> str:
	raw = row.get("itemid") or row.get("ecli") or f"case_{index + 1}"
	return str(raw)


def _build_facts_instruction(case_key: str, row: dict[str, Any]) -> str:
	"""Return the editable facts prompt template for one case.

	Edit this function to tune extraction behavior. It intentionally mirrors the
	policy style used in run_single_case.py while staying concise for batch runs.
	"""
	case_name = str(row.get("case_name") or row.get("ecli") or "")
	facts_iri_base = f"https://github.com/dahrb/Art_6/tree/main/facts/{case_key}#"

	return (
		f"Target Case ID: {case_key}. "
		f"Case name: {case_name}. "
		"NAMESPACE POLICY: Use doc: for case-specific individuals and reserve seed: for shared ontology terms only. "
		f"doc: base IRI is <{facts_iri_base}>. "
		"PARTIES: Extract applicants/appellants and link them to the case using seed:Party-compatible relations. "
		"DOMESTIC PROCEEDINGS: Build a chronological chain of domestic events as seed:DomesticProceeding instances. "
		"Use stable IRIs such as doc:proc_YYYY_MM_DD and suffix variants (_a, _b) when needed. "
		"MANDATORY SHAPE: Each proceeding should include one seed:hasDecisionDate (xsd:date) and one seed:hasCourt when evidence exists. "
		"CHAINING: Use seed:followsProceeding only when chronology supports it. Never create self-links or cycles. "
		"TYPED LITERALS: Dates must be xsd:date and booleans must be xsd:boolean. "
		"PREDICATE POLICY: Use declared ontology predicates only; do not invent undeclared predicates. "
		"EXTRACTION SCOPE: Prioritize party details, domestic chronology, proceeding duration, legal issues, and per-issue outcomes. "
		"COMPLETENESS: Extract as much grounded, schema-compatible information as the text supports."
        "Only use underscores in iris or alphanumeric characters, nothing else."
	)


def _build_agent_state(case_key: str, text: str, config: Any, facts_instruction: str) -> Any:
	from ontocast.onto.state import AgentState

	return AgentState(
		raw_input={f"{case_key}.txt": json.dumps(text).encode("utf-8")},
		facts_user_instruction=facts_instruction,
		max_visits=config.server.max_visits_per_node,
		# Keep None to mirror /process behavior where chunking is not capped by this runner.
		max_chunks=None,
		render_mode=config.server.render_mode,
		llm_graph_format=config.server.llm_graph_format,
		ontology_context_mode=config.server.ontology_context_mode,
		ontology_context_fixed_ontology_id=config.server.ontology_context_fixed_ontology_id,
		ontology_max_triples=config.server.ontology_max_triples,
	)

async def _execute_case(
	row: dict[str, Any],
	index: int,
	tools: Any,
	config: Any,
	workflow: Any,
	sem: asyncio.Semaphore,
) -> CaseExecution:
	from langchain_core.runnables import RunnableConfig

	case_key = _case_key(row, index)
	case_name = str(row.get("case_name") or row.get("ecli") or "")
	full_text = str(row.get("full_text") or "")

	async with sem:
		start = perf_counter()
		try:
			facts_instruction = _build_facts_instruction(case_key, row)
			state = _build_agent_state(case_key, full_text, config, facts_instruction)
			recursion_limit = max(
				config.server.base_recursion_limit,
				state.max_visits * config.server.estimated_chunks * 10,
			)

			workflow_state: dict[str, Any] | None = None
			async for chunk in workflow.astream(
				state,
				stream_mode="values",
				config=RunnableConfig(recursion_limit=recursion_limit),
			):
				workflow_state = chunk

			if workflow_state is None:
				raise ValueError("Workflow did not return a valid state")

			aggregated_facts = workflow_state.get("aggregated_facts")
			facts_triples = len(aggregated_facts) if aggregated_facts is not None else 0

			elapsed = perf_counter() - start
			print(f"[case {index + 1}] OK {case_key} ({facts_triples} facts, {elapsed:.1f}s)")
			return CaseExecution(
				case_key=case_key,
				case_name=case_name,
				status="ok",
				elapsed_s=elapsed,
				error=None,
				error_traceback=None,
				facts_triples=facts_triples,
			)
		except Exception as exc:
			elapsed = perf_counter() - start
			error_traceback = traceback.format_exc()
			print(f"[case {index + 1}] ERROR {case_key}: {exc}")
			print(error_traceback)
			return CaseExecution(
				case_key=case_key,
				case_name=case_name,
				status="error",
				elapsed_s=elapsed,
				error=str(exc),
				error_traceback=error_traceback,
				facts_triples=0,
			)

def _write_json(path: Path, payload: dict[str, Any]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

async def _amain(settings: Settings, selected_env_path: Path, config: Any, tools: Any) -> int:
	from ontocast.onto.enum import OntologyContextMode
	from ontocast.stategraph import create_agent_graph

	records = _load_metadata_records(settings.n_cases)

	run_id = f"{_utc_stamp()}_{_sanitize(settings.env_file)}_n{len(records)}"
	run_root = RESULTS_BASE / run_id
	manifest_path = run_root / "run_manifest.json"
	metrics_path = run_root / "run_metrics.json"

	vector_mode_enabled = (
		config.server.ontology_context_mode
		== OntologyContextMode.SELECTED_VECTOR_SEARCH_ONTOLOGY
	)

	# Mimic server startup behavior.
	await tools.initialize(
		ontology_context_mode=config.server.ontology_context_mode,
		fail_on_vector_store_error=vector_mode_enabled,
	)
	workflow = create_agent_graph(tools)

	effective_parallel = min(MAX_PARALLEL, len(records))
	sem = asyncio.Semaphore(effective_parallel)

	print("=" * 70)
	print("OntoCast Facts Batch Runner")
	print(f"Run id            : {run_id}")
	print(f"Source            : {METADATA_PARQUET}")
	print(f"Env               : {selected_env_path}")
	print(f"Cases selected    : {len(records)}")
	print(f"Parallel cap      : {effective_parallel} (hard max {MAX_PARALLEL})")
	print(f"Render mode       : {config.server.render_mode}")
	print("=" * 70)

	started = perf_counter()
	tasks = [
		_execute_case(row, idx, tools, config, workflow, sem)
		for idx, row in enumerate(records)
	]
	case_results = await asyncio.gather(*tasks)
	total_elapsed = perf_counter() - started

	manifest = {
		"run_id": run_id,
		"timestamp_utc": _utc_stamp(),
		"settings": {
			"n_cases": settings.n_cases,
			"env_file": settings.env_file,
			"parallel_cap": effective_parallel,
			"parallel_hard_max": MAX_PARALLEL,
		},
		"env_path": str(selected_env_path),
		"source_path": str(METADATA_PARQUET),
		"selected_cases": [
			{
				"index": idx,
				"case_key": _case_key(row, idx),
				"case_name": str(row.get("case_name") or row.get("ecli") or ""),
			}
			for idx, row in enumerate(records)
		],
	}

	metrics = {
		"run_id": run_id,
		"summary": {
			"requested": settings.n_cases,
			"selected": len(records),
			"processed_ok": sum(1 for c in case_results if c.status == "ok"),
			"processed_error": sum(1 for c in case_results if c.status == "error"),
			"published_ok": None,
			"published_error": None,
			"publish_mode": "in_graph_serialize",
			"total_elapsed_s": round(total_elapsed, 3),
		},
		"cases": [
			{
				"case_key": c.case_key,
				"case_name": c.case_name,
				"status": c.status,
				"elapsed_s": round(c.elapsed_s, 3),
				"facts_triples": c.facts_triples,
				"error": c.error,
				"error_traceback": c.error_traceback,
			}
			for c in case_results
		],
	}

	_write_json(manifest_path, manifest)
	_write_json(metrics_path, metrics)

	print(f"Manifest written   : {manifest_path}")
	print(f"Metrics written    : {metrics_path}")
	print(
		"Done: "
		f"ok={metrics['summary']['processed_ok']} "
		f"error={metrics['summary']['processed_error']} "
		"published=in_graph "
		f"elapsed={total_elapsed:.1f}s"
	)

	return 0


def main() -> None:
	try:
		settings = _parse_args()
		selected_env_path = _load_env(settings)

		from ontocast.config import Config
		from ontocast.toolbox import ToolBox

		_install_lenient_json_parser()

		from turtle_repair_patch import apply_patches
		apply_patches()

		config = Config()
		config.validate_llm_config()
		tools = ToolBox(config)

		exit_code = asyncio.run(_amain(settings, selected_env_path, config, tools))
	except KeyboardInterrupt:
		print("Interrupted")
		exit_code = 130
	except Exception as exc:
		print(f"Fatal error: {exc}")
		exit_code = 1
	raise SystemExit(exit_code)


if __name__ == "__main__":
	main()
