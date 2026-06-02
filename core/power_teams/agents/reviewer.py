"""
reviewer.py — Reviewer agent cycle.

The Reviewer runs asynchronously after each worker task, providing UI/UX
feedback and optionally creating follow-up suggestions for the manager.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from power_teams.agents.base import (
    _create_suggestion,
    _extract_section,
    _get_latest_handoff,
    connect,
    log,
    read_text,
    send_to_agent,
    update_agent,
    utc_now,
    worker_report_path,
    worker_status_path,
    write_text,
    ROOT,
    DB_PATH,
)
from power_teams.db import (
    create_reviewer_session,
    get_latest_worker_report,
    update_reviewer_session,
)


# ── Reviewer prompt template ──────────────────────────────────────────────────

REVIEWER_PROMPT_TEMPLATE = """
You are the Reviewer Agent — a UI/UX expert and documentation specialist.

Your job is to review completed work from a visual and user experience perspective.

=== TASK CONTEXT ===
{suggestion_content}

=== WORKER REPORT ===
{worker_report}

=== YOUR JOB ===

Analyze the completed work and provide feedback on:

1. **UI/UX Quality**:
   - Is the implementation clean and professional?
   - Are there any usability issues or confusing elements?
   - What would frustrate users? What would delight them?

2. **Information Design**:
   - Is information presented clearly?
   - Are labels, instructions, and feedback helpful?
   - Is there appropriate information hierarchy?

3. **Style & Consistency**:
   - Does it follow good design practices?
   - Are patterns used consistently?
   - Any visual or interaction inconsistencies?

4. **Documentation**:
   - What commands/scripts are needed to run/test this?
   - How does a user access/open the feature?
   - Any setup or configuration steps required?

=== OUTPUT FORMAT ===

Provide your analysis in this structure:

**UI/UX Observations:**
- [List 2-4 key observations about the implementation]

**Usability Issues:**
- [List any problems found, or "None identified" if all good]

**Style Feedback:**
- [Design consistency notes, or "Consistent and well-designed" if good]

**Useful development/bin/Commands:**
```bash
# Command to run/test the feature
[command here]
```

**Recommendations:**
- [1-3 actionable suggestions for improvement, or "No immediate improvements needed"]

Keep your review concise but insightful. Focus on what matters for user experience.
"""


def capture_screenshots_simple(suggestion_id: int) -> list[str]:
    """
    Placeholder screenshot capture.  Returns list of paths (empty until
    Playwright integration is added).
    """
    screenshot_dir = ROOT / "core" / "runtime" / "reviewer_screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    log(f"Screenshot capture: Manual step for suggestion #{suggestion_id}")
    log(f"  To add automated screenshots, install playwright: pip install playwright")
    log(f"  Then run: playwright install chromium")

    return []


def run_reviewer_session(suggestion_id: int) -> None:
    """
    Run a complete reviewer session for a completed suggestion.
    Designed to be called in a background thread.
    """
    session_id = None
    try:
        session_id = create_reviewer_session(suggestion_id)
        update_reviewer_session(session_id, status="running")
        update_agent(
            "reviewer",
            state="busy",
            current_step=f"reviewing suggestion #{suggestion_id}",
            step_source="reviewer",
            current_step_started_at=utc_now(),
            last_stream_at=utc_now(),
            last_seen=utc_now(),
        )
        log(f"Reviewer session #{session_id} started for suggestion #{suggestion_id}")

        with connect() as db:
            suggestion = db.execute(
                "SELECT * FROM suggestion_queue WHERE id=?", (suggestion_id,)
            ).fetchone()

        if not suggestion:
            log(f"Reviewer: Suggestion #{suggestion_id} not found")
            update_reviewer_session(session_id, status="failed")
            return

        report_row = get_latest_worker_report(suggestion["session_id"], path=DB_PATH) if suggestion["session_id"] else None
        worker_report = report_row["report"] if report_row else read_text(worker_report_path())

        prompt = REVIEWER_PROMPT_TEMPLATE.format(
            suggestion_content=suggestion["content"],
            worker_report=worker_report if worker_report and worker_report != "# Worker Report\n" else "(No worker report available)"
        )

        log(f"Reviewer: Analyzing suggestion #{suggestion_id}")
        review_response = send_to_agent("reviewer", prompt, max_retries=0)

        ui_ux_obs      = _extract_section(review_response, "UI/UX Observations")
        usability_issues = _extract_section(review_response, "Usability Issues")
        style_feedback  = _extract_section(review_response, "Style Feedback")
        useful_scripts  = _extract_section(review_response, "Useful development/bin/Commands")
        recommendations = _extract_section(review_response, "Recommendations")

        screenshots = capture_screenshots_simple(suggestion_id)

        review_notes = []
        if ui_ux_obs:
            review_notes.append(f"**UI/UX Observations:**\n{ui_ux_obs}")
        if recommendations:
            review_notes.append(f"**Recommendations:**\n{recommendations}")
        review_notes_text = "\n\n".join(review_notes) if review_notes else "Review completed."

        update_reviewer_session(
            session_id,
            status="completed",
            screenshot_paths=json.dumps(screenshots) if screenshots else None,
            review_notes=review_notes_text,
            usability_issues=usability_issues or "None identified",
            style_feedback=style_feedback or "Consistent and well-designed",
            scripts_documented=useful_scripts or "(No specific scripts documented)",
            completed_at=datetime.now(timezone.utc).isoformat()
        )

        log(f"Reviewer session #{session_id} completed successfully")
        update_agent("reviewer", state="idle", current_step=None, step_source=None, current_step_started_at=None, last_seen=utc_now())
        write_text(worker_status_path(), "idle\n")
        update_agent("worker", state="idle", current_step=None, step_source=None, current_step_started_at=None, last_seen=utc_now())

        # If significant issues found, create a follow-up suggestion
        if usability_issues and usability_issues.lower() not in ("none identified", "n/a", ""):
            follow_up_content = (
                f"Address usability issues identified by reviewer:\n\n"
                f"{usability_issues}\n\n"
                f"Reviewer recommendations:\n{recommendations or 'Improve based on issues above.'}"
            )
            handoff = _get_latest_handoff()
            handoff_ver = handoff["version"] if handoff else None
            new_sid = _create_suggestion(
                content=follow_up_content,
                verification="[ ] Usability issues resolved\n[ ] User experience improved",
                handoff_version=handoff_ver,
            )
            log(f"Reviewer created follow-up suggestion #{new_sid} for usability improvements")

    except Exception as exc:
        log(f"Reviewer session failed: {exc}")
        if session_id:
            update_reviewer_session(session_id, status="failed")
        update_agent("reviewer", state="error", current_step=None, step_source=None, current_step_started_at=None, last_error=str(exc), last_seen=utc_now())
        write_text(worker_status_path(), "idle\n")
        update_agent("worker", state="idle", current_step=None, step_source=None, current_step_started_at=None, last_seen=utc_now())


def _trigger_reviewer_async(suggestion_id: int) -> None:
    """Trigger reviewer in a background daemon thread (non-blocking)."""
    t = threading.Thread(
        target=run_reviewer_session,
        args=(suggestion_id,),
        daemon=True,
    )
    t.start()
    log(f"Reviewer started in background thread for suggestion #{suggestion_id}")
