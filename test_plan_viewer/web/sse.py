"""Server-sent event parsing and serialization."""

import json


def iter_sse_events(response):
    event_name = None
    data_lines = []
    for raw_line in response:
        line = raw_line.decode(
            "utf-8",
            errors="replace",
        ).rstrip("\r\n")
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event_name, "\n".join(data_lines)


def sse_payload(event, payload):
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )


__all__ = ["iter_sse_events", "sse_payload"]
