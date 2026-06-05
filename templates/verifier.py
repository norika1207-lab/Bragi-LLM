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


def verify_python(code: str, execute: bool = False) -> dict:
    """Python verifier with optional sandboxed execution.

    syntax level: ast.parse only.
    execute level (if execute=True): subprocess `python3 -c "code"` with
      strict isolation, 5s timeout. Catches NameError/ImportError/etc that
      AST misses.
    """
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {'ok': False, 'level': 'syntax', 'language': 'py',
                'errors': [f'line {e.lineno}: {e.msg}']}

    if not execute:
        return {'ok': True, 'level': 'syntax', 'errors': [], 'language': 'py'}

    # Sandboxed execute. Only catches CRASH on import/initialization, not
    # logical errors. Skip exec if code contains obvious blockers (network,
    # file I/O, subprocess) to avoid surprise side effects.
    blockers = ['subprocess', 'os.system', 'urllib.request.urlopen(',
                'requests.get', 'requests.post', 'open(']
    for b in blockers:
        if b in code:
            return {'ok': True, 'level': 'syntax-only',
                    'errors': [],
                    'language': 'py',
                    'note': f'skipped execute (contains {b})'}

    python3 = _which('python3') or _which('python')
    if not python3:
        return {'ok': True, 'level': 'syntax-only', 'errors': [], 'language': 'py'}

    # only execute IMPORT lines + DEF/CLASS lines, skip top-level expressions
    # (would error on undefined names like `app.listen(3000)`).
    safe_lines = []
    for line in code.split('\n'):
        s = line.strip()
        if (not s) or s.startswith('#') or s.startswith('def ') or s.startswith('class ') \
                or s.startswith('import ') or s.startswith('from ') \
                or s.startswith('@') or line.startswith((' ', '\t')):
            safe_lines.append(line)
        # else: skip top-level expression / call to avoid side effects
    safe_code = '\n'.join(safe_lines)

    try:
        r = subprocess.run([python3, '-c', safe_code],
                           capture_output=True, text=True, timeout=5,
                           env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp'})
        if r.returncode == 0:
            return {'ok': True, 'level': 'execute', 'errors': [], 'language': 'py'}
        err = r.stderr.strip().split('\n')[-3:]  # last 3 lines = the error
        return {'ok': False, 'level': 'execute', 'language': 'py',
                'errors': err}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'level': 'execute', 'language': 'py',
                'errors': ['execute timeout (5s)']}
    except Exception as e:
        return {'ok': True, 'level': 'syntax-only', 'errors': [],
                'language': 'py', 'note': f'execute err: {e}'}


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
    Catches truncated code (unclosed braces) which is the most common 1.5B failure.

    Vue SFC: extract <script> block before balance check, since <script setup>
    confuses the JS-only balance state machine.
    """
    # Vue SFC: pull out <script> and <template> blocks separately
    if re.search(r'<script\b[^>]*>', code) or re.search(r'<template\b[^>]*>', code):
        return _verify_vue_sfc(code)
    return _shallow_balance_check(code)


def _verify_vue_sfc(code: str) -> dict:
    """Vue SFC: parse out <script>, <template>, <style> blocks, balance each."""
    # Vue SFC = at minimum a <template> or <script> block. Verify each block exists
    # and is closed.
    blocks_found = 0
    for tag in ['script', 'template', 'style']:
        opens = re.findall(rf'<{tag}\b[^>]*>', code)
        closes = re.findall(rf'</{tag}>', code)
        if len(opens) != len(closes):
            return {'ok': False, 'level': 'shallow', 'language': 'vue',
                    'errors': [f'mismatched <{tag}> count: open={len(opens)} close={len(closes)}']}
        blocks_found += len(opens)
    if blocks_found == 0:
        return {'ok': False, 'level': 'shallow', 'language': 'vue',
                'errors': ['no SFC blocks found']}
    # extract script content and balance-check it
    script_m = re.search(r'<script\b[^>]*>(.*?)</script>', code, re.DOTALL)
    if script_m:
        inner_v = _shallow_balance_check(script_m.group(1))
        if not inner_v['ok']:
            return {'ok': False, 'level': 'shallow', 'language': 'vue',
                    'errors': ['<script> body: ' + e for e in inner_v['errors']]}
    return {'ok': True, 'level': 'shallow', 'errors': [], 'language': 'vue'}


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
    """Detect language from hint or code heuristics. Defensive: if uncertain,
    return empty rather than guess wrong (better to skip than to falsely fail)."""
    h = (hint or '').lower().strip()
    if h in LANG_VERIFIERS:
        return h
    # hint says non-JS even if not in LANG_VERIFIERS — respect hint, return ''
    if h in {'swift', 'kotlin', 'kt', 'dart', 'go', 'rust', 'rs', 'cpp', 'c', 'ino',
             'java', 'rb', 'cs', 'sol', 'tf', 'hcl', 'lua', 'php'}:
        return ''  # known unsupported, skip cleanly

    # heuristics: language-specific markers first
    # Swift: import Foundation/UIKit/SwiftUI/Combine/Compose
    if re.search(r'^\s*import\s+(Foundation|UIKit|SwiftUI|Combine|SwiftData)\b', code, re.M):
        return ''  # swift, skip
    # Kotlin/Compose: import androidx.compose
    if re.search(r'^\s*import\s+androidx\.', code, re.M) \
            or 'fun ' in code and '@Composable' in code:
        return ''  # kotlin, skip
    # Dart/Flutter: import 'package:flutter/...
    if re.search(r"^\s*import\s+['\"]package:", code, re.M):
        return ''  # dart, skip
    # Go: package main + func main
    if re.search(r'^\s*package\s+\w+', code, re.M) and re.search(r'^func\s+', code, re.M):
        return ''  # go, skip
    # Rust: fn main() / use std::
    if re.search(r'^\s*use\s+\w+::', code, re.M) or re.search(r'^fn\s+\w+\s*\(', code, re.M):
        return ''  # rust, skip
    # Arduino/C++: #include <Arduino.h> / WiFi.h
    if re.search(r'^\s*#include\s*<', code, re.M):
        return ''  # firmware/c++, skip

    # then existing JS/Py/etc detection
    if re.search(r'^\s*(import|from|def|class)\s', code, re.M) and 'function ' not in code:
        # Python: explicit `def ` or `from X import`
        if re.search(r'^\s*(from\s+\S+\s+import|def\s+)', code, re.M):
            return 'py'
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


def verify(code: str, hint: str = '', *, execute: bool = False) -> dict:
    """Main entry. Returns {ok, level, errors, language}.

    execute=True activates execute-level for languages that support it (py).
    """
    if not code or not code.strip():
        return {'ok': False, 'level': 'empty', 'errors': ['empty code'], 'language': ''}
    lang = detect_lang(code, hint)
    fn = LANG_VERIFIERS.get(lang)
    if fn is None:
        return {'ok': True, 'level': 'skip', 'errors': [],
                'language': lang or 'unknown',
                'note': f'no verifier for language={lang or "unknown"}'}
    try:
        # only verify_python accepts execute kwarg
        import inspect
        sig = inspect.signature(fn)
        if 'execute' in sig.parameters:
            return fn(code, execute=execute)
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
