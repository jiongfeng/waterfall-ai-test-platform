"""Shared browser security headers and CSRF validation."""

import hmac
import secrets
from urllib.parse import urlsplit

from flask import jsonify, request, session

CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_SESSION_KEY = "_csrf_token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' http: https:",
        "font-src 'self'",
        "connect-src 'self'",
        "media-src 'self'",
        "frame-src 'self'",
        "worker-src 'none'",
        "manifest-src 'self'",
    )
)

PLAYWRIGHT_REPORT_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'none'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'self'",
        "form-action 'none'",
        "script-src 'unsafe-inline' blob:",
        "style-src 'unsafe-inline'",
        "img-src data: blob:",
        "font-src data:",
        "media-src data: blob:",
        "connect-src 'none'",
        "worker-src blob:",
    )
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
}


def issue_csrf_token():
    """Return the session's CSRF token, creating a strong token if needed."""

    token = session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _normalized_origin(value):
    """Return a comparable HTTP(S) origin tuple or ``None``."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        port = parsed.port
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, parsed.hostname.lower(), port


def request_has_same_origin():
    """Validate Origin first, then use Referer only when Origin is absent."""

    request_origin = _normalized_origin(request.host_url)
    origin = request.headers.get("Origin")
    if origin is not None:
        return _normalized_origin(origin) == request_origin

    referer = request.headers.get("Referer")
    if referer is not None:
        return _normalized_origin(referer) == request_origin
    return False


def request_has_valid_csrf_token():
    """Compare the explicit request token with the current session token."""

    expected = session.get(CSRF_SESSION_KEY)
    provided = request.headers.get(CSRF_HEADER_NAME)
    return (
        isinstance(expected, str)
        and len(expected) >= 32
        and isinstance(provided, str)
        and hmac.compare_digest(expected, provided)
    )


def validate_csrf_request():
    """Return whether the current unsafe request passes CSRF validation."""

    if request.method.upper() not in UNSAFE_METHODS:
        return True
    return request_has_same_origin() or request_has_valid_csrf_token()


def csrf_error_response():
    """Build the stable JSON error returned for a rejected API request."""

    return (
        jsonify({"error": "请求来源校验失败，请刷新页面后重试。"}),
        403,
    )


def install_web_security(application):
    """Install security headers and the template CSRF token provider."""

    @application.after_request
    def add_security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if request.endpoint == "get_playwright_report":
            # Playwright's self-contained report needs inline assets. It is
            # rendered in a sandboxed iframe, receives no network capability,
            # and cannot be framed by another origin.
            response.headers["Content-Security-Policy"] = PLAYWRIGHT_REPORT_CONTENT_SECURITY_POLICY
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @application.context_processor
    def inject_csrf_token():
        return {"csrf_token": issue_csrf_token()}
