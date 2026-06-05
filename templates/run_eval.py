"""Bragi template system eval runner.

Reads test_scenarios.json, queries the template index for each entry,
records match domain / slot fill success / syntactic validity, then
prints per-domain hit rate and the top-5 unmatched queries by score.

Run: python -m templates.run_eval
"""
from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "test_scenarios.json"

try:
    from templates import index as bragi_index
except ImportError:
    sys.path.insert(0, str(HERE.parent))
    from templates import index as bragi_index


def syntactic_ok(code: str, language: str) -> bool:
    if not code:
        return False
    if language == "python":
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False
    # for non-python, fall back to a non-empty + balanced-braces heuristic
    return code.count("{") == code.count("}")


def run_one(query: str):
    """Query the index and return a dict of outcomes."""
    result = bragi_index.retrieve(query, top_k=1)
    if not result:
        return {"matched": False, "domain": None, "score": 0.0, "slot_ok": False, "syntax_ok": False}
    top = result[0]
    slot_ok = False
    syntax_ok = False
    try:
        filled = bragi_index.fill_slots(top, query)
        slot_ok = filled is not None and bool(filled.get("code"))
        if slot_ok:
            syntax_ok = syntactic_ok(filled["code"], filled.get("language", ""))
    except Exception:
        pass
    return {
        "matched": True,
        "domain": top.get("domain"),
        "template_id": top.get("id"),
        "score": float(top.get("score", 0.0)),
        "slot_ok": slot_ok,
        "syntax_ok": syntax_ok,
    }


def main():
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    per_domain_total = defaultdict(int)
    per_domain_hit = defaultdict(int)
    per_domain_slot = defaultdict(int)
    per_domain_syntax = defaultdict(int)
    unmatched = []

    print(f"running {len(scenarios)} scenarios\n")

    for i, s in enumerate(scenarios):
        # alternate zh / en so both surfaces get exercised
        query = s["query_zh"] if i % 2 == 0 else s["query_en"]
        expected = s["expected_domain"]
        out = run_one(query)
        per_domain_total[expected] += 1
        domain_match = out["matched"] and out["domain"] == expected
        if domain_match:
            per_domain_hit[expected] += 1
        if out["slot_ok"]:
            per_domain_slot[expected] += 1
        if out["syntax_ok"]:
            per_domain_syntax[expected] += 1
        if not domain_match:
            unmatched.append({"query": query, "expected": expected, "got": out.get("domain"), "score": out["score"]})
        mark = "OK" if domain_match else "--"
        print(f"[{mark}] {expected:<18} got={out.get('domain')!s:<18} slot={out['slot_ok']!s:<5} syn={out['syntax_ok']!s:<5} :: {query}")

    print("\n=== per-domain hit rate ===")
    for dom, total in sorted(per_domain_total.items()):
        hit = per_domain_hit[dom]
        slot = per_domain_slot[dom]
        syn = per_domain_syntax[dom]
        print(f"  {dom:<20} match {hit}/{total}  slot {slot}/{total}  syntax {syn}/{total}")

    overall_total = sum(per_domain_total.values())
    overall_hit = sum(per_domain_hit.values())
    print(f"\noverall: {overall_hit}/{overall_total} = {overall_hit / overall_total:.1%}" if overall_total else "no scenarios")

    print("\n=== top 5 unmatched (by score, lowest first) ===")
    unmatched.sort(key=lambda x: x["score"])
    for u in unmatched[:5]:
        print(f"  score={u['score']:.3f} expected={u['expected']} got={u['got']} :: {u['query']}")


if __name__ == "__main__":
    main()
