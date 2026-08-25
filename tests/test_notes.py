from hashlib import sha256
from pathlib import Path

import pytest

from agent_notes_mcp.config import RootConfig
from agent_notes_mcp.notes import (
    NoteConflictError,
    append_note,
    create_note,
    list_notes,
    move_note,
    read_note,
    replace_note,
    search_notes,
)
from agent_notes_mcp.paths import PathAccessError


@pytest.fixture
def root(tmp_path: Path) -> RootConfig:
    (tmp_path / "memos").mkdir()
    (tmp_path / ".trash").mkdir()
    (tmp_path / "memos" / "age.md").write_text(
        "---\ntitle: age command\nproject: notes-service\ndate: 2026-07-31\nstatus: new\ntype: feedback\ntags:\n  - tool/age\n---\n\nUse age to encrypt files.\n",
        encoding="utf-8",
    )
    (tmp_path / ".trash" / "old.md").write_text("age old copy", encoding="utf-8")
    return RootConfig("notes", tmp_path, (".trash/**",))


def test_searches_filename_frontmatter_and_body_but_excludes_trash(
    root: RootConfig,
) -> None:
    results = search_notes(root, "age")

    assert [result["path"] for result in results] == ["memos/age.md"]
    assert results[0]["title"] == "age command"
    assert results[0]["project"] == "notes-service"
    assert results[0]["tags"] == ["tool/age"]
    assert results[0]["date"] == "2026-07-31"
    assert results[0]["status"] == "new"
    assert results[0]["type"] == "feedback"
    assert results[0]["search_score"] > 0


def test_filters_notes_by_frontmatter_metadata(root: RootConfig) -> None:
    (root.path / "memos" / "other.md").write_text(
        "---\ntitle: other\nproject: other-service\nstatus: accepted\ntype: plan\ntags:\n  - tool/age\n  - design\n---\n",
        encoding="utf-8",
    )

    results = list_notes(
        root,
        project="notes-service",
        status=["new", "triage"],
        note_type=["feedback"],
        tags=["tool/age"],
    )

    assert [result["path"] for result in results] == ["memos/age.md"]


def test_filters_notes_by_arbitrary_scalar_frontmatter(root: RootConfig) -> None:
    (root.path / "memos" / "active.md").write_text(
        "---\ntitle: active branch\nlifecycle: active\nbranch: feature/notes\n---\n",
        encoding="utf-8",
    )
    (root.path / "memos" / "merged.md").write_text(
        "---\ntitle: merged branch\nlifecycle: merged\nbranch: feature/notes\n---\nactive\n",
        encoding="utf-8",
    )
    (root.path / "memos" / "array.md").write_text(
        "---\ntitle: array\nlifecycle: [active]\n---\n",
        encoding="utf-8",
    )
    (root.path / "memos" / "nested.md").write_text(
        "---\nmetadata:\n  lifecycle: active\n---\n",
        encoding="utf-8",
    )

    results = list_notes(
        root, frontmatter={"lifecycle": "ACTIVE", "branch": "feature/notes"}
    )

    assert [result["path"] for result in results] == ["memos/active.md"]

    searched = search_notes(root, "branch", frontmatter={"lifecycle": "active"})
    assert [result["path"] for result in searched] == ["memos/active.md"]

    with pytest.raises(ValueError, match="Use tags"):
        list_notes(root, frontmatter={"tags": "tool/age"})


def test_search_filters_notes_before_ranking(root: RootConfig) -> None:
    (root.path / "memos" / "newer-age.md").write_text(
        "---\ntitle: age plan\nproject: notes-service\nstatus: accepted\ntype: plan\ntags:\n  - design\n---\n\nage age age\n",
        encoding="utf-8",
    )

    results = search_notes(root, "age", status=["new"], tags=["tool/age"])

    assert [result["path"] for result in results] == ["memos/age.md"]


def test_replace_requires_matching_hash_and_preserves_manual_change(
    root: RootConfig,
) -> None:
    first = read_note(root, "memos/age.md")
    path = root.path / "memos" / "age.md"
    path.write_text("manual edit", encoding="utf-8")

    with pytest.raises(NoteConflictError):
        replace_note(root, "memos/age.md", "codex edit", first.sha256)

    assert path.read_text(encoding="utf-8") == "manual edit"


def test_replace_requires_a_hash(root: RootConfig) -> None:
    with pytest.raises(ValueError, match="expected_sha256"):
        replace_note(root, "memos/age.md", "codex edit", "")


def test_create_append_and_replace(root: RootConfig) -> None:
    created = create_note(root, "memos/new.md", "first")
    assert created.content == "first"

    appended = append_note(root, "memos/new.md", "second")
    assert appended.content == "first\nsecond"

    replaced = replace_note(root, "memos/new.md", "third", appended.sha256)
    assert replaced.content == "third"
    assert replaced.sha256 == sha256(b"third").hexdigest()
    assert replaced.to_reference_dict() == {
        "path": "memos/new.md",
        "sha256": sha256(b"third").hexdigest(),
    }


def test_move_creates_parent_and_does_not_overwrite(root: RootConfig) -> None:
    source = read_note(root, "memos/age.md")

    moved = move_note(
        root,
        "memos/age.md",
        root,
        "organized/security/age.md",
        source.sha256,
        create_parents=True,
    )

    assert moved.path == "organized/security/age.md"
    assert not (root.path / "memos" / "age.md").exists()
    assert (root.path / "organized" / "security" / "age.md").exists()
    with pytest.raises(FileExistsError):
        move_note(
            root,
            "organized/security/age.md",
            root,
            "organized/security/age.md",
            moved.sha256,
        )


def test_move_rejects_stale_source(root: RootConfig) -> None:
    source = read_note(root, "memos/age.md")
    (root.path / "memos" / "age.md").write_text("manual edit", encoding="utf-8")

    with pytest.raises(NoteConflictError):
        move_note(root, "memos/age.md", root, "moved.md", source.sha256)

    assert not (root.path / "moved.md").exists()


def test_move_allows_different_write_roots(root: RootConfig) -> None:
    destination_root_path = root.path / "inbox"
    destination_root_path.mkdir()
    destination_root = RootConfig("inbox", destination_root_path)
    source = read_note(root, "memos/age.md")

    moved = move_note(root, "memos/age.md", destination_root, "age.md", source.sha256)

    assert moved.path == "age.md"
    assert (destination_root_path / "age.md").exists()
    assert not (root.path / "memos" / "age.md").exists()


def test_rejects_parent_escape(root: RootConfig) -> None:
    with pytest.raises(PathAccessError):
        read_note(root, "../secret.md")


def test_read_rejects_excluded_notes_at_any_depth(root: RootConfig) -> None:
    nested = root.path / ".trash" / "nested"
    nested.mkdir()
    (nested / "old.md").write_text("nested old copy", encoding="utf-8")

    for path in (".trash/old.md", ".trash/nested/old.md"):
        with pytest.raises(PathAccessError, match="excluded"):
            read_note(root, path)
