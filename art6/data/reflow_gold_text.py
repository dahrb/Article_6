"""Reflow annotation/gold_text/*.txt to a portrait-monitor-friendly width.

The source `text` field is ECHR HUDOC markdown: `##` section headers on their
own line, numbered paragraphs (`**12.**`) usually one per blank-line-delimited
block but sometimes several run together, and no line wrapping at all -- some
judgments carry single lines several hundred characters long (see
annotation/README or just `wc -L` before running this). This reflows each
non-header block to WIDTH columns with Python's textwrap, exactly reproducing
the wrapping already present on 5 of the 30 gold_text files (verified
byte-for-byte before this script was written) so the whole set reads
consistently.

break_on_hyphens=False matters: the default wraps "sixty-two" as "sixty-" /
"two" across the line break, which the existing wrapped files never do.

Only rewrites a file whose current longest line exceeds WIDTH, so an
already-wrapped file is left untouched (and re-running this is a no-op).

Usage:
  uv run python -m art6.data.reflow_gold_text
  uv run python -m art6.data.reflow_gold_text --width 84 --dry-run
"""

from __future__ import annotations

import argparse
import re
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def reflow(text: str, width: int) -> str:
    blocks = re.split(r"\n\s*\n+", text.strip())
    out = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if block.startswith("#"):
            out.append(block)
        else:
            collapsed = re.sub(r"\s+", " ", block)
            out.append(
                textwrap.fill(
                    collapsed,
                    width=width,
                    break_on_hyphens=False,
                    break_long_words=False,
                )
            )
    return "\n\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text-dir", type=Path, default=REPO_ROOT / "annotation/gold_text")
    ap.add_argument("--width", type=int, default=84)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(args.text_dir.glob("*.txt"))
    changed = skipped = 0
    for path in files:
        current = path.read_text(encoding="utf-8")
        longest = max((len(l) for l in current.splitlines()), default=0)
        if longest <= args.width:
            skipped += 1
            continue
        new_text = reflow(current, args.width)
        if args.dry_run:
            print(
                f"  would rewrap {path.name}  (longest line {longest} -> <= {args.width})"
            )
        else:
            path.write_text(new_text, encoding="utf-8")
            print(
                f"  rewrapped {path.name}  (longest line {longest} -> <= {args.width})"
            )
        changed += 1

    verb = "would rewrap" if args.dry_run else "rewrapped"
    print(
        f"\n{verb} {changed} file(s), {skipped} already <= {args.width} cols, "
        f"{len(files)} total"
    )


if __name__ == "__main__":
    main()
