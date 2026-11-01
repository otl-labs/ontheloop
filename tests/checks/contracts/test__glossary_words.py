"""Mirror test for the shared relative-word matcher (Decision 240)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.contracts import _glossary_words as words

_CONTRACT = yaml.safe_load((Path(__file__).parents[3] / "docs" / "contracts" / "glossary.yaml").read_text(encoding="utf-8"))
_IDS = words.identifiers(_CONTRACT)


def _uses(text: str, *, yaml_keys: bool = False) -> list[tuple[int, str]]:
    return words.unqualified_uses(text, yaml_keys=yaml_keys, identifiers=_IDS)


def test_identifiers_lists_the_contract_tokens() -> None:
    assert {"agent-platform-*", "platform-security-*", "PlatformDev", "agent_platform", "platform-roadmap"} <= set(_IDS)
    assert "platform" not in _IDS


@pytest.mark.parametrize(
    "text",
    [
        "owner='platform'",
        "owner: platform",
        "the PLATFORM axis",
        'a "platform" in quotes',
        "a `product` in backticks",
        "an adopter product ships",
        "the adopters' products ship",
        "agent-platform",
        "platform-x.y",
        "foo-platform-bar",
        "agent-platform-data-lake",
        "platform-security-detector",
        "the platform-roadmap route",
        "docs/ROADMAP-PLATFORM.yaml",
        "ontheloop/platform.yaml",
        "profile agent_platform",
        "PlatformDev",
        "ROADMAP-PLATFORM",
        "see (x): 'platform' here",
        "a.platform.b/c",
    ],
)
def test_exemptions_are_not_uses(text: str) -> None:
    assert _uses(text) == []


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("The platforms differ.", "platforms"),
        ("Product's scope", "Product"),
        ('a "quoted span of far more than six words here" platform', "platform"),
        ("a platform-only repository", "platform"),
        ("the product-boundary", "product"),
        ("Platform: the runner", "Platform"),
        ("ownership of the platform", "platform"),
        ('a quote that never closes "platform', "platform"),
    ],
)
def test_uses_are_found(text: str, word: str) -> None:
    assert _uses(text) == [(1, word)]


def test_yaml_keys_exempt_only_when_asked() -> None:
    assert _uses("platform: runner", yaml_keys=True) == []
    assert _uses("  - product: runner", yaml_keys=True) == []
    assert _uses("platform: runner") == [(1, "platform")]
    assert _uses("key: the platform", yaml_keys=True) == [(1, "platform")]


def test_line_numbers_are_one_based() -> None:
    assert _uses("clean\nthe product\n") == [(2, "product")]


def test_say_instead_folds_plural_and_case() -> None:
    assert words.say_instead("Platforms", _CONTRACT) == _CONTRACT["relative_words"]["platform"]["say_instead"]
    assert words.say_instead("PRODUCT", _CONTRACT) == _CONTRACT["relative_words"]["product"]["say_instead"]
