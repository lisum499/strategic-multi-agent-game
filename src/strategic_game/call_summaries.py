"""Small, redacted model-call summaries for the desktop view."""


def summarize_calls(data):
    settled = {row.get("round") for row in data.get("rounds", []) if isinstance(row, dict)}
    latest = {}
    result = []
    for record in data.get("records", []):
        if not isinstance(record, dict):
            continue
        round_no, country, attempt = record.get("round"), record.get("country_id"), record.get("attempt")
        status = record.get("status")
        if not isinstance(round_no, int) or not isinstance(country, str) or not isinstance(attempt, int):
            continue
        if status not in ("started", "completed", "failed"):
            continue
        # Provider or validation messages can quote untrusted model output or secrets.
        # Keep only a generic category rather than copying any exception text.
        summary = {"round": round_no, "country_id": country, "attempt": attempt,
                   "status": status, "error": "模型请求或输出校验失败" if status == "failed" else None,
                   "fallback": None}
        result.append(summary)
        latest[(round_no, country)] = len(result) - 1
    for (round_no, _), index in latest.items():
        summary = result[index]
        if round_no in settled and summary["status"] in ("completed", "failed"):
            summary["fallback"] = summary["status"] == "failed"
    return result


def read_live_summaries(data):
    """Do not trust optional fields in existing or externally edited live files."""
    result = []
    for item in data.get("call_summaries", []):
        if not isinstance(item, dict):
            continue
        round_no, country, attempt, status = (item.get(k) for k in ("round", "country_id", "attempt", "status"))
        if not isinstance(round_no, int) or not isinstance(country, str) or not isinstance(attempt, int):
            continue
        if status not in ("started", "completed", "failed"):
            continue
        fallback = item.get("fallback")
        result.append({"round": round_no, "country_id": country, "attempt": attempt, "status": status,
                       "error": "模型请求或输出校验失败" if status == "failed" else None,
                       "fallback": fallback if isinstance(fallback, bool) else None})
    return result
