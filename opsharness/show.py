"""Print an episode trace in a readable form.

  python -m opsharness.show runs/<stamp>/<policy>/<env>/seed_3.json
  python -m opsharness.show runs/<stamp> --failing      # every trace scoring under 1.0
"""
import argparse
import json
from pathlib import Path


def show(path, width=160):
    d = json.loads(Path(path).read_text())
    r = d["result"]
    print(f"== {path}")
    print(f"   score={r['score']} stop={r['stop']} steps={r['steps']} details={json.dumps(r['details'])}")
    print(f"   metrics={json.dumps(r['metrics'])}")
    for m in d["messages"][1:]:
        if m["role"] == "user":
            print(f"\nUSER  {m['content']}")
        elif m["role"] == "assistant":
            if m["content"]:
                print(f"\nMODEL {m['content'][:width * 3]}")
            for c in m["calls"]:
                print(f"  -> {c['name']}({json.dumps(c.get('args'))[:width]})")
        elif m["role"] == "tool":
            flag = "!!" if m["content"].startswith('{"error"') else "  "
            print(f"  {flag} {m['content'][:width]}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--failing", action="store_true")
    ap.add_argument("--width", type=int, default=160)
    a = ap.parse_args()
    p = Path(a.path)
    files = sorted(p.rglob("seed_*.json")) if p.is_dir() else [p]
    for f in files:
        if a.failing and json.loads(f.read_text())["result"]["score"] >= 1.0:
            continue
        show(f, a.width)


if __name__ == "__main__":
    main()
