"""FastMCP tool registration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from typing import Literal, NotRequired, TypedDict

from .activity_log import append_activity_log
from .config import RootConfig, Settings
from .managed_blocks import ManagedBlockUpdate, replace_managed_blocks
from .metadata import set_updated_if_enabled
from .notes import (
    Note,
    NoteConflictError,
    _list_notes,
    _search_notes,
    append_note,
    create_note,
    move_note,
    read_note,
    replace_note,
)


# Declare all four MCP behaviour hints explicitly.  Clients use these advisory
# hints to present appropriate confirmation and safety UI; they are not an
# authorization boundary.
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
WRITE_NONDESTRUCTIVE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
WRITE_DESTRUCTIVE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)


def _root_metadata(root: RootConfig) -> dict[str, object]:
    return {
        "root_id": root.id,
        "root_path_hint": root.path.name or str(root.path),
    }


def describe_roots(settings: Settings) -> list[dict[str, object]]:
    """Return read/write capabilities for each configured root ID."""
    read_by_id = {root.id: root for root in settings.read_roots}
    write_by_id = {root.id: root for root in settings.write_roots}
    root_ids = sorted(set(read_by_id) | set(write_by_id))
    described_roots: list[dict[str, object]] = []
    for root_id in root_ids:
        root = read_by_id.get(root_id) or write_by_id[root_id]
        described_roots.append(
            {
                "root_id": root_id,
                "path": str(root.path),
                "path_hint": root.path.name or str(root.path),
                "readable": root_id in read_by_id or root_id in write_by_id,
                "writable": root_id in write_by_id,
                "exclude": list(read_by_id[root_id].exclude)
                if root_id in read_by_id
                else [],
            }
        )
    return described_roots


def _sort_listing_results(
    results: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    return sorted(results, key=lambda item: (str(item["root_id"]), str(item["path"])))


def list_notes_for_roots(
    settings: Settings,
    *,
    root_id: str | None = None,
    path_prefix: str | None = None,
    limit: int = 100,
    project: str | None = None,
    status: list[str] | None = None,
    note_type: list[str] | None = None,
    tags: list[str] | None = None,
    frontmatter: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """List notes from one or all roots with stable cross-root ordering."""
    roots = (settings.read_root(root_id),) if root_id else settings.readable_roots
    results: list[dict[str, object]] = []
    for root in roots:
        for result in _list_notes(
            root,
            path_prefix,
            limit=10_000,
            project=project,
            status=status,
            note_type=note_type,
            tags=tags,
            frontmatter=frontmatter,
        ):
            results.append({**_root_metadata(root), **result})
    return _sort_listing_results(results)[:limit]


def search_notes_for_roots(
    settings: Settings,
    *,
    query: str,
    root_id: str | None = None,
    path_prefix: str | None = None,
    limit: int = 10,
    project: str | None = None,
    status: list[str] | None = None,
    note_type: list[str] | None = None,
    tags: list[str] | None = None,
    frontmatter: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """Search one or all roots and rank matches across the combined result set."""
    roots = (settings.read_root(root_id),) if root_id else settings.readable_roots
    results: list[dict[str, object]] = []
    for root in roots:
        for result in _search_notes(
            root,
            query,
            path_prefix,
            limit=10_000,
            project=project,
            status=status,
            note_type=note_type,
            tags=tags,
            frontmatter=frontmatter,
        ):
            results.append({**_root_metadata(root), **result})
    results.sort(
        key=lambda item: (
            -int(item["search_score"]),
            str(item["root_id"]),
            str(item["path"]),
        )
    )
    return results[:limit]


def _managed_block_updates(
    updates: list[dict[str, str]],
) -> list[ManagedBlockUpdate]:
    """Validate the JSON-shaped updates accepted by the MCP tool."""
    parsed: list[ManagedBlockUpdate] = []
    for update in updates:
        if not isinstance(update, dict) or set(update) != {"block_id", "content"}:
            raise ValueError(
                "Each managed block update must contain exactly block_id and content."
            )
        block_id = update["block_id"]
        content = update["content"]
        if not isinstance(block_id, str) or not isinstance(content, str):
            raise ValueError("Managed block block_id and content must both be strings.")
        parsed.append(ManagedBlockUpdate(block_id, content))
    return parsed


def _with_updated_metadata(settings: Settings, content: str) -> str:
    return set_updated_if_enabled(content, settings.note_metadata)


@dataclass(frozen=True)
class _BatchOperation:
    operation: str
    path: str
    expected_sha256: str
    content: str
    activity_summary: str | None


class _BatchOperationCommon(TypedDict):
    """Fields required by every batch operation."""

    path: str
    expected_sha256: str
    activity_summary: NotRequired[str]


class _ReplaceBatchOperation(_BatchOperationCommon):
    operation: Literal["replace"]
    content: str


class _AppendBatchOperation(_BatchOperationCommon):
    operation: Literal["append"]
    content: str


class _ManagedBlocksBatchOperation(_BatchOperationCommon):
    operation: Literal["update_managed_blocks"]
    updates: list[dict[str, str]]


BatchOperationInput = (
    _ReplaceBatchOperation | _AppendBatchOperation | _ManagedBlocksBatchOperation
)


def _batch_operations(
    settings: Settings, root: RootConfig, operations: list[dict[str, object]]
) -> list[_BatchOperation]:
    """Preflight every batch operation before any target is written."""
    if not operations:
        raise ValueError("At least one batch operation is required.")
    planned: list[_BatchOperation] = []
    seen_paths: set[str] = set()
    for raw in operations:
        if not isinstance(raw, dict):
            raise ValueError("Each batch operation must be an object.")
        operation = raw.get("operation")
        path = raw.get("path")
        expected_sha256 = raw.get("expected_sha256")
        summary = raw.get("activity_summary")
        if operation not in {"replace", "append", "update_managed_blocks"}:
            raise ValueError("operation must be replace, append, or update_managed_blocks.")
        if not isinstance(path, str) or not path:
            raise ValueError("Each batch operation needs a non-empty path.")
        if path in seen_paths:
            raise ValueError("A batch operation may update each path only once.")
        seen_paths.add(path)
        if not isinstance(expected_sha256, str) or not expected_sha256:
            raise ValueError("Each batch operation needs expected_sha256.")
        if summary is not None and not isinstance(summary, str):
            raise ValueError("activity_summary must be a string.")

        current = read_note(root, path)
        if current.sha256 != expected_sha256:
            raise NoteConflictError(
                "The note changed after it was read; read it again before batch updating it."
            )
        if operation == "replace":
            if set(raw) - {"operation", "path", "expected_sha256", "content", "activity_summary"}:
                raise ValueError("replace accepts only content and common batch operation fields.")
            content = raw.get("content")
            if not isinstance(content, str):
                raise ValueError("replace needs string content.")
        elif operation == "append":
            if set(raw) - {"operation", "path", "expected_sha256", "content", "activity_summary"}:
                raise ValueError("append accepts only content and common batch operation fields.")
            content = raw.get("content")
            if not isinstance(content, str):
                raise ValueError("append needs string content.")
            separator = "" if not current.content or current.content.endswith("\n") else "\n"
            content = f"{current.content}{separator}{content}"
        else:
            if set(raw) - {"operation", "path", "expected_sha256", "updates", "activity_summary"}:
                raise ValueError(
                    "update_managed_blocks accepts only updates and common batch operation fields."
                )
            updates = raw.get("updates")
            if not isinstance(updates, list):
                raise ValueError("update_managed_blocks needs an updates array.")
            content = replace_managed_blocks(
                current.content, _managed_block_updates(updates)  # type: ignore[arg-type]
            )
        planned.append(
            _BatchOperation(
                operation=operation,
                path=path,
                expected_sha256=expected_sha256,
                content=_with_updated_metadata(settings, content),
                activity_summary=summary,
            )
        )
    return planned


def create_server(settings: Settings) -> FastMCP:
    """Create the configured local notes MCP server."""
    server = FastMCP(
        "Local Wiki",
        instructions=(
            "Search personal Markdown notes before relying on memory. "
            "Read a note before replacing it, and preserve manual edits when a hash conflict occurs."
        ),
    )

    @server.tool(name="search_notes", annotations=READ_ONLY_ANNOTATIONS)
    def search_notes_tool(
        query: str,
        root_id: str | None = None,
        path_prefix: str | None = None,
        limit: int = 10,
        project: str | None = None,
        status: list[str] | None = None,
        note_type: list[str] | None = None,
        tags: list[str] | None = None,
        frontmatter: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        """Search Markdown and filter exact frontmatter values; return ranked snippets.

        frontmatter matches arbitrary top-level scalar fields with AND semantics;
        field names are exact and values are case-insensitive exact matches. Use
        tags for the frontmatter tags list. Arrays and nested frontmatter are not
        supported by frontmatter.
        """
        return search_notes_for_roots(
            settings,
            query=query,
            root_id=root_id,
            path_prefix=path_prefix,
            limit=limit,
            project=project,
            status=status,
            note_type=note_type,
            tags=tags,
            frontmatter=frontmatter,
        )

    @server.tool(name="read_note", annotations=READ_ONLY_ANNOTATIONS)
    def read_note_tool(root_id: str, path: str) -> dict[str, str]:
        """Read a Markdown note and its SHA-256 for a safe later replacement."""
        return read_note(settings.accessible_root(root_id), path).to_dict()

    @server.tool(name="list_roots", annotations=READ_ONLY_ANNOTATIONS)
    def list_roots_tool() -> list[dict[str, object]]:
        """List configured roots, their filesystem paths, and read/write capabilities."""
        return describe_roots(settings)

    @server.tool(name="list_notes", annotations=READ_ONLY_ANNOTATIONS)
    def list_notes_tool(
        root_id: str | None = None,
        path_prefix: str | None = None,
        limit: int = 100,
        project: str | None = None,
        status: list[str] | None = None,
        note_type: list[str] | None = None,
        tags: list[str] | None = None,
        frontmatter: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        """List notes and filter exact frontmatter metadata.

        frontmatter matches arbitrary top-level scalar fields with AND semantics;
        field names are exact and values are case-insensitive exact matches. Use
        tags for the frontmatter tags list. Arrays and nested frontmatter are not
        supported by frontmatter.
        """
        return list_notes_for_roots(
            settings,
            root_id=root_id,
            path_prefix=path_prefix,
            limit=limit,
            project=project,
            status=status,
            note_type=note_type,
            tags=tags,
            frontmatter=frontmatter,
        )

    if settings.write_roots:

        def write_result(note: Note, include_content: bool) -> dict[str, str]:
            """Keep successful writes compact unless the caller requests the body."""
            return note.to_dict() if include_content else note.to_reference_dict()

        @server.tool(
            name="write_note", annotations=WRITE_DESTRUCTIVE_ANNOTATIONS
        )
        def write_note_tool(
            root_id: str,
            path: str,
            content: str,
            mode: str,
            expected_sha256: str | None = None,
            create_parents: bool = False,
            activity_summary: str | None = None,
            include_content: bool = False,
        ) -> dict[str, str]:
            """Create or replace a note; return its path and hash, or content when requested."""
            root = settings.write_root(root_id)
            if mode == "create":
                if expected_sha256 is not None:
                    raise ValueError(
                        "expected_sha256 is only valid for mode='replace'."
                    )
                note = create_note(
                    root,
                    path,
                    _with_updated_metadata(settings, content),
                    create_parents=create_parents,
                )
            elif mode == "replace":
                if expected_sha256 is None:
                    raise ValueError("expected_sha256 is required for mode='replace'.")
                note = replace_note(
                    root, path, _with_updated_metadata(settings, content), expected_sha256
                )
            else:
                raise ValueError("mode must be either 'create' or 'replace'.")
            append_activity_log(
                settings,
                operation=f"write_note:{mode}",
                note_root_id=root_id,
                note_path=path,
                summary=activity_summary,
            )
            return write_result(note, include_content)

        @server.tool(
            name="append_note", annotations=WRITE_NONDESTRUCTIVE_ANNOTATIONS
        )
        def append_note_tool(
            root_id: str,
            path: str,
            content: str,
            activity_summary: str | None = None,
            include_content: bool = False,
        ) -> dict[str, str]:
            """Append content; return the new path and hash, or content when requested."""
            note = append_note(
                settings.write_root(root_id),
                path,
                content,
                transform=lambda current: _with_updated_metadata(settings, current),
            )
            append_activity_log(
                settings,
                operation="append_note",
                note_root_id=root_id,
                note_path=path,
                summary=activity_summary,
            )
            return write_result(note, include_content)

        @server.tool(
            name="move_note", annotations=WRITE_NONDESTRUCTIVE_ANNOTATIONS
        )
        def move_note_tool(
            source_root_id: str,
            source_path: str,
            destination_root_id: str,
            destination_path: str,
            expected_sha256: str,
            create_parents: bool = False,
            activity_summary: str | None = None,
            include_content: bool = False,
        ) -> dict[str, str]:
            """Move a note without overwriting it; return its path and hash or content."""
            note = move_note(
                settings.write_root(source_root_id),
                source_path,
                settings.write_root(destination_root_id),
                destination_path,
                expected_sha256,
                create_parents=create_parents,
                transform=lambda current: _with_updated_metadata(settings, current),
            )
            append_activity_log(
                settings,
                operation="move_note",
                note_root_id=destination_root_id,
                note_path=destination_path,
                summary=activity_summary,
            )
            return write_result(note, include_content)

        @server.tool(
            name="update_managed_blocks", annotations=WRITE_DESTRUCTIVE_ANNOTATIONS
        )
        def update_managed_blocks_tool(
            root_id: str,
            path: str,
            expected_sha256: str,
            updates: list[dict[str, str]],
            activity_summary: str | None = None,
            include_content: bool = False,
        ) -> dict[str, str]:
            """Atomically replace validated managed Markdown blocks in an existing note."""
            root = settings.write_root(root_id)
            current = read_note(root, path)
            if current.sha256 != expected_sha256:
                raise NoteConflictError(
                    "The note changed after it was read; read it again before replacing it."
                )
            updated_content = replace_managed_blocks(
                current.content, _managed_block_updates(updates)
            )
            note = replace_note(
                root,
                path,
                _with_updated_metadata(settings, updated_content),
                expected_sha256,
            )
            append_activity_log(
                settings,
                operation="update_managed_blocks",
                note_root_id=root_id,
                note_path=path,
                summary=activity_summary,
            )
            return write_result(note, include_content)

        @server.tool(
            name="batch_update_notes", annotations=WRITE_DESTRUCTIVE_ANNOTATIONS
        )
        def batch_update_notes_tool(
            root_id: str,
            operations: list[BatchOperationInput],
            include_content: bool = False,
        ) -> dict[str, object]:
            """Update existing notes in one write root after SHA-256 preflight.

            Each operation needs path and expected_sha256. Use operation=replace or
            append with string content, or update_managed_blocks with updates of
            {block_id, content}. Create and move are not batch operations; use
            write_note(mode="create") or move_note instead.
            """
            root = settings.write_root(root_id)
            planned = _batch_operations(settings, root, operations)
            updated: list[dict[str, str]] = []
            for index, operation in enumerate(planned):
                written = False
                try:
                    note = replace_note(
                        root,
                        operation.path,
                        operation.content,
                        operation.expected_sha256,
                    )
                    written = True
                    updated.append(write_result(note, include_content))
                    append_activity_log(
                        settings,
                        operation=f"batch_update_notes:{operation.operation}",
                        note_root_id=root_id,
                        note_path=operation.path,
                        summary=operation.activity_summary,
                    )
                except (NoteConflictError, OSError, ValueError) as error:
                    return {
                        "status": "partially_completed",
                        "updated": updated,
                        "unprocessed": [
                            item.path for item in planned[index + 1 if written else index :]
                        ],
                        "error": str(error),
                    }
            return {"status": "completed", "updated": updated}

    return server
