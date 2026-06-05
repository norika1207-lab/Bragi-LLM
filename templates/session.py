"""
Session state for multi-turn Bragi conversations.

A session = a sequence of turns. Each turn records:
  - user query
  - chosen template id
  - slot values Bragi filled
  - final generated code

Stored as JSONL under ~/.bragi/sessions/<session_id>.jsonl
so it survives restarts.

Used by:
  - followup.py to detect "改一下" / "再加" follow-ups
  - server.py to thread session_id through OpenAI-style requests
"""
from __future__ import annotations
import json
import os
import pathlib
import time
import uuid
from datetime import datetime

SESSION_ROOT = pathlib.Path(os.environ.get('BRAGI_SESSION_DIR',
                                            str(pathlib.Path.home() / '.bragi/sessions')))


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def _path(sid: str) -> pathlib.Path:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    return SESSION_ROOT / f'{sid}.jsonl'


def record_turn(sid: str, *, query: str, template_id: str | None,
                template_domain: str | None, slot_values: dict | None,
                final_code: str | None, mode: str = 'template',
                extra: dict | None = None) -> None:
    """Append one turn to a session log."""
    rec = {
        'ts': datetime.now().isoformat(),
        'turn': _next_turn_no(sid),
        'mode': mode,
        'query': query,
        'template_id': template_id,
        'template_domain': template_domain,
        'slot_values': slot_values,
        'final_code': final_code,
    }
    if extra:
        rec.update(extra)
    with _path(sid).open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def history(sid: str, limit: int = 10) -> list[dict]:
    """Read last N turns of a session."""
    p = _path(sid)
    if not p.exists():
        return []
    lines = p.read_text(encoding='utf-8').splitlines()
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def latest_turn(sid: str) -> dict | None:
    h = history(sid, limit=1)
    return h[0] if h else None


def _next_turn_no(sid: str) -> int:
    p = _path(sid)
    if not p.exists():
        return 1
    return sum(1 for line in p.read_text(encoding='utf-8').splitlines() if line.strip()) + 1


def list_sessions() -> list[str]:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    return [p.stem for p in SESSION_ROOT.glob('*.jsonl')]


def gc(max_age_days: int = 30) -> int:
    """Delete sessions older than N days. Returns count deleted."""
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - (max_age_days * 86400)
    n = 0
    for p in SESSION_ROOT.glob('*.jsonl'):
        if p.stat().st_mtime < cutoff:
            p.unlink()
            n += 1
    return n
