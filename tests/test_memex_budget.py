"""Exact MEMEX budget measurement and projection headers."""

from __future__ import annotations

import tiktoken

from syke.memory.memex_budget import (
    MEMEX_TOKEN_ENCODING,
    MEMEX_TOKEN_LIMIT,
    count_memex_tokens,
    format_memex_projection,
    measure_memex,
    strip_memex_header,
)


def test_memex_counter_is_the_canonical_o200k_base_encoding() -> None:
    content = "Routes: native sessions, records, receipts, and graph evidence."
    expected = len(tiktoken.get_encoding(MEMEX_TOKEN_ENCODING).encode_ordinary(content))

    assert count_memex_tokens(content) == expected


def test_memex_budget_accepts_2000_tokens_and_rejects_2001() -> None:
    exact_limit = " x" * MEMEX_TOKEN_LIMIT
    one_over = exact_limit + " x"

    assert measure_memex(exact_limit) == {
        "tokens": 2_000,
        "limit": 2_000,
        "encoding": "o200k_base",
        "fill_pct": 100,
        "over_budget": False,
    }
    assert measure_memex(one_over)["tokens"] == 2_001
    assert measure_memex(one_over)["over_budget"] is True


def test_projection_header_reports_exact_count_and_encoding() -> None:
    rendered = format_memex_projection("current route")

    assert rendered.startswith("# MEMEX [2 / 2,000 tokens · 0% · o200k_base]")
    assert strip_memex_header(rendered) == "current route"
    assert count_memex_tokens(rendered) == 2
