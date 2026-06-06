"""Bulk fix: add /api/debug-logs and replace 'return None' with 'return {}' in compat.py and workflow.py.

The UI crashes when these routes return None (null) because components
try to access .content on the response. Single-object read endpoints
should always return {} instead of None when no active session.
"""
import re
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core\task_hounds_api\api\routes")

# Count fixes
fixed_compat = 0
fixed_workflow = 0


def fix_file(path: Path) -> int:
    """Replace 'return None' after 'if not sid:' with 'return {}'."""
    text = path.read_text(encoding="utf-8")
    new_text = re.sub(
        r"(if not sid:\s*\n\s*)return None",
        r"\1return {}",
        text,
    )
    if new_text != text:
        n = text.count("return None")
        path.write_text(new_text, encoding="utf-8")
        return n
    return 0


fixed_compat = fix_file(ROOT / "compat.py")
fixed_workflow = fix_file(ROOT / "workflow.py")

print(f"Fixed {fixed_compat} 'return None' -> 'return {{}}' in compat.py")
print(f"Fixed {fixed_workflow} 'return None' -> 'return {{}}' in workflow.py")
