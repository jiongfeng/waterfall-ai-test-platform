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


def format_opencode_execution_error(message, tool_status_error_pattern):
    if not isinstance(message, str):
        return str(message)
    normalized = message.lower()
    if any(
        marker in normalized
        for marker in (
            "unknown certificate verification error",
            "unable to get local issuer certificate",
            "unable to verify the first certificate",
        )
    ):
        return (
            "OpenCode 模型请求 TLS 证书校验失败。这个错误来自 OpenCode provider 调用，"
            "不是 Seed 脚本、Playwright MCP 或被测系统页面证书问题。"
            "请检查当前模型 provider 的网络、代理/VPN 和 CA 信任链，"
            "或切换到已正确配置证书信任的兼容 provider。"
        )
    if all(marker in message for marker in ("Type validation failed", '"choices"', '"error"', '"status"')):
        match = tool_status_error_pattern.search(message)
        tool_name = match.group(1) if match else "unknown"
        return (
            "OpenCode provider 兼容性错误：上游返回了工具运行状态事件，"
            "但 OpenCode 当前按 OpenAI choices/error 响应格式解析。"
            f"触发工具：{tool_name}。请重启 OpenCode Server 后重试；"
            "如果仍失败，请调整 OpenAI-compatible 输出或改用支持 OpenCode 工具流的模型。"
        )
    return message


__all__ = [
    "build_opencode_prompt_parts",
    "build_opencode_prompt_payload",
    "build_opencode_session_payload",
    "format_opencode_execution_error",
    "split_opencode_prompt",
]
