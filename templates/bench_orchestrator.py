"""
Benchmark single-pass vs multi-pass on test scenarios.

For each scenario:
  - Run single-pass (conversation.handle, retrieval + slot fill)
  - Run multi-pass (orchestrator.run, plan + gen + verify + review + revise)
  - Record: time, verifier pass/fail, code length, sub-task count

Output: bench_orchestrator.json + summary stats.

Note: multi-pass is SLOW (30-120s per query, vs 5-15s single-pass).
We expect single-pass = faster, multi-pass = more correct.
"""
from __future__ import annotations
import json
import pathlib
import time
from datetime import datetime

from templates import conversation as conv
from templates import orchestrator as orch
from templates import verifier as vmod

ROOT = pathlib.Path(__file__).parent
SCENARIOS_PATH = ROOT / 'test_scenarios.json'
OUT_PATH = ROOT / 'bench_orchestrator.json'

# Use a SUBSET of scenarios so it doesn't take all night.
# multi-pass at ~60s/query x 20 queries = 20 min. ok.
SUBSET_SIZE = 20


def main():
    scenarios = json.loads(SCENARIOS_PATH.read_text())
    if isinstance(scenarios, dict):
        scenarios = scenarios.get('scenarios') or list(scenarios.values())[0]
    scenarios = scenarios[:SUBSET_SIZE]

    # warm up index
    print(f'warming retrieval index...', flush=True)
    info = conv.info()
    print(f'engine={info["engine"]} templates={info["template_count"]}', flush=True)

    # retriever for orchestrator
    def retriever(q, top_k=5):
        idx, engine = conv._load_index()
        return conv.retrieve(q, top_k=top_k)

    results = []
    for i, sc in enumerate(scenarios):
        q = sc.get('query_zh') or sc.get('query_en') or ''
        expected = sc.get('expected_domain', '?')
        print(f'\n[{i+1}/{len(scenarios)}] {q[:60]}', flush=True)

        # single-pass
        sid = f'bench_single_{i}'
        t0 = time.time()
        try:
            sp = conv.handle(q, session_id=sid)
        except Exception as e:
            sp = {'error': str(e), 'code': ''}
        sp_time = time.time() - t0
        sp_verify = vmod.verify(sp.get('code', ''), sp.get('template_id', '').split('-')[0] if sp.get('template_id') else '')
        print(f'  single-pass: {sp_time:.0f}s, verify={sp_verify["ok"]} ({sp_verify["level"]})', flush=True)

        # multi-pass
        t0 = time.time()
        try:
            mp = orch.run(q, retriever, n_template_candidates=3)
        except Exception as e:
            mp = {'error': str(e), 'code': '', 'verified': False, 'trace': []}
        mp_time = time.time() - t0
        print(f'  multi-pass:  {mp_time:.0f}s, verify={mp.get("verified")}, cands={mp.get("candidate_count", "?")}/{mp.get("verified_count", "?")}', flush=True)

        results.append({
            'idx': i + 1,
            'query': q,
            'expected_domain': expected,
            'single_pass': {
                'time_s': round(sp_time, 1),
                'verify_ok': sp_verify['ok'],
                'verify_level': sp_verify['level'],
                'verify_errors': sp_verify.get('errors', []),
                'code_len': len(sp.get('code', '')),
                'template_id': sp.get('template_id'),
                'template_domain': sp.get('template_domain'),
                'mode': sp.get('mode'),
            },
            'multi_pass': {
                'time_s': round(mp_time, 1),
                'verify_ok': mp.get('verified'),
                'verifier_level': mp.get('verifier_level'),
                'verifier_errors': mp.get('verifier_errors', []),
                'code_len': len(mp.get('code', '')),
                'candidate_count': mp.get('candidate_count'),
                'verified_count': mp.get('verified_count'),
                'chosen_source': mp.get('chosen_source'),
                'chosen_template_id': mp.get('chosen_template_id'),
                'trace_summary': [t.get('stage') for t in mp.get('trace', [])],
            },
            'code_preview_single': (sp.get('code', '') or '')[:300],
            'code_preview_multi': (mp.get('code', '') or '')[:300],
        })

    # summary
    n = len(results)
    sp_verified = sum(1 for r in results if r['single_pass']['verify_ok'])
    mp_verified = sum(1 for r in results if r['multi_pass']['verify_ok'])
    sp_avg_time = sum(r['single_pass']['time_s'] for r in results) / max(n, 1)
    mp_avg_time = sum(r['multi_pass']['time_s'] for r in results) / max(n, 1)

    summary = {
        'ts': datetime.now().isoformat(),
        'subset_size': n,
        'single_pass': {
            'verified_pct': round(sp_verified / max(n, 1) * 100, 1),
            'avg_time_s': round(sp_avg_time, 1),
        },
        'multi_pass': {
            'verified_pct': round(mp_verified / max(n, 1) * 100, 1),
            'avg_time_s': round(mp_avg_time, 1),
        },
        'verified_lift_pct_pts': round((mp_verified - sp_verified) / max(n, 1) * 100, 1),
        'time_cost_multiplier': round(mp_avg_time / max(sp_avg_time, 0.001), 1),
    }

    full = {'summary': summary, 'results': results}
    OUT_PATH.write_text(json.dumps(full, ensure_ascii=False, indent=2))
    print('\n=== summary ===')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
