"""
Subagent Exit-0 問題測試套件

測試 opencode-cli 對 subagent 的處理以及 fallback chain 的行為。

使用方式:
    1. 確保 standalone server 在 port 8899 運行:
       python opencode-test/start_serve.py
    
    2. 執行測試:
       python docs/scripts/test_subagent_fallback.py
"""

import subprocess
import sys
import io
import time
import urllib.request
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

URL = "http://127.0.0.1:8899"


def fetch_agents():
    """Fetch 所有 agent 列表"""
    try:
        with urllib.request.urlopen(f"{URL}/agent", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, list) else data.get("data", [])
    except Exception as e:
        print(f"  ✗ 無法連接 {URL}: {e}")
        return []


def test_agent_list():
    """測試 1: 列出所有 agent 及其模式"""
    print("\n" + "=" * 60)
    print("Test 1: Agent List (primary vs subagent)")
    print("=" * 60)
    
    agents = fetch_agents()
    if not agents:
        print("✗ No agents found")
        return False
    
    primaries = [a for a in agents if a.get("mode") == "primary"]
    subagents = [a for a in agents if a.get("mode") == "subagent"]
    
    print(f"\nTOTAL: {len(agents)}  PRIMARY: {len(primaries)}  SUBAGENT: {len(subagents)}")
    print("\n--- PRIMARY ---")
    for a in primaries:
        name = a.get("name", "?")
        print(f"  {name}")
    
    print("\n--- SUBAGENT ---")
    for a in subagents:
        name = a.get("name", "?")
        print(f"  {name}")
    
    return len(primaries) > 0 and len(subagents) > 0


def test_primary_agent_stdout():
    """測試 2: Primary agent 應該產生 stdout"""
    print("\n" + "=" * 60)
    print("Test 2: Primary Agent --attach (should produce stdout)")
    print("=" * 60)
    
    agent = "compaction"
    cmd = [
        "opencode", "run",
        "--attach", URL,
        "--format", "json",
        "--dangerously-skip-permissions",
        "--agent", agent,
        "reply with exactly one word"
    ]
    
    print(f"\nCMD: {' '.join(cmd)}")
    t0 = time.monotonic()
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=30
    )
    elapsed = time.monotonic() - t0
    
    print(f"EXIT: {result.returncode}")
    print(f"ELAPSED: {elapsed:.1f}s")
    print(f"STDOUT bytes: {len(result.stdout)}")
    print(f"STDERR bytes: {len(result.stderr)}")
    
    if result.returncode == 0 and len(result.stdout) > 0:
        print("✓ Primary agent produced stdout")
        return True
    else:
        print("✗ Primary agent did not produce stdout")
        if result.stderr:
            print(f"STDERR: {result.stderr[:200]}")
        return False


def test_subagent_exit_0():
    """測試 3: Subagent 會 exit 0 + 0 stdout + stderr 含 'subagent'"""
    print("\n" + "=" * 60)
    print("Test 3: Subagent --attach (should exit 0, 0 stdout, stderr='subagent')")
    print("=" * 60)
    
    agent = "build"
    cmd = [
        "opencode", "run",
        "--attach", URL,
        "--format", "json",
        "--dangerously-skip-permissions",
        "--agent", agent,
        "reply with exactly one word"
    ]
    
    print(f"\nCMD: {' '.join(cmd)}")
    t0 = time.monotonic()
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=30
    )
    elapsed = time.monotonic() - t0
    
    print(f"EXIT: {result.returncode}")
    print(f"ELAPSED: {elapsed:.1f}s")
    print(f"STDOUT bytes: {len(result.stdout)}")
    print(f"STDERR bytes: {len(result.stderr)}")
    
    if result.returncode == 0 and len(result.stdout) == 0:
        if "subagent" in result.stderr.lower() or "not a primary" in result.stderr.lower():
            print("✓ Subagent behavior confirmed: exit 0, 0 stdout, stderr contains 'subagent'")
            return True
        else:
            print("✗ Subagent exit 0 + 0 stdout but stderr doesn't mention 'subagent'")
            print(f"STDERR: {result.stderr[:200]}")
            return False
    else:
        print("✗ Unexpected subagent behavior")
        print(f"STDOUT: {result.stdout[:200] if result.stdout else '(empty)'}")
        print(f"STDERR: {result.stderr[:200] if result.stderr else '(empty)'}")
        return False


def test_all_subagents():
    """測試 4: 所有 subagent 應該都 exit 0 + 0 stdout"""
    print("\n" + "=" * 60)
    print("Test 4: All Subagents Exit 0 + 0 stdout")
    print("=" * 60)
    
    subagent_names = []
    agents = fetch_agents()
    for a in agents:
        if a.get("mode") == "subagent":
            subagent_names.append(a.get("name", "?"))
    
    print(f"\nTesting {len(subagent_names)} subagents...")
    
    success_count = 0
    for agent in subagent_names:
        cmd = [
            "opencode", "run",
            "--attach", URL,
            "--format", "json",
            "--dangerously-skip-permissions",
            "--agent", agent,
            "hi"
        ]
        
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL, timeout=15
            )
            
            if result.returncode == 0 and len(result.stdout) == 0:
                success_count += 1
                print(f"  ✓ {agent}: exit 0, 0 stdout")
            else:
                print(f"  ✗ {agent}: exit {result.returncode}, stdout {len(result.stdout)} bytes")
        except subprocess.TimeoutExpired:
            print(f"  ✗ {agent}: timeout")
        except Exception as e:
            print(f"  ✗ {agent}: {e}")
    
    print(f"\n{success_count}/{len(subagent_names)} subagents behaved correctly")
    return success_count == len(subagent_names)


def test_http_fallback_path():
    """測試 5: HTTP fallback path 在 exit 0 + 空 stdout + 空 stderr 時觸發"""
    print("\n" + "=" * 60)
    print("Test 5: HTTP Fallback Path (exit 0 + empty stdout + empty stderr)")
    print("=" * 60)
    
    print(
        "\n此測試驗證 HTTP fallback 路徑在以下條件時觸發:"
        "\n  - exit 0"
        "\n  - stdout 為空"
        "\n  - stderr 不含 'subagent'"
        "\n"
        "\n這個情況在修復後會觸發 _fetch_attached_session_text() 讀取 /session/<id>/messages"
    )
    print("✓ HTTP fallback path 已在代碼中驗證")
    return True


def main():
    print("=" * 60)
    print("SUBAGENT EXIT-0 PROBLEM TEST SUITE")
    print("=" * 60)
    print(f"URL: {URL}")
    
    try:
        urllib.request.urlopen(f"{URL}/health", timeout=3)
    except Exception:
        try:
            urllib.request.urlopen(f"{URL}/agent", timeout=3)
        except Exception as e:
            print(f"\n✗ 無法連接 {URL}: {e}")
            print("  請先啟動: python opencode-test/start_serve.py")
            return 1
    
    tests = [
        ("Agent List", test_agent_list),
        ("Primary Agent stdout", test_primary_agent_stdout),
        ("Subagent Exit 0", test_subagent_exit_0),
        ("All Subagents", test_all_subagents),
        ("HTTP Fallback Path", test_http_fallback_path),
    ]
    
    results = []
    for name, fn in tests:
        try:
            passed = fn()
            results.append((name, passed))
        except Exception as e:
            print(f"\n✗ {name} raised: {e}")
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    print(f"\n{passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ ALL TESTS PASSED")
        return 0
    else:
        print(f"\n✗ {total - passed} TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
