"""Verify source offsets without normalization or inferred quotations."""


def evidence(raw: dict, text: str) -> list[dict]:
    result = []
    for key, label in (
        ("what_happened_span", "Event quotation"),
        ("authority_span", "Authority"),
        ("outcome_span", "Outcome"),
        ("link_span", "Connection"),
        ("decision_date_span", "Decision date"),
        ("start_date_span", "Start date"),
    ):
        value = raw.get(key)
        if not isinstance(value, dict) or not value.get("text"):
            continue
        quote, start, end = value["text"], value.get("start"), value.get("end")
        verified = (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start < end <= len(text)
            and text[start:end] == quote
        )
        result.append(
            {
                "label": label,
                "text": quote,
                "start": start,
                "end": end,
                "verified": verified,
            }
        )
    return result


def track_summary(rows: list[dict]) -> dict:
    dated = [r for r in rows if r["bounds"]]
    outcomes = [r for r in dated if r["outcome"] != "unknown"]
    # Overlapping partial dates cannot establish a unique latest outcome.
    latest = []
    if outcomes:
        threshold = max(r["bounds"][0] for r in outcomes)
        latest = [r for r in outcomes if r["bounds"][1] >= threshold]
    return {
        "range": (
            f"{min(dated, key=lambda r: r['bounds'][0])['date']} → "
            f"{max(dated, key=lambda r: r['bounds'][1])['date']}"
        )
        if dated
        else "Dates not recorded",
        "undated": len(rows) - len(dated),
        "levels": list(
            dict.fromkeys(r["level"] for r in rows if r["level"] != "unknown")
        ),
        "latest": "; ".join(
            dict.fromkeys(f"{r['outcome']} ({r['date']})" for r in latest)
        )
        or "Not recorded",
        "ambiguous": len(latest) > 1,
        "undatedOutcomes": sum(
            not r["bounds"] and r["outcome"] != "unknown" for r in rows
        ),
    }
