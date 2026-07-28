"""Decode and summarize output captured from child processes."""

import locale
import os


PROCESS_OUTPUT_ENCODING_FALLBACKS = (
    "utf-8",
    "utf-8-sig",
    "gb18030",
    "cp936",
    "gbk",
)
PROCESS_OUTPUT_MOJIBAKE_MARKERS = (
    "\ufffd",
    "锟斤拷",
    "����",
    "Ã",
    "Â",
    "涓",
    "鎴",
    "鐢",
    "璇",
    "杈",
    "妯",
    "瀹",
    "闂",
    "鍏",
    "绋",
    "閿",
    "鏂",
    "姝",
    "浣",
)


def get_console_text_encoding():
    return locale.getpreferredencoding(False) or "utf-8"


def get_process_output_encoding_candidates():
    candidates = ["utf-8", "utf-8-sig"]
    preferred = get_console_text_encoding()
    if preferred:
        candidates.append(preferred)
    candidates.extend(PROCESS_OUTPUT_ENCODING_FALLBACKS)
    if os.name == "nt":
        candidates.append("mbcs")

    seen = set()
    result = []
    for encoding in candidates:
        normalized = str(encoding or "").strip()
        if not normalized:
            continue
        key = normalized.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def score_decoded_process_output(text):
    if not text:
        return 0

    score = 0
    for marker in PROCESS_OUTPUT_MOJIBAKE_MARKERS:
        score += text.count(marker) * 20

    for character in text:
        codepoint = ord(character)
        if character in "\r\n\t":
            continue
        if codepoint < 32 or 0x7F <= codepoint <= 0x9F:
            score += 20
    return score


def decode_process_output(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes) or not value:
        return ""

    best = None
    for index, encoding in enumerate(get_process_output_encoding_candidates()):
        try:
            text = value.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        candidate = (score_decoded_process_output(text), index, text)
        if best is None or candidate[:2] < best[:2]:
            best = candidate

    if best is not None:
        return best[2]

    fallback = None
    for index, encoding in enumerate(get_process_output_encoding_candidates()):
        try:
            text = value.decode(encoding, errors="replace")
        except LookupError:
            continue
        candidate = (score_decoded_process_output(text), index, text)
        if fallback is None or candidate[:2] < fallback[:2]:
            fallback = candidate

    return fallback[2] if fallback is not None else value.decode("utf-8", errors="replace")


def normalize_process_output(value):
    return decode_process_output(value)


def summarize_process_output(stdout, stderr, limit=4000):
    parts = [
        normalize_process_output(stdout).strip(),
        normalize_process_output(stderr).strip(),
    ]
    output = "\n".join(part for part in parts if part)
    return output[-limit:]


__all__ = [
    "PROCESS_OUTPUT_ENCODING_FALLBACKS",
    "PROCESS_OUTPUT_MOJIBAKE_MARKERS",
    "decode_process_output",
    "get_console_text_encoding",
    "get_process_output_encoding_candidates",
    "normalize_process_output",
    "score_decoded_process_output",
    "summarize_process_output",
]
