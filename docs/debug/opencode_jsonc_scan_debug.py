import sys
sys.path.insert(0, ".")
from pathlib import Path
from api.model_validation import _strip_jsonc, _parse_jsonc_config, load_available_models_from_config

config_path = Path("runtime/opencode_config/opencode.jsonc")

# Step 1: Test strip
raw = config_path.read_text(encoding="utf-8-sig")
cleaned = _strip_jsonc(raw)

# Step 2: Test parse
import json
try:
    parsed = json.loads(cleaned)
    print(f"JSON parse OK: {list(parsed.keys())}")
except json.JSONDecodeError as e:
    print(f"JSON parse FAILED: {e}")
    print(f"Around error (chars {e.pos-50}:{e.pos+50}): {cleaned[max(0,e.pos-50):e.pos+50]}")
    sys.exit(1)

# Step 3: Test full scan
providers = parsed.get("provider", {})
print(f"Providers: {list(providers.keys())}")
for pid, pdata in providers.items():
    models = pdata.get("models", {})
    print(f"  {pid}: {len(models)} models = {list(models.keys())}")

# Step 4: Test load function
cache = load_available_models_from_config(Path("runtime"))
print(f"\nFinal cache: {len(cache)} models")
for k, v in cache.items():
    print(f"  {k}: {v}")
