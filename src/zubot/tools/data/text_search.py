"""Text search helpers across readable repository files."""

from __future__ import annotations

from typing import Any

from src.zubot.core.path_policy import can_read, repo_root


def search_text(
    query: str,
    *,
    path_or_glob: str = "**/*",
    case_sensitive: bool = False,
    max_results: int = 200,
) -> dict[str, Any]:
    if not query:
        return {
            "ok": False,
            "query": query,
            "matches": [],
            "error": "Query must be non-empty.",
            "source": "text_search",
        }

    root = repo_root()
    pattern = path_or_glob or "**/*"
    q = query if case_sensitive else query.lower()
    matches: list[dict[str, Any]] = []

    for candidate in root.glob(pattern):
        if not candidate.is_file():
            continue
        rel = candidate.relative_to(root).as_posix()
        if not can_read(rel):
            continue

        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError:
            continue

        for idx, line in enumerate(text.splitlines(), start=1):
            hay = line if case_sensitive else line.lower()
            if q in hay:
                matches.append({"path": rel, "line": idx, "content": line})
                if len(matches) >= max_results:
                    return {
                        "ok": True,
                        "query": query,
                        "matches": matches,
                        "truncated": True,
                        "error": None,
                        "source": "text_search",
                    }

    return {
        "ok": True,
        "query": query,
        "matches": matches,
        "truncated": False,
        "error": None,
        "source": "text_search",
    }
