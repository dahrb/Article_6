"""Run the Art. 6 pipeline end to end, or a subset of its stages.

    uv run python main.py                     # every stage, in order
    uv run python main.py --stages 2 3        # just metadata + text processing
    uv run python main.py --list              # what each stage does

Stage 1 downloads ~49,000 files from HUDOC and takes many hours; it is resumable
and skips anything already on disk. Each stage is also runnable on its own, with
its own flags, as `uv run python -m art6.data.collection` and friends.
"""

from __future__ import annotations

import argparse

from art6.data import (
    build_ontocast_test_set,
    collection,
    metadata_processing,
    text_processing,
)
from art6.paths import (
    DECISIONS_METADATA_JSON,
    DECISIONS_METADATA_PROCESSED_JSON,
    JUDGMENTS_METADATA_JSON,
    JUDGMENTS_METADATA_PROCESSED_JSON,
)


def stage_1_collect():
    """HUDOC REST API -> raw metadata + per-case HTML"""
    collection.main()


def stage_2_metadata():
    """raw metadata -> cleaned, enriched metadata (judges, countries, limb)"""
    for input_json, output_json in (
        (JUDGMENTS_METADATA_JSON, JUDGMENTS_METADATA_PROCESSED_JSON),
        (DECISIONS_METADATA_JSON, DECISIONS_METADATA_PROCESSED_JSON),
    ):
        args = metadata_processing.parse_args([])
        args.input_json = input_json
        args.output_json = output_json
        metadata_processing.main(args)


def stage_3_text():
    """case HTML -> sectioned JSONL text corpus"""
    for corpus in ("judgments", "decisions"):
        args = text_processing.parse_args(["--corpus", corpus])
        text_processing.main(args)


def stage_4_test_set():
    """sampled metadata -> small hand-balanced extraction test set"""
    build_ontocast_test_set.main()


STAGES = {
    1: stage_1_collect,
    2: stage_2_metadata,
    3: stage_3_text,
    4: stage_4_test_set,
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--stages",
        type=int,
        nargs="+",
        choices=sorted(STAGES),
        default=sorted(STAGES),
        help="Which stages to run, in the order given. Defaults to all of them.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Describe the stages and exit without running anything.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.list:
        for number, stage in sorted(STAGES.items()):
            print(f"{number}. {stage.__doc__}")
        return

    for number in args.stages:
        stage = STAGES[number]
        print(f"\n{'=' * 70}\nStage {number}: {stage.__doc__}\n{'=' * 70}")
        stage()


if __name__ == "__main__":
    main()
