"""Render untrusted Markdown through a strict HTML allowlist."""

import re

import markdown as markdown_renderer

try:
    import nh3 as _nh3
except ImportError as exc:  # pragma: no cover - exercised through a patched dependency
    _nh3 = None
    _NH3_IMPORT_ERROR = exc
else:
    _NH3_IMPORT_ERROR = None


MARKDOWN_EXTENSIONS = (
    "extra",
    "fenced_code",
    "tables",
    "sane_lists",
    "toc",
    "nl2br",
)

ALLOWED_TAGS = frozenset(
    {
        "a",
        "blockquote",
        "br",
        "code",
        "del",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "strong",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)

ALLOWED_ATTRIBUTES = {
    "a": frozenset({"href", "title"}),
    "code": frozenset({"class"}),
    "img": frozenset({"alt", "src", "title"}),
}

# Remove these elements with all descendants. Unwrapping them can expose
# parser-confusion payloads or executable descendants in a different context.
CLEAN_CONTENT_TAGS = frozenset(
    {
        "embed",
        "form",
        "iframe",
        "math",
        "noscript",
        "object",
        "script",
        "style",
        "svg",
        "template",
    }
)

ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})
LANGUAGE_CLASS_PATTERN = re.compile(r"language-[A-Za-z0-9_+.-]{1,64}")


def _filter_attribute(tag, attribute, value):
    """Keep only syntax-highlighting classes generated for fenced code."""

    if tag == "code" and attribute == "class":
        language_classes = [
            token for token in value.split() if LANGUAGE_CLASS_PATTERN.fullmatch(token)
        ]
        return " ".join(language_classes) or None
    return value


def sanitize_html_fragment(rendered_html):
    """Sanitize an HTML fragment, refusing to render if nh3 is unavailable."""

    if _nh3 is None:
        raise RuntimeError(
            "Markdown preview is unavailable because the nh3 sanitizer could not be loaded."
        ) from _NH3_IMPORT_ERROR

    return _nh3.clean(
        rendered_html,
        tags=ALLOWED_TAGS,
        clean_content_tags=CLEAN_CONTENT_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_filter_attribute,
        strip_comments=True,
        link_rel="noopener noreferrer",
        url_schemes=ALLOWED_URL_SCHEMES,
        url_relative="deny",
        set_tag_attribute_values={
            "img": {
                "loading": "lazy",
                "referrerpolicy": "no-referrer",
            }
        },
    )


def render_markdown(content):
    """Convert Markdown to HTML and sanitize the complete result."""

    if not isinstance(content, str):
        raise TypeError("Markdown content must be a string.")

    rendered_html = markdown_renderer.markdown(
        content,
        extensions=MARKDOWN_EXTENSIONS,
        output_format="html5",
    )
    return sanitize_html_fragment(rendered_html)
