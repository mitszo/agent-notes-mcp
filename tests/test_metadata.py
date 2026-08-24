from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agent_notes_mcp.metadata import FrontmatterError, set_updated


TIMESTAMP = datetime(2026, 8, 18, 12, 34, 56, tzinfo=ZoneInfo("Asia/Tokyo"))


def test_adds_frontmatter_when_missing() -> None:
    assert set_updated("# Memo\n", TIMESTAMP) == (
        "---\nupdated: 2026-08-18T12:34:56+09:00\n---\n\n# Memo\n"
    )


def test_updates_existing_frontmatter_without_reserializing_it() -> None:
    source = "---\ntitle: Memo\nupdated: old # keep\ntags: [one]\n---\n\n# Memo\n"

    assert set_updated(source, TIMESTAMP) == (
        "---\ntitle: Memo\nupdated: 2026-08-18T12:34:56+09:00 # keep\n"
        "tags: [one]\n---\n\n# Memo\n"
    )


@pytest.mark.parametrize(
    "source",
    ["---\ntitle: missing close\n", "---\nupdated: one\nupdated: two\n---\n"],
)
def test_rejects_frontmatter_that_cannot_be_safely_updated(source: str) -> None:
    with pytest.raises(FrontmatterError):
        set_updated(source, TIMESTAMP)
