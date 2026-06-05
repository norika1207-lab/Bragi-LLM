"""
Test-time-compute orchestrator on Bragi 1.5B Q3.

The diamond pressure layer. Trades local compute for correctness:

    user query
        │
   [Planner pass]    Bragi splits the request into 1-3 sub-tasks.
        │
   [Generator x N]   For each sub-task:
        │              - retrieve top-K templates as scaffold prior
        │              - generate N=3 candidate slot-fills (vary temperature)
        │              - deterministic verifier (verifier.py) filters
        │              - if 0 pass, free-gen 1 candidate (no template), re-verify
        │              - pick highest score among verified
        │
   [Composer pass]   Bragi merges sub-task outputs into a single coherent output.
        │
   [Reviewer pass]   Bragi self-critiques: "Does this answer the user? List issues."
        │
   [Reviser]         If issues, Bragi revises; re-verify; loop up to R=2 times.
        │
   final code + reviewer note

Cost per request: 5-15 Bragi calls, total wall-clock ~30-120s on a slow CPU.
Benefit: closer to verified-correct output than single-pass.

This is what nobody has done at sub-1GB scale.
"""
from __future__ import annotations
import json
import re
import urllib.request

from templates import verifier as vmod

BRAGI_URL = 'http://localhost:8080/v1/chat/completions'

# ============== Bragi raw call ==============

def _bragi(prompt: str, *, temperature: float = 0.3, max_tokens: int = 800,
           timeout: int = 90) -> str:
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


# ============== Planner ==============

def plan(query: str) -> list[str]:
    """Decompose user query into 1-3 sub-tasks. Each sub-task should be
    a single code generation goal."""
    prompt = (
        f'User request: "{query}"\n\n'
        f'Decompose this into 1 to 3 specific code generation sub-tasks. '
        f'Each sub-task should be one concrete deliverable, e.g. "a React '
        f'login form component", or "an Express endpoint for X". '
        f'Return ONLY a JSON array of strings, no other text. '
        f'If the request is already atomic, return one element.\n\n'
        f'Examples:\n'
        f'  "做個登入頁" -> ["a React login form component"]\n'
        f'  "做個 todo app 用 React 加 backend Express" -> '
        f'["a React todo UI component", "an Express CRUD endpoint for todos"]'
    )
    reply = _bragi(prompt, temperature=0.2, max_tokens=300)
    reply = _strip_fences(reply)
    # extract first JSON array
    m = re.search(r'\[[^\[\]]*\]', reply, re.DOTALL)
    if not m:
        return [query]  # fallback: single sub-task = original query
    try:
        arr = json.loads(m.group())
        if isinstance(arr, list) and arr:
            return [str(x) for x in arr][:3]
    except Exception:
        pass
    return [query]


# ============== Generator ==============

def generate_candidates(sub_task: str, retriever, n: int = 3) -> list[dict]:
    """For a sub-task, retrieve top templates and generate N slot-filled candidates.
    Vary temperature to get diversity."""
    candidates = []

    # 1. retrieve top templates
    top = retriever(sub_task, top_k=5) if retriever else []
    for rank, (tpl, score) in enumerate(top[:3]):
        # try slot fill at varying temperatures
        for ti, temp in enumerate([0.1, 0.4][:1 if rank < 2 else 0]):
            code = _slot_fill_one(tpl, sub_task, temperature=temp)
            if code:
                candidates.append({
                    'source': 'template',
                    'template_id': tpl.get('id'),
                    'template_domain': tpl.get('_domain'),
                    'language': tpl.get('language', ''),
                    'retrieval_score': round(score, 3),
                    'temperature': temp,
                    'code': code,
                })
        if len(candidates) >= n:
            break

    # 2. if nothing or all candidates fail verify, add a free-gen candidate
    return candidates[:n]


def _slot_fill_one(template: dict, query: str, temperature: float = 0.2) -> str:
    """Single slot-fill attempt. Returns final code."""
    code = template.get('code', '')
    slots = template.get('slots') or []
    if not slots:
        return code

    prompt = (
        f'Template summary: {template.get("summary_zh", "")}\n'
        f'Template code:\n```\n{code}\n```\n\n'
        f'User wants: {query}\n\n'
        f'Return ONLY a JSON object with these keys, sensible default values: '
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


def free_gen(sub_task: str, lang_hint: str = '') -> dict:
    """Generate code without template. Last resort."""
    prompt = (
        f'Generate {lang_hint or "code"} for: {sub_task}\n\n'
        f'Return ONLY the code, no prose, no markdown fence. Just code that runs.'
    )
    code = _strip_fences(_bragi(prompt, temperature=0.3, max_tokens=800))
    return {
        'source': 'free-gen',
        'template_id': None,
        'template_domain': None,
        'language': lang_hint,
        'retrieval_score': 0,
        'temperature': 0.3,
        'code': code,
    }


# ============== Verifier filter + select ==============

def verify_and_select(candidates: list[dict]) -> tuple[dict | None, list[dict]]:
    """Run verifier on all candidates, return (best_passing, all_with_verify_results).
    best_passing is the highest-score candidate whose verifier said ok=True.
    """
    scored = vmod.score_candidates(candidates)
    passing = [c for c in scored if c['verify']['ok']]
    return (passing[0] if passing else None), scored


# ============== Composer ==============

def compose(sub_task_outputs: list[dict], original_query: str) -> str:
    """Merge multiple sub-task code outputs into one coherent response.
    If only one sub-task, just return its code."""
    if not sub_task_outputs:
        return ''
    if len(sub_task_outputs) == 1:
        return sub_task_outputs[0].get('code', '')

    # multiple outputs: build a brief composite
    parts = ['// Composite output from multiple sub-tasks\n']
    for i, out in enumerate(sub_task_outputs, 1):
        st = out.get('sub_task', f'task {i}')
        code = out.get('code', '')
        lang = out.get('language', '')
        parts.append(f'\n// ===== sub-task {i}: {st} =====\n')
        parts.append(f'// language: {lang}\n')
        parts.append(code)
        parts.append('\n')
    return ''.join(parts)


# ============== Reviewer ==============

def review(code: str, query: str) -> dict:
    """Bragi self-critiques. Returns {ok, issues}."""
    prompt = (
        f'User wanted: {query}\n\n'
        f'Generated code:\n```\n{code[:2000]}\n```\n\n'
        f'Does this code actually answer the user request? '
        f'Return ONLY JSON: {{"ok": true|false, "issues": ["issue1", "issue2"]}}\n'
        f'If ok=true, issues should be empty array.\n'
        f'Issues to flag: missing feature, wrong language, broken syntax visible to you, irrelevant.'
    )
    reply = _bragi(prompt, temperature=0.1, max_tokens=300)
    reply = _strip_fences(reply)
    m = re.search(r'\{.*?\}', reply, re.DOTALL)
    if not m:
        return {'ok': True, 'issues': []}
    try:
        r = json.loads(m.group())
        return {
            'ok': bool(r.get('ok', True)),
            'issues': list(r.get('issues') or [])[:5],
        }
    except Exception:
        return {'ok': True, 'issues': []}


# ============== Reviser ==============

def revise(code: str, query: str, issues: list[str]) -> str:
    """Bragi tries to fix the listed issues."""
    issues_str = '; '.join(issues[:3])
    prompt = (
        f'User wanted: {query}\n\n'
        f'Current code (has issues: {issues_str}):\n```\n{code[:2000]}\n```\n\n'
        f'Return ONLY the FIXED full code. No prose. Same language as before.'
    )
    return _strip_fences(_bragi(prompt, temperature=0.2, max_tokens=1200))


# ============== Main orchestrator ==============

def run(query: str, retriever, *, max_revise_rounds: int = 2,
        max_candidates_per_subtask: int = 3) -> dict:
    """Full multi-pass orchestration. Returns dict with code, trace, stats.

    retriever: callable(query, top_k) -> list of (template, score).
    """
    trace = []

    # 1. PLAN
    sub_tasks = plan(query)
    trace.append({'stage': 'plan', 'sub_tasks': sub_tasks})

    # 2. GENERATE + VERIFY each sub-task
    sub_outputs = []
    for st in sub_tasks:
        cands = generate_candidates(st, retriever, n=max_candidates_per_subtask)
        best, all_scored = verify_and_select(cands)
        if best is None:
            # free-gen fallback
            free = free_gen(st)
            best, _ = verify_and_select([free])
            if best is None:
                best = free  # accept ungated
        best['sub_task'] = st
        sub_outputs.append(best)
        trace.append({
            'stage': 'sub-task',
            'sub_task': st,
            'candidate_count': len(all_scored) if cands else 1,
            'verified_pass': len([c for c in all_scored if c.get('verify', {}).get('ok')]) if cands else None,
            'chosen_source': best.get('source'),
            'chosen_template_id': best.get('template_id'),
        })

    # 3. COMPOSE
    composed = compose(sub_outputs, query)
    trace.append({'stage': 'compose', 'output_len': len(composed)})

    # 4. REVIEW + REVISE loop
    current_code = composed
    for round_i in range(max_revise_rounds):
        rev = review(current_code, query)
        trace.append({'stage': f'review_r{round_i+1}', 'ok': rev['ok'], 'issues': rev['issues']})
        if rev['ok']:
            break
        if not rev['issues']:
            break
        revised = revise(current_code, query, rev['issues'])
        # re-verify
        v = vmod.verify(revised)
        trace.append({'stage': f'revise_r{round_i+1}', 'verify_ok': v['ok']})
        if v['ok']:
            current_code = revised
        # if revise broke things, keep prior
    final_verify = vmod.verify(current_code)
    return {
        'mode': 'multi-pass',
        'code': current_code,
        'verified': final_verify['ok'],
        'verifier_level': final_verify['level'],
        'verifier_lang': final_verify.get('language'),
        'verifier_errors': final_verify.get('errors', []),
        'sub_task_count': len(sub_tasks),
        'pass_count': len(trace),
        'trace': trace,
    }
