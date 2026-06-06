"""Tests for frontend security headers shipped in index.html (Fase 4.2).

The dashboard renders match notes returned by the model via template
literals + escapeHtml. CSP is a defense-in-depth: if a future render
function forgets to escape, the policy still blocks the payload from
executing as JavaScript. These tests lock the contract so anyone who
edits the HTML must update them deliberately.
"""
from __future__ import annotations

from pathlib import Path

import pytest

INDEX_PATH = Path(__file__).resolve().parents[2] / "frontend" / "index.html"


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX_PATH.read_text(encoding="utf-8")


def test_index_declares_strict_csp(index_html: str) -> None:
    """The Content-Security-Policy must restrict every directive used by the
    dashboard to `self` so an injected `<script>` from outside cannot run."""
    assert 'http-equiv="Content-Security-Policy"' in index_html
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
    ):
        assert directive in index_html, f"missing CSP directive: {directive}"


def test_index_sets_nosniff_and_referrer_policy(index_html: str) -> None:
    """Hardens MIME sniffing and referrer leaks alongside CSP."""
    assert 'http-equiv="X-Content-Type-Options"' in index_html
    assert 'content="nosniff"' in index_html
    assert 'name="referrer"' in index_html


def test_index_does_not_load_inline_scripts(index_html: str) -> None:
    """`script-src 'self'` rejects inline scripts; this guarantees we
    never accidentally ship one so the policy stays enforceable."""
    assert "<script>" not in index_html, "inline scripts violate the CSP we just declared"
    # All <script src=...> tags must point inside the frontend/ folder.
    import re

    src_attrs = re.findall(r'<script\s+src="([^"]+)"', index_html)
    assert src_attrs, "expected at least one external script include"
    for src in src_attrs:
        assert src.startswith("./"), f"script src must be relative: {src}"
