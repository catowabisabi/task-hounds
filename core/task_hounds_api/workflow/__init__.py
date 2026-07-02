"""workflow — Task Hounds Manager/Worker/Reviewer engine.

Public API:
    models     — FlowInput, FlowState, FlowOutput dataclasses
    executor   — Manager/Worker/Reviewer step functions
    graph      — LangGraph state machine
    signals    — DB-based signal emitter
    loop       — Background loop runner
    run_once   — Synchronous one-shot
    build_graph — Return a compiled LangGraph for embedding
    run_loop    — Run one full loop
"""
from task_hounds_api.workflow.graph import build_graph, run_loop
from task_hounds_api.workflow.loop import BackgroundLoop, run_once
from task_hounds_api.workflow import models, executor, signals  # noqa: F401
