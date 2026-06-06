"""Pass 2: Wrap raw DB returns in compat.py with 'or {}' to prevent null.content crashes.

These routes have a valid active session but the DB function can still
return None (e.g. no plan row yet, no handoff yet). The fix: return {}
instead of None.
"""
import re
from pathlib import Path

f = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core\task_hounds_api\api\routes\compat.py")
text = f.read_text(encoding="utf-8")

# Single-object DB returns that can be None
SINGLE_OBJECT = [
    "return db_wf.get_plan(sid)",
    "return db_wf.get_active_suggestion(sid)",
    "return db_wf.get_handoff(sid)",
    "return db_wf.get_latest_manager_message(sid)",
    "return db_wf.latest_worker_report(sid)",
    "return db_chat.get_latest_directive(sid, status=\"pending\")",
    "return db_chat.get_latest_directive(sid)",
]

count = 0
for old in SINGLE_OBJECT:
    new = old + " or {}"
    if old in text:
        text = text.replace(old, new)
        count += text.count(new) - text.count(old)  # not used, but tracks changes
        count += 1

# Count actual replacements
for old in SINGLE_OBJECT:
    occurrences = text.count(old)
    if occurrences > 0:
        # already replaced
        pass

f.write_text(text, encoding="utf-8")
print(f"Wrapped {sum(1 for old in SINGLE_OBJECT if (old + ' or {}') in text)} single-object returns with 'or {{}}'")
