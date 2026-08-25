"""Safe conversion of MCP-relative paths to filesystem paths."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .config import RootConfig


class PathAccessError(ValueError):
    """Raised when a path falls outside its configured root."""


def resolve_note_path(root: RootConfig, relative_path: str, *, must_exist: bool) -> Path:
    """Resolve a Markdown path without allowing escapes from *root*."""
    if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
        raise PathAccessError("path must be a non-empty relative path.")

    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise PathAccessError("path must not be absolute or contain '.' or '..'.")
    if path.suffix.lower() != ".md":
        raise PathAccessError("Only Markdown (.md) files are supported.")

    candidate = root.path.joinpath(*path.parts)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.path)
    except ValueError as error:
        raise PathAccessError("path resolves outside the configured root.") from error

    if must_exist and not resolved.is_file():
        raise PathAccessError("The requested note does not exist or is not a file.")
    return resolved


def is_excluded(root: RootConfig, relative_path: PurePosixPath) -> bool:
    """Return whether an in-root relative path matches an exclusion pattern."""
    return any(relative_path.full_match(pattern) for pattern in root.exclude)
