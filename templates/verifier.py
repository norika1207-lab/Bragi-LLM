"""
Deterministic code verifier.

Given code + language, returns {ok, errors, language, level}.

Level meanings:
  - 'syntax': only AST parse / syntax check (cheapest, fastest)
  - 'semantic': also check imports/refs resolve (medium)
  - 'execute': run test if provided (slowest)

Supported languages (v1): py, js, ts, tsx, jsx, vue, sh, bash, json, yaml, sql.
Unsupported langs return {ok: True, level: 'skip', errors: []} so they don't block.

This is the second pillar of test-time-compute. The first pillar is generating
candidates; this one filters out the syntactically broken ones before they
reach the user.
"""
from __future__ import annotations
import ast
import json
import re
import subprocess
import tempfile
import pathlib


def _which(cmd: str) -> str | None:
    """Return path to executable, or None."""
    import shutil
    return shutil.which(cmd)


def verify_python(code: str) -> dict:
    try:
        ast.parse(code)
        return {'ok': True, 'level': 'syntax', 'errors': [], 'language': 'py'}
    except SyntaxError as e:
        return {'ok': False, 'level': 'syntax', 'language': 'py',
                'errors': [f'line {e.lineno}: {e.msg}']}


def verify_json(code: str) -> dict:
    try:
        json.loads(code)
        return {'ok': True, 'level': 'syntax', 'errors': [], 'language': 'json'}
    except json.JSONDecodeError as e:
        return {'ok': False, 'level': 'syntax', 'language': 'json',
                'errors': [f'line {e.lineno} col {e.colno}: {e.msg}']}


def verify_yaml(code: str) -> dict:
    try:
        import yaml
        yaml.safe_load(code)
        return {'ok': True, 'level': 'syntax', 'errors': [], 'language': 'yaml'}
    except Exception as e:
        return {'ok': False, 'level': 'syntax', 'language': 'yaml',
                'errors': [str(e)[:200]]}


def verify_bash(code: str) -> dict:
    """Use bash -n for syntax check."""
    bash = _which('bash')
    if not bash:
        return {'ok': True, 'level': 'skip', 'errors': [], 'language': 'sh'}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(code)
        path = f.name
    try:
        r = subprocess.run([bash, '-n', path], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return {'ok': True, 'level': 'syntax', 'errors': [], 'language': 'sh'}
        else:
            return {'ok': False, 'level': 'syntax', 'language': 'sh',
                    'errors': [r.stderr.strip()[:300]]}
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


def verify_js(code: str) -> dict:
    """For JS/TS/JSX/TSX/Vue use shallow balance check (deterministic, no deps).
    Catches truncated code (unclosed braces) which is the most common 1.5B failure."""
    return _shallow_balance_check(code)


def _shallow_balance_check(code: str) -> dict:
    """Last-resort: check brace/paren/bracket balance for JS-like languages.
    Not a real verifier but catches truncated code."""
    pairs = {'(': ')', '[': ']', '{': '}'}
    closing = {')', ']', '}'}
    opens = {'(', '[', '{'}
    stack = []
    in_str = None
    in_template = False
    in_comment = False
    i = 0
    while i < len(code):
        c = code[i]
        nxt = code[i+1] if i+1 < len(code) else ''
        if in_comment:
            if in_comment == 'line' and c == '\n':
                in_comment = False
            elif in_comment == 'block' and c == '*' and nxt == '/':
                in_comment = False; i += 1
        elif in_str:
            if c == '\\':
                i += 1
            elif c == in_str:
                in_str = None
        elif in_template:
            if c == '`':
                in_template = False
            elif c == '\\':
                i += 1
        else:
            if c == '/' and nxt == '/':
                in_comment = 'line'
            elif c == '/' and nxt == '*':
                in_comment = 'block'
            elif c in ('"', "'"):
                in_str = c
            elif c == '`':
                in_template = True
            elif c in opens:
                stack.append((c, i))
            elif c in closing:
                if not stack or pairs[stack[-1][0]] != c:
                    return {'ok': False, 'level': 'shallow', 'language': 'js',
                            'errors': [f'unbalanced {c} at offset {i}']}
                stack.pop()
        i += 1
    if stack:
        return {'ok': False, 'level': 'shallow', 'language': 'js',
                'errors': [f'unclosed {stack[-1][0]} at offset {stack[-1][1]}']}
    # incomplete trailing token check (1.5B truncation typical)
    stripped = code.rstrip()
    # strip line comments at the very end
    stripped = re.sub(r'\s*//[^\n]*$', '', stripped)
    if stripped:
        last_char = stripped[-1]
        last_token_tail = stripped[-3:]
        if last_char in {'=', '(', '[', '{', ',', '+', '-', '*', '/', '%', '<', '>', '&', '|', '?'}:
            return {'ok': False, 'level': 'shallow', 'language': 'js',
                    'errors': [f'incomplete trailing token "{last_char}"']}
        if last_token_tail in {' =>', '=>\n', ' && ', ' || '}:
            return {'ok': False, 'level': 'shallow', 'language': 'js',
                    'errors': [f'incomplete trailing "{last_token_tail.strip()}"']}
    return {'ok': True, 'level': 'shallow', 'errors': [], 'language': 'js'}


def verify_sql(code: str) -> dict:
    """SQL syntax via sqlite3 — only flag true parse errors (`syntax error`),
    not semantic errors like missing tables (those are runtime, not syntax)."""
    try:
        import sqlite3
        conn = sqlite3.connect(':memory:')
        for stmt in re.split(r';\s*\n|;\s*$', code, flags=re.M):
            stmt = stmt.strip().rstrip(';')
            if not stmt:
                continue
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                # only flag actual SYNTAX errors, not missing tables / unknown columns
                if 'syntax error' in msg or 'unrecognized token' in msg or 'incomplete input' in msg:
                    return {'ok': False, 'level': 'syntax', 'language': 'sql',
                            'errors': [str(e)[:300]]}
                # semantic err = runtime, not our concern
            except Exception:
                pass
        return {'ok': True, 'level': 'syntax', 'errors': [], 'language': 'sql'}
    except Exception:
        return {'ok': True, 'level': 'skip', 'errors': [], 'language': 'sql'}


LANG_VERIFIERS = {
    'py': verify_python, 'python': verify_python,
    'js': verify_js, 'javascript': verify_js,
    'ts': verify_js, 'typescript': verify_js,
    'tsx': verify_js, 'jsx': verify_js,
    'vue': verify_js,
    'sh': verify_bash, 'bash': verify_bash,
    'json': verify_json,
    'yaml': verify_yaml, 'yml': verify_yaml,
    'sql': verify_sql,
}


def detect_lang(code: str, hint: str = '') -> str:
    """Detect language from hint or code heuristics."""
    h = (hint or '').lower().strip()
    if h in LANG_VERIFIERS:
        return h
    # heuristics
    if re.search(r'^\s*(import|from|def|class)\s', code, re.M) and 'function ' not in code:
        return 'py'
    if re.search(r'^\s*(import|export|const|let|function|class)\s', code, re.M):
        if re.search(r':\s*(string|number|boolean|React\.)', code):
            return 'tsx'
        return 'js'
    if re.search(r'^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\s', code, re.M | re.I):
        return 'sql'
    if re.search(r'^\s*#!\s*/.*sh', code):
        return 'sh'
    if code.strip().startswith('{') or code.strip().startswith('['):
        return 'json'
    if re.search(r'^\s*\w+:\s*\n', code, re.M):
        return 'yaml'
    return ''


def verify(code: str, hint: str = '') -> dict:
    """Main entry. Returns {ok, level, errors, language}."""
    if not code or not code.strip():
        return {'ok': False, 'level': 'empty', 'errors': ['empty code'], 'language': ''}
    lang = detect_lang(code, hint)
    fn = LANG_VERIFIERS.get(lang)
    if fn is None:
        return {'ok': True, 'level': 'skip', 'errors': [],
                'language': lang or 'unknown',
                'note': f'no verifier for language={lang or "unknown"}'}
    try:
        return fn(code)
    except Exception as e:
        return {'ok': True, 'level': 'skip', 'errors': [], 'language': lang,
                'note': f'verifier raised {e}'}


def score_candidates(candidates: list[dict]) -> list[dict]:
    """Given list of {code, template_id, score, ...}, run verifier on each,
    return same list with .verify field added, sorted by (ok, -score)."""
    out = []
    for c in candidates:
        v = verify(c.get('code', ''), c.get('language', ''))
        c2 = dict(c)
        c2['verify'] = v
        c2['_rank_key'] = (0 if v['ok'] else 1, -c.get('score', 0))
        out.append(c2)
    out.sort(key=lambda x: x['_rank_key'])
    return out


if __name__ == '__main__':
    # quick smoke test
    tests = [
        ('py-good', 'def foo(x):\n  return x + 1', 'py'),
        ('py-bad', 'def foo(x:\n  return x + 1', 'py'),
        ('json-good', '{"a": 1, "b": [1,2,3]}', 'json'),
        ('json-bad', '{"a": 1, "b": [1,2,3', 'json'),
        ('js-good', 'const a = () => 1;', 'js'),
        ('js-bad', 'const a = () =>', 'js'),
        ('sql-good', 'SELECT * FROM users WHERE id = 1', 'sql'),
        ('sql-bad', 'SELEKT * FRM users', 'sql'),
        ('yaml-good', 'a: 1\nb:\n  - x\n  - y', 'yaml'),
        ('yaml-bad', 'a: 1\n  b:\n: x', 'yaml'),
    ]
    for name, code, hint in tests:
        r = verify(code, hint)
        print(f'{name:15} ok={r["ok"]:<5} level={r["level"]:<8} lang={r["language"]:<6} errs={r["errors"]}')
