"""Optional, concise activity records for successful note mutations."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

from .config import ActivityLogConfig, Settings
from .metadata import set_updated_if_enabled
from .notes import append_note, create_note
from .paths import PathAccessError


def append_activity_log(
    settings: Settings,
    *,
    operation: str,
    note_root_id: str,
    note_path: str,
    summary: str | None,
    now: datetime | None = None,
) -> None:
    """Append one user-supplied summary when the optional feature is enabled."""
    if summary is None or settings.activity_log is None:
        return
    summary = _normalise_summary(summary)
    config = settings.activity_log
    timestamp = (now or datetime.now(config.timezone)).astimezone(config.timezone)
    log_path = _log_path(config, timestamp)
    if _is_activity_log_target(config, note_root_id, note_path, log_path):
        # A direct update to the activity log is already the requested record.
        # Do not turn it into a second, near-identical self-record.
        return
    escaped_note_path = note_path.replace("`", "\\`")
    entry = (
        f"- {timestamp:%Y-%m-%d %H:%M} [{operation}] "
        f"`{note_root_id}:{escaped_note_path}` — {summary}\n"
    )
    root = settings.write_root(config.root_id)
    try:
        append_note(
            root,
            log_path,
            entry,
            transform=lambda content: set_updated_if_enabled(
                content, settings.note_metadata, now=timestamp
            ),
        )
    except PathAccessError:
        # A missing log is created lazily; all parent directories are under the
        # configured write root because config validation constrains the path.
        create_note(
            root,
            log_path,
            set_updated_if_enabled(entry, settings.note_metadata, now=timestamp),
        )


def _log_path(config: ActivityLogConfig, timestamp: datetime) -> str:
    return timestamp.strftime(config.filename_template)


def _is_activity_log_target(
    config: ActivityLogConfig, note_root_id: str, note_path: str, log_path: str
) -> bool:
    """Return whether a successful mutation directly updated this log file."""
    return note_root_id == config.root_id and PurePosixPath(note_path).as_posix() == log_path


def _normalise_summary(summary: str) -> str:
    if not isinstance(summary, str):
        raise ValueError("activity_summary must be a string.")
    normalised = " ".join(summary.split())
    if not normalised:
        raise ValueError("activity_summary must not be empty.")
    return normalised
