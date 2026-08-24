import pytest

from agent_notes_mcp.managed_blocks import (
    ManagedBlockError,
    ManagedBlockUpdate,
    replace_managed_blocks,
)


def test_replaces_requested_managed_blocks_and_preserves_other_content() -> None:
    source = """# Handoff

<!-- agent-notes:managed current-state -->
## Current state
old state
<!-- /agent-notes:managed current-state -->

Human note.

<!-- agent-notes:managed next-actions -->
## Next actions
- old task
<!-- /agent-notes:managed next-actions -->
"""

    result = replace_managed_blocks(
        source,
        [
            ManagedBlockUpdate("current-state", "## Current state\nnew state"),
            ManagedBlockUpdate("next-actions", "## Next actions\n- new task\n"),
        ],
    )

    assert result == """# Handoff

<!-- agent-notes:managed current-state -->
## Current state
new state
<!-- /agent-notes:managed current-state -->

Human note.

<!-- agent-notes:managed next-actions -->
## Next actions
- new task
<!-- /agent-notes:managed next-actions -->
"""


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("<!-- /agent-notes:managed state -->\n", "without an opening"),
        (
            "<!-- agent-notes:managed state -->\ntext\n",
            "is not closed",
        ),
        (
            "<!-- agent-notes:managed state -->\n<!-- agent-notes:managed next -->\n",
            "before state is closed",
        ),
        (
            "<!-- agent-notes:managed state -->\n<!-- /agent-notes:managed next -->\n",
            "ends with next",
        ),
        (
            "<!-- agent-notes:managed state -->\n<!-- /agent-notes:managed state -->\n"
            "<!-- agent-notes:managed state -->\n<!-- /agent-notes:managed state -->\n",
            "appears more than once",
        ),
    ],
)
def test_rejects_malformed_managed_block_structure(source: str, message: str) -> None:
    with pytest.raises(ManagedBlockError, match=message):
        replace_managed_blocks(source, [ManagedBlockUpdate("state", "new")])


def test_rejects_unknown_or_duplicate_updates_and_markers_in_content() -> None:
    source = "<!-- agent-notes:managed state -->\nold\n<!-- /agent-notes:managed state -->\n"

    with pytest.raises(ManagedBlockError, match="not found"):
        replace_managed_blocks(source, [ManagedBlockUpdate("other", "new")])
    with pytest.raises(ManagedBlockError, match="more than once"):
        replace_managed_blocks(
            source,
            [ManagedBlockUpdate("state", "one"), ManagedBlockUpdate("state", "two")],
        )
    with pytest.raises(ManagedBlockError, match="must not contain"):
        replace_managed_blocks(
            source,
            [ManagedBlockUpdate("state", "<!-- agent-notes:managed nested -->")],
        )
