from pathlib import Path
import asyncio
from hashlib import sha256
from zoneinfo import ZoneInfo

import pytest

from agent_notes_mcp.config import RootConfig, Settings
from agent_notes_mcp.config import NoteMetadataConfig
from agent_notes_mcp.notes import NoteConflictError
from agent_notes_mcp.server import (
    create_server,
    describe_roots,
    list_notes_for_roots,
    search_notes_for_roots,
)


def test_server_can_be_created(tmp_path: Path) -> None:
    settings = Settings(
        read_roots=(RootConfig("read", tmp_path),),
        write_roots=(RootConfig("write", tmp_path),),
    )

    server = create_server(settings)

    assert server.name == "Local Wiki"
    tools = asyncio.run(server.list_tools())

    assert [tool.name for tool in tools] == [
        "search_notes",
        "read_note",
        "list_roots",
        "list_notes",
        "write_note",
        "append_note",
        "move_note",
        "update_managed_blocks",
        "batch_update_notes",
    ]
    schemas = {tool.name: tool.inputSchema for tool in tools}
    tool_by_name = {tool.name: tool for tool in tools}
    assert "activity_summary" in schemas["write_note"]["properties"]
    assert "activity_summary" in schemas["append_note"]["properties"]
    assert "activity_summary" in schemas["move_note"]["properties"]
    assert "include_content" in schemas["write_note"]["properties"]
    assert "include_content" in schemas["append_note"]["properties"]
    assert "include_content" in schemas["move_note"]["properties"]
    assert {"expected_sha256", "updates", "activity_summary", "include_content"} <= set(
        schemas["update_managed_blocks"]["properties"]
    )
    assert {"root_id", "operations", "include_content"} <= set(
        schemas["batch_update_notes"]["properties"]
    )
    operation_schema = schemas["batch_update_notes"]["properties"]["operations"][
        "items"
    ]
    definitions = schemas["batch_update_notes"]["$defs"]
    variants = [
        definitions[variant["$ref"].rpartition("/")[2]]
        for variant in operation_schema["anyOf"]
    ]
    assert {variant["properties"]["operation"]["const"] for variant in variants} == {
        "replace",
        "append",
        "update_managed_blocks",
    }
    assert {"path", "expected_sha256", "content"} <= set(
        variants[0]["properties"]
    )
    assert {"path", "expected_sha256", "updates"} <= set(
        variants[2]["properties"]
    )
    assert {"project", "status", "note_type", "tags", "frontmatter"} <= set(
        schemas["search_notes"]["properties"]
    )
    assert {"project", "status", "note_type", "tags", "frontmatter"} <= set(
        schemas["list_notes"]["properties"]
    )
    assert "top-level scalar" in tool_by_name["search_notes"].description
    assert "Arrays and nested" in tool_by_name["list_notes"].description
    expected_annotations = {
        "search_notes": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "read_note": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "list_roots": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "list_notes": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "write_note": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "append_note": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "move_note": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "update_managed_blocks": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "batch_update_notes": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }
    assert {
        name: tool.annotations.model_dump(exclude_none=True)
        for name, tool in tool_by_name.items()
    } == expected_annotations


def test_write_tools_return_compact_results_unless_content_is_requested(
    tmp_path: Path,
) -> None:
    settings = Settings(read_roots=(), write_roots=(RootConfig("notes", tmp_path),))
    server = create_server(settings)
    tools = server._tool_manager._tools

    created = tools["write_note"].fn(
        root_id="notes", path="note.md", content="first", mode="create"
    )
    assert created == {
        "path": "note.md",
        "sha256": sha256(b"first").hexdigest(),
    }

    appended = tools["append_note"].fn(
        root_id="notes", path="note.md", content="second", include_content=True
    )
    assert appended["path"] == "note.md"
    assert appended["content"] == "first\nsecond"
    assert "sha256" in appended


def test_update_managed_blocks_replaces_only_validated_blocks(tmp_path: Path) -> None:
    settings = Settings(read_roots=(), write_roots=(RootConfig("notes", tmp_path),))
    server = create_server(settings)
    tools = server._tool_manager._tools
    original = (
        "# Handoff\n\n"
        "<!-- agent-notes:managed next-actions -->\n"
        "## Next actions\n- old\n"
        "<!-- /agent-notes:managed next-actions -->\n\n"
        "Manual note.\n"
    )
    tools["write_note"].fn(
        root_id="notes", path="handoff.md", content=original, mode="create"
    )

    result = tools["update_managed_blocks"].fn(
        root_id="notes",
        path="handoff.md",
        expected_sha256=sha256(original.encode()).hexdigest(),
        updates=[{"block_id": "next-actions", "content": "## Next actions\n- new"}],
        include_content=True,
    )

    assert result["content"] == original.replace("- old", "- new")
    assert result["path"] == "handoff.md"


def test_update_managed_blocks_rejects_a_stale_note(tmp_path: Path) -> None:
    settings = Settings(read_roots=(), write_roots=(RootConfig("notes", tmp_path),))
    server = create_server(settings)
    tools = server._tool_manager._tools
    original = (
        "<!-- agent-notes:managed next-actions -->\nold\n"
        "<!-- /agent-notes:managed next-actions -->\n"
    )
    tools["write_note"].fn(
        root_id="notes", path="handoff.md", content=original, mode="create"
    )
    (tmp_path / "handoff.md").write_text("manual edit\n", encoding="utf-8")

    with pytest.raises(NoteConflictError, match="changed after it was read"):
        tools["update_managed_blocks"].fn(
            root_id="notes",
            path="handoff.md",
            expected_sha256=sha256(original.encode()).hexdigest(),
            updates=[{"block_id": "next-actions", "content": "new"}],
        )

    assert (tmp_path / "handoff.md").read_text(encoding="utf-8") == "manual edit\n"


def test_write_tools_add_updated_metadata_when_enabled(tmp_path: Path) -> None:
    settings = Settings(
        read_roots=(),
        write_roots=(RootConfig("notes", tmp_path),),
        note_metadata=NoteMetadataConfig(ZoneInfo("UTC")),
    )
    server = create_server(settings)
    tools = server._tool_manager._tools

    created = tools["write_note"].fn(
        root_id="notes", path="note.md", content="# Note\n", mode="create", include_content=True
    )
    assert created["content"].startswith("---\nupdated: ")
    assert created["content"].endswith("---\n\n# Note\n")

    appended = tools["append_note"].fn(
        root_id="notes", path="note.md", content="more\n", include_content=True
    )
    assert appended["content"].count("updated:") == 1
    assert appended["content"].endswith("# Note\nmore\n")

    moved = tools["move_note"].fn(
        source_root_id="notes",
        source_path="note.md",
        destination_root_id="notes",
        destination_path="moved.md",
        expected_sha256=appended["sha256"],
        include_content=True,
    )
    assert moved["content"].count("updated:") == 1

    managed = tools["write_note"].fn(
        root_id="notes",
        path="handoff.md",
        content=(
            "<!-- agent-notes:managed state -->\nold\n"
            "<!-- /agent-notes:managed state -->\n"
        ),
        mode="create",
        include_content=True,
    )
    updated = tools["update_managed_blocks"].fn(
        root_id="notes",
        path="handoff.md",
        expected_sha256=managed["sha256"],
        updates=[{"block_id": "state", "content": "new"}],
        include_content=True,
    )
    assert updated["content"].count("updated:") == 1
    assert "<!-- agent-notes:managed state -->\nnew\n" in updated["content"]


def test_batch_update_notes_combines_replace_append_and_block_updates(
    tmp_path: Path,
) -> None:
    settings = Settings(read_roots=(), write_roots=(RootConfig("notes", tmp_path),))
    tools = create_server(settings)._tool_manager._tools
    handoff = tools["write_note"].fn(
        root_id="notes", path="handoff.md", content="old handoff", mode="create"
    )
    decisions = tools["write_note"].fn(
        root_id="notes", path="decisions.md", content="first\n", mode="create"
    )
    branch = tools["write_note"].fn(
        root_id="notes",
        path="branch.md",
        content="<!-- agent-notes:managed state -->\nold\n<!-- /agent-notes:managed state -->\n",
        mode="create",
    )

    result = tools["batch_update_notes"].fn(
        root_id="notes",
        operations=[
            {
                "operation": "replace",
                "path": "handoff.md",
                "expected_sha256": handoff["sha256"],
                "content": "new handoff",
            },
            {
                "operation": "append",
                "path": "decisions.md",
                "expected_sha256": decisions["sha256"],
                "content": "second\n",
            },
            {
                "operation": "update_managed_blocks",
                "path": "branch.md",
                "expected_sha256": branch["sha256"],
                "updates": [{"block_id": "state", "content": "new"}],
            },
        ],
    )

    assert result["status"] == "completed"
    assert [item["path"] for item in result["updated"]] == [
        "handoff.md",
        "decisions.md",
        "branch.md",
    ]
    assert (tmp_path / "handoff.md").read_text(encoding="utf-8") == "new handoff"
    assert (tmp_path / "decisions.md").read_text(encoding="utf-8") == "first\nsecond\n"
    assert "\nnew\n" in (tmp_path / "branch.md").read_text(encoding="utf-8")


def test_batch_update_notes_does_not_write_when_preflight_detects_conflict(
    tmp_path: Path,
) -> None:
    settings = Settings(read_roots=(), write_roots=(RootConfig("notes", tmp_path),))
    tools = create_server(settings)._tool_manager._tools
    first = tools["write_note"].fn(
        root_id="notes", path="first.md", content="first", mode="create"
    )
    second = tools["write_note"].fn(
        root_id="notes", path="second.md", content="second", mode="create"
    )
    (tmp_path / "second.md").write_text("manual edit", encoding="utf-8")

    with pytest.raises(NoteConflictError):
        tools["batch_update_notes"].fn(
            root_id="notes",
            operations=[
                {
                    "operation": "replace",
                    "path": "first.md",
                    "expected_sha256": first["sha256"],
                    "content": "new first",
                },
                {
                    "operation": "replace",
                    "path": "second.md",
                    "expected_sha256": second["sha256"],
                    "content": "new second",
                },
            ],
        )

    assert (tmp_path / "first.md").read_text(encoding="utf-8") == "first"


def test_describe_roots_reports_paths_and_capabilities(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    settings = Settings(
        read_roots=(RootConfig("notes", tmp_path, (".trash/**",)),),
        write_roots=(RootConfig("notes", tmp_path), RootConfig("notes-inbox", inbox)),
    )

    roots = describe_roots(settings)

    assert roots == [
        {
            "root_id": "notes",
            "path": str(tmp_path),
            "path_hint": tmp_path.name,
            "readable": True,
            "writable": True,
            "exclude": [".trash/**"],
        },
        {
            "root_id": "notes-inbox",
            "path": str(inbox),
            "path_hint": "inbox",
            "readable": True,
            "writable": True,
            "exclude": [],
        },
    ]


def test_search_notes_for_roots_reranks_across_roots(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "alpha.md").write_text("alpha only\n", encoding="utf-8")
    (second / "alpha-guide.md").write_text(
        "---\ntitle: alpha guide\ntags:\n  - alpha\n---\n\nalpha body alpha\n",
        encoding="utf-8",
    )
    settings = Settings(
        read_roots=(RootConfig("z-root", first), RootConfig("a-root", second)),
        write_roots=(),
    )

    results = search_notes_for_roots(settings, query="alpha", limit=2)

    assert [result["root_id"] for result in results] == ["a-root", "z-root"]
    assert results[0]["root_path_hint"] == "second"
    assert results[0]["search_score"] > results[1]["search_score"]


def test_list_notes_for_roots_adds_root_metadata(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "b.md").write_text("# b\n", encoding="utf-8")
    (second / "a.md").write_text("# a\n", encoding="utf-8")
    settings = Settings(
        read_roots=(RootConfig("z-root", first), RootConfig("a-root", second)),
        write_roots=(),
    )

    results = list_notes_for_roots(settings, limit=10)

    assert results == [
        {
            "root_id": "a-root",
            "root_path_hint": "second",
            "path": "a.md",
            "title": None,
            "project": None,
            "tags": [],
            "date": None,
            "status": None,
            "type": None,
        },
        {
            "root_id": "z-root",
            "root_path_hint": "first",
            "path": "b.md",
            "title": None,
            "project": None,
            "tags": [],
            "date": None,
            "status": None,
            "type": None,
        },
    ]


def test_implicit_write_root_is_included_in_cross_root_search(tmp_path: Path) -> None:
    readable = tmp_path / "readable"
    writable = tmp_path / "writable"
    readable.mkdir()
    writable.mkdir()
    (writable / "activity.md").write_text("activity summary\n", encoding="utf-8")
    settings = Settings(
        read_roots=(RootConfig("readable", readable),),
        write_roots=(RootConfig("writable", writable),),
    )

    results = search_notes_for_roots(settings, query="activity")

    assert [(result["root_id"], result["path"]) for result in results] == [
        ("writable", "activity.md")
    ]


def test_list_notes_for_roots_filters_before_applying_limit(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    (root / "first.md").write_text(
        "---\nproject: other\nstatus: new\n---\n",
        encoding="utf-8",
    )
    (root / "second.md").write_text(
        "---\nproject: target\nstatus: triage\n---\n",
        encoding="utf-8",
    )
    settings = Settings(read_roots=(RootConfig("notes", root),), write_roots=())

    results = list_notes_for_roots(
        settings, project="target", status=["new", "triage"], limit=1
    )

    assert [(result["root_id"], result["path"]) for result in results] == [
        ("notes", "second.md")
    ]
