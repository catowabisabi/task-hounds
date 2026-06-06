from pathlib import Path

from task_hounds_api.api.debug_logs import write_debug_batch


def test_debug_writer_formats_json_text_and_redacts_secrets(tmp_path: Path) -> None:
    result = write_debug_batch(
        {
            "session_id": "../../unsafe/session",
            "user_agent": "pytest",
            "entries": [
                {
                    "sequence": 1,
                    "timestamp": "2026-06-04T12:00:00Z",
                    "level": "debug",
                    "category": "INTERACTION",
                    "event": "click",
                    "format": "json",
                    "data": {
                        "button": "Save",
                        "nested": {"ok": True},
                        "apiKey": "must-not-appear",
                    },
                    "page": "/settings",
                },
                {
                    "sequence": 2,
                    "timestamp": "2026-06-04T12:00:01Z",
                    "level": "info",
                    "category": "APP_DEBUG",
                    "event": "plain",
                    "format": "text",
                    "data": "original\nformat",
                    "page": "/settings",
                },
                {
                    "sequence": 3,
                    "timestamp": "2026-06-04T12:00:02Z",
                    "level": "debug",
                    "category": "API_RESPONSE",
                    "event": "204 GET /api/empty",
                    "format": "json",
                    "data": {"status": 204, "body": None},
                    "page": "/settings",
                },
            ],
        },
        log_dir=tmp_path,
    )

    path = tmp_path / f"{result['session_id']}.log"
    content = path.read_text(encoding="utf-8")

    assert path.parent == tmp_path
    assert "button: Save" in content
    assert "ok: true" in content
    assert "apiKey: [redacted]" in content
    assert "must-not-appear" not in content
    assert "original" in content
    assert "format" in content
    assert "body: null" in content
