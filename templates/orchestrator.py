"""
Test-time-compute orchestrator v2: best-of-N with deterministic verifier.

The diamond pressure layer (v1 burned). v1 tried planner/composer/reviewer/
reviser cycle and got WORSE than single-pass (-10 pp verified, 7.3x slower)
because the extra LLM passes added noise on top of already-good templates.

v2 keeps only the part that actually works: generate N candidates, filter
through deterministic syntax verifier, pick best.

    user query
        │
   [Generator x N]    retrieve top-K templates, slot-fill each at different
        │             temperatures. Plus 1 free-gen candidate if templates miss.
        │
   [Verifier filter]  ast.parse / shallow balance / json.loads, etc. Deterministic.
        │
        ├─ ≥1 verified  → pick highest retrieval score among verified, DONE
        │
        └─ 0 verified   → free-gen one more attempt with different prompt,
                          re-verify, return best-effort (flagged unverified)

Cost: 2-6 Bragi calls per request (depending on path), wall ~5-30s.
Benefit: filter out broken code BEFORE it reaches user.

That's it. No planner, no composer, no reviewer. Those were noise.
"""
from __future__ import annotations
import json
import re
import urllib.request

from templates import verifier as vmod

BRAGI_URL = 'http://localhost:8080/v1/chat/completions'

DEFAULT_TEMPS = [0.1, 0.4, 0.7]


def _bragi(prompt: str, *, temperature: float = 0.3, max_tokens: int = 800,
           timeout: int = 60) -> str:
    body = json.dumps({
        'model': 'bragi-llm',
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }).encode()
    try:
        req = urllib.request.Request(BRAGI_URL, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        return resp['choices'][0]['message']['content']
    except Exception as e:
        return f'[BRAGI_ERROR: {e}]'


def _strip_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r'^```[a-zA-Z]*\n', '', t)
    t = re.sub(r'\n```\s*$', '', t)
    return t


def _slot_fill(template: dict, query: str, temperature: float = 0.2) -> str:
    code = template.get('code', '')
    slots = template.get('slots') or []
    if not slots:
        return code

    prompt = (
        f'Template purpose: {template.get("summary_zh", "")}\n'
        f'Template code:\n```\n{code}\n```\n\n'
        f'User wants: {query}\n\n'
        f'Return ONLY a JSON object with these keys, sensible defaults: '
        f'{", ".join(slots)}\n'
        f'Example: {{"port": "3000", "route_path": "/"}}'
    )
    reply = _bragi(prompt, temperature=temperature, max_tokens=400)
    m = re.search(r'\{[^{}]*\}', reply, re.DOTALL)
    values = {}
    if m:
        try:
            values = json.loads(m.group())
        except Exception:
            pass
    out = code
    for s in slots:
        out = out.replace('{{' + s + '}}', str(values.get(s, f'<{s}>')))
    return out


def _free_gen(query: str, lang_hint: str = '') -> str:
    prompt = (
        f'Generate {lang_hint or "code"} for: {query}\n\n'
        f'Return ONLY the code. No prose, no markdown fence. Just runnable code.'
    )
    return _strip_fences(_bragi(prompt, temperature=0.3, max_tokens=800))


def run(query: str, retriever, *, n_template_candidates: int = 3,
        verify_first_then_skip: bool = True) -> dict:
    """Best-of-N with verifier filter.

    Strategy:
      1. Retrieve top-K templates (K = n_template_candidates).
      2. Slot-fill the top one first (temperature 0.1). Verify.
         If verified AND verify_first_then_skip=True, return immediately
         (fastest path — matches single-pass speed for clean cases).
      3. Otherwise, slot-fill the rest in parallel, verify each.
      4. Pick highest-retrieval-score among verified.
      5. If 0 verified, free-gen one more candidate, verify, return.
    """
    trace = []
    top = retriever(query, top_k=n_template_candidates) if retriever else []
    trace.append({'stage': 'retrieve', 'candidates': len(top)})

    candidates = []

    # 1. quick path: slot-fill #1 first, verify, return if pass
    if top:
        tpl, score = top[0]
        code = _slot_fill(tpl, query, temperature=0.1)
        v = vmod.verify(code, tpl.get('language', ''))
        candidates.append({
            'source': 'template',
            'rank': 0,
            'template_id': tpl.get('id'),
            'template_domain': tpl.get('_domain'),
            'language': tpl.get('language', ''),
            'retrieval_score': round(score, 3),
            'temperature': 0.1,
            'code': code,
            'verify': v,
        })
        if v['ok'] and verify_first_then_skip:
            trace.append({'stage': 'quick-path-pass', 'template_id': tpl.get('id')})
            return _result(candidates[0], trace, candidates)

    # 2. slow path: slot-fill remaining candidates
    for rank, (tpl, score) in enumerate(top[1:], 1):
        code = _slot_fill(tpl, query, temperature=DEFAULT_TEMPS[rank % len(DEFAULT_TEMPS)])
        v = vmod.verify(code, tpl.get('language', ''))
        candidates.append({
            'source': 'template',
            'rank': rank,
            'template_id': tpl.get('id'),
            'template_domain': tpl.get('_domain'),
            'language': tpl.get('language', ''),
            'retrieval_score': round(score, 3),
            'temperature': DEFAULT_TEMPS[rank % len(DEFAULT_TEMPS)],
            'code': code,
            'verify': v,
        })
    trace.append({'stage': 'all-candidates', 'count': len(candidates),
                  'verified_count': sum(1 for c in candidates if c['verify']['ok'])})

    # 3. pick best verified by retrieval score
    verified = [c for c in candidates if c['verify']['ok']]
    if verified:
        best = max(verified, key=lambda c: c.get('retrieval_score', 0))
        trace.append({'stage': 'select-verified', 'chosen_template': best.get('template_id')})
        return _result(best, trace, candidates)

    # 4. nothing verified — free-gen one more
    lang_hint = top[0][0].get('language', '') if top else ''
    fg_code = _free_gen(query, lang_hint)
    fg_v = vmod.verify(fg_code, lang_hint)
    fg = {
        'source': 'free-gen',
        'rank': -1,
        'template_id': None,
        'template_domain': None,
        'language': lang_hint,
        'retrieval_score': 0,
        'temperature': 0.3,
        'code': fg_code,
        'verify': fg_v,
    }
    candidates.append(fg)
    trace.append({'stage': 'free-gen-fallback', 'verify_ok': fg_v['ok']})

    if fg_v['ok']:
        return _result(fg, trace, candidates)

    # 5. nothing passes — return highest-retrieval-score unverified candidate,
    #    flagged. Better than nothing; user sees broken code with a warning.
    candidates_with_score = [c for c in candidates if c.get('retrieval_score') is not None]
    fallback = max(candidates_with_score, key=lambda c: c.get('retrieval_score', 0)) if candidates_with_score else fg
    trace.append({'stage': 'unverified-fallback', 'chosen': fallback.get('template_id') or 'free-gen'})
    return _result(fallback, trace, candidates, unverified=True)


def _result(chosen: dict, trace: list, all_candidates: list, unverified: bool = False) -> dict:
    return {
        'mode': 'best-of-n',
        'code': chosen.get('code', ''),
        'verified': chosen.get('verify', {}).get('ok', False) and not unverified,
        'verifier_level': chosen.get('verify', {}).get('level'),
        'verifier_lang': chosen.get('verify', {}).get('language'),
        'verifier_errors': chosen.get('verify', {}).get('errors', []),
        'chosen_source': chosen.get('source'),
        'chosen_template_id': chosen.get('template_id'),
        'chosen_template_domain': chosen.get('template_domain'),
        'chosen_temperature': chosen.get('temperature'),
        'candidate_count': len(all_candidates),
        'verified_count': sum(1 for c in all_candidates if c.get('verify', {}).get('ok')),
        'trace': trace,
    }
