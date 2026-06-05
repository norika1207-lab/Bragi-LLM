"""
Detect + handle multi-turn follow-up requests like:
  - 「加 loading state」
  - 「再給我加 dark mode」
  - 「改成 TypeScript」
  - 「把 button 換成綠色」
  - 「extract this into a hook」

These are NOT new-template requests — they are patches on the previous turn's
generated code. Bragi (the 1.5B Q3 backbone) is asked to produce a unified diff
or a full replacement, given the previous code + the change request.

This is the layer that makes the system feel multi-turn / Cursor-ish, without
needing a bigger LLM.
"""
from __future__ import annotations
import json
import re
import urllib.request

from templates.session import latest_turn

BRAGI_URL = 'http://localhost:8080/v1/chat/completions'

# Heuristic patterns for detecting follow-up vs new request.
# A request is a follow-up if:
#   - There is a previous turn in the session
#   - The query contains delta-style markers (add/change/remove/rename/extract)
#   - The query does NOT name a new project type
#
# Zh + En patterns. Light, fast, no LLM call.

ZH_DELTA_VERBS = [
    '加', '再加', '補', '補上', '加上', '附帶',
    '改', '改成', '改為', '換成', '換成', '替換', '取代',
    '移除', '刪掉', '拿掉', '去掉', '砍掉',
    '重命名', '改名', '取個', '改個名',
    '抽出', '抽成', 'extract', '提取',
    '加註解', '加 comment', '加註釋',
    '加類型', '加 type', '加 TS', '改成 TS', '轉成 TypeScript',
    '加錯誤處理', '加 error handling', '加 try catch',
    '改 dark', '改成 dark', 'dark mode', 'dark-mode',
    '加 loading', '加 spinner', '加進度',
    '改成 async', '改 async',
    '加 test', '加單元測試', '加 unit test',
    '加 i18n', '加多語',
    '加 prop', '加參數', '加 arg',
    '改 responsive', '改成 responsive',
    '更乾淨', '簡化', '重構', '優化',
]

EN_DELTA_VERBS = [
    'add ', 'also ', 'plus ', 'and add ',
    'change ', 'replace ', 'rename ', 'swap ',
    'remove ', 'delete ', 'drop ', 'strip ',
    'extract ', 'refactor ', 'simplify ',
    'convert to ', 'turn into ', 'make it ',
    'use ', 'with ',
]

NEW_PROJECT_MARKERS_ZH = [
    '做個', '做一個', '幫我寫', '幫我做', '給我一個', '寫個', '寫一個', '建立',
    '新增一個', '生成', '產生', '創建',
]

NEW_PROJECT_MARKERS_EN = [
    'create a ', 'create an ', 'build a ', 'build an ',
    'make a ', 'make an ', 'write a ', 'write an ',
    'generate a ', 'scaffold ',
]


def is_followup(query: str, sid: str | None) -> bool:
    """Heuristic: is this a follow-up on the previous turn?"""
    if not sid:
        return False
    last = latest_turn(sid)
    if not last:
        return False
    q = query.strip().lower()

    # explicit new-project markers veto follow-up
    for m in NEW_PROJECT_MARKERS_ZH + NEW_PROJECT_MARKERS_EN:
        if m.lower() in q:
            return False

    # delta verbs trigger follow-up
    for v in ZH_DELTA_VERBS:
        if v in query:
            return True
    for v in EN_DELTA_VERBS:
        if v in q:
            return True

    # short query (< 25 chars) right after a turn = likely follow-up
    if len(query) < 25 and last.get('final_code'):
        return True

    return False


# Deterministic delta patches for very common follow-up patterns.
# These bypass Bragi entirely because the 1.5B Q3 model is unreliable at
# applying open-ended patches. Recognised intents get a known code transform.

DETERMINISTIC_PATCHES = {
    'dark_mode_tailwind': {
        'triggers': ['dark mode', 'dark-mode', '改 dark', '改成 dark', '加 dark', 'darkmode'],
        'requires': ['className='],  # only apply if it's a Tailwind component
        'transform': 'add_tailwind_dark_classes',
    },
    'loading_state_react': {
        'triggers': ['加 loading', '加 spinner', 'add loading', 'with loading', '加進度'],
        'requires': ['useState', 'onSubmit'],
        'transform': 'add_react_loading_state',
    },
    'typescript_convert': {
        'triggers': ['改成 ts', '改成 TS', '改成 TypeScript', '轉成 ts', '加 type', 'convert to typescript', 'add types'],
        'requires': [],
        'transform': 'add_ts_annotations',
    },
    'try_catch': {
        'triggers': ['加 try catch', '加錯誤處理', 'add error handling', 'add try catch'],
        'requires': ['await', 'fetch'],
        'transform': 'wrap_try_catch',
    },
}


def _add_tailwind_dark_classes(code: str) -> str:
    """Add dark: variants to common Tailwind classes."""
    import re as _re
    def patch(m):
        cls = m.group(1)
        new_parts = []
        for p in cls.split():
            new_parts.append(p)
            # add dark variants for common color classes
            if p.startswith('bg-white'):
                new_parts.append('dark:bg-gray-900')
            elif p.startswith('bg-gray-50'):
                new_parts.append('dark:bg-gray-800')
            elif p.startswith('bg-gray-100'):
                new_parts.append('dark:bg-gray-700')
            elif p.startswith('text-gray-900') or p == 'text-black':
                new_parts.append('dark:text-gray-100')
            elif p.startswith('text-gray-700') or p.startswith('text-gray-800'):
                new_parts.append('dark:text-gray-200')
            elif p.startswith('border-gray-200') or p.startswith('border-gray-100'):
                new_parts.append('dark:border-gray-700')
        return 'className="' + ' '.join(new_parts) + '"'
    return _re.sub(r'className="([^"]+)"', patch, code)


def _add_react_loading_state(code: str) -> str:
    """If a React component doesn't already have loading state on its async form, add it."""
    if 'const [loading' in code or 'setLoading' in code:
        return code  # already has it
    import re as _re
    # add useState declaration after the last existing useState line
    matches = list(_re.finditer(r"const \[\w+, set\w+\] = useState\([^)]*\);", code))
    if matches:
        last = matches[-1]
        insertion = "\n  const [loading, setLoading] = useState(false);"
        code = code[:last.end()] + insertion + code[last.end():]
    # wrap any async onSubmit body with setLoading(true)/false
    code = _re.sub(
        r'(async function onSubmit\([^)]*\)\s*\{)',
        r'\1\n    setLoading(true);\n    try {',
        code, count=1
    )
    if 'setLoading(true);\n    try {' in code:
        code = _re.sub(
            r'(\n  \})\s*\n\s*return',
            r'\n    } finally {\n      setLoading(false);\n    }\1\n\n  return',
            code, count=1
        )
    # disable button while loading
    code = _re.sub(
        r'<button type="submit">',
        '<button type="submit" disabled={loading}>',
        code
    )
    return code


def _add_ts_annotations(code: str) -> str:
    """Light TS conversion: rename .jsx-style props to typed equivalents."""
    if 'React.FormEvent' in code or ': string' in code:
        return code  # already typed
    import re as _re
    code = _re.sub(r'function onSubmit\(e\)', 'function onSubmit(e: React.FormEvent)', code)
    code = _re.sub(r'function onSubmit\(e\) \{', 'function onSubmit(e: React.FormEvent) {', code)
    code = _re.sub(r'\(e\) =>', '(e: React.ChangeEvent<HTMLInputElement>) =>', code)
    return code


def _wrap_try_catch(code: str) -> str:
    """Wrap await fetch in try/catch if not already."""
    if 'try {' in code and 'catch' in code:
        return code
    import re as _re
    code = _re.sub(
        r'(const res = await fetch\([^;]+;)',
        r'try {\n      \1',
        code, count=1
    )
    code = _re.sub(
        r'(if \(!res\.ok\)[^\n]+throw [^\n]+;)',
        r'\1\n    } catch (err) {\n      console.error(err);\n      setError(err instanceof Error ? err.message : "Request failed");\n    }',
        code, count=1
    )
    return code


PATCH_FNS = {
    'add_tailwind_dark_classes': _add_tailwind_dark_classes,
    'add_react_loading_state': _add_react_loading_state,
    'add_ts_annotations': _add_ts_annotations,
    'wrap_try_catch': _wrap_try_catch,
}


def _try_deterministic_patch(query: str, prev_code: str) -> str | None:
    """Return patched code if a deterministic delta matches, else None."""
    q = query.strip().lower()
    for name, spec in DETERMINISTIC_PATCHES.items():
        if not any(t.lower() in q for t in spec['triggers']):
            continue
        if spec['requires'] and not all(req in prev_code for req in spec['requires']):
            continue
        fn = PATCH_FNS.get(spec['transform'])
        if fn:
            return fn(prev_code)
    return None


def apply_followup(query: str, sid: str, timeout: int = 90) -> dict:
    """Take the user's delta request + the previous turn's code.

    First tries deterministic patches for common intents (dark mode, loading,
    TS conversion, try/catch). Only if no deterministic match found does it
    fall through to Bragi LLM, which is unreliable for delta patches.
    """
    last = latest_turn(sid)
    if not last or not last.get('final_code'):
        return {'ok': False, 'error': 'no previous turn with code'}

    prev_code = last['final_code']
    prev_query = last.get('query', '')

    # Try deterministic first (much more reliable than 1.5B Q3 for these patterns)
    patched = _try_deterministic_patch(query, prev_code)
    if patched and patched != prev_code:
        return {
            'ok': True,
            'code': patched,
            'reply': '[deterministic patch applied]',
            'prev_template_id': last.get('template_id'),
            'patch_type': 'deterministic',
        }

    prompt = (
        f'Previous user request: {prev_query}\n\n'
        f'Previous code:\n```\n{prev_code}\n```\n\n'
        f'User now says: {query}\n\n'
        f'Apply the requested change and return ONLY the updated full code (same language). '
        f'No prose, no markdown fence, just the raw code.'
    )

    body = json.dumps({
        'model': 'bragi-llm',
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
        'temperature': 0.2,
        'max_tokens': 1200,
    }).encode()

    try:
        req = urllib.request.Request(BRAGI_URL, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        reply = resp['choices'][0]['message']['content']
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    # strip markdown fences if Bragi snuck them in
    code = reply.strip()
    code = re.sub(r'^```[a-zA-Z]*\n', '', code)
    code = re.sub(r'\n```$', '', code)

    return {'ok': True, 'code': code, 'reply': reply,
            'prev_template_id': last.get('template_id'),
            'patch_type': 'llm-fallback'}
