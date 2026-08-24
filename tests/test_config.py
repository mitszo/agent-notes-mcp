from pathlib import Path

import pytest

from agent_notes_mcp.config import (
    ConfigError,
    RootConfig,
    default_config_path,
    load_settings,
)


def write_config(path: Path, read_root: Path, write_root: Path) -> None:
    path.write_text(
        f'''[[read_roots]]
id = "read"
path = "{read_root}"

[[write_roots]]
id = "write"
path = "{write_root}"
''',
        encoding="utf-8",
    )


def test_loads_separate_read_and_write_roots(tmp_path: Path) -> None:
    read_root = tmp_path / "notes"
    write_root = read_root / "inbox"
    write_root.mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    write_config(config_path, read_root, write_root)

    settings = load_settings(config_path)

    assert settings.read_root("read").path == read_root
    assert settings.write_root("write").path == write_root


def test_uses_new_default_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_path() == tmp_path / "agent-notes-mcp" / "config.toml"


def test_write_root_is_readable_without_an_explicit_read_root(tmp_path: Path) -> None:
    read_root = tmp_path / "notes"
    write_root = tmp_path / "other"
    read_root.mkdir()
    write_root.mkdir()
    config_path = tmp_path / "config.toml"
    write_config(config_path, read_root, write_root)

    settings = load_settings(config_path)

    assert settings.read_root("write").path == write_root
    assert settings.readable_roots == (RootConfig("read", read_root), RootConfig("write", write_root))


def test_loads_a_write_only_configuration(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[[write_roots]]
id = "notes"
path = "{root}"
''',
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.readable_roots == (RootConfig("notes", root),)


def test_implicit_reading_does_not_duplicate_a_write_subroot(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    write_config(config_path, root, inbox)

    settings = load_settings(config_path)

    assert settings.readable_roots == (RootConfig("read", root),)


def test_allows_same_id_for_identical_read_and_write_root(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[[read_roots]]
id = "notes"
path = "{root}"

[[write_roots]]
id = "notes"
path = "{root}"
''',
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.read_root("notes").path == settings.write_root("notes").path


def test_rejects_same_id_for_different_read_and_write_roots(tmp_path: Path) -> None:
    read_root = tmp_path / "notes"
    write_root = read_root / "inbox"
    write_root.mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[[read_roots]]
id = "notes"
path = "{read_root}"

[[write_roots]]
id = "notes"
path = "{write_root}"
''',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="different"):
        load_settings(config_path)


def test_loads_explicitly_enabled_activity_log(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[[read_roots]]
id = "notes"
path = "{root}"

[[write_roots]]
id = "notes"
path = "{root}"

[activity_log]
enabled = true
root_id = "notes"
filename_template = "%Y-%m-%d.md"
timezone = "Asia/Tokyo"
''',
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.activity_log is not None
    assert settings.activity_log.root_id == "notes"
    assert settings.activity_log.timezone.key == "Asia/Tokyo"


def test_loads_explicitly_enabled_note_metadata(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[[write_roots]]
id = "notes"
path = "{root}"

[note_metadata]
enabled = true
timezone = "Asia/Tokyo"
''',
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.note_metadata is not None
    assert settings.note_metadata.timezone.key == "Asia/Tokyo"


@pytest.mark.parametrize(
    ("activity_log", "message"),
    [
        ('enabled = true\nroot_id = "missing"\nfilename_template = "%Y.md"', "Unknown write"),
        ('enabled = true\nroot_id = "notes"\nfilename_template = "daily/%Y.md"', "one Markdown filename"),
    ],
)
def test_rejects_invalid_activity_log(tmp_path: Path, activity_log: str, message: str) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''[[read_roots]]
id = "notes"
path = "{root}"

[[write_roots]]
id = "notes"
path = "{root}"

[activity_log]
{activity_log}
''',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_settings(config_path)
