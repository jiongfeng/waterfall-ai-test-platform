"""Pure helpers for OpenCode agent mentions and request payloads."""

import re


OPENCODE_AGENT_MENTION_PATTERN = re.compile(
    r"^@([A-Za-z0-9][A-Za-z0-9_.-]*)$"
)


def split_opencode_prompt(prompt):
    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        return None, ""

    lines = prompt_text.splitlines()
    agent_name = None
    body_start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            body_start = index + 1
            continue
        match = OPENCODE_AGENT_MENTION_PATTERN.match(stripped)
        if not match:
            break
        if agent_name is None:
            agent_name = match.group(1)
        body_start = index + 1

    body_text = "\n".join(lines[body_start:]).lstrip()
    if not body_text and not agent_name:
        body_text = prompt_text
    return agent_name, body_text


def build_opencode_prompt_parts(prompt):
    _, prompt_text = split_opencode_prompt(prompt)
    return [{"type": "text", "text": prompt_text}]


def build_opencode_prompt_payload(prompt, default_agent=None):
    agent_name, prompt_text = split_opencode_prompt(prompt)
    payload = {
        "parts": [{"type": "text", "text": prompt_text}]
    }
    agent_name = agent_name or default_agent
    if agent_name:
        payload["agent"] = agent_name
    return payload


def build_opencode_session_payload(
    title,
    prompt,
    default_agent=None,
):
    payload = {"title": title}
    agent_name, _ = split_opencode_prompt(prompt)
    agent_name = agent_name or default_agent
    if agent_name:
        payload["agent"] = agent_name
    return payload


__all__ = [
    "build_opencode_prompt_parts",
    "build_opencode_prompt_payload",
    "build_opencode_session_payload",
    "split_opencode_prompt",
]
