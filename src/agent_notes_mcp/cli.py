"""Command-line entry point for the stdio server."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import ConfigError, default_config_path, load_settings
from .server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve local Markdown notes over MCP stdio.")
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            f"Path to the TOML configuration file. Defaults to {default_config_path()}."
        ),
    )
    arguments = parser.parse_args()
    try:
        settings = load_settings(arguments.config)
    except ConfigError as error:
        parser.error(str(error))
    create_server(settings).run(transport="stdio")
