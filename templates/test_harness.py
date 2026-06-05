"""
End-to-end test harness for the Bragi template stack.

Bypass the sentence-transformers dependency (env conflict). Use sklearn TF-IDF
as the retrieval engine. This proves whether the template library + retrieval
+ slot-filling pipeline works at all. Embeddings can be swapped back in later.

Outputs JSON report to ~/Documents/Bragi-LLM/templates/test_report.json
plus a human-readable markdown to test_report.md.
"""
from __future__ import annotations
import json
import re
import sys
import time
import urllib.request
import pathlib
from datetime import datetime
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = pathlib.Path(__file__).parent
LIBRARY = ROOT / 'library'
SCENARIOS = ROOT / 'test_scenarios.json'
REPORT_JSON = ROOT / 'test_report.json'
REPORT_MD = ROOT / 'test_report.md'
BRAGI_URL = 'http://localhost:8080/v1/chat/completions'

# ============ load templates ============

def load_templates():
    tpls = []
    for domain_dir in sorted(LIBRARY.glob('*/')):
        if domain_dir.name.startswith('_') or not domain_dir.is_dir():
            continue
        for tpl_path in sorted(domain_dir.glob('*.json')):
            try:
                t = json.loads(tpl_path.read_text())
                t['_domain'] = domain_dir.name
                t['_path'] = str(tpl_path.relative_to(ROOT))
                # build a single searchable text: intents zh + en + summary + category
                t['_search_text'] = ' '.join(
                    (t.get('intents_zh', []) or []) +
                    (t.get('intents_en', []) or []) +
                    [t.get('summary_zh', '')] +
                    [t.get('category', '').replace('/', ' ')]
                )
                tpls.append(t)
            except Exception as e:
                print(f'  WARN: failed {tpl_path.name}: {e}', file=sys.stderr)
    return tpls


# ============ retrieval ============

def build_index(templates):
    vec = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4), max_features=20000)
    texts = [t['_search_text'] for t in templates]
    mat = vec.fit_transform(texts)
    return vec, mat


def retrieve(vec, mat, templates, query, top_k=5):
    q = vec.transform([query])
    sims = cosine_similarity(q, mat)[0]
    idx = sims.argsort()[::-1][:top_k]
    return [(templates[i], float(sims[i])) for i in idx]


# ============ slot filling (call Bragi) ============

def fill_slots(template, query, timeout=60):
    if not template.get('slots'):
        return template.get('code', '')

    slots = template['slots']
    code = template.get('code', '')
    summary = template.get('summary_zh', '')

    prompt = (
        f'Template summary: {summary}\n'
        f'Template code template:\n```\n{code}\n```\n\n'
        f'User request: {query}\n\n'
        f'Return ONLY a JSON object with these keys, no other text: {", ".join(slots)}\n'
        f'Each value should be a sensible default if the user did not specify.\n'
        f'Example: {{"port": "3000", "route_path": "/"}}'
    )

    body = json.dumps({
        'model': 'bragi-llm',
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
        'temperature': 0.2,
        'max_tokens': 300,
    }).encode()

    try:
        req = urllib.request.Request(BRAGI_URL, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        reply = resp['choices'][0]['message']['content']
    except Exception as e:
        return f'[SLOT_FILL_ERROR: {e}]\n\n{code}'

    # extract JSON from reply
    m = re.search(r'\{[^{}]*\}', reply, re.DOTALL)
    if not m:
        # use defaults
        values = {s: f'<{s}>' for s in slots}
    else:
        try:
            values = json.loads(m.group())
        except Exception:
            values = {s: f'<{s}>' for s in slots}

    # substitute
    out = code
    for s in slots:
        out = out.replace('{{' + s + '}}', str(values.get(s, f'<{s}>')))
    return out, values, reply


# ============ run ============

def main():
    print('=== loading templates ===', flush=True)
    templates = load_templates()
    by_domain = {}
    for t in templates:
        by_domain.setdefault(t['_domain'], 0)
        by_domain[t['_domain']] += 1
    print(f'  total: {len(templates)} templates')
    for d, c in sorted(by_domain.items()):
        print(f'  - {d}: {c}')

    print('\n=== building TF-IDF index ===', flush=True)
    t0 = time.time()
    vec, mat = build_index(templates)
    print(f'  index built in {time.time()-t0:.1f}s, shape={mat.shape}')

    print('\n=== loading test scenarios ===', flush=True)
    scenarios = json.loads(SCENARIOS.read_text())
    if isinstance(scenarios, dict):
        scenarios = scenarios.get('scenarios') or scenarios.get('test_scenarios') or list(scenarios.values())[0]
    print(f'  {len(scenarios)} scenarios')

    print('\n=== running tests ===', flush=True)
    results = []
    for i, sc in enumerate(scenarios):
        query = sc.get('query_zh') or sc.get('query_en') or ''
        expected_domain = sc.get('expected_domain', '?')
        print(f'  [{i+1}/{len(scenarios)}] {query[:60]} -> expected {expected_domain}')
        top = retrieve(vec, mat, templates, query, top_k=3)
        best, score = top[0]
        domain_match = (best['_domain'] == expected_domain)

        # try slot filling on the top template (skip if no slots or bragi down)
        fill_result = None
        if best.get('slots'):
            fr = fill_slots(best, query, timeout=45)
            if isinstance(fr, tuple):
                final_code, slot_values, raw_reply = fr
                fill_result = {
                    'ok': True,
                    'final_code': final_code[:1000],
                    'slot_values': slot_values,
                    'raw_reply_preview': raw_reply[:200],
                }
            else:
                fill_result = {'ok': False, 'error': fr[:200]}

        results.append({
            'idx': i + 1,
            'query_zh': sc.get('query_zh'),
            'query_en': sc.get('query_en'),
            'expected_domain': expected_domain,
            'top_template_id': best['id'],
            'top_template_summary': best.get('summary_zh'),
            'top_template_domain': best['_domain'],
            'top_score': round(score, 3),
            'domain_match': domain_match,
            'top3': [{'id': t['id'], 'domain': t['_domain'], 'score': round(s, 3),
                      'summary': t.get('summary_zh', '')[:60]} for t, s in top[:3]],
            'fill_result': fill_result,
        })
        print(f'      hit: {best["_domain"]}/{best["id"]} (score={score:.3f}) match={domain_match}')

    # summary
    n_total = len(results)
    n_domain_match = sum(1 for r in results if r['domain_match'])
    n_fill_ok = sum(1 for r in results if (r.get('fill_result') or {}).get('ok'))
    n_fill_attempted = sum(1 for r in results if r.get('fill_result') is not None)

    by_domain_hits = {}
    for r in results:
        d = r['expected_domain']
        by_domain_hits.setdefault(d, {'total': 0, 'match': 0})
        by_domain_hits[d]['total'] += 1
        if r['domain_match']:
            by_domain_hits[d]['match'] += 1

    summary = {
        'ts': datetime.now().isoformat(),
        'templates_loaded': len(templates),
        'templates_by_domain': by_domain,
        'scenarios_run': n_total,
        'domain_match_rate': round(n_domain_match / max(n_total, 1) * 100, 1),
        'slot_fill_attempted': n_fill_attempted,
        'slot_fill_success_rate': round(n_fill_ok / max(n_fill_attempted, 1) * 100, 1) if n_fill_attempted else None,
        'per_domain_match': {d: {**v, 'rate': round(v['match']/max(v['total'],1)*100, 1)} for d, v in by_domain_hits.items()},
        'retrieval_engine': 'TF-IDF char_wb 2-4grams (sentence-transformers env conflict, swap back later)',
    }

    full_report = {'summary': summary, 'results': results}
    REPORT_JSON.write_text(json.dumps(full_report, ensure_ascii=False, indent=2))
    print(f'\n=== wrote {REPORT_JSON.relative_to(ROOT)} ===')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
