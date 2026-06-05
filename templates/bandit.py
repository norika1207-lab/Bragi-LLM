"""
Layer 7: Bandit-style anti-template memory.

Track which (query-feature-set, template_id) pairs have failed verifier
historically. Future retrieval can avoid them as a soft prior (lower score).

Implementation: simple persistent counter under ~/.bragi/bandit.json.
For each (query_signature, template_id) keep: {fails, attempts, ts}.
On retrieval, downweight templates whose fail-rate is >0.5 with > 3 attempts.

This is conservative: a template only gets penalised after multiple confirmed
failures. So a single noisy failure doesn't permanently kill a good template.
"""
from __future__ import annotations
import json
import pathlib
import re
import time
from datetime import datetime

BANDIT_PATH = pathlib.Path.home() / '.bragi' / 'bandit.json'
PENALTY_THRESHOLD_ATTEMPTS = 3
PENALTY_THRESHOLD_FAIL_RATE = 0.5
PENALTY_SCORE_MULTIPLIER = 0.5


def _load() -> dict:
    if not BANDIT_PATH.exists():
        return {}
    try:
        return json.loads(BANDIT_PATH.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    BANDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    BANDIT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# query signature: simplest possible — sorted keywords. Two queries with
# similar nouns hash to the same signature.

_STOP_WORDS = {'的', '了', '一個', '請', '幫我', '我要', '想', '做', '寫',
               'a', 'an', 'the', 'i', 'want', 'please', 'make', 'write',
               'create', 'with', 'for', 'of', 'in', 'to', 'and', 'or'}


def _signature(query: str) -> str:
    """Return a normalised signature for the query for clustering similar
    queries together in the bandit memory."""
    # split on non-alphanumeric, lowercase, drop short / stop words
    tokens = re.findall(r'[a-zA-Z0-9一-鿿]+', query.lower())
    keep = sorted(set(t for t in tokens if t not in _STOP_WORDS and len(t) > 1))
    return '|'.join(keep[:8])


def record_attempt(query: str, template_id: str, verify_ok: bool) -> None:
    """Log a (query, template) attempt result."""
    if not template_id:
        return
    data = _load()
    sig = _signature(query)
    key = f'{sig}__{template_id}'
    rec = data.get(key) or {'fails': 0, 'attempts': 0, 'last_ts': ''}
    rec['attempts'] += 1
    if not verify_ok:
        rec['fails'] += 1
    rec['last_ts'] = datetime.now().isoformat()
    data[key] = rec
    _save(data)


def penalty_for(query: str, template_id: str) -> float:
    """Return a multiplier (0,1] for the template's retrieval score.
    1.0 means no penalty. <1.0 means downweight."""
    data = _load()
    sig = _signature(query)
    key = f'{sig}__{template_id}'
    rec = data.get(key)
    if not rec or rec['attempts'] < PENALTY_THRESHOLD_ATTEMPTS:
        return 1.0
    rate = rec['fails'] / max(rec['attempts'], 1)
    if rate >= PENALTY_THRESHOLD_FAIL_RATE:
        return PENALTY_SCORE_MULTIPLIER
    return 1.0


def stats() -> dict:
    data = _load()
    total = len(data)
    penalised = sum(1 for r in data.values()
                    if r['attempts'] >= PENALTY_THRESHOLD_ATTEMPTS
                    and r['fails'] / max(r['attempts'], 1) >= PENALTY_THRESHOLD_FAIL_RATE)
    return {'total_pairs': total, 'penalised_pairs': penalised,
            'storage': str(BANDIT_PATH)}


if __name__ == '__main__':
    # CLI smoke
    record_attempt('test query foo bar', 'tpl-1', verify_ok=False)
    record_attempt('test query foo bar', 'tpl-1', verify_ok=False)
    record_attempt('test query foo bar', 'tpl-1', verify_ok=False)
    print('after 3 fails:', penalty_for('test query foo bar', 'tpl-1'))
    print('different query:', penalty_for('different query', 'tpl-1'))
    print(stats())
