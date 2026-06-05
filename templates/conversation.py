"""
Conversation orchestrator: the brain that decides how to handle each user message.

For each incoming message:
  1. If `session_id` is given and the message looks like a follow-up,
     run followup.apply_followup() against the previous turn's code.
  2. Else, run retrieval (TF-IDF or sentence-transformers) + slot fill.
  3. If retrieval score is below threshold, fall through to raw Bragi.
  4. Record the turn into the session log.

This is the single entry point used by server.py and cli.py.
"""
from __future__ import annotations
import json
import re
import urllib.request
import pathlib

from templates import session as sessionmod
from templates import followup as followupmod
from templates import file_context as fcmod

BRAGI_URL = 'http://localhost:8080/v1/chat/completions'
RETRIEVAL_THRESHOLD = 0.20  # below this, fall through to raw Bragi

# Lazy-load the retrieval index
_index = None
_engine = None  # 'st' (sentence-transformers) or 'tfidf'


def _load_index():
    """Build retrieval index. Try sentence-transformers; fall back to TF-IDF."""
    global _index, _engine
    if _index is not None:
        return _index, _engine

    library_root = pathlib.Path(__file__).parent / 'library'
    templates = []
    for domain_dir in sorted(library_root.glob('*/')):
        if not domain_dir.is_dir() or domain_dir.name.startswith('_'):
            continue
        for tpl_path in sorted(domain_dir.glob('*.json')):
            try:
                t = json.loads(tpl_path.read_text(encoding='utf-8'))
                t['_domain'] = domain_dir.name
                t['_search_text'] = ' '.join(
                    (t.get('intents_zh', []) or []) +
                    (t.get('intents_en', []) or []) +
                    [t.get('summary_zh', '')] +
                    [t.get('category', '').replace('/', ' ')]
                )
                templates.append(t)
            except Exception:
                pass

    # try sentence-transformers
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
        texts = [t['_search_text'] for t in templates]
        embs = model.encode(texts, batch_size=64, show_progress_bar=False, normalize_embeddings=True)
        _index = {'engine': 'st', 'model': model, 'embs': embs, 'templates': templates, 'np': np}
        _engine = 'st'
        return _index, _engine
    except Exception:
        pass

    # fall back to TF-IDF
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    vec = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4), max_features=20000)
    mat = vec.fit_transform([t['_search_text'] for t in templates])
    _index = {'engine': 'tfidf', 'vec': vec, 'mat': mat, 'templates': templates,
              'cosine_similarity': cosine_similarity}
    _engine = 'tfidf'
    return _index, _engine


def retrieve(query: str, top_k: int = 5) -> list[tuple[dict, float]]:
    idx, engine = _load_index()
    templates = idx['templates']
    if engine == 'st':
        q = idx['model'].encode([query], normalize_embeddings=True)
        sims = (idx['embs'] @ q.T).flatten()
    else:
        q = idx['vec'].transform([query])
        sims = idx['cosine_similarity'](q, idx['mat'])[0]
    order = sims.argsort()[::-1][:top_k]
    return [(templates[i], float(sims[i])) for i in order]


def fill_slots(template: dict, query: str, context_snippet: str = '', timeout: int = 60):
    if not template.get('slots'):
        return template.get('code', ''), {}, ''

    slots = template['slots']
    code = template.get('code', '')

    prompt_parts = []
    if context_snippet:
        prompt_parts.append(context_snippet)
    prompt_parts.extend([
        f'Template purpose: {template.get("summary_zh", "")}',
        f'Template code with slot placeholders:',
        '```',
        code,
        '```',
        '',
        f'User request: {query}',
        '',
        f'Return ONLY a JSON object with these keys, no other text: {", ".join(slots)}',
        f'Each value sensible default if user did not specify.',
        f'Example: {{"port": "3000", "route_path": "/"}}',
    ])

    body = json.dumps({
        'model': 'bragi-llm',
        'messages': [{'role': 'user', 'content': '\n'.join(prompt_parts)}],
        'stream': False,
        'temperature': 0.2,
        'max_tokens': 400,
    }).encode()

    try:
        req = urllib.request.Request(BRAGI_URL, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        reply = resp['choices'][0]['message']['content']
    except Exception as e:
        return f'[SLOT_FILL_ERROR: {e}]\n\n{code}', {}, str(e)

    m = re.search(r'\{[^{}]*\}', reply, re.DOTALL)
    if not m:
        values = {s: f'<{s}>' for s in slots}
    else:
        try:
            values = json.loads(m.group())
        except Exception:
            values = {s: f'<{s}>' for s in slots}

    out = code
    for s in slots:
        out = out.replace('{{' + s + '}}', str(values.get(s, f'<{s}>')))
    return out, values, reply


def fallback_to_bragi(query: str, context_snippet: str = '', timeout: int = 90) -> str:
    """No template matched — ask Bragi directly. Honest: this is the
    capability boundary, Bragi may produce poor output here."""
    msg = (context_snippet + '\n\n' if context_snippet else '') + query
    body = json.dumps({
        'model': 'bragi-llm',
        'messages': [{'role': 'user', 'content': msg}],
        'stream': False,
        'temperature': 0.5,
        'max_tokens': 800,
    }).encode()
    try:
        req = urllib.request.Request(BRAGI_URL, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        return resp['choices'][0]['message']['content']
    except Exception as e:
        return f'[BRAGI_ERROR: {e}]'


def handle(query: str, session_id: str | None = None,
           file_paths: list[str] | None = None) -> dict:
    """Single entry point. Returns dict with mode, code, metadata."""
    # 1. file context
    context_snippet = ''
    if file_paths:
        ctx = fcmod.load_context(file_paths)
        context_snippet = fcmod.context_to_prompt_snippet(ctx)

    # 2. follow-up detection
    if session_id and followupmod.is_followup(query, session_id):
        result = followupmod.apply_followup(query, session_id)
        if result.get('ok'):
            sessionmod.record_turn(session_id,
                                    query=query,
                                    template_id=result.get('prev_template_id'),
                                    template_domain='followup',
                                    slot_values=None,
                                    final_code=result['code'],
                                    mode='followup')
            return {
                'mode': 'followup',
                'code': result['code'],
                'based_on_template': result.get('prev_template_id'),
            }
        # if followup failed, fall through to retrieval

    # 3. retrieve + slot fill
    candidates = retrieve(query, top_k=5)
    if not candidates:
        out = fallback_to_bragi(query, context_snippet)
        if session_id:
            sessionmod.record_turn(session_id,
                                    query=query, template_id=None,
                                    template_domain=None, slot_values=None,
                                    final_code=out, mode='fallback')
        return {'mode': 'fallback-no-templates', 'code': out}

    best, score = candidates[0]
    if score < RETRIEVAL_THRESHOLD:
        out = fallback_to_bragi(query, context_snippet)
        if session_id:
            sessionmod.record_turn(session_id,
                                    query=query, template_id=None,
                                    template_domain=None, slot_values=None,
                                    final_code=out, mode='fallback')
        return {'mode': 'fallback-low-score', 'code': out, 'best_score': score,
                'best_template_id': best['id']}

    final_code, slot_values, raw_reply = fill_slots(best, query, context_snippet)
    if session_id:
        sessionmod.record_turn(session_id,
                                query=query, template_id=best['id'],
                                template_domain=best.get('_domain'),
                                slot_values=slot_values,
                                final_code=final_code,
                                mode='template')
    return {
        'mode': 'template',
        'template_id': best['id'],
        'template_domain': best.get('_domain'),
        'score': round(score, 3),
        'slot_values': slot_values,
        'code': final_code,
        'engine': _engine,
    }


def info() -> dict:
    """Diagnostic info about the loaded index."""
    idx, engine = _load_index()
    return {
        'engine': engine,
        'template_count': len(idx['templates']),
        'domains': sorted(set(t['_domain'] for t in idx['templates'])),
    }
