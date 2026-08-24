from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_notes_mcp.activity_log import append_activity_log
from agent_notes_mcp.config import (
    ActivityLogConfig,
    NoteMetadataConfig,
    RootConfig,
    Settings,
)


def settings_for(root: Path) -> Settings:
    return Settings(
        read_roots=(RootConfig("notes", root),),
        write_roots=(RootConfig("notes", root),),
        activity_log=ActivityLogConfig(
            root_id="notes",
            filename_template="%Y-%m-%d.md",
            timezone=ZoneInfo("Asia/Tokyo"),
        ),
    )


def test_creates_daily_log_and_appends_normalised_summary(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    timestamp = datetime(2026, 8, 6, 9, 15, tzinfo=ZoneInfo("Asia/Tokyo"))

    append_activity_log(
        settings,
        operation="write_note:create",
        note_root_id="notes",
        note_path="projects/plan.md",
        summary="  方針を\n決定した。  ",
        now=timestamp,
    )
    append_activity_log(
        settings,
        operation="append_note",
        note_root_id="notes",
        note_path="projects/plan.md",
        summary="次の作業を追加。",
        now=timestamp,
    )

    assert (tmp_path / "2026-08-06.md").read_text(encoding="utf-8") == (
        "- 2026-08-06 09:15 [write_note:create] `notes:projects/plan.md` — 方針を 決定した。\n"
        "- 2026-08-06 09:15 [append_note] `notes:projects/plan.md` — 次の作業を追加。\n"
    )


def test_does_not_write_without_summary_or_configuration(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    append_activity_log(
        settings,
        operation="write_note:create",
        note_root_id="notes",
        note_path="projects/plan.md",
        summary=None,
    )
    assert not (tmp_path / "2026-08-06.md").exists()

    disabled = Settings(
        read_roots=(RootConfig("notes", tmp_path),), write_roots=(RootConfig("notes", tmp_path),)
    )
    append_activity_log(
        disabled,
        operation="write_note:create",
        note_root_id="notes",
        note_path="projects/plan.md",
        summary="記録しない。",
    )
    assert not (tmp_path / "2026-08-06.md").exists()


def test_does_not_self_record_a_direct_activity_log_update(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    timestamp = datetime(2026, 8, 6, 9, 15, tzinfo=ZoneInfo("Asia/Tokyo"))
    log = tmp_path / "2026-08-06.md"
    requested_entry = "- 2026-08-06 午前 [meeting] チーム内ミーティングを2時間実施した。\n"
    log.write_text(requested_entry, encoding="utf-8")

    append_activity_log(
        settings,
        operation="append_note",
        note_root_id="notes",
        note_path="2026-08-06.md",
        summary="午前中に2時間のチーム内ミーティングを実施。",
        now=timestamp,
    )

    assert log.read_text(encoding="utf-8") == requested_entry


def test_rejects_empty_activity_summary(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        append_activity_log(
            settings_for(tmp_path),
            operation="write_note:create",
            note_root_id="notes",
            note_path="projects/plan.md",
            summary="\n  ",
        )


def test_activity_log_receives_updated_metadata_when_enabled(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings = Settings(
        read_roots=settings.read_roots,
        write_roots=settings.write_roots,
        activity_log=settings.activity_log,
        note_metadata=NoteMetadataConfig(ZoneInfo("Asia/Tokyo")),
    )
    timestamp = datetime(2026, 8, 6, 9, 15, tzinfo=ZoneInfo("Asia/Tokyo"))

    append_activity_log(
        settings,
        operation="write_note:create",
        note_root_id="notes",
        note_path="projects/plan.md",
        summary="方針を記録。",
        now=timestamp,
    )

    assert (tmp_path / "2026-08-06.md").read_text(encoding="utf-8") == (
        "---\nupdated: 2026-08-06T09:15:00+09:00\n---\n\n"
        "- 2026-08-06 09:15 [write_note:create] `notes:projects/plan.md` — 方針を記録。\n"
    )
