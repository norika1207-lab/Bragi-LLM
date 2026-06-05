"""CLI：python -m templates.cli \"做個登入頁面要記住帳號\"

直接把生成的 code 印到 stdout。診斷資訊 (template id, 分數, 候選清單) 印到 stderr。
"""
from __future__ import annotations

import argparse
import sys

from templates.index import generate


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="bragi",
        description="樣板檢索 + LLM 槽位填充，輸出 code",
    )
    parser.add_argument("query", help="自然語言需求，中英皆可")
    parser.add_argument("--top-k", type=int, default=5, help="檢索候選數，預設 5")
    parser.add_argument("--min-score", type=float, default=0.25, help="分數門檻")
    parser.add_argument("--verbose", action="store_true", help="印候選清單到 stderr")
    args = parser.parse_args()

    result = generate(args.query, top_k=args.top_k, min_score=args.min_score)

    if result.get("error"):
        print(f"[bragi] {result['error']}", file=sys.stderr)
        if args.verbose and result.get("candidates"):
            print("[bragi] 候選：", file=sys.stderr)
            for tid, score in result["candidates"]:
                print(f"  {tid}  {score:.3f}", file=sys.stderr)
        return 1

    if args.verbose:
        print(f"[bragi] template = {result['template_id']}  score = {result['score']:.3f}", file=sys.stderr)
        print(f"[bragi] slots = {result['slots']}", file=sys.stderr)
        print("[bragi] 候選：", file=sys.stderr)
        for tid, score in result["candidates"]:
            print(f"  {tid}  {score:.3f}", file=sys.stderr)

    print(result["code"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
