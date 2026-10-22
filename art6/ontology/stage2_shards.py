"""Stage 2 on just the documents still missing, split across processes.

OntoCast processes the records of one JSONL one at a time and names each
output `<stem>.L<line>.facts.ttl` -- from the input file's stem and the
record's LINE NUMBER. Both facts made stage 2 all-or-nothing per arm: filtering
the input down to the undone documents renumbered them, so a rerun redid every
document, and one slow process handled them all.

The way round both is that OntoCast skips blank lines WITHOUT renumbering. A
shard file that keeps every line of the arm's input but blanks the ones it is
not responsible for produces exactly the names a single process over the whole
file would. So:

    plan    which documents have no graph yet; write N shard files covering
            only those, each a full-length copy with the others blanked;
            print one shard directory per line
    merge   copy each shard's outputs into the arm's raw/ directory

One wrinkle: OntoCast adds `.L<line>` only when a JSONL holds more than one
record, so a shard holding a single document writes `<stem>.facts.ttl`. merge
renames those to the `.L<line>` form, from the shard's own one non-blank line.

Standard library only: it runs from OntoCast's checkout, outside this
package's environment.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def record_lines(path: Path) -> list[int]:
    """1-based numbers of the non-blank lines -- OntoCast's record numbers."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [n for n, line in enumerate(lines, 1) if line.strip()]


def missing(input_path: Path, raw_dir: Path) -> list[int]:
    stem = input_path.stem
    return [
        n
        for n in record_lines(input_path)
        if not (raw_dir / f"{stem}.L{n}.facts.ttl").exists()
    ]


def plan(input_path: Path, raw_dir: Path, shards: int, root: Path) -> list[Path]:
    todo = missing(input_path, raw_dir)
    if not todo:
        return []
    lines = input_path.read_text(encoding="utf-8").splitlines()
    k = max(1, min(shards, len(todo)))
    out = []
    for i in range(k):
        mine = set(todo[i::k])
        shard = root / str(i)
        shard.mkdir(parents=True, exist_ok=True)
        (shard / input_path.name).write_text(
            "\n".join(line if n in mine else "" for n, line in enumerate(lines, 1))
            + "\n",
            encoding="utf-8",
        )
        out.append(shard)
    return out


def merge(input_path: Path, raw_dir: Path, root: Path) -> int:
    """Copy outputs into raw_dir, never overwriting; returns graphs added."""
    stem = input_path.stem
    raw_dir.mkdir(parents=True, exist_ok=True)
    added = 0
    for shard in sorted(p for p in root.iterdir() if p.is_dir()):
        out_dir = shard / "raw"
        if not out_dir.is_dir():
            continue
        numbered = record_lines(shard / input_path.name)
        for f in sorted(out_dir.iterdir()):
            if f.name == "extract.log":
                with (raw_dir / "extract.log").open("a", encoding="utf-8") as log:
                    log.write(f.read_text(encoding="utf-8", errors="replace"))
                continue
            name = f.name
            # A single-record shard writes "<stem>.facts.ttl" etc.: give it
            # back the line number every later stage pairs files by.
            if name.startswith(f"{stem}.") and not name.startswith(f"{stem}.L"):
                if len(numbered) != 1:
                    continue  # cannot attribute it to a line; leave it out
                name = f"{stem}.L{numbered[0]}.{name[len(stem) + 1 :]}"
            target = raw_dir / name
            if target.exists():
                continue
            shutil.copy2(f, target)
            added += name.endswith(".facts.ttl")
    return added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("plan", "merge", "missing"):
        p = sub.add_parser(cmd)
        p.add_argument("--input", required=True, type=Path)
        p.add_argument("--raw-dir", required=True, type=Path)
        if cmd != "missing":
            p.add_argument("--root", required=True, type=Path)
        if cmd == "plan":
            p.add_argument("--shards", type=int, default=1)
    a = ap.parse_args()
    if a.cmd == "plan":
        for shard in plan(a.input, a.raw_dir, a.shards, a.root):
            print(shard)
    elif a.cmd == "merge":
        print(merge(a.input, a.raw_dir, a.root))
    else:
        print(" ".join(f"L{n}" for n in missing(a.input, a.raw_dir)))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
