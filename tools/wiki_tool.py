#!/usr/bin/env python3
"""Local wiki retrieval tools.

The wiki is a compiled knowledge base, not personal memory. These tools expose
explicit search/read access to the configured wiki root and always return source
paths so answers can cite the canonical page.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List


DEFAULT_MAX_CHARS = 20_000
DEFAULT_SNIPPET_CHARS = 320
SUPPORTED_SUFFIXES = {".md", ".markdown"}


def _get_wiki_root() -> Path:
    """Return the configured wiki root.

    Preferred config shape is ``wiki.path``. ``wiki_path`` is accepted for older
    local configs because several Hermes profiles already carried that key.
    """

    try:
        from hermes_cli.config import load_config

        config = load_config()
    except Exception:
        config = {}

    raw_path = None
    if isinstance(config, dict):
        wiki_cfg = config.get("wiki")
        if isinstance(wiki_cfg, dict):
            raw_path = wiki_cfg.get("path") or wiki_cfg.get("root")
        raw_path = raw_path or config.get("wiki_path")

    if not raw_path:
        raw_path = "~/Documents/Sync/wiki"

    root = Path(str(raw_path)).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Configured wiki root does not exist: {root}")
    return root


def _is_indexable(path: Path, root: Path) -> bool:
    if path.is_symlink():
        return False
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return False
    try:
        path.resolve().relative_to(root)
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel_parts)


def _iter_pages(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and _is_indexable(path, root):
            yield path


def _title_for(path: Path, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem


def _snippet_for(content: str, query: str, max_chars: int = DEFAULT_SNIPPET_CHARS) -> str:
    terms = [t for t in re.findall(r"[\w-]+", query, flags=re.UNICODE) if t]
    lowered = content.lower()
    positions = [lowered.find(term.lower()) for term in terms]
    positions = [pos for pos in positions if pos >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - max_chars // 3)
    end = min(len(content), start + max_chars)
    snippet = content[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet += "…"
    return " ".join(snippet.split())


def _safe_fts_query(query: str) -> str:
    terms = re.findall(r"[\w-]+", query, flags=re.UNICODE)
    if not terms:
        raise ValueError("query must contain at least one searchable term")
    # Prefix matching improves recall for page titles and project terminology.
    return " OR ".join(f'"{term}"*' for term in terms[:12])


def _build_memory_index(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE pages USING fts5(path UNINDEXED, title, content, tokenize='unicode61')"
    )
    rows = []
    for path in _iter_pages(root):
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        rows.append((rel, _title_for(path, content), content))
    conn.executemany("INSERT INTO pages(path, title, content) VALUES (?, ?, ?)", rows)
    return conn


def wiki_search(query: str, limit: int = 5, snippet_chars: int = DEFAULT_SNIPPET_CHARS) -> Dict[str, Any]:
    """Search the configured local wiki and return path-cited results."""

    query = str(query or "").strip()
    if not query:
        raise ValueError("query must be non-empty")
    limit = max(1, min(int(limit or 5), 20))
    snippet_chars = max(120, min(int(snippet_chars or DEFAULT_SNIPPET_CHARS), 2_000))

    root = _get_wiki_root()
    fts_query = _safe_fts_query(query)
    conn = _build_memory_index(root)
    try:
        rows = conn.execute(
            """
            SELECT path, title, content, bm25(pages) AS score
            FROM pages
            WHERE pages MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
    finally:
        conn.close()

    results = []
    for rel_path, title, content, score in rows:
        citation = str((root / rel_path).resolve())
        results.append(
            {
                "path": rel_path,
                "title": title,
                "snippet": _snippet_for(content, query, snippet_chars),
                "citation": citation,
                "score": score,
            }
        )

    return {
        "query": query,
        "wiki_root": str(root),
        "results": results,
    }


def _resolve_page_path(page_path: str, root: Path) -> Path:
    raw = str(page_path or "").strip()
    if not raw:
        raise ValueError("path must be non-empty")
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path is outside the configured wiki root") from exc
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"Wiki page not found: {raw}")
    if not _is_indexable(candidate, root):
        raise ValueError("path is not a readable wiki text page")
    return candidate


def wiki_read(path: str, max_chars: int = DEFAULT_MAX_CHARS) -> Dict[str, Any]:
    """Read one wiki page by relative path and return content plus citation."""

    root = _get_wiki_root()
    page = _resolve_page_path(path, root)
    content = page.read_text(encoding="utf-8", errors="replace")
    max_chars = max(1, min(int(max_chars or DEFAULT_MAX_CHARS), 100_000))
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]

    return {
        "path": page.relative_to(root).as_posix(),
        "title": _title_for(page, content),
        "citation": str(page.resolve()),
        "content": content,
        "truncated": truncated,
        "wiki_root": str(root),
    }


def _handler(fn, args: Dict[str, Any]) -> str:
    from tools.registry import tool_error, tool_result

    try:
        return tool_result(fn(**args))
    except Exception as exc:
        return tool_error(str(exc))


WIKI_SEARCH_SCHEMA = {
    "name": "wiki_search",
    "description": (
        "Search the configured local wiki/knowledge base. Use this when a user asks about "
        "project knowledge, internal terminology, prior research, AI/tooling concepts, or "
        "anything likely to live in the wiki rather than personal memory. Returns snippets "
        "with canonical file-path citations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {"type": "integer", "description": "Maximum results, 1-20.", "default": 5},
            "snippet_chars": {
                "type": "integer",
                "description": "Approximate maximum snippet length per result.",
                "default": DEFAULT_SNIPPET_CHARS,
            },
        },
        "required": ["query"],
    },
}

WIKI_READ_SCHEMA = {
    "name": "wiki_read",
    "description": (
        "Read a specific page from the configured local wiki by relative path. Use after "
        "wiki_search when the snippet is not enough. The path is sandboxed to the wiki root."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Wiki-relative page path, e.g. AI/Hindsight.md."},
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return, capped at 100000.",
                "default": DEFAULT_MAX_CHARS,
            },
        },
        "required": ["path"],
    },
}


def check_wiki_requirements() -> bool:
    try:
        _get_wiki_root()
        return True
    except Exception:
        return False


from tools.registry import registry

registry.register(
    name="wiki_search",
    toolset="wiki",
    schema=WIKI_SEARCH_SCHEMA,
    handler=lambda args, **kw: _handler(wiki_search, args),
    check_fn=check_wiki_requirements,
    emoji="📖",
)

registry.register(
    name="wiki_read",
    toolset="wiki",
    schema=WIKI_READ_SCHEMA,
    handler=lambda args, **kw: _handler(wiki_read, args),
    check_fn=check_wiki_requirements,
    emoji="📖",
)
