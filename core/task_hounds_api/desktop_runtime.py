"""Frozen desktop runtime dispatcher.

PyInstaller child processes cannot use ``python -m package`` because
``sys.executable`` points back to the frozen executable. This entry point lets
the supervisor start each runtime role through that same executable.
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task Hounds desktop runtime")
    parser.add_argument(
        "--runtime-role",
        choices=("supervisor", "api", "worker"),
        default="supervisor",
    )
    args, remaining = parser.parse_known_args(argv)

    if args.runtime_role == "api":
        from task_hounds_api.__main__ import main as role_main
    elif args.runtime_role == "worker":
        from task_hounds_api.graphflow_worker import main as role_main
    else:
        from task_hounds_api.supervisor import main as role_main
    return role_main(remaining)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
