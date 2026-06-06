"""opencode — Task Hounds OpenCode integration.

Public API:
    config   — read opencode.jsonc (one config, no fallback)
    binary   — find the managed opencode.exe path
    result   — unified JsonResult contract
    process  — spawn, monitor, kill the serve process
    client   — HTTP client (run, health, list_agents, precreate_session)
    lifecycle — start/stop/restart the shared server
"""
