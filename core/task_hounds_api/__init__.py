"""task_hounds_api public API.

Layered architecture (no reverse dependencies):

    db/         SQLite layer. Pure CRUD. No business logic.
    opencode/   OpenCode CLI client. Depends on db/.
    workflow/   LangGraph engine. Depends on db/, opencode/.
    skills/     Standalone tools (DB MCP). Depends on db/.
    api/        HTTP layer. Depends on db/, workflow/, opencode/.

Strict rules:
  - No module imports from api/
  - workflow/ may import from db/ and opencode/, not api/
  - opencode/ may import from db/, not workflow/ or api/
  - db/ imports nothing from this package
"""
