"""Local annotation app for the set model (annotation_guide.txt v3.1).

    uv run python annotation_tool/server.py --annotator sacha
    uv run python annotation_tool/server.py --annotator david --texts annotation/test_text --port 8766

Then open http://localhost:8765 (the port given). One annotator per server: the
app reads and writes only OUT/<annotator>/<case>.yaml, in the guide's file
format (section 8), and never shows another annotator's files or any model
output -- that is what keeps a double annotation blind. Every save is checked
by art6/model.py (the same checks as the test suite, plus every quote found in
the text); problems are reported, never blocking, so a draft can be saved
half-done. Active annotation time per document is logged to
OUT/<annotator>/_status.yaml with the done flag.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import webbrowser
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from art6 import model

STATIC = Path(__file__).resolve().parent / "static"
GUIDE = ROOT / "annotation" / "annotation_guide.txt"
GUIDE_VERSION = "3.1"
EVENT_ORDER = (
    "id",
    "date",
    "body",
    "authority_kind",
    "level",
    "outcome",
    "direction",
    "parties",
    "matter",
    "reviews",
    "appealed_by",
    "appeal_date",
    "communication_date",
    "costs",
    "quote",
    "note",
)
_CASE = re.compile(r"^[\w.-]+$")


def _now() -> datetime:
    """Local time, timezone-aware."""
    return datetime.now(UTC).astimezone()


class Store:
    def __init__(self, texts: Path, out: Path, annotator: str):
        self.texts, self.dir, self.annotator = texts, out / annotator, annotator
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    @property
    def status_file(self) -> Path:
        return self.dir / "_status.yaml"

    def status(self) -> dict:
        if self.status_file.exists():
            return yaml.safe_load(self.status_file.read_text(encoding="utf-8")) or {}
        return {}

    def cases(self) -> list[dict]:
        st = self.status()
        out = []
        for f in sorted(self.texts.glob("*.txt")):
            s = st.get(f.stem, {})
            out.append(
                {
                    "id": f.stem,
                    "done": bool(s.get("done")),
                    "seconds": s.get("seconds", 0),
                    "started": (self.dir / f"{f.stem}.yaml").exists(),
                    "events": s.get("events", 0),
                }
            )
        return out

    def text(self, case: str) -> str:
        return (self.texts / f"{case}.txt").read_text(encoding="utf-8")

    def load(self, case: str) -> dict:
        f = self.dir / f"{case}.yaml"
        if not f.exists():
            return {"case_id": case, "guide_version": GUIDE_VERSION, "sets": []}
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for s in data.get("sets") or []:  # YAML may read an unquoted year as an int
            for e in [s.get("start")] + list(s.get("decisions") or []):
                for k in ("date", "appeal_date", "communication_date"):
                    if e and e.get(k) is not None:
                        e[k] = str(e[k])
        return data

    def save(self, case: str, data: dict, seconds: int, done: bool) -> list[str]:
        data = clean(case, data)
        errs = model.check(data, self.text(case))
        header = (
            f"# annotator: {self.annotator}; annotation_guide.txt v{GUIDE_VERSION}; "
            f"saved {_now().isoformat(timespec='seconds')} by annotation_tool\n"
        )
        body = yaml.dump(
            data, Dumper=model._Dumper, sort_keys=False, allow_unicode=True, width=100
        )
        with self.lock:
            (self.dir / f"{case}.yaml").write_text(header + body, encoding="utf-8")
            st = self.status()
            st[case] = {
                "done": bool(done),
                "seconds": int(seconds),
                "problems": len(errs),
                "events": sum(
                    bool(s.get("start")) + len(s.get("decisions") or [])
                    for s in data["sets"]
                ),
                "saved": _now().isoformat(timespec="seconds"),
            }
            self.status_file.write_text(
                yaml.safe_dump(st, sort_keys=True), encoding="utf-8"
            )
        return errs


def _event(e: dict, decision: bool) -> dict:
    out = {}
    for k in EVENT_ORDER:
        v = e.get(k)
        if k == "id" and not decision:
            continue
        if k == "parties":
            v = [
                {"name": p["name"].strip(), "side": p.get("side") or "Unknown"}
                for p in v or []
                if (p.get("name") or "").strip()
            ]
        elif isinstance(v, str):
            v = v.strip()
        if v in (None, "", []):
            continue
        out[k] = v
    return out


def clean(case: str, data: dict) -> dict:
    """The client's document in the file's canonical key order, empties dropped."""
    sets = []
    for s in data.get("sets") or []:
        ns = {k: s[k] for k in ("id", "kind", "label") if s.get(k) not in (None, "")}
        if s.get("pending"):
            ns["pending"] = True
        for k in ("enforces", "note"):
            if s.get(k):
                ns[k] = s[k].strip() if isinstance(s[k], str) else s[k]
        if s.get("start"):
            ns["start"] = _event(s["start"], decision=False)
        ns["decisions"] = [_event(d, decision=True) for d in s.get("decisions") or []]
        sets.append(ns)
    return {"case_id": case, "guide_version": GUIDE_VERSION, "sets": sets}


def make_handler(store: Store):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(STATIC), **kw)

        def log_message(self, fmt, *args):  # quiet: one line per save only
            pass

        def _json(self, obj, status=HTTPStatus.OK):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _case(self) -> str | None:
            case = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            if _CASE.match(case) and (store.texts / f"{case}.txt").exists():
                return case
            self._json({"error": f"unknown case {case!r}"}, HTTPStatus.NOT_FOUND)
            return None

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._json(
                    {
                        "annotator": store.annotator,
                        "guide_version": GUIDE_VERSION,
                        "texts": str(store.texts),
                        "out": str(store.dir),
                        "cases": store.cases(),
                        "vocab": {
                            "kind": model.KINDS,
                            "level": model.LEVELS,
                            "outcome": model.OUTCOMES,
                            "direction": model.DIRECTIONS,
                            "authority_kind": model.AUTHORITY_KINDS,
                            "side": model.SIDES,
                            "appealed_by": model.APPEALED_BY,
                            "costs": model.COSTS,
                            "matter": model.MATTERS,
                        },
                    }
                )
            if path == "/api/guide":
                return self._json({"text": GUIDE.read_text(encoding="utf-8")})
            if path == "/api/case":
                case = self._case()
                if case:
                    data = store.load(case)
                    st = store.status().get(case, {})
                    self._json(
                        {
                            "id": case,
                            "text": store.text(case),
                            "data": data,
                            "problems": model.check(data, store.text(case))
                            if data["sets"]
                            else [],
                            "done": bool(st.get("done")),
                            "seconds": st.get("seconds", 0),
                        }
                    )
                return None
            if path == "/":
                self.path = "/index.html"
            return super().do_GET()

        def do_POST(self):
            if urlparse(self.path).path != "/api/case":
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            case = self._case()
            if not case:
                return None
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
            )
            errs = store.save(
                case,
                body.get("data") or {},
                body.get("seconds", 0),
                body.get("done", False),
            )
            print(
                f"{_now():%H:%M:%S} saved {case}: {len(errs)} problem(s)",
                flush=True,
            )
            return self._json({"problems": errs, "saved": _now().strftime("%H:%M:%S")})

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--annotator", required=True, help="your name: files go to OUT/<annotator>/"
    )
    ap.add_argument(
        "--texts",
        type=Path,
        default=ROOT / "annotation" / "gold_text",
        help="directory of <case>.txt judgments (default: the gold30 texts)",
    )
    ap.add_argument("--out", type=Path, default=ROOT / "annotation" / "annotators")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if not re.match(r"^[a-z][a-z0-9_-]*$", a.annotator):
        sys.exit("--annotator: lower-case letters, digits, - and _ only")
    if not any(a.texts.glob("*.txt")):
        sys.exit(f"no <case>.txt files in {a.texts}")
    store = Store(a.texts, a.out, a.annotator)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(store))
    url = f"http://localhost:{a.port}"
    print(
        f"annotating as {a.annotator}: {len(store.cases())} text(s) from {a.texts}\n"
        f"files -> {store.dir}\nopen {url}  (Ctrl+C to stop)",
        flush=True,
    )
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
