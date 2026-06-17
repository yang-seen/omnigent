"""Unit tests for :mod:`omnigent.reasoning_effort`.

Covers the public surface: the effort-value constants and provider
support sets, plus the formatting / validation helpers. These functions
gate user-supplied reasoning-effort values across client and runtime
paths, so the exact wording of error messages and the precise return
contract (``None`` vs. validated string vs. raised error) are part of
the public API and asserted exactly here.
"""

from __future__ import annotations

import pytest

from omnigent.llms.errors import PermanentLLMError
from omnigent.reasoning_effort import (
    ANTHROPIC_EFFORTS,
    CLAUDE_EFFORTS,
    CODEX_EFFORTS,
    EFFORT_CLEAR_VALUES,
    EFFORT_VALUES,
    OPENAI_AGENTS_EFFORTS,
    OPENAI_EFFORTS,
    format_supported,
    unsupported_effort_message,
    validate_effort,
    validate_effort_or_llm_error,
)

# The canonical low-to-high ordering used by ``format_supported``.
CANONICAL_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


# --------------------------------------------------------------------------- #
# Constants / provider support sets
# --------------------------------------------------------------------------- #


def test_effort_values_membership() -> None:
    """``EFFORT_VALUES`` is the full set of accepted effort levels."""
    assert frozenset(
        {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
    ) == EFFORT_VALUES


def test_effort_clear_values_membership() -> None:
    """``EFFORT_CLEAR_VALUES`` are the sentinels that reset effort."""
    assert frozenset({"default", "off", "reset"}) == EFFORT_CLEAR_VALUES


def test_openai_efforts_membership() -> None:
    """OpenAI supports everything except ``max``."""
    assert frozenset(
        {"none", "minimal", "low", "medium", "high", "xhigh"}
    ) == OPENAI_EFFORTS
    assert "max" not in OPENAI_EFFORTS


def test_anthropic_efforts_membership() -> None:
    """Anthropic supports the higher band, including ``max`` but not
    ``none``/``minimal``."""
    assert frozenset({"low", "medium", "high", "xhigh", "max"}) == ANTHROPIC_EFFORTS
    assert "none" not in ANTHROPIC_EFFORTS
    assert "minimal" not in ANTHROPIC_EFFORTS


def test_provider_aliases_share_identity() -> None:
    """Alias constants are the same object as their canonical set."""
    assert CLAUDE_EFFORTS is ANTHROPIC_EFFORTS
    assert CODEX_EFFORTS is OPENAI_EFFORTS
    assert OPENAI_AGENTS_EFFORTS is OPENAI_EFFORTS


@pytest.mark.parametrize(
    "support_set",
    [
        EFFORT_VALUES,
        EFFORT_CLEAR_VALUES,
        OPENAI_EFFORTS,
        ANTHROPIC_EFFORTS,
    ],
)
def test_constants_are_frozensets(support_set: frozenset[str]) -> None:
    """Support sets are immutable frozensets (safe to share as module
    globals)."""
    assert isinstance(support_set, frozenset)


def test_provider_sets_are_subsets_of_effort_values() -> None:
    """Every provider-supported effort is a recognized effort value."""
    assert OPENAI_EFFORTS <= EFFORT_VALUES
    assert ANTHROPIC_EFFORTS <= EFFORT_VALUES


def test_clear_values_disjoint_from_effort_values() -> None:
    """Clear sentinels never collide with real effort levels."""
    assert EFFORT_CLEAR_VALUES.isdisjoint(EFFORT_VALUES)


# --------------------------------------------------------------------------- #
# format_supported
# --------------------------------------------------------------------------- #


def test_format_supported_full_set_stable_order() -> None:
    """All values render in the canonical low-to-high order."""
    assert format_supported(EFFORT_VALUES) == ", ".join(CANONICAL_ORDER)


def test_format_supported_orders_regardless_of_input_order() -> None:
    """Input iteration order does not affect output ordering."""
    shuffled = ["max", "none", "high", "low", "xhigh", "medium", "minimal"]
    assert format_supported(shuffled) == ", ".join(CANONICAL_ORDER)


def test_format_supported_dedups_repeats() -> None:
    """Duplicate inputs appear exactly once."""
    assert format_supported(["high", "high", "low", "low", "high"]) == "low, high"


def test_format_supported_filters_unknown_values() -> None:
    """Values outside the canonical order are dropped (subset filtering)."""
    assert format_supported(["low", "banana", "high", "ULTRA"]) == "low, high"


def test_format_supported_empty() -> None:
    """An empty iterable yields an empty string."""
    assert format_supported([]) == ""


def test_format_supported_all_unknown() -> None:
    """If nothing matches the canonical order, the result is empty."""
    assert format_supported(["nope", "", "extreme"]) == ""


@pytest.mark.parametrize(
    ("support_set", "expected"),
    [
        (OPENAI_EFFORTS, "none, minimal, low, medium, high, xhigh"),
        (ANTHROPIC_EFFORTS, "low, medium, high, xhigh, max"),
    ],
)
def test_format_supported_provider_sets(
    support_set: frozenset[str], expected: str
) -> None:
    """Provider support sets render in canonical order."""
    assert format_supported(support_set) == expected


def test_format_supported_accepts_set_input() -> None:
    """A ``set`` (unordered) input still renders deterministically."""
    assert format_supported({"medium", "low"}) == "low, medium"


# --------------------------------------------------------------------------- #
# unsupported_effort_message
# --------------------------------------------------------------------------- #


def test_unsupported_effort_message_exact() -> None:
    """The message quotes the effort, names the provider, and lists the
    supported values in canonical order."""
    msg = unsupported_effort_message("max", "openai", OPENAI_EFFORTS)
    assert msg == (
        "Effort 'max' is not supported by openai; "
        "supported values: none, minimal, low, medium, high, xhigh"
    )


def test_unsupported_effort_message_quotes_effort() -> None:
    """The effort value is repr-quoted (``!r``)."""
    msg = unsupported_effort_message("none", "anthropic", ANTHROPIC_EFFORTS)
    assert "Effort 'none' is not supported by anthropic" in msg
    assert "supported values: low, medium, high, xhigh, max" in msg


# --------------------------------------------------------------------------- #
# validate_effort
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("empty", [None, ""])
def test_validate_effort_none_and_empty_return_none(empty: object) -> None:
    """``None`` and the empty string clear the effort (return ``None``)."""
    assert validate_effort(empty, "openai", OPENAI_EFFORTS) is None


@pytest.mark.parametrize("value", sorted(OPENAI_EFFORTS))
def test_validate_effort_valid_passthrough(value: str) -> None:
    """A supported value is returned unchanged."""
    assert validate_effort(value, "openai", OPENAI_EFFORTS) == value


def test_validate_effort_coerces_non_string_to_str() -> None:
    """A non-string value is stringified before comparison."""
    # ``str(123)`` is not in the support set -> raises.
    with pytest.raises(ValueError):
        validate_effort(123, "openai", OPENAI_EFFORTS)


def test_validate_effort_invalid_raises_with_exact_message() -> None:
    """An unsupported value raises ``ValueError`` with the standard
    message."""
    with pytest.raises(ValueError) as exc_info:
        validate_effort("max", "openai", OPENAI_EFFORTS)
    assert str(exc_info.value) == unsupported_effort_message(
        "max", "openai", OPENAI_EFFORTS
    )


def test_validate_effort_anthropic_rejects_none_level() -> None:
    """``none`` is a real effort value but unsupported by Anthropic."""
    with pytest.raises(ValueError) as exc_info:
        validate_effort("none", "anthropic", ANTHROPIC_EFFORTS)
    assert "not supported by anthropic" in str(exc_info.value)


def test_validate_effort_clear_sentinel_is_not_a_valid_level() -> None:
    """Clear sentinels like ``default`` are not valid efforts and raise."""
    with pytest.raises(ValueError):
        validate_effort("default", "openai", OPENAI_EFFORTS)


# --------------------------------------------------------------------------- #
# validate_effort_or_llm_error
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("empty", [None, ""])
def test_validate_effort_or_llm_error_empty_returns_none(empty: object) -> None:
    """Empty inputs clear effort without raising."""
    assert validate_effort_or_llm_error(empty, "openai", OPENAI_EFFORTS) is None


def test_validate_effort_or_llm_error_valid_passthrough() -> None:
    """A supported value passes through unchanged."""
    assert validate_effort_or_llm_error("high", "openai", OPENAI_EFFORTS) == "high"


def test_validate_effort_or_llm_error_invalid_raises_permanent() -> None:
    """Invalid input raises a non-retryable ``PermanentLLMError`` carrying
    the dedicated error code and the underlying ``ValueError`` as cause."""
    with pytest.raises(PermanentLLMError) as exc_info:
        validate_effort_or_llm_error("max", "openai", OPENAI_EFFORTS)
    err = exc_info.value
    assert err.code == "unsupported_reasoning_effort"
    assert str(err) == unsupported_effort_message("max", "openai", OPENAI_EFFORTS)
    assert isinstance(err.__cause__, ValueError)
