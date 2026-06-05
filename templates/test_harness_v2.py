"""
v2 test harness: uses conversation.handle() (the real server path).

Tests:
  1. 48 single-turn scenarios from test_scenarios.json (domain match rate)
  2. 6 multi-turn scenarios (each turn 1 + 1-2 follow-ups)

Outputs test_report_v2.json + test_report_v2.md.
"""
from __future__ import annotations
import json
import pathlib
import time
from datetime import datetime

from templates import conversation as conv
from templates import session as sm

ROOT = pathlib.Path(__file__).parent
SCENARIOS = ROOT / 'test_scenarios.json'
REPORT_JSON = ROOT / 'test_report_v2.json'

MULTI_TURN_SCENARIOS = [
    {
        'name': 'tailwind-login-then-extras',
        'turns': [
            ('做個 Tailwind login 頁面要記住帳號', 'web-frontend'),
            ('加 dark mode', 'followup'),
            ('加 try catch', 'followup'),
        ],
    },
    {
        'name': 'react-signup-then-types',
        'turns': [
            ('React 註冊表單,含 email 跟密碼確認', 'web-frontend'),
            ('改成 TypeScript', 'followup'),
        ],
    },
    {
        'name': 'express-api-then-rate',
        'turns': [
            ('Express REST API CRUD 給 user 資料', 'web-backend'),
            ('加 rate limiting', 'followup'),
        ],
    },
    {
        'name': 'esp32-then-mqtt',
        'turns': [
            ('ESP32 連 WiFi', 'firmware'),
            ('加 MQTT publish', 'followup'),
        ],
    },
    {
        'name': 'todo-vue-then-dark',
        'turns': [
            ('Vue 3 composition API todo list', 'web-frontend'),
            ('加 dark mode', 'followup'),
        ],
    },
    {
        'name': 'algorithm-quicksort-then-comments',
        'turns': [
            ('Python quicksort', 'algorithm'),
            ('加註解 + type annotation', 'followup'),
        ],
    },
]


def run_single_turn():
    scenarios = json.loads(SCENARIOS.read_text())
    if isinstance(scenarios, dict):
        scenarios = scenarios.get('scenarios') or list(scenarios.values())[0]
    print(f'=== single turn: {len(scenarios)} scenarios ===', flush=True)
    results = []
    for i, sc in enumerate(scenarios):
        query = sc.get('query_zh') or sc.get('query_en') or ''
        expected = sc.get('expected_domain', '?')
        sid = f'eval_single_{i}'
        try:
            r = conv.handle(query, session_id=sid)
        except Exception as e:
            results.append({'idx': i + 1, 'query': query, 'expected': expected,
                             'error': str(e)})
            continue
        match = r.get('template_domain') == expected
        results.append({
            'idx': i + 1,
            'query': query,
            'expected_domain': expected,
            'got_domain': r.get('template_domain'),
            'template_id': r.get('template_id'),
            'score': r.get('score'),
            'mode': r.get('mode'),
            'domain_match': match,
            'code_preview': (r.get('code', '') or '')[:200],
        })
        ok = '✓' if match else '✗'
        print(f'  {ok} [{i+1}/{len(scenarios)}] {query[:50]:<50} -> {r.get("template_domain")}', flush=True)
    return results


def run_multi_turn():
    print(f'\n=== multi-turn: {len(MULTI_TURN_SCENARIOS)} scenarios ===', flush=True)
    results = []
    for sc in MULTI_TURN_SCENARIOS:
        sid = f'eval_mt_{sc["name"]}'
        # clean previous session
        p = pathlib.Path.home() / '.bragi/sessions' / f'{sid}.jsonl'
        if p.exists():
            p.unlink()
        scenario_log = {'name': sc['name'], 'turns': []}
        for turn_idx, (query, expected) in enumerate(sc['turns']):
            try:
                r = conv.handle(query, session_id=sid)
            except Exception as e:
                scenario_log['turns'].append({'turn': turn_idx + 1, 'query': query,
                                               'error': str(e)})
                continue
            scenario_log['turns'].append({
                'turn': turn_idx + 1,
                'query': query,
                'expected': expected,
                'mode': r.get('mode'),
                'template_id': r.get('template_id'),
                'template_domain': r.get('template_domain'),
                'score': r.get('score'),
                'code_preview': (r.get('code', '') or '')[:300],
            })
            print(f'  [{sc["name"]} t{turn_idx+1}] {query[:50]:<50} -> mode={r.get("mode")} dom={r.get("template_domain")}', flush=True)
        results.append(scenario_log)
    return results


def main():
    info = conv.info()
    print(f'engine: {info["engine"]}', flush=True)
    print(f'templates: {info["template_count"]}', flush=True)
    print(f'domains: {info["domains"]}', flush=True)

    t0 = time.time()
    single_results = run_single_turn()
    t_single = time.time() - t0

    t0 = time.time()
    multi_results = run_multi_turn()
    t_multi = time.time() - t0

    n_single_match = sum(1 for r in single_results if r.get('domain_match'))
    n_total = len(single_results)
    by_domain = {}
    for r in single_results:
        d = r.get('expected_domain', '?')
        by_domain.setdefault(d, {'match': 0, 'total': 0})
        by_domain[d]['total'] += 1
        if r.get('domain_match'):
            by_domain[d]['match'] += 1

    # multi-turn: count follow-up modes
    n_mt_turns = sum(len(s['turns']) for s in multi_results)
    n_mt_followup = sum(1 for s in multi_results for t in s['turns'] if t.get('mode') == 'followup')

    summary = {
        'ts': datetime.now().isoformat(),
        'engine': info['engine'],
        'template_count': info['template_count'],
        'templates_by_domain': {d: sum(1 for f in (ROOT / 'library' / d).glob('*.json')) for d in info['domains']},
        'single_turn': {
            'n_total': n_total,
            'n_match': n_single_match,
            'rate': round(n_single_match / max(n_total, 1) * 100, 1),
            'duration_s': round(t_single, 1),
            'per_domain': {d: {**v, 'rate': round(v['match']/max(v['total'],1)*100, 1)} for d, v in by_domain.items()},
        },
        'multi_turn': {
            'n_scenarios': len(multi_results),
            'n_turns': n_mt_turns,
            'n_followup_mode': n_mt_followup,
            'duration_s': round(t_multi, 1),
        },
    }
    full = {'summary': summary, 'single_results': single_results, 'multi_results': multi_results}
    REPORT_JSON.write_text(json.dumps(full, ensure_ascii=False, indent=2))
    print('\n=== summary ===')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
