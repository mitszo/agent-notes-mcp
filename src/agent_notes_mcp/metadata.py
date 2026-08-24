"""Preserve Markdown frontmatter while recording note freshness."""

from __future__ import annotations

from datetime import datetime
import re

from .config import NoteMetadataConfig


class FrontmatterError(ValueError):
    """Raised when frontmatter cannot be safely updated."""


_DELIMITER = re.compile(r"^(---|\.\.\.)\r?\n?$")
_UPDATED = re.compile(r"^updated\s*:\s*(?P<value>.*?)(?P<ending>\r?\n|$)")


def set_updated(content: str, timestamp: datetime) -> str:
    """Set a top-level ``updated`` value without reserializing other frontmatter."""
    value = timestamp.isoformat(timespec="seconds")
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return f"---\nupdated: {value}\n---\n\n{content}"

    closing_index = next(
        (index for index, line in enumerate(lines[1:], 1) if _DELIMITER.fullmatch(line)),
        None,
    )
    if closing_index is None:
        raise FrontmatterError("Frontmatter starts with '---' but is not closed.")

    updated_indices = [
        index
        for index, line in enumerate(lines[1:closing_index], 1)
        if _UPDATED.match(line)
    ]
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    if len(updated_indices) > 1:
        raise FrontmatterError("Frontmatter contains more than one top-level updated field.")
    if updated_indices:
        index = updated_indices[0]
        match = _UPDATED.match(lines[index])
        assert match is not None
        previous = match["value"]
        if previous.lstrip().startswith(("|", ">")):
            raise FrontmatterError("The updated field must be a scalar value.")
        comment = previous[previous.index("#") :] if "#" in previous else ""
        lines[index] = f"updated: {value}{' ' if comment else ''}{comment}{newline}"
    else:
        lines.insert(closing_index, f"updated: {value}{newline}")
    return "".join(lines)


def set_updated_if_enabled(
    content: str, config: NoteMetadataConfig | None, *, now: datetime | None = None
) -> str:
    """Return content with freshness metadata when the feature is enabled."""
    if config is None:
        return content
    timestamp = (now or datetime.now(config.timezone)).astimezone(config.timezone)
    return set_updated(content, timestamp)
