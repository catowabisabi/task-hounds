# Task Hounds runtime contract tests

These scripts verify backend/runtime assumptions that have caused regressions before.

## Main runtime contract

Run from the repository root:

```powershell
$env:PYTHONPATH="core"
python docs/testing/scripts/test_task_hounds_runtime_contract.py
```

The default run does not send a chat message. It confirms:

- Task Hounds resolves the managed local OpenCode binary, not global npm/PATH OpenCode.
- OpenCode version is pinned to `opencode-ai@1.15.13`.
- Runtime config files exist under `core/runtime/opencode_home` and `core/runtime/opencode_config`.
- Plugins and MCP packages are pinned, not `@latest`.
- OpenCode child processes receive isolated `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `OPENCODE_CONFIG_DIR`.
- The startup retry guard is not locked by stale `process_gone` / unreachable history.
- `agent_runtime_bindings` has manager, worker, reviewer, and chat bound to one reachable managed server.
- The bound OpenCode server eventually returns a non-empty `/agent` list.
- FastAPI `/api/health` and `/api/chat/status` are reachable.

To also test the chat agent end-to-end, which writes one diagnostic message to the active session:

```powershell
$env:PYTHONPATH="core"
python docs/testing/scripts/test_task_hounds_runtime_contract.py --chat-ping
```

For a different FastAPI port:

```powershell
$env:PYTHONPATH="core"
python docs/testing/scripts/test_task_hounds_runtime_contract.py --api-base http://127.0.0.1:8767
```

For local-only checks without FastAPI:

```powershell
$env:PYTHONPATH="core"
python docs/testing/scripts/test_task_hounds_runtime_contract.py --skip-api
```

