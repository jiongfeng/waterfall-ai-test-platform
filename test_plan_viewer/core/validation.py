"""Reusable normalization and identifier validation helpers."""

import json
import re


def normalize_string_list(value):
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n,，、;；]+", value)
        return [part.strip().strip("`").strip() for part in parts if part.strip().strip("`").strip()]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("name") or item.get("title") or item.get("text") or item.get("value")
            else:
                text = item
            text = str(text or "").strip().strip("`").strip()
            if text:
                result.append(text)
        return result
    return [str(value).strip()] if str(value).strip() else []


def normalize_json_object_or_array(value, fallback):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"notes": value.strip()} if value.strip() else fallback
        return parsed if isinstance(parsed, (dict, list)) else fallback
    return fallback


def normalize_confidence(value):
    if value in (None, ""):
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, confidence))


def validate_uid(value, field_name="uid"):
    uid = str(value or "").strip()
    if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", uid):
        raise ValueError(f"Invalid {field_name}.")
    return uid


__all__ = [
    "normalize_confidence",
    "normalize_json_object_or_array",
    "normalize_string_list",
    "validate_uid",
]
