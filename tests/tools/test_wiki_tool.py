"""Tests for local wiki retrieval tools."""

from pathlib import Path
from unittest.mock import patch

import pytest


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestWikiConfig:
    def test_wiki_root_uses_configured_path(self, tmp_path):
        from tools.wiki_tool import _get_wiki_root

        wiki = tmp_path / "wiki"
        wiki.mkdir()
        with patch("hermes_cli.config.load_config", return_value={"wiki": {"path": str(wiki)}}):
            assert _get_wiki_root() == wiki.resolve()

    def test_wiki_root_supports_legacy_wiki_path_key(self, tmp_path):
        from tools.wiki_tool import _get_wiki_root

        wiki = tmp_path / "wiki"
        wiki.mkdir()
        with patch("hermes_cli.config.load_config", return_value={"wiki_path": str(wiki)}):
            assert _get_wiki_root() == wiki.resolve()


class TestWikiSearch:
    def test_search_returns_ranked_markdown_matches_with_citations(self, tmp_path):
        from tools.wiki_tool import wiki_search

        wiki = tmp_path / "wiki"
        _write(
            wiki / "AI" / "Hindsight.md",
            "# Hindsight\n\nHindsight is the local embedded memory provider for Hermes.",
        )
        _write(
            wiki / "AI" / "Unrelated.md",
            "# Bananas\n\nThis page is about fruit.",
        )

        with patch("hermes_cli.config.load_config", return_value={"wiki": {"path": str(wiki)}}):
            result = wiki_search("embedded memory provider", limit=5)

        assert result["query"] == "embedded memory provider"
        assert result["wiki_root"] == str(wiki.resolve())
        assert len(result["results"]) == 1
        match = result["results"][0]
        assert match["path"] == "AI/Hindsight.md"
        assert match["title"] == "Hindsight"
        assert "local embedded memory provider" in match["snippet"]
        assert match["citation"] == f"{wiki.resolve()}/AI/Hindsight.md"

    def test_search_ignores_dotdirs_and_non_markdown_files(self, tmp_path):
        from tools.wiki_tool import wiki_search

        wiki = tmp_path / "wiki"
        _write(wiki / ".trash" / "Secret.md", "# Secret\n\nneedle")
        _write(wiki / "notes.txt", "needle")
        _write(wiki / "Visible.md", "# Visible\n\nneedle")

        with patch("hermes_cli.config.load_config", return_value={"wiki": {"path": str(wiki)}}):
            result = wiki_search("needle", limit=10)

        assert [r["path"] for r in result["results"]] == ["Visible.md"]

    def test_search_skips_symlinks_that_escape_wiki_root(self, tmp_path):
        from tools.wiki_tool import wiki_search

        wiki = tmp_path / "wiki"
        wiki.mkdir()
        outside = tmp_path / "secret.md"
        outside.write_text("# Secret\n\nneedle outside secret", encoding="utf-8")
        (wiki / "Visible.md").write_text("# Visible\n\nneedle public", encoding="utf-8")
        (wiki / "Linked.md").symlink_to(outside)

        with patch("hermes_cli.config.load_config", return_value={"wiki": {"path": str(wiki)}}):
            result = wiki_search("needle", limit=10)

        assert [r["path"] for r in result["results"]] == ["Visible.md"]
        assert "outside secret" not in str(result)

    def test_search_skips_symlinked_directories_that_escape_wiki_root(self, tmp_path):
        from tools.wiki_tool import wiki_search

        wiki = tmp_path / "wiki"
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir(parents=True)
        wiki.mkdir()
        (outside_dir / "Secret.md").write_text("# Secret\n\nneedle outside dir", encoding="utf-8")
        (wiki / "Visible.md").write_text("# Visible\n\nneedle public", encoding="utf-8")
        (wiki / "linked-dir").symlink_to(outside_dir, target_is_directory=True)

        with patch("hermes_cli.config.load_config", return_value={"wiki": {"path": str(wiki)}}):
            result = wiki_search("needle", limit=10)

        assert [r["path"] for r in result["results"]] == ["Visible.md"]
        assert "outside dir" not in str(result)

    def test_search_requires_non_empty_query(self, tmp_path):
        from tools.wiki_tool import wiki_search

        wiki = tmp_path / "wiki"
        wiki.mkdir()
        with patch("hermes_cli.config.load_config", return_value={"wiki": {"path": str(wiki)}}):
            with pytest.raises(ValueError, match="query"):
                wiki_search("   ")


class TestWikiRead:
    def test_read_returns_page_content_and_metadata(self, tmp_path):
        from tools.wiki_tool import wiki_read

        wiki = tmp_path / "wiki"
        _write(wiki / "AI" / "Hindsight.md", "# Hindsight\n\nBody text")

        with patch("hermes_cli.config.load_config", return_value={"wiki": {"path": str(wiki)}}):
            result = wiki_read("AI/Hindsight.md")

        assert result["path"] == "AI/Hindsight.md"
        assert result["title"] == "Hindsight"
        assert result["citation"] == f"{wiki.resolve()}/AI/Hindsight.md"
        assert result["content"] == "# Hindsight\n\nBody text"
        assert result["truncated"] is False

    def test_read_enforces_wiki_root_scope(self, tmp_path):
        from tools.wiki_tool import wiki_read

        wiki = tmp_path / "wiki"
        wiki.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("nope", encoding="utf-8")

        with patch("hermes_cli.config.load_config", return_value={"wiki": {"path": str(wiki)}}):
            with pytest.raises(ValueError, match="outside the configured wiki root"):
                wiki_read("../outside.md")

    def test_read_truncates_with_flag(self, tmp_path):
        from tools.wiki_tool import wiki_read

        wiki = tmp_path / "wiki"
        _write(wiki / "Long.md", "# Long\n\n" + "x" * 100)

        with patch("hermes_cli.config.load_config", return_value={"wiki": {"path": str(wiki)}}):
            result = wiki_read("Long.md", max_chars=20)

        assert result["truncated"] is True
        assert len(result["content"]) <= 20


class TestToolRegistration:
    def test_wiki_toolset_is_registered_and_in_core_tools(self):
        import toolsets
        from tools.registry import registry
        import tools.wiki_tool  # noqa: F401 - import triggers registration

        assert set(toolsets.TOOLSETS["wiki"]["tools"]) == {"wiki_search", "wiki_read"}
        assert "wiki_search" in toolsets._HERMES_CORE_TOOLS
        assert "wiki_read" in toolsets._HERMES_CORE_TOOLS
        assert registry.get_entry("wiki_search") is not None
        assert registry.get_entry("wiki_read") is not None
