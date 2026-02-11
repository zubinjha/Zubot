"""Load base and situational context files for agent turns."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .path_policy import repo_root

DEFAULT_BASE_CONTEXT_FILES = [
    "context/AGENT.md",
    "context/SOUL.md",
    "context/USER.md",
    "context/more-about-human/README.md",
]

DEFAULT_SUPPLEMENTAL_GLOBS = [
    "context/more-about-human/*.md",
    "context/more-about-human/projects/*.md",
]


def _read_text(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def load_base_context(
    *,
    root: Path | None = None,
    files: list[str] | None = None,
) -> dict[str, str]:
    root_path = root or repo_root()
    paths = files or DEFAULT_BASE_CONTEXT_FILES
    loaded: dict[str, str] = {}
    for rel in paths:
        text = _read_text(root_path / rel)
        if text:
            loaded[rel] = text
    return loaded


def _score_file(query: str, rel_path: str, text: str) -> int:
    if not query.strip():
        return 0
    q_tokens = [tok for tok in query.lower().split() if len(tok) >= 3]
    hay = f"{rel_path.lower()} {text[:2000].lower()}"
    score = 0
    for token in q_tokens:
        if token in hay:
            score += 1
    return score


def select_supplemental_context_files(
    query: str,
    *,
    root: Path | None = None,
    max_files: int = 3,
    globs: list[str] | None = None,
) -> list[str]:
    root_path = root or repo_root()
    patterns = globs or DEFAULT_SUPPLEMENTAL_GLOBS
    candidates: list[tuple[int, str]] = []

    for pattern in patterns:
        for file_path in sorted(root_path.glob(pattern)):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(root_path).as_posix()
            text = _read_text(file_path)
            if not text:
                continue
            score = _score_file(query, rel, text)
            if score > 0:
                candidates.append((score, rel))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    seen: set[str] = set()
    selected: list[str] = []
    for _, rel in candidates:
        if rel in seen:
            continue
        seen.add(rel)
        selected.append(rel)
        if len(selected) >= max_files:
            break
    return selected


def load_context_bundle(
    *,
    query: str,
    root: Path | None = None,
    max_supplemental_files: int = 3,
) -> dict[str, Any]:
    root_path = root or repo_root()
    base = load_base_context(root=root_path)
    supplemental_paths = select_supplemental_context_files(
        query,
        root=root_path,
        max_files=max_supplemental_files,
    )
    supplemental: dict[str, str] = {}
    for rel in supplemental_paths:
        text = _read_text(root_path / rel)
        if text:
            supplemental[rel] = text

    return {
        "base": base,
        "supplemental": supplemental,
    }
