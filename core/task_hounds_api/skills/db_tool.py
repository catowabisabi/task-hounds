"""
db_tool.py — CLI for Task Hounds DB Skill v1

Allows opencode agents to interact with DB skill via shell,
without knowing the actual DB file path.

Usage:
    python -m power_teams.skills.db_tool read-project-context --project-session-id ... --role ... --role-session-id ...
    python -m power_teams.skills.db_tool read-table --project-session-id ... --role ... --role-session-id ... --table suggestion_queue --limit 20
    python -m power_teams.skills.db_tool write --project-session-id ... --role ... --role-session-id ... --operation append_manager_message --payload-json '{...}'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure skill module is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from task_hounds_api.skills.db_skill import (
    read_project_context,
    read_table,
    validate_identity,
    write_operation,
)


def _json_output(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False))


def cmd_read_project_context(args: argparse.Namespace) -> None:
    result = read_project_context(
        project_session_id=args.project_session_id,
        role=args.role,
        role_session_id=args.role_session_id,
    )
    _json_output(result)


def cmd_read_table(args: argparse.Namespace) -> None:
    filters = None
    if args.filter:
        try:
            filters = json.loads(args.filter)
        except json.JSONDecodeError:
            _json_output({"ok": False, "error": {"type": "ArgError", "message": f"Invalid filter JSON: {args.filter}"}})
            sys.exit(1)

    result = read_table(
        project_session_id=args.project_session_id,
        role=args.role,
        role_session_id=args.role_session_id,
        table=args.table,
        filters=filters,
        limit=args.limit,
    )
    _json_output(result)


def cmd_write(args: argparse.Namespace) -> None:
    try:
        payload = json.loads(args.payload_json)
    except json.JSONDecodeError:
        _json_output({"ok": False, "error": {"type": "ArgError", "message": f"Invalid payload JSON: {args.payload_json}"}})
        sys.exit(1)

    result = write_operation(
        project_session_id=args.project_session_id,
        role=args.role,
        role_session_id=args.role_session_id,
        operation=args.operation,
        payload=payload,
    )
    _json_output(result)


def cmd_validate(args: argparse.Namespace) -> None:
    ok, err = validate_identity(
        project_session_id=args.project_session_id,
        role=args.role,
        role_session_id=args.role_session_id,
    )
    _json_output({"ok": ok, "error": err if not ok else None})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m power_teams.skills.db_tool")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_parser = sub.add_parser("validate", help="Validate identity")
    validate_parser.add_argument("--project-session-id", required=True)
    validate_parser.add_argument("--role", required=True)
    validate_parser.add_argument("--role-session-id", required=True)
    validate_parser.set_defaults(func=cmd_validate)

    rpc_parser = sub.add_parser("read-project-context", help="Read project context")
    rpc_parser.add_argument("--project-session-id", required=True)
    rpc_parser.add_argument("--role", required=True)
    rpc_parser.add_argument("--role-session-id", required=True)
    rpc_parser.set_defaults(func=cmd_read_project_context)

    rt_parser = sub.add_parser("read-table", help="Read a table")
    rt_parser.add_argument("--project-session-id", required=True)
    rt_parser.add_argument("--role", required=True)
    rt_parser.add_argument("--role-session-id", required=True)
    rt_parser.add_argument("--table", required=True)
    rt_parser.add_argument("--filter", help="JSON object of column=value filters")
    rt_parser.add_argument("--limit", type=int, default=50)
    rt_parser.set_defaults(func=cmd_read_table)

    write_parser = sub.add_parser("write", help="Execute a write operation")
    write_parser.add_argument("--project-session-id", required=True)
    write_parser.add_argument("--role", required=True)
    write_parser.add_argument("--role-session-id", required=True)
    write_parser.add_argument("--operation", required=True)
    write_parser.add_argument("--payload-json", required=True)
    write_parser.set_defaults(func=cmd_write)

    args = parser.parse_args(argv)

    try:
        args.func(args)
        return 0
    except Exception as exc:
        _json_output({"ok": False, "error": {"type": "UnexpectedError", "message": str(exc)}})
        return 1


if __name__ == "__main__":
    sys.exit(main())