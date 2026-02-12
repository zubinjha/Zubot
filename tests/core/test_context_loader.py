from pathlib import Path

from src.zubot.core.context_loader import (
    load_base_context,
    load_context_bundle,
    select_supplemental_context_files,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_base_context(tmp_path: Path):
    _write(tmp_path / "context/KERNEL.md", "kernel assumptions")
    _write(tmp_path / "context/AGENT.md", "agent rules")
    _write(tmp_path / "context/SOUL.md", "soul principles")
    _write(tmp_path / "context/USER.md", "user profile")
    _write(tmp_path / "context/more-about-human/README.md", "index of user profile details")

    bundle = load_base_context(root=tmp_path)
    assert "context/KERNEL.md" in bundle
    assert "context/AGENT.md" in bundle
    assert "context/SOUL.md" in bundle
    assert "context/USER.md" in bundle
    assert "context/more-about-human/README.md" in bundle


def test_select_supplemental_context_files_by_query(tmp_path: Path):
    _write(tmp_path / "context/more-about-human/resume.md", "Software engineer at Ohio State project")
    _write(tmp_path / "context/more-about-human/projects/newsletter-builder.md", "Newsletter project details")
    _write(tmp_path / "context/more-about-human/projects/other.md", "Unrelated content")

    selected = select_supplemental_context_files("newsletter project", root=tmp_path, max_files=2)
    assert any("newsletter-builder.md" in path for path in selected)


def test_load_context_bundle(tmp_path: Path):
    _write(tmp_path / "context/KERNEL.md", "kernel")
    _write(tmp_path / "context/AGENT.md", "agent")
    _write(tmp_path / "context/SOUL.md", "soul")
    _write(tmp_path / "context/USER.md", "user")
    _write(tmp_path / "context/more-about-human/README.md", "more about human index")
    _write(tmp_path / "context/more-about-human/resume.md", "resume with project")

    bundle = load_context_bundle(query="resume", root=tmp_path, max_supplemental_files=2)
    assert "base" in bundle
    assert "supplemental" in bundle
    assert "context/KERNEL.md" in bundle["base"]
    assert "context/AGENT.md" in bundle["base"]
    assert "context/more-about-human/README.md" in bundle["base"]
