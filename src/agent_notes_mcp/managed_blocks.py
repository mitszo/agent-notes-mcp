"""Validation and replacement for explicitly managed Markdown blocks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re


_MARKER_PATTERN = re.compile(
    r"^<!-- (?P<closing>/)?agent-notes:managed (?P<block_id>[a-z][a-z0-9-]{0,63}) -->$"
)
_MARKER_PREFIXES = ("<!-- agent-notes:managed", "<!-- /agent-notes:managed")


class ManagedBlockError(ValueError):
    """Raised when managed Markdown blocks are malformed or cannot be updated."""


@dataclass(frozen=True)
class ManagedBlock:
    """Character offsets for one well-formed managed block."""

    block_id: str
    content_start: int
    content_end: int
    newline: str


@dataclass(frozen=True)
class ManagedBlockUpdate:
    """A replacement body for one explicitly managed block."""

    block_id: str
    content: str


def replace_managed_blocks(
    markdown: str, updates: Iterable[ManagedBlockUpdate]
) -> str:
    """Replace managed block bodies after validating the complete marker structure."""
    updates_by_id = _updates_by_id(updates)
    blocks = _parse_blocks(markdown)
    blocks_by_id = {block.block_id: block for block in blocks}
    missing = sorted(set(updates_by_id) - set(blocks_by_id))
    if missing:
        raise ManagedBlockError(f"Managed block not found: {', '.join(missing)}.")

    result: list[str] = []
    cursor = 0
    for block in blocks:
        update = updates_by_id.get(block.block_id)
        if update is None:
            continue
        result.append(markdown[cursor : block.content_start])
        result.append(_replacement_body(update.content, block.newline))
        cursor = block.content_end
    result.append(markdown[cursor:])
    return "".join(result)


def _updates_by_id(
    updates: Iterable[ManagedBlockUpdate],
) -> dict[str, ManagedBlockUpdate]:
    result: dict[str, ManagedBlockUpdate] = {}
    for update in updates:
        if not _MARKER_PATTERN.fullmatch(
            f"<!-- agent-notes:managed {update.block_id} -->"
        ):
            raise ManagedBlockError("Managed block IDs must use lowercase letters, digits, and hyphens.")
        if _contains_marker(update.content):
            raise ManagedBlockError("Managed block content must not contain managed block markers.")
        if update.block_id in result:
            raise ManagedBlockError(f"Managed block was updated more than once: {update.block_id}.")
        result[update.block_id] = update
    if not result:
        raise ManagedBlockError("At least one managed block update is required.")
    return result


def _parse_blocks(markdown: str) -> list[ManagedBlock]:
    blocks: list[ManagedBlock] = []
    open_block: tuple[str, int, str] | None = None
    seen_ids: set[str] = set()
    position = 0

    for line in markdown.splitlines(keepends=True):
        marker = line.rstrip("\r\n")
        match = _MARKER_PATTERN.fullmatch(marker)
        if match is None:
            if marker.startswith(_MARKER_PREFIXES):
                raise ManagedBlockError(f"Malformed managed block marker: {marker}")
            position += len(line)
            continue

        block_id = match["block_id"]
        if match["closing"]:
            if open_block is None:
                raise ManagedBlockError(f"Managed block ends without an opening marker: {block_id}.")
            open_id, content_start, newline = open_block
            if block_id != open_id:
                raise ManagedBlockError(
                    f"Managed block ends with {block_id}, but {open_id} is open."
                )
            blocks.append(
                ManagedBlock(
                    block_id=block_id,
                    content_start=content_start,
                    content_end=position,
                    newline=newline,
                )
            )
            open_block = None
        else:
            if open_block is not None:
                raise ManagedBlockError(
                    f"Managed block {block_id} starts before {open_block[0]} is closed."
                )
            if block_id in seen_ids:
                raise ManagedBlockError(f"Managed block appears more than once: {block_id}.")
            seen_ids.add(block_id)
            open_block = (block_id, position + len(line), _line_ending(line))
        position += len(line)

    if open_block is not None:
        raise ManagedBlockError(f"Managed block is not closed: {open_block[0]}.")
    return blocks


def _replacement_body(content: str, newline: str) -> str:
    body = content.rstrip("\r\n")
    return f"{body}{newline}" if body else ""


def _contains_marker(content: str) -> bool:
    return any(line.rstrip("\r\n").startswith(_MARKER_PREFIXES) for line in content.splitlines())


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    raise ManagedBlockError("Managed block opening marker must end with a newline.")
