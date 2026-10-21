"""One definition of what the knowledge graph holds, shared by every mode.

The conditions differ in HOW they extract -- one flat call (C0), OntoCast on
the judgment (C1/C2), the agentic evidence stage then OntoCast (C3/C4) -- and
must not differ in WHAT they are asked for. Before this module each mode had
its own wording: C0 counted "one entry per instance" (a filing folded into its
decision, which gold and the evidence stage split in two), OntoCast on the raw
judgment never saw the outcome, matter or granularity rules the evidence stage
applies, and the evidence stage collected inactivity and adjournment spans the
ontology has no property for. Each of those is a difference in the target, not
in the method, and it would show up as a difference between conditions.

So the rules that DEFINE the target live in one place -- the named sections of
prompts/extract.txt -- and every mode's instruction is composed from them here:

    facts_instruction()   OntoCast, both arms, and every stage-3 call that
                          says "the instructions the graph was built under"
    condition_prompt()    the C0 baseline

On the bundle arm the shared rules are redundant -- stage 1 already applied
them and facts.txt says to transcribe its decisions -- but they are the same
rules, so they cannot disagree with what the bundle says.
"""

from __future__ import annotations

from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
EXTRACT = PROMPT_DIR / "extract.txt"
FACTS = PROMPT_DIR / "facts.txt"
PARTIES = PROMPT_DIR / "parties.txt"

# The sections of extract.txt that say WHAT to extract. Excluded on purpose:
# THE ONE RULE and OUTPUT (stage 1's span-only JSON contract), WHAT YOU MUST
# NOT DO (likewise), and CONTINUATIONS, which tells stage 1 NOT to link --
# right for stage 1, where a later agent links, and wrong for a mode that must
# produce the chain itself.
SHARED_SECTIONS = (
    "CLASSIFY THE RUNG AND THE RESULT",
    "CLASSIFY THE TRACK AND THE DIRECTION",
    "CLASSIFY THE MATTER",
    "COVERAGE AND GRANULARITY",
    "PARTIES — EVERY ENTRY NEEDS THEM",
    "FINALITY, PENDING AND DETENTION",
    "BEFORE YOU FINISH — COVERAGE CHECK",
)
PARTY_SECTIONS = ("WHAT A PARTY IS", "HONESTY")

BRIDGE = """\
WHAT TO EXTRACT -- THE SHARED DEFINITIONS

The rules below are the ones every extraction in this study is held to. They were
written for an evidence-selection pass, so they speak of "entries" (one domestic event
each) and of span fields; read them as defining WHAT to record, whatever form your own
output takes:

  - an "entry" is one domestic event -- one node, or one record;
  - `instance_level`, `outcome`, `proceeding_type`, `outcome_direction`, `matter` are the
    classifications to assign, with exactly the values and decision rules given;
  - `*_span` fields name the evidence for a value; `institution_narrated_span` marks a
    filing narrated as its own act, which is its own event;
  - `interim_spans` are steps that are NOT events: do not record them as events at all;
  - `finality_span`, `pending_span`, `non_occurring_span`, `detention` are facts about
    an event to record on it;
  - parties, persons and applicants are recorded as the sections say, and no judge is
    ever a person or a party.

Where these rules and your output format seem to disagree, the rules decide what is
extracted and the format decides only how it is written down.
"""


def load_sections(path: Path, names: tuple[str, ...]) -> str:
    """The named `## ` sections of a prompt file, in file order. Every name
    must be found: a renamed heading silently dropping a rule from one mode
    is the inconsistency this module exists to prevent."""
    text = path.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for part in text.split("\n## ")[1:]:
        heading = part.splitlines()[0].strip()
        if heading in names:
            found[heading] = "## " + part.rstrip()
    missing = [n for n in names if n not in found]
    if missing:
        raise KeyError(f"{path.name} has no section(s) {missing}")
    return "\n\n".join(found[n] for n in names)


def shared_rules() -> str:
    return (
        BRIDGE
        + "\n"
        + load_sections(EXTRACT, SHARED_SECTIONS)
        + "\n\n"
        + load_sections(PARTIES, PARTY_SECTIONS)
        + "\n"
    )


def facts_instruction() -> str:
    """The OntoCast instruction: facts.txt (how to write the graph) followed by
    the shared definitions (what goes in it)."""
    return FACTS.read_text(encoding="utf-8").strip() + "\n\n" + shared_rules().strip()


def condition_prompt(base: str) -> str:
    """A baseline prompt with the shared definitions appended."""
    return base.strip() + "\n\n" + shared_rules().strip() + "\n"
