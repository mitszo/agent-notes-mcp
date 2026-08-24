"""Markdown note reads, search, and safe writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import tempfile
from collections.abc import Callable

from .config import RootConfig
from .paths import PathAccessError, is_excluded, resolve_note_path


class NoteConflictError(ValueError):
    """Raised when a note changed after it was read."""


@dataclass(frozen=True)
class Note:
    path: str
    content: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def to_reference_dict(self) -> dict[str, str]:
        """Return the identifiers needed to refer to a successfully written note."""
        return {"path": self.path, "sha256": self.sha256}


def read_note(root: RootConfig, path: str) -> Note:
    """Read a note and calculate the optimistic-concurrency hash."""
    resolved = resolve_note_path(root, path, must_exist=True)
    content = resolved.read_text(encoding="utf-8")
    return Note(path=_relative(root, resolved), content=content, sha256=_hash(content))


def list_notes(
    root: RootConfig,
    path_prefix: str | None = None,
    limit: int = 100,
    *,
    project: str | None = None,
    status: list[str] | None = None,
    note_type: list[str] | None = None,
    tags: list[str] | None = None,
    frontmatter: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """List searchable Markdown notes without returning their full contents."""
    _validate_limit(limit)
    return _list_notes(
        root, path_prefix, limit, project, status, note_type, tags, frontmatter
    )


def _list_notes(
    root: RootConfig,
    path_prefix: str | None,
    limit: int,
    project: str | None = None,
    status: list[str] | None = None,
    note_type: list[str] | None = None,
    tags: list[str] | None = None,
    frontmatter: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """List notes with an internal limit used by search."""
    base = root.path
    if path_prefix:
        prefix = PurePosixPath(path_prefix)
        if prefix.is_absolute() or ".." in prefix.parts or "." in prefix.parts:
            raise PathAccessError("path_prefix must be a safe relative directory.")
        base = root.path.joinpath(*prefix.parts).resolve(strict=False)
        try:
            base.relative_to(root.path)
        except ValueError as error:
            raise PathAccessError(
                "path_prefix resolves outside the configured root."
            ) from error
        if not base.is_dir():
            return []

    frontmatter_filters = _validated_frontmatter_filters(frontmatter)
    result: list[dict[str, object]] = []
    for file_path in sorted(base.rglob("*.md")):
        try:
            resolved = resolve_note_path(
                root, _relative(root, file_path), must_exist=True
            )
        except PathAccessError:
            continue
        relative = PurePosixPath(_relative(root, resolved))
        if is_excluded(root, relative):
            continue
        content = resolved.read_text(encoding="utf-8")
        metadata, scalars = _parsed_metadata(content)
        if not _matches_metadata(
            metadata, project, status, note_type, tags, frontmatter_filters, scalars
        ):
            continue
        result.append({"path": relative.as_posix(), **metadata})
        if len(result) >= limit:
            break
    return result


def search_notes(
    root: RootConfig,
    query: str,
    path_prefix: str | None = None,
    limit: int = 10,
    *,
    project: str | None = None,
    status: list[str] | None = None,
    note_type: list[str] | None = None,
    tags: list[str] | None = None,
    frontmatter: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """Search file names, simple YAML frontmatter, and Markdown body."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty.")
    _validate_limit(limit)
    return _search_notes(
        root, query, path_prefix, limit, project, status, note_type, tags, frontmatter
    )


def _search_notes(
    root: RootConfig,
    query: str,
    path_prefix: str | None,
    limit: int,
    project: str | None = None,
    status: list[str] | None = None,
    note_type: list[str] | None = None,
    tags: list[str] | None = None,
    frontmatter: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """Search notes with an internal limit used by cross-root aggregation."""
    terms = query.casefold().split()
    candidates = _list_notes(
        root,
        path_prefix,
        limit=10_000,
        project=project,
        status=status,
        note_type=note_type,
        tags=tags,
        frontmatter=frontmatter,
    )
    matches: list[tuple[int, dict[str, object]]] = []
    for candidate in candidates:
        path = str(candidate["path"])
        try:
            note = read_note(root, path)
        except OSError, UnicodeDecodeError, PathAccessError:
            continue
        title = str(candidate.get("title") or "")
        tags = " ".join(candidate.get("tags", []))
        name = Path(path).name
        haystack = "\n".join((name, title, tags, note.content)).casefold()
        if not all(term in haystack for term in terms):
            continue
        score = (
            sum(100 if term in name.casefold() else 0 for term in terms)
            + sum(50 if term in title.casefold() else 0 for term in terms)
            + sum(25 if term in tags.casefold() else 0 for term in terms)
            + sum(haystack.count(term) for term in terms)
        )
        matches.append(
            (
                score,
                {
                    "path": path,
                    **_metadata(note.content),
                    "snippet": _snippet(note.content, terms),
                    "search_score": score,
                },
            )
        )
    matches.sort(key=lambda item: (-item[0], str(item[1]["path"])))
    return [match for _, match in matches[:limit]]


def create_note(
    root: RootConfig, path: str, content: str, *, create_parents: bool = False
) -> Note:
    """Create a new note and fail if it already exists."""
    resolved = resolve_note_path(root, path, must_exist=False)
    if resolved.exists():
        raise FileExistsError(
            "The note already exists; use replace only after reading it."
        )
    if create_parents:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    if not resolved.parent.is_dir():
        raise FileNotFoundError("The parent directory does not exist.")
    _atomic_write(resolved, content)
    return read_note(root, path)


def replace_note(
    root: RootConfig, path: str, content: str, expected_sha256: str
) -> Note:
    """Atomically replace a note only when its last-read hash still matches."""
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise ValueError("expected_sha256 is required when replacing a note.")
    resolved = resolve_note_path(root, path, must_exist=True)
    current = resolved.read_text(encoding="utf-8")
    if _hash(current) != expected_sha256:
        raise NoteConflictError(
            "The note changed after it was read; read it again before replacing it."
        )
    _atomic_write(resolved, content)
    return read_note(root, path)


def append_note(
    root: RootConfig,
    path: str,
    content: str,
    *,
    transform: Callable[[str], str] | None = None,
) -> Note:
    """Append content as a complete Markdown block to an existing note."""
    resolved = resolve_note_path(root, path, must_exist=True)
    current = resolved.read_text(encoding="utf-8")
    separator = "" if not current or current.endswith("\n") else "\n"
    updated = f"{current}{separator}{content}"
    _atomic_write(resolved, transform(updated) if transform else updated)
    return read_note(root, path)


def move_note(
    source_root: RootConfig,
    source_path: str,
    destination_root: RootConfig,
    destination_path: str,
    expected_sha256: str,
    *,
    create_parents: bool = False,
    transform: Callable[[str], str] | None = None,
) -> Note:
    """Move a note without overwriting a destination in any writable root."""
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise ValueError("expected_sha256 is required when moving a note.")
    source = resolve_note_path(source_root, source_path, must_exist=True)
    destination = resolve_note_path(
        destination_root, destination_path, must_exist=False
    )
    if os.path.lexists(destination):
        raise FileExistsError("The destination note already exists.")
    if create_parents:
        destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.parent.is_dir():
        raise FileNotFoundError("The destination parent directory does not exist.")

    content = source.read_text(encoding="utf-8")
    if _hash(content) != expected_sha256:
        raise NoteConflictError(
            "The note changed after it was read; read it again before moving it."
        )

    destination_content = transform(content) if transform else content
    _create_exclusive_file(destination, destination_content, source.stat().st_mode)
    try:
        # Check immediately before removal so a detected manual edit is never removed.
        if _hash(source.read_text(encoding="utf-8")) != expected_sha256:
            raise NoteConflictError("The note changed while it was being moved.")
        source.unlink()
    except BaseException:
        # The destination was created exclusively by this operation. Remove it rather
        # than turn a failed move into a silent copy.
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    return read_note(destination_root, destination_path)


def _atomic_write(path: Path, content: str) -> None:
    data = content.encode("utf-8")
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _create_exclusive_file(path: Path, content: str, source_mode: int) -> None:
    """Create *path* once, never replacing an existing directory entry."""
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, source_mode & 0o777
    )
    try:
        with os.fdopen(descriptor, "wb") as destination_file:
            destination_file.write(content.encode("utf-8"))
            destination_file.flush()
            os.fsync(destination_file.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise


def _metadata(content: str) -> dict[str, object]:
    """Return the fixed, lightweight metadata exposed in search results."""
    return _parsed_metadata(content)[0]


def _parsed_metadata(content: str) -> tuple[dict[str, object], dict[str, str]]:
    """Extract public metadata plus arbitrary top-level scalar frontmatter."""
    metadata = _empty_metadata()
    scalars, tags = _frontmatter_scalars_and_tags(content)
    for key in ("title", "project", "date", "status", "type"):
        metadata[key] = scalars.get(key)
    metadata["tags"] = tags
    return metadata, scalars


def _frontmatter_scalars_and_tags(content: str) -> tuple[dict[str, str], list[str]]:
    if not content.startswith("---\n"):
        return {}, []
    try:
        frontmatter, _ = content[4:].split("\n---\n", maxsplit=1)
    except ValueError:
        return {}, []
    scalars: dict[str, str] = {}
    tags: list[str] = []
    reading_tags = False
    for line in frontmatter.splitlines():
        if line and not line.startswith((" ", "\t")):
            key, separator, raw_value = line.partition(":")
            if not separator:
                reading_tags = False
                continue
            value = _frontmatter_value(line)
            raw_value = raw_value.strip()
            if (
                value is not None
                and not raw_value.startswith(("[", "{"))
                and raw_value not in {"|", ">", "|-", ">-", "|+", ">+"}
            ):
                scalars[key] = value
            reading_tags = key == "tags" and not raw_value
        elif reading_tags and line.lstrip().startswith("- "):
            tags.append(line.lstrip()[2:].strip().strip("\"'"))
    return scalars, tags


def _empty_metadata() -> dict[str, object]:
    return {
        "title": None,
        "project": None,
        "tags": [],
        "date": None,
        "status": None,
        "type": None,
    }


def _frontmatter_value(line: str) -> str | None:
    return line.partition(":")[2].strip().strip("\"'") or None


def _matches_metadata(
    metadata: dict[str, object],
    project: str | None,
    status: list[str] | None,
    note_type: list[str] | None,
    tags: list[str] | None,
    frontmatter: dict[str, str],
    scalars: dict[str, str],
) -> bool:
    """Apply exact, case-insensitive frontmatter filters to one note."""
    if project is not None and _fold(metadata["project"]) != _fold(project):
        return False
    if status and _fold(metadata["status"]) not in _folded_values(status):
        return False
    if note_type and _fold(metadata["type"]) not in _folded_values(note_type):
        return False
    if any(_fold(scalars.get(key)) != _fold(value) for key, value in frontmatter.items()):
        return False
    note_tags = {_fold(tag) for tag in metadata["tags"] if isinstance(tag, str)}
    return not tags or _folded_values(tags).issubset(note_tags)


def _validated_frontmatter_filters(
    frontmatter: dict[str, str] | None,
) -> dict[str, str]:
    """Validate filters for arbitrary top-level scalar frontmatter fields."""
    if frontmatter is None:
        return {}
    if not isinstance(frontmatter, dict):
        raise ValueError("frontmatter must be an object mapping field names to strings.")
    for key, value in frontmatter.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("frontmatter field names must be non-empty strings.")
        if key == "tags":
            raise ValueError("Use tags for the frontmatter tags list, not frontmatter.")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("frontmatter values must be non-empty strings.")
    return frontmatter


def _fold(value: object) -> str:
    return value.casefold() if isinstance(value, str) else ""


def _folded_values(values: list[str]) -> set[str]:
    return {value.casefold() for value in values if value.strip()}


def _snippet(content: str, terms: list[str]) -> str:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        folded = line.casefold()
        if any(term in folded for term in terms):
            start = max(0, index - 1)
            end = min(len(lines), index + 2)
            return "\n".join(lines[start:end])[:600]
    return content[:600]


def _relative(root: RootConfig, path: Path) -> str:
    return path.relative_to(root.path).as_posix()


def _hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _validate_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100.")
