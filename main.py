"""Command-line and Windows Service entry point for the Savior bridge."""

from __future__ import annotations

import argparse
import logging
import sys

from savior_client.config import ConfigurationError, Settings
from savior_client.logging_setup import configure_logging
from savior_client.runner import SaviorRunner


def run_console(*, once: bool = False) -> int:
    try:
        settings = Settings.load()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_file, console=True)
    runner = SaviorRunner.build(settings)
    runner.prepare()
    if once:
        result = runner.run_once()
        logging.getLogger(__name__).info(
            "Polling cycle complete: selected=%d delivered=%d failed=%d",
            result.selected,
            result.delivered,
            result.failed,
        )
    else:
        runner.run_forever()
    return 0


def run_windows_service_command() -> int:
    if sys.platform != "win32":
        print("Windows Service commands require Windows and pywin32.", file=sys.stderr)
        return 2

    from savior_client.windows_service import handle_command_line

    # pywin32 expects install/start/stop/remove directly after the script name.
    if len(sys.argv) > 1 and sys.argv[1] == "service":
        sys.argv.pop(1)
    handle_command_line()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize Savior punches to OneHash HRMS")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "once", "service"),
        default="run",
        help="run continuously, process one batch, or pass commands to pywin32",
    )
    args, unknown = parser.parse_known_args()
    if args.command != "service" and unknown:
        parser.error("unrecognized arguments: " + " ".join(unknown))
    return args


def main() -> int:
    args = parse_args()
    if args.command == "service":
        return run_windows_service_command()
    return run_console(once=args.command == "once")


if __name__ == "__main__":
    raise SystemExit(main())
