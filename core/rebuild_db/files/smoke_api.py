"""Smoke test API layer."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core")

# Use a fresh test DB
test_db = Path(tempfile.gettempdir()) / "api_smoke.db"
if test_db.exists():
    try:
        test_db.unlink()
    except Exception:
        pass
os.environ["POWER_TEAMS_DB"] = str(test_db)

# Try to import the app
try:
    from task_hounds_api.api import create_app
    app = create_app()
    print(f"App created. Routes:")
    for r in app.routes:
        if hasattr(r, "methods") and hasattr(r, "path"):
            methods = ", ".join(sorted(r.methods - {"HEAD", "OPTIONS"}))
            print(f"  [{methods:10s}] {r.path}")
except Exception as e:
    print(f"FAIL: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print(f"\n=== API smoke test PASSED ===")
