"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
import os
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    """Raised when the server configuration is invalid."""


@dataclass(frozen=True)
class RootConfig:
    """A named filesystem root available to one class of operations."""

    id: str
    path: Path
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActivityLogConfig:
    """Optional destination for concise records of note mutations."""

    root_id: str
    filename_template: str
    timezone: ZoneInfo


@dataclass(frozen=True)
class NoteMetadataConfig:
    """Optional automatic freshness metadata for every written Markdown note."""

    timezone: ZoneInfo


@dataclass(frozen=True)
class Settings:
    """Validated server settings."""

    read_roots: tuple[RootConfig, ...]
    write_roots: tuple[RootConfig, ...]
    activity_log: ActivityLogConfig | None = None
    note_metadata: NoteMetadataConfig | None = None

    def read_root(self, root_id: str) -> RootConfig:
        """Return a root with effective read access, including write roots."""
        return self.accessible_root(root_id)

    @property
    def readable_roots(self) -> tuple[RootConfig, ...]:
        """Return roots searched by default without duplicate writable sub-roots."""
        roots = list(self.read_roots)
        for write_root in self.write_roots:
            if not any(_is_within(write_root.path, read_root.path) for read_root in roots):
                roots.append(write_root)
        return tuple(roots)

    def accessible_root(self, root_id: str) -> RootConfig:
        """Return a root usable for reading, including a writable sub-root."""
        return _root_by_id((*self.read_roots, *self.write_roots), root_id, "read")

    def write_root(self, root_id: str) -> RootConfig:
        return _root_by_id(self.write_roots, root_id, "write")


def default_config_path() -> Path:
    """Return the standard per-user configuration path."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "agent-notes-mcp" / "config.toml"


def load_settings(path: Path | None = None) -> Settings:
    """Load and validate a TOML configuration file."""
    config_path = (path or default_config_path()).expanduser()
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"Configuration file was not found: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Configuration file is not valid TOML: {config_path}") from error

    read_roots = _parse_roots(raw.get("read_roots", []), "read_roots", allow_exclude=True)
    write_roots = _parse_roots(raw.get("write_roots", []), "write_roots", allow_exclude=False)
    if not read_roots and not write_roots:
        raise ConfigError("At least one read_roots or write_roots entry is required.")

    _validate_root_ids(read_roots, write_roots)
    activity_log = _parse_activity_log(raw.get("activity_log"), write_roots)
    note_metadata = _parse_note_metadata(raw.get("note_metadata"))
    return Settings(
        read_roots=read_roots,
        write_roots=write_roots,
        activity_log=activity_log,
        note_metadata=note_metadata,
    )


def _parse_roots(value: object, field: str, *, allow_exclude: bool) -> tuple[RootConfig, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be an array of tables.")

    roots: list[RootConfig] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ConfigError(f"Every {field} entry must be a table.")
        root_id = entry.get("id")
        raw_path = entry.get("path")
        if not isinstance(root_id, str) or not root_id:
            raise ConfigError(f"Every {field} entry needs a non-empty id.")
        if not isinstance(raw_path, str) or not raw_path:
            raise ConfigError(f"Every {field} entry needs a non-empty path.")
        root_path = Path(raw_path).expanduser().resolve()
        if not root_path.is_dir():
            raise ConfigError(f"Configured root is not a directory: {root_path}")

        raw_exclude = entry.get("exclude", [])
        if not allow_exclude and raw_exclude:
            raise ConfigError(f"{field} entries cannot define exclude patterns.")
        if not isinstance(raw_exclude, list) or not all(
            isinstance(pattern, str) and pattern for pattern in raw_exclude
        ):
            raise ConfigError(f"{field}.exclude must be an array of non-empty strings.")
        roots.append(RootConfig(root_id, root_path, tuple(raw_exclude)))
    return tuple(roots)


def _root_by_id(roots: tuple[RootConfig, ...], root_id: str, kind: str) -> RootConfig:
    for root in roots:
        if root.id == root_id:
            return root
    raise ConfigError(f"Unknown {kind} root ID: {root_id}")


def _validate_root_ids(
    read_roots: tuple[RootConfig, ...], write_roots: tuple[RootConfig, ...]
) -> None:
    """Allow one read/write ID pair only when both refer to the same path."""
    for roots, kind in ((read_roots, "read_roots"), (write_roots, "write_roots")):
        ids = [root.id for root in roots]
        if len(ids) != len(set(ids)):
            raise ConfigError(f"Root IDs must be unique within {kind}.")
    read_by_id = {root.id: root for root in read_roots}
    for write_root in write_roots:
        read_root = read_by_id.get(write_root.id)
        if read_root is not None and read_root.path != write_root.path:
            raise ConfigError(
                f"Root ID {write_root.id!r} refers to different read and write paths."
            )


def _parse_activity_log(
    value: object, write_roots: tuple[RootConfig, ...]
) -> ActivityLogConfig | None:
    """Parse an explicitly enabled activity-log destination.

    The destination is deliberately tied to a write root: it is an operational
    convenience, not a second, independent access-control mechanism.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError("activity_log must be a table.")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("activity_log.enabled must be a boolean.")
    if not enabled:
        return None

    root_id = value.get("root_id")
    filename_template = value.get("filename_template")
    timezone_name = value.get("timezone", "UTC")
    if not isinstance(root_id, str) or not root_id:
        raise ConfigError("activity_log.root_id must be a non-empty write root ID.")
    _root_by_id(write_roots, root_id, "write")
    if not isinstance(filename_template, str) or not filename_template:
        raise ConfigError("activity_log.filename_template must be a non-empty filename template.")
    _validate_filename_template(filename_template)
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ConfigError("activity_log.timezone must be an IANA timezone name.")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ConfigError(f"activity_log.timezone is not available: {timezone_name!r}") from error
    return ActivityLogConfig(root_id, filename_template, timezone)


def _parse_note_metadata(value: object) -> NoteMetadataConfig | None:
    """Parse opt-in automatic ``updated`` metadata shared by all write roots."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError("note_metadata must be a table.")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("note_metadata.enabled must be a boolean.")
    if not enabled:
        return None
    timezone_name = value.get("timezone", "UTC")
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ConfigError("note_metadata.timezone must be an IANA timezone name.")
    try:
        return NoteMetadataConfig(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError as error:
        raise ConfigError(
            f"note_metadata.timezone is not available: {timezone_name!r}"
        ) from error


def _validate_filename_template(filename_template: str) -> None:
    # strftime directives are allowed, but their result must always name one Markdown file.
    rendered = datetime(2026, 8, 6).strftime(filename_template)
    path = PurePosixPath(rendered)
    if (
        chr(0) in filename_template
        or not rendered
        or path.name != rendered
        or rendered in {".", ".."}
        or path.suffix.lower() != ".md"
    ):
        raise ConfigError(
            "activity_log.filename_template must render to one Markdown filename."
        )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
