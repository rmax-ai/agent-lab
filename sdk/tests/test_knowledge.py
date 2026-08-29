"""Markdown knowledge layer: loading, frontmatter, search, injection delimiter."""

from __future__ import annotations

from pathlib import Path

from agentlab.sdk.knowledge import (
    DATA_DELIMITER,
    MarkdownKnowledgeProvider,
    render_for_context,
)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


async def test_markdown_provider_loads_frontmatter_and_fallback(tmp_path: Path) -> None:
    _write(
        tmp_path / "device-policy.md",
        "---\n"
        "title: Device Policy\n"
        "owner: IT\n"
        "status: active\n"
        "updated: 2026-01-01\n"
        "---\n"
        "\n"
        "# Device Policy\n"
        "\n"
        "MacBook Pro is the standard device. MacBook Pro is issued to engineers.\n",
    )
    _write(
        tmp_path / "substitution.md",
        "# Substitution\n"
        "\n"
        "A MacBook Air may replace a MacBook Pro with manager approval.\n",
    )

    provider = MarkdownKnowledgeProvider(str(tmp_path))

    assert len(provider.documents) == 2
    assert [document.id for document in provider.documents] == [
        "device-policy",
        "substitution",
    ]

    policy = await provider.get_document("device-policy")
    assert policy is not None
    assert policy.title == "Device Policy"
    assert policy.metadata["owner"] == "IT"

    substitution = await provider.get_document("substitution")
    assert substitution is not None
    assert substitution.title == "Substitution"
    assert substitution.metadata == {}

    assert await provider.get_document("missing") is None


async def test_search_scores_and_sorts_case_insensitively(tmp_path: Path) -> None:
    _write(
        tmp_path / "device-policy.md",
        "---\n"
        "title: Device Policy\n"
        "---\n"
        "\n"
        "MacBook Pro is the standard device. MacBook Pro is issued to engineers.\n",
    )
    _write(
        tmp_path / "substitution.md",
        "# Substitution\n"
        "\n"
        "A MacBook Air may replace a MacBook Pro with manager approval.\n",
    )

    provider = MarkdownKnowledgeProvider(str(tmp_path))

    results = await provider.search("macbook pro")
    assert [document.id for document in results] == ["device-policy", "substitution"]

    assert await provider.search("no such phrase") == []


async def test_render_for_context_wraps_with_delimiter(tmp_path: Path) -> None:
    _write(
        tmp_path / "device-policy.md",
        "---\n"
        "title: Device Policy\n"
        "---\n"
        "\n"
        "# Device Policy\n"
        "\n"
        "MacBook Pro is the standard device.\n",
    )

    provider = MarkdownKnowledgeProvider(str(tmp_path))
    document = await provider.get_document("device-policy")
    assert document is not None

    rendered = render_for_context(document)
    assert DATA_DELIMITER in rendered
    assert "Device Policy" in rendered
    assert "MacBook Pro is the standard device." in rendered
