"""
run_ontocast.py
---------------
Run OntoCast on a single English-language ECHR case from the exported parquet to test the running.
"""

import asyncio
from datetime import datetime, timezone
import json
import os
import re
import shutil
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

import polars as pl
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths (adjust if running from a different working directory)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
PARQUET_PATH = REPO_ROOT / "data" / "sampled_metadata_with_text.parquet"

# ---------------------------------------------------------------------------
# Load environment variables (same order as the notebook)
# ---------------------------------------------------------------------------
keys_env = REPO_ROOT / "keys.env"
ontology_env = SCRIPT_DIR / "ontology.env"

for env_file in (keys_env, ontology_env):
    if not env_file.exists():
        sys.exit(f"Missing env file: {env_file}")
    load_dotenv(env_file, override=True)

# Normalise blank domain allow/block lists so Config() parses safely.
for _name in ("WEB_SEARCH_ALLOWED_DOMAINS", "WEB_SEARCH_BLOCKED_DOMAINS"):
    if not (os.getenv(_name) or "").strip():
        os.environ[_name] = "[]"

# Normalise optional int field.
_max_triples_raw = (os.getenv("ONTOLOGY_MAX_TRIPLES") or "").strip()
if not _max_triples_raw or _max_triples_raw.startswith("#"):
    os.environ.pop("ONTOLOGY_MAX_TRIPLES", None)

# Normalise Fuseki auth into user/password format expected by OntoCast.
_fuseki_auth = os.getenv("FUSEKI_AUTH", "")
if ":" in _fuseki_auth and "/" not in _fuseki_auth:
    user, pw = _fuseki_auth.split(":", 1)
    os.environ["FUSEKI_AUTH"] = f"{user}/{pw}"

# Force OntoCast to bootstrap from seed_schema.ttl only.
SEED_SCHEMA_PATH = SCRIPT_DIR / "seed_schema.ttl"
if not SEED_SCHEMA_PATH.exists():
    sys.exit(f"Missing schema file: {SEED_SCHEMA_PATH}")

RUNTIME_ONTOLOGY_DIR = REPO_ROOT / ".cache" / "ontocast" / "seed_schema_only"
RUNTIME_ONTOLOGY_DIR.mkdir(parents=True, exist_ok=True)

for ttl_file in RUNTIME_ONTOLOGY_DIR.glob("*.ttl"):
    ttl_file.unlink()

runtime_seed_schema = RUNTIME_ONTOLOGY_DIR / "seed_schema.ttl"
shutil.copy2(SEED_SCHEMA_PATH, runtime_seed_schema)
os.environ["ONTOCAST_ONTOLOGY_DIRECTORY"] = str(RUNTIME_ONTOLOGY_DIR)

from ontocast.config import Config

config = Config()

# Access triple store configuration
tool_config = config.get_tool_config()

# Check which triple store is configured
if tool_config.fuseki.uri and tool_config.fuseki.auth:
    print("Using Fuseki triple store")
elif tool_config.neo4j.uri and tool_config.neo4j.auth:
    print("Using Neo4j triple store")
else:
    print("Using filesystem storage")

# ---------------------------------------------------------------------------
# Load data and pick one English case
# ---------------------------------------------------------------------------
if not PARQUET_PATH.exists():
    sys.exit(
        f"Parquet file not found: {PARQUET_PATH}\n"
        "Run the export cell in ontocast.ipynb first."
    )

df = pl.read_parquet(PARQUET_PATH)

english_with_text = df.filter(
    pl.col("full_text").is_not_null()
    & (pl.col("full_text").cast(pl.Utf8).str.len_chars() > 0)
    & (pl.col("languageisocode").cast(pl.Utf8).str.to_uppercase() == "ENG")
)

if english_with_text.height == 0:
    sys.exit("No English-language cases with full_text found in the parquet export.")

one_case = english_with_text.head(1).to_dicts()[0]
case_key = one_case.get("itemid") or one_case.get("ecli") or "unknown"
input_text = str(one_case["full_text"])

print(f"Selected case : {case_key}")
print(f"Case name     : {one_case.get('case_name')}")
print(f"Source        : {one_case.get('source')}")
print(f"Language      : {one_case.get('languageisocode')}")
print(f"Text length   : {len(input_text):,} chars")
print(f"Ontology seed : {runtime_seed_schema}")

REPORTS_DIR = REPO_ROOT / "results" / "ontocast_reports"
LLM_TRACE_LOG: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Local JSON parser hardening
# ---------------------------------------------------------------------------
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

_install_lenient_json_parser()

## Generate a Report
def _truncate_text(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... [truncated {len(value) - limit:,} chars]"
def _serialize_plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        return {str(k): _serialize_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _serialize_plain(value.model_dump())
        except Exception:
            return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if hasattr(value, "value"):
        try:
            return value.value
        except Exception:
            return str(value)
    return str(value)

def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(_serialize_plain(content), ensure_ascii=False, indent=2)
    except Exception:
        return str(content)

def _message_to_dict(message: Any) -> dict[str, Any]:
    payload = {
        "message_type": type(message).__name__,
        "content": _truncate_text(_stringify_content(getattr(message, "content", ""))),
    }
    for attr in ("type", "name", "id"):
        value = getattr(message, attr, None)
        if value not in (None, ""):
            payload[attr] = value
    for attr in ("additional_kwargs", "response_metadata", "tool_calls", "invalid_tool_calls"):
        value = getattr(message, attr, None)
        if value not in (None, {}, []):
            payload[attr] = _serialize_plain(value)
    return payload

def _normalize_llm_input(input_value: Any) -> dict[str, Any]:
    from langchain_core.messages import convert_to_messages

    if isinstance(input_value, str):
        return {"kind": "text", "text": _truncate_text(input_value)}

    if hasattr(input_value, "to_messages"):
        try:
            messages = input_value.to_messages()
            return {
                "kind": type(input_value).__name__,
                "messages": [_message_to_dict(message) for message in messages],
            }
        except Exception:
            pass

    try:
        messages = convert_to_messages(input_value)
        return {
            "kind": type(input_value).__name__,
            "messages": [_message_to_dict(message) for message in messages],
        }
    except Exception:
        return {
            "kind": type(input_value).__name__,
            "value": _truncate_text(_stringify_content(input_value)),
        }

def _normalize_llm_output(output_value: Any) -> dict[str, Any]:
    if hasattr(output_value, "content"):
        return {
            "kind": type(output_value).__name__,
            "message": _message_to_dict(output_value),
        }
    return {
        "kind": type(output_value).__name__,
        "value": _serialize_plain(output_value),
    }

def _install_llm_trace_capture() -> None:
    from langchain_core.language_models.chat_models import BaseChatModel

    if getattr(BaseChatModel, "_ontocast_trace_capture_installed", False):
        return

    original_invoke = BaseChatModel.invoke
    original_ainvoke = BaseChatModel.ainvoke

    def _record_trace(
        model: Any,
        method: str,
        prompt_payload: dict[str, Any],
        *,
        response_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        LLM_TRACE_LOG.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": method,
                "model": getattr(model, "model_name", None)
                or getattr(model, "model", None)
                or type(model).__name__,
                "prompt": prompt_payload,
                "kwargs": _serialize_plain(kwargs or {}),
                "response": response_payload,
                "error": error_message,
            }
        )

    def _traced_invoke(self, input: Any, *args, **kwargs):
        prompt_payload = _normalize_llm_input(input)
        try:
            result_value = original_invoke(self, input, *args, **kwargs)
            _record_trace(
                self,
                "invoke",
                prompt_payload,
                response_payload=_normalize_llm_output(result_value),
                kwargs=kwargs,
            )
            return result_value
        except Exception as exc:
            _record_trace(
                self,
                "invoke",
                prompt_payload,
                error_message=repr(exc),
                kwargs=kwargs,
            )
            raise

    async def _traced_ainvoke(self, input: Any, *args, **kwargs):
        prompt_payload = _normalize_llm_input(input)
        try:
            result_value = await original_ainvoke(self, input, *args, **kwargs)
            _record_trace(
                self,
                "ainvoke",
                prompt_payload,
                response_payload=_normalize_llm_output(result_value),
                kwargs=kwargs,
            )
            return result_value
        except Exception as exc:
            _record_trace(
                self,
                "ainvoke",
                prompt_payload,
                error_message=repr(exc),
                kwargs=kwargs,
            )
            raise

    BaseChatModel.invoke = _traced_invoke
    BaseChatModel.ainvoke = _traced_ainvoke
    BaseChatModel._ontocast_trace_capture_installed = True

def _get_field(container: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        return container.get(field_name, default)
    return getattr(container, field_name, default)

def _graph_to_turtle(graph: Any) -> str:
    if graph is None:
        return ""
    try:
        if len(graph) == 0:
            return ""
    except Exception:
        return _truncate_text(_stringify_content(graph))

    try:
        from ontocast.onto.rdfgraph import RDFGraph

        turtle = RDFGraph._to_turtle_str(graph)
        return turtle.strip()
    except Exception:
        return _truncate_text(_stringify_content(graph))

def _graph_update_to_markdown(update: Any, title: str) -> str:
    lines = [f"### {title}"]

    queries: list[str] = []
    if hasattr(update, "generate_sparql_queries"):
        try:
            queries = update.generate_sparql_queries()
        except Exception:
            queries = []

    if queries:
        for idx, query in enumerate(queries, start=1):
            lines.append(f"Query {idx}:")
            lines.append("```sparql")
            lines.append(query.strip())
            lines.append("```")
        return "\n".join(lines)

    lines.append("```json")
    lines.append(json.dumps(_serialize_plain(update), ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines)

def _render_updates_section(title: str, updates: list[Any]) -> str:
    lines = [f"## {title}"]
    if not updates:
        lines.append("None.")
        return "\n".join(lines)

    lines.append(f"Count: {len(updates)}")
    for idx, update in enumerate(updates, start=1):
        lines.append("")
        lines.append(_graph_update_to_markdown(update, f"Update {idx}"))
    return "\n".join(lines)

def _collect_context_critiques(context_manager: Any) -> list[dict[str, Any]]:
    critiques: list[dict[str, Any]] = []
    if context_manager is None:
        return critiques

    history = _get_field(context_manager, "context_history", []) or []
    for idx, ctx in enumerate(history, start=1):
        agent_type = _get_field(ctx, "agent_type")
        facts_critique = _get_field(ctx, "previous_facts_critique")
        ontology_critique = _get_field(ctx, "previous_ontology_critique")
        conversation_memory = _get_field(ctx, "conversation_memory", []) or []

        if facts_critique:
            critiques.append(
                {
                    "context_index": idx,
                    "agent_type": _serialize_plain(agent_type),
                    "kind": "facts_critique",
                    "payload": _serialize_plain(facts_critique),
                }
            )
        if ontology_critique:
            critiques.append(
                {
                    "context_index": idx,
                    "agent_type": _serialize_plain(agent_type),
                    "kind": "ontology_critique",
                    "payload": _serialize_plain(ontology_critique),
                }
            )
        if conversation_memory:
            critiques.append(
                {
                    "context_index": idx,
                    "agent_type": _serialize_plain(agent_type),
                    "kind": "conversation_memory",
                    "payload": _serialize_plain(conversation_memory),
                }
            )

    return critiques

def _build_report_payload(raw_result: Any, normalized_result: dict[str, Any]) -> dict[str, Any]:
    state_obj = raw_result if not isinstance(raw_result, dict) else normalized_result
    context_manager = _get_field(state_obj, "context_manager")

    ontology_addendum = _get_field(state_obj, "ontology_addendum")
    ontology_addendum_graph = _get_field(ontology_addendum, "graph")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case": {
            "itemid": case_key,
            "case_name": one_case.get("case_name"),
            "source": one_case.get("source"),
            "language": one_case.get("languageisocode"),
            "text_length": len(input_text),
        },
        "run_summary": {
            "status": _serialize_plain(_get_field(state_obj, "status")),
            "success_score": _serialize_plain(_get_field(state_obj, "success_score")),
            "budget_tracker": _serialize_plain(_get_field(state_obj, "budget_tracker")),
            "result_keys": sorted(normalized_result.keys()),
        },
        "llm_traces": _serialize_plain(LLM_TRACE_LOG),
        "stored_context": _collect_context_critiques(context_manager),
        "critic": {
            "suggestions": _serialize_plain(_get_field(state_obj, "suggestions")),
            "improvements_suggestions": _serialize_plain(
                _get_field(state_obj, "improvements_suggestions", [])
            ),
        },
        "ontology": {
            "proposed_addendum_turtle": _graph_to_turtle(ontology_addendum_graph),
            "pending_updates": _serialize_plain(_get_field(state_obj, "ontology_updates", [])),
            "applied_updates": _serialize_plain(
                _get_field(state_obj, "ontology_updates_applied", [])
            ),
        },
        "facts": {
            "pending_updates": _serialize_plain(_get_field(state_obj, "facts_updates", [])),
            "applied_updates": _serialize_plain(
                _get_field(state_obj, "facts_updates_applied", [])
            ),
            "aggregated_facts_turtle": _graph_to_turtle(_get_field(state_obj, "aggregated_facts")),
        },
        "raw_state_excerpt": {
            "statuses": _serialize_plain(_get_field(state_obj, "statuses")),
            "node_visits": _serialize_plain(_get_field(state_obj, "node_visits")),
            "render_mode": _serialize_plain(_get_field(state_obj, "render_mode")),
        },
    }
    return payload

def _render_llm_trace_markdown(llm_traces: list[dict[str, Any]]) -> str:
    lines = ["## LLM Prompts And Responses"]
    if not llm_traces:
        lines.append("No LLM calls were captured by the local trace hook.")
        return "\n".join(lines)

    lines.append(f"Captured calls: {len(llm_traces)}")
    for idx, trace in enumerate(llm_traces, start=1):
        lines.append("")
        lines.append(f"### Call {idx}")
        lines.append(f"- Timestamp: {trace.get('timestamp')}")
        lines.append(f"- Model: {trace.get('model')}")
        lines.append(f"- Method: {trace.get('method')}")
        if trace.get("error"):
            lines.append(f"- Error: {trace['error']}")
        lines.append("Prompt:")
        lines.append("```json")
        lines.append(json.dumps(trace.get("prompt"), ensure_ascii=False, indent=2))
        lines.append("```")
        if trace.get("response") is not None:
            lines.append("Response:")
            lines.append("```json")
            lines.append(json.dumps(trace.get("response"), ensure_ascii=False, indent=2))
            lines.append("```")
    return "\n".join(lines)

def _render_context_markdown(stored_context: list[dict[str, Any]]) -> str:
    lines = ["## Stored Context And Critiques"]
    if not stored_context:
        lines.append("No critique or conversation history was persisted in the returned context manager.")
        return "\n".join(lines)

    for idx, item in enumerate(stored_context, start=1):
        lines.append("")
        lines.append(f"### Context Entry {idx}")
        lines.append(f"- Context index: {item.get('context_index')}")
        lines.append(f"- Agent type: {item.get('agent_type')}")
        lines.append(f"- Kind: {item.get('kind')}")
        lines.append("```json")
        lines.append(json.dumps(item.get("payload"), ensure_ascii=False, indent=2))
        lines.append("```")
    return "\n".join(lines)

def _write_run_report(raw_result: Any, normalized_result: dict[str, Any]) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_case_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(case_key))
    report_stem = f"{timestamp}_{safe_case_key}"
    markdown_path = REPORTS_DIR / f"{report_stem}.md"
    json_path = REPORTS_DIR / f"{report_stem}.json"

    payload = _build_report_payload(raw_result, normalized_result)

    ontology_pending = _get_field(raw_result if not isinstance(raw_result, dict) else normalized_result, "ontology_updates", []) or []
    ontology_applied = _get_field(raw_result if not isinstance(raw_result, dict) else normalized_result, "ontology_updates_applied", []) or []
    facts_pending = _get_field(raw_result if not isinstance(raw_result, dict) else normalized_result, "facts_updates", []) or []
    facts_applied = _get_field(raw_result if not isinstance(raw_result, dict) else normalized_result, "facts_updates_applied", []) or []

    markdown_lines = [
        f"# OntoCast Run Report: {case_key}",
        "",
        "## Run Summary",
        f"- Generated at: {payload['generated_at']}",
        f"- Case name: {one_case.get('case_name')}",
        f"- Source: {one_case.get('source')}",
        f"- Language: {one_case.get('languageisocode')}",
        f"- Status: {payload['run_summary']['status']}",
        f"- Success score: {payload['run_summary']['success_score']}",
        f"- Budget tracker: {json.dumps(payload['run_summary']['budget_tracker'], ensure_ascii=False)}",
        "",
        _render_llm_trace_markdown(payload["llm_traces"]),
        "",
        "## Critic Summary",
        "Suggestions object:",
        "```json",
        json.dumps(payload["critic"]["suggestions"], ensure_ascii=False, indent=2),
        "```",
        "Improvement suggestions list:",
        "```json",
        json.dumps(payload["critic"]["improvements_suggestions"], ensure_ascii=False, indent=2),
        "```",
        "",
        _render_context_markdown(payload["stored_context"]),
        "",
        "## Proposed Ontology Additions",
    ]

    proposed_addendum_turtle = payload["ontology"]["proposed_addendum_turtle"]
    if proposed_addendum_turtle:
        markdown_lines.extend([
            "Ontology addendum graph:",
            "```turtle",
            proposed_addendum_turtle,
            "```",
            "",
        ])
    else:
        markdown_lines.extend(["No ontology addendum graph was returned.", ""])

    markdown_lines.extend([
        _render_updates_section("Pending Ontology Updates", ontology_pending),
        "",
        _render_updates_section("Applied Ontology Updates", ontology_applied),
        "",
        _render_updates_section("Pending Fact Updates", facts_pending),
        "",
        _render_updates_section("Applied Fact Updates", facts_applied),
        "",
        "## Aggregated Facts Graph",
    ])

    aggregated_facts_turtle = payload["facts"]["aggregated_facts_turtle"]
    if aggregated_facts_turtle:
        markdown_lines.extend([
            "```turtle",
            aggregated_facts_turtle,
            "```",
        ])
    else:
        markdown_lines.append("No aggregated facts graph was returned.")

    markdown_path.write_text("\n".join(markdown_lines), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return markdown_path, json_path


_install_llm_trace_capture()

# ---------------------------------------------------------------------------
# OntoCast run
# ---------------------------------------------------------------------------
from ontocast.config import Config
from ontocast.onto.state import AgentState
from ontocast.stategraph import create_agent_graph
from ontocast.toolbox import ToolBox


section_t0 = perf_counter()
state = AgentState(
    input_text=input_text,

    ontology_user_instruction=(
        "Use the existing echr namespace for any new classes."
    ), 
    facts_user_instruction=(
        ""
    )
)

config = Config()
tools = ToolBox(config)
setup_elapsed = perf_counter() - section_t0

async def _run():
    await tools.initialize()
    graph = create_agent_graph(tools)
    return await graph.ainvoke(state)

invoke_t0 = perf_counter()
result = asyncio.run(_run())
invoke_elapsed = perf_counter() - invoke_t0
out = result if isinstance(result, dict) else (
    result.model_dump() if hasattr(result, "model_dump") else result.dict()
)
section_elapsed = perf_counter() - section_t0

print(f"Timed section total: {section_elapsed:.3f}s")

report_markdown_path, report_json_path = _write_run_report(result, out)
print(f"Report (Markdown): {report_markdown_path}")
print(f"Report (JSON)    : {report_json_path}")
