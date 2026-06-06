"""Smoke test opencode layer."""
import os
import sys
sys.path.insert(0, r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams\core")

from task_hounds_api.opencode import config, binary, result
from task_hounds_api.opencode.config import is_model_available, model_supports_thinking, list_providers

print("=== config ===")
try:
    cfg = config.load()
    print(f"OK loaded config with {len(cfg.get('provider', {}))} providers")
    providers = list_providers()
    for pid, p in providers.items():
        print(f"  provider {pid}: {len(p.get('models', {}))} models")
    print(f"is_model_available('bailian-coding-plan/MiniMax-M2.5'):",
          is_model_available("bailian-coding-plan/MiniMax-M2.5"))
    print(f"model_supports_thinking('bailian-coding-plan/MiniMax-M2.5'):",
          model_supports_thinking("bailian-coding-plan/MiniMax-M2.5"))
except FileNotFoundError as e:
    print(f"Config not found (expected if not yet installed): {e}")

print("\n=== binary ===")
b = binary.find()
print(f"binary path: {b}")
print(f"exists: {b.exists() if b else 'N/A'}")

print("\n=== result ===")
r_ok = result.ok(agent="manager", text="hello world")
r_err = result.err(agent="worker", error_type="X", message="bad", retryable=True)
print(f"ok result: {r_ok}")
print(f"err result: {r_err}")
print(f"is_retryable(err): {result.is_retryable(r_err)}")
print(f"get_text(ok): {result.get_text(r_ok)!r}")
