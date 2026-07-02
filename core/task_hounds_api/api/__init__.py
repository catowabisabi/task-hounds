"""api — Task Hounds FastAPI HTTP layer.

Public API:
    schemas    — Pydantic models
    deps       — FastAPI dependencies
    routes     — route modules
    create_app — build the FastAPI app
"""
from task_hounds_api.api.main import create_app  # noqa: F401
