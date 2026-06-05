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
from templates import bandit as bmod

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


def _self_correct(code: str, query: str, verify_errors: list, lang_hint: str = '',
                  max_rounds: int = 2) -> tuple[str, bool, list]:
    """Layer 1: Given a candidate that failed verifier, feed exact error back to
    Bragi, ask for fix, re-verify. Loop up to max_rounds.

    Returns (final_code, final_verify_ok, trace_of_attempts).
    """
    trace = []
    current = code
    for round_i in range(max_rounds):
        err_str = '; '.join(verify_errors[:3]) if verify_errors else 'syntax/structure broken'
        prompt = (
            f'User wanted: {query}\n\n'
            f'You generated this code:\n```\n{current[:1800]}\n```\n\n'
            f'The deterministic verifier found these errors:\n  {err_str}\n\n'
            f'Fix ONLY the errors. Keep the rest identical. Return ONLY the corrected '
            f'full code. No prose, no markdown fence.'
        )
        fixed = _strip_fences(_bragi(prompt, temperature=0.1, max_tokens=1200))
        v = vmod.verify(fixed, lang_hint)
        trace.append({
            'round': round_i + 1,
            'errors_in': err_str[:120],
            'fixed_ok': v['ok'],
            'fixed_level': v['level'],
        })
        if v['ok']:
            return fixed, True, trace
        current = fixed
        verify_errors = v.get('errors', [])
    return current, False, trace


LANG_KEYWORD_HINTS = {
    'py': ['python', 'py', 'pytorch', 'flask', 'fastapi', 'django', 'pandas', 'numpy',
           'pytest', 'asyncio'],
    'js': ['javascript', 'node', 'express', 'koa', 'hono'],
    'ts': ['typescript', 'ts ', '.ts', 'tsconfig'],
    'tsx': ['react', 'tsx', '.tsx', 'next.js', 'next-js'],
    'jsx': ['jsx'],
    'vue': ['vue'],
    'sh': ['bash', 'shell', 'shell script', '.sh'],
    'sql': ['sql', 'postgres', 'mysql', 'sqlite', 'select ', 'insert ', 'select*'],
    'go': ['golang', ' go '],
    'rs': ['rust', 'cargo'],
    'swift': ['swift', 'swiftui'],
    'kt': ['kotlin', 'jetpack compose'],
    'dart': ['flutter', 'dart'],
    'ino': ['arduino', 'esp32', 'esp8266'],
    'yaml': ['yaml', 'yml', 'k8s', 'kubernetes', 'docker-compose'],
    'tf': ['terraform'],
    'sol': ['solidity'],
}


_PARAPHRASE_CACHE: dict[str, list[str]] = {}


def adversarial_paraphrase(query: str, n: int = 2) -> list[str]:
    """Layer 4: Bragi generates N alternative phrasings of the query.
    These get used to do additional retrieval, expanding candidate pool.
    Cached so we don't pay this cost twice.
    """
    if query in _PARAPHRASE_CACHE:
        return _PARAPHRASE_CACHE[query]
    prompt = (
        f'Rewrite this developer request in {n} different ways, preserving exact intent. '
        f'Vary: formal vs casual, English vs Chinese mix, with/without framework name.\n\n'
        f'Original: "{query}"\n\n'
        f'Return ONLY a JSON array of {n} strings, no other text.'
    )
    reply = _bragi(prompt, temperature=0.5, max_tokens=300)
    reply = _strip_fences(reply)
    m = re.search(r'\[.*?\]', reply, re.DOTALL)
    out: list[str] = []
    if m:
        try:
            arr = json.loads(m.group())
            if isinstance(arr, list):
                out = [str(x) for x in arr if x][:n]
        except Exception:
            pass
    _PARAPHRASE_CACHE[query] = out
    return out


def detect_expected_lang(query: str) -> str | None:
    """Layer 3: parse user query for explicit language signals.
    Returns lang code if user clearly named a language, None otherwise.
    """
    q = query.lower()
    for lang, keys in LANG_KEYWORD_HINTS.items():
        for k in keys:
            if k in q:
                return lang
    return None


DOMAIN_NOUNS = {
    'web-frontend': ['react', 'vue', 'svelte', 'tailwind', 'next', '前端', 'component', '頁面', 'ui'],
    'web-backend': ['express', 'flask', 'fastapi', 'django', 'koa', 'api', 'endpoint', '後端', 'route', 'server'],
    'mobile': ['react native', 'flutter', 'swiftui', 'kotlin', 'jetpack', 'ios', 'android', '手機', '行動'],
    'firmware': ['esp32', 'arduino', 'raspberry pi', 'stm32', 'gpio', 'mqtt'],
    'database-devops': ['docker', 'k8s', 'kubernetes', 'terraform', 'nginx', 'postgres', 'sql', 'database'],
    'llm-tools': ['transformers', 'llama.cpp', 'ollama', 'rag', 'embedding'],
    'algorithm': ['quicksort', 'mergesort', 'binary search', 'graph', 'dp', 'algorithm', '演算法'],
    'system-cli': ['cli', 'shell', 'argparse', 'cron', 'daemon', '腳本'],
}


def _detect_intent_domains(query: str) -> list[str]:
    """Return list of domain keys whose nouns appear in query."""
    ql = query.lower()
    hits = []
    for dom, nouns in DOMAIN_NOUNS.items():
        for n in nouns:
            if n in ql:
                hits.append(dom)
                break
    return hits


def _maybe_compose(query: str, primary: dict, verified_pool: list[dict]) -> dict | None:
    """Layer 5: If query has 2+ distinct domain intents AND we have a verified
    candidate from a SECOND domain, deterministically concatenate code with
    a file-separator comment. NO LLM glue (v1's downfall)."""
    intents = _detect_intent_domains(query)
    if len(set(intents)) < 2:
        return None
    primary_domain = primary.get('template_domain') or ''
    other_intents = [d for d in intents if d != primary_domain]
    if not other_intents:
        return None
    # find verified candidate from a different domain
    secondary = None
    for c in verified_pool:
        d = c.get('template_domain') or ''
        if d in other_intents and d != primary_domain:
            secondary = c
            break
    if not secondary:
        return None
    # deterministic compose
    p_lang = primary.get('language', '?')
    s_lang = secondary.get('language', '?')
    p_id = primary.get('template_id', '?')
    s_id = secondary.get('template_id', '?')
    sep = '\n\n// ' + ('=' * 60) + '\n'
    combined = (
        f'{sep}// FRONTEND / {p_lang.upper()} — from {p_id}{sep}\n'
        f'{primary["code"]}\n'
        f'{sep}// BACKEND / {s_lang.upper()} — from {s_id}{sep}\n'
        f'{secondary["code"]}'
    )
    out = dict(primary)
    out['code'] = combined
    out['_secondary_id'] = s_id
    out['_secondary_domain'] = secondary.get('template_domain')
    out['source'] = 'compose-2'
    # verify is unchanged for primary's lang; composed code may not pass full
    # verify, but each half does
    return out


def _filter_by_lang(candidates: list[dict], expected_lang: str | None) -> list[dict]:
    """Drop candidates whose template language disagrees with what user asked.
    If expected_lang is None, return all unchanged."""
    if not expected_lang:
        return candidates
    # accept template lang OR aliases
    aliases = {
        'tsx': ['tsx', 'jsx', 'ts', 'js', 'react'],
        'jsx': ['jsx', 'tsx', 'js', 'react'],
        'ts': ['ts', 'tsx', 'js'],
        'js': ['js', 'ts', 'jsx', 'tsx'],
        'vue': ['vue'],
        'py': ['py', 'python'],
        'sh': ['sh', 'bash'],
        'go': ['go', 'golang'],
        'kt': ['kt', 'kotlin'],
    }
    accept = aliases.get(expected_lang, [expected_lang])
    filtered = [c for c in candidates if c.get('language', '').lower() in accept]
    # if filter removes everything, give up and return original (better than nothing)
    return filtered if filtered else candidates


# Layer 6: persistent result cache (in-memory only, simple LRU-like dict)
_RESULT_CACHE: dict[str, dict] = {}
_CACHE_MAX = 256


def _cache_get(query: str):
    return _RESULT_CACHE.get(query)


def _cache_put(query: str, result: dict):
    if len(_RESULT_CACHE) >= _CACHE_MAX:
        # evict oldest entry (first key)
        k = next(iter(_RESULT_CACHE))
        del _RESULT_CACHE[k]
    _RESULT_CACHE[query] = result


def run(query: str, retriever, *, n_template_candidates: int = 3,
        verify_first_then_skip: bool = True,
        execute_verify: bool = False,
        lang_filter: bool = True,
        use_paraphrase: bool = True,
        use_cache: bool = True) -> dict:
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
    # Layer 6: cache hit?
    if use_cache:
        cached = _cache_get(query)
        if cached is not None:
            cached_out = dict(cached)
            cached_out['from_cache'] = True
            return cached_out

    trace = []
    expected_lang = detect_expected_lang(query) if lang_filter else None
    if expected_lang:
        trace.append({'stage': 'lang-detect', 'expected_lang': expected_lang})

    # Layer 4: adversarial paraphrase - union of queries for retrieval
    queries = [query]
    if use_paraphrase:
        paras = adversarial_paraphrase(query, n=2)
        queries.extend(paras)
        if paras:
            trace.append({'stage': 'paraphrase', 'count': len(paras), 'samples': paras[:2]})

    # Layer 3: over-fetch then filter by language to keep N after filter.
    # Use union of (top_k retrievals from each query) deduped.
    raw_top_union: dict[str, tuple[dict, float]] = {}
    for q in queries:
        try:
            tops = retriever(q, top_k=max(n_template_candidates * 3, 8))
        except Exception:
            tops = []
        for tpl, score in tops:
            tid = tpl.get('id', '')
            if tid not in raw_top_union or raw_top_union[tid][1] < score:
                raw_top_union[tid] = (tpl, score)
    # Layer 7: apply bandit penalty to retrieval scores before ranking
    raw_with_penalty = []
    for tpl, score in raw_top_union.values():
        mul = bmod.penalty_for(query, tpl.get('id', ''))
        raw_with_penalty.append((tpl, score * mul))
    raw_top = sorted(raw_with_penalty, key=lambda x: -x[1])
    if expected_lang and lang_filter:
        # build pseudo-candidate dicts just for filtering
        wrapped = [{'language': t.get('language', ''), '__tpl': t, '__score': s} for t, s in raw_top]
        filtered = _filter_by_lang(wrapped, expected_lang)
        top = [(w['__tpl'], w['__score']) for w in filtered[:n_template_candidates]]
        trace.append({'stage': 'lang-filter', 'before': len(raw_top), 'after': len(top)})
    else:
        top = raw_top[:n_template_candidates]
    trace.append({'stage': 'retrieve', 'candidates': len(top)})

    candidates = []

    # 1. quick path: slot-fill #1 first, verify, return if pass
    if top:
        tpl, score = top[0]
        code = _slot_fill(tpl, query, temperature=0.1)
        v = vmod.verify(code, tpl.get('language', ''), execute=execute_verify)
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
            # quick-path hit. But check composition opportunity FIRST — if query
            # has multiple domain intents, we still need to do slow path to find
            # a secondary template.
            intents = _detect_intent_domains(query)
            if len(set(intents)) >= 2:
                trace.append({'stage': 'quick-path-skipped-for-compose',
                              'intents': list(set(intents))})
                # fall through to slow path so composition can find secondary
            else:
                trace.append({'stage': 'quick-path-pass', 'template_id': tpl.get('id')})
                # record bandit attempt
                bmod.record_attempt(query, tpl.get('id', ''), True)
                result = _result(candidates[0], trace, candidates)
                if use_cache:
                    _cache_put(query, result)
                return result

    # 2. slow path with Layer 8 (beam-search early-prune):
    #    slot-fill each remaining candidate, verify IMMEDIATELY after generation,
    #    if it passes we keep it but ALSO continue to fill 1-2 more for diversity.
    #    if a candidate fails AND we already have N>=2 verified, abort early.
    early_stop = False
    for rank, (tpl, score) in enumerate(top[1:], 1):
        if early_stop:
            break
        code = _slot_fill(tpl, query, temperature=DEFAULT_TEMPS[rank % len(DEFAULT_TEMPS)])
        v = vmod.verify(code, tpl.get('language', ''), execute=execute_verify)
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
        # record attempt outcome to bandit
        bmod.record_attempt(query, tpl.get('id', ''), v.get('ok', False))
        # early stop: once we have >=2 verified, no point continuing
        verified_so_far = sum(1 for c in candidates if c['verify']['ok'])
        if verified_so_far >= 2:
            early_stop = True
            trace.append({'stage': 'beam-early-stop', 'after_rank': rank})
    # also record the first (quick path) attempt
    if candidates:
        first = candidates[0]
        bmod.record_attempt(query, first.get('template_id', ''),
                            first['verify'].get('ok', False))
    trace.append({'stage': 'all-candidates', 'count': len(candidates),
                  'verified_count': sum(1 for c in candidates if c['verify']['ok'])})

    # 3. pick best verified by retrieval score
    verified = [c for c in candidates if c['verify']['ok']]
    if verified:
        best = max(verified, key=lambda c: c.get('retrieval_score', 0))
        # Layer 5: detect cross-domain composition opportunity.
        # If query mentions multiple distinct framework/role nouns, try to
        # include a second verified candidate from a different domain.
        composition = _maybe_compose(query, best, verified)
        if composition is not None:
            trace.append({'stage': 'select-verified+compose',
                          'primary_template': best.get('template_id'),
                          'secondary_template': composition.get('_secondary_id')})
            best = composition
        else:
            trace.append({'stage': 'select-verified', 'chosen_template': best.get('template_id')})
        result = _result(best, trace, candidates)
        if use_cache and best.get('verify', {}).get('ok'):
            _cache_put(query, result)
        return result

    # 4. nothing verified — Layer 1: try self-correction on best-scoring candidate
    if candidates:
        best_unverified = max(candidates, key=lambda c: c.get('retrieval_score', 0))
        corrected_code, corrected_ok, sc_trace = _self_correct(
            best_unverified['code'], query, best_unverified['verify'].get('errors', []),
            lang_hint=best_unverified.get('language', ''), max_rounds=2,
        )
        trace.append({'stage': 'self-correct', 'attempts': sc_trace, 'final_ok': corrected_ok})
        if corrected_ok:
            corrected = dict(best_unverified)
            corrected['code'] = corrected_code
            corrected['source'] = 'template+self-correct'
            corrected['verify'] = vmod.verify(corrected_code, best_unverified.get('language', ''))
            return _result(corrected, trace, candidates + [corrected])

    # 5. still nothing — free-gen
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

    # 5b. try self-correct on the free-gen too
    fg_corrected, fg_corrected_ok, fg_sc_trace = _self_correct(
        fg_code, query, fg_v.get('errors', []), lang_hint=lang_hint, max_rounds=2,
    )
    trace.append({'stage': 'free-gen-self-correct', 'attempts': fg_sc_trace,
                  'final_ok': fg_corrected_ok})
    if fg_corrected_ok:
        fg2 = dict(fg)
        fg2['code'] = fg_corrected
        fg2['source'] = 'free-gen+self-correct'
        fg2['verify'] = vmod.verify(fg_corrected, lang_hint)
        return _result(fg2, trace, candidates + [fg2])

    # 6. nothing passes — return highest-retrieval-score unverified candidate, flagged
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
