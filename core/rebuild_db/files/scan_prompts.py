"""One-off scan: extract prompt strings from core/ Python files."""
import ast
import json
import re
from pathlib import Path

ROOT = Path(r"C:\Users\enoma\Desktop\opencode-work\agent-works\software\power-teams")
OUT = ROOT / "core" / "rebuild_db" / "files" / "prompt_scan.json"

# Regex to detect prompt-like text: multiline strings >= 200 chars with directive keywords
PROMPT_HINTS = re.compile(
    r"(?:TOOL[-_ ]FIRST|system[_-]?prompt|return JSON|You are the |"
    r"=== HUMAN|=== MANAGER|=== EXISTING|=== CURRENT|=== PREVIOUS|"
    r"instruction\.|\".*Return JSON.*\")",
    re.IGNORECASE | re.DOTALL,
)


def is_prompt_string(s: str) -> bool:
    """Heuristic: a string is a prompt if it's long and contains directive keywords."""
    if not s or len(s) < 200:
        return False
    if PROMPT_HINTS.search(s):
        return True
    # Heuristic: very long strings (>500 chars) that are clearly instructions
    if len(s) > 500 and any(
        kw in s.lower()
        for kw in [
            "do not",
            "must",
            "return exactly",
            "include",
            "fenced",
            "todo",
            "manager",
            "worker",
            "reviewer",
            "directive",
            "evidence",
            "verification",
        ]
    ):
        return True
    return False


def scan_node(node, prompts):
    """Recursively scan AST node for string constants that look like prompts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if is_prompt_string(node.value):
            prompts.append(
                {
                    "line": node.lineno,
                    "col": node.col_offset,
                    "length": len(node.value),
                    "preview": node.value[:100].replace("\n", " ") + "...",
                    "full": node.value,
                }
            )
    elif isinstance(node, ast.JoinedStr):
        # f-string: try to extract the value
        try:
            value = ast.unparse(node)
        except Exception:
            value = None
        if value and is_prompt_string(value):
            prompts.append(
                {
                    "line": node.lineno,
                    "col": node.col_offset,
                    "length": len(value),
                    "preview": value[:100].replace("\n", " ") + "...",
                    "full": value,
                }
            )
    elif hasattr(node, "_fields"):
        for child in ast.iter_child_nodes(node):
            scan_node(child, prompts)


def main():
    results = []
    for p in sorted((ROOT / "core").rglob("*.py")):
        if "__pycache__" in p.parts or "node_modules" in p.parts:
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except Exception as e:
            results.append(
                {
                    "path": str(p.relative_to(ROOT)).replace("\\", "/"),
                    "error": str(e),
                    "prompts": [],
                }
            )
            continue
        prompts = []
        scan_node(tree, prompts)
        if prompts:
            results.append(
                {
                    "path": str(p.relative_to(ROOT)).replace("\\", "/"),
                    "lines": src.count("\n") + 1,
                    "prompt_count": len(prompts),
                    "prompts": prompts,
                }
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    total = sum(r.get("prompt_count", 0) for r in results)
    print(f"Found {total} prompts in {len(results)} files. Wrote to {OUT}")


if __name__ == "__main__":
    main()
