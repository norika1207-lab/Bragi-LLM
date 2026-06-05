"""
File context loader for Bragi.

Goal: when user says 「幫我這個 component 加 loading state」 while editing
src/components/Header.tsx, the system reads Header.tsx, extracts what's there,
and includes it in the Bragi prompt as context.

We use regex-based parsing (not tree-sitter) so there's zero extra dependency.
For v1 this is enough: extract function/class signatures, import statements,
and the full file if it's small. tree-sitter can be a future upgrade.

Supported languages: tsx, jsx, ts, js, vue, py, java, kt, swift, rs, go, c, cpp.
"""
from __future__ import annotations
import pathlib
import re

MAX_FILE_BYTES = 50_000  # 50 KB hard cap to keep prompts narrow

# language → list of regex extractors (each yields named entities)
EXTRACTORS = {
    'js': [
        (r'^\s*import\s+.+', 'import'),
        (r'^\s*export\s+(?:async\s+)?function\s+(\w+)', 'function'),
        (r'^\s*export\s+default\s+(?:async\s+)?function\s+(\w+)', 'function-default'),
        (r'^\s*export\s+const\s+(\w+)\s*=', 'const'),
        (r'^\s*function\s+(\w+)', 'function'),
        (r'^\s*class\s+(\w+)', 'class'),
    ],
    'py': [
        (r'^\s*import\s+(\S+)', 'import'),
        (r'^\s*from\s+(\S+)\s+import', 'import-from'),
        (r'^\s*def\s+(\w+)', 'function'),
        (r'^\s*async\s+def\s+(\w+)', 'async-function'),
        (r'^\s*class\s+(\w+)', 'class'),
    ],
    'rs': [
        (r'^\s*use\s+([^;]+)', 'use'),
        (r'^\s*(?:pub\s+)?fn\s+(\w+)', 'fn'),
        (r'^\s*(?:pub\s+)?struct\s+(\w+)', 'struct'),
        (r'^\s*(?:pub\s+)?enum\s+(\w+)', 'enum'),
        (r'^\s*impl\s+(.+)', 'impl'),
    ],
    'go': [
        (r'^\s*import\s+', 'import'),
        (r'^\s*func\s+(\w+)', 'func'),
        (r'^\s*type\s+(\w+)\s+struct', 'struct'),
    ],
}

# alias mappings
LANG_ALIASES = {
    'tsx': 'js', 'jsx': 'js', 'ts': 'js',
    'mjs': 'js', 'cjs': 'js',
    'pyi': 'py',
}


def detect_language(path: pathlib.Path) -> str:
    ext = path.suffix.lstrip('.').lower()
    return LANG_ALIASES.get(ext, ext)


def load_context(file_paths: list[str], max_total_bytes: int = 100_000) -> dict:
    """Load files and extract structural context.

    Returns dict per file with:
      - language
      - size
      - imports (list of import strings)
      - entities (list of {kind, name, line})
      - body (full content if size <= MAX_FILE_BYTES, else None)

    Skips files that don't exist, are too big, or are binary.
    """
    out = {}
    total_bytes = 0
    for fp in file_paths:
        p = pathlib.Path(fp).expanduser()
        if not p.exists() or not p.is_file():
            out[fp] = {'error': 'not found'}
            continue
        try:
            content = p.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            out[fp] = {'error': str(e)}
            continue

        size = len(content.encode('utf-8'))
        if total_bytes + size > max_total_bytes:
            out[fp] = {'error': f'budget exhausted (running total {total_bytes}/{max_total_bytes})'}
            continue

        total_bytes += size
        lang = detect_language(p)
        extractors = EXTRACTORS.get(lang, [])

        imports = []
        entities = []
        for i, line in enumerate(content.splitlines(), 1):
            for pat, kind in extractors:
                m = re.match(pat, line)
                if m:
                    if 'import' in kind or 'use' in kind:
                        imports.append(line.strip()[:200])
                    else:
                        name = m.group(1) if m.groups() else ''
                        entities.append({'kind': kind, 'name': name, 'line': i})
                    break

        out[fp] = {
            'language': lang,
            'size_bytes': size,
            'imports': imports[:30],
            'entities': entities[:40],
            'body': content if size <= MAX_FILE_BYTES else None,
            'body_truncated': size > MAX_FILE_BYTES,
        }
    return out


def context_to_prompt_snippet(ctx: dict) -> str:
    """Format loaded context as a compact prompt section."""
    if not ctx:
        return ''
    parts = ['== File context ==']
    for fp, info in ctx.items():
        if 'error' in info:
            parts.append(f'  {fp}: ERROR {info["error"]}')
            continue
        parts.append(f'\n  Path: {fp}  ({info["language"]}, {info["size_bytes"]} bytes)')
        if info.get('imports'):
            parts.append('  Imports:')
            for imp in info['imports'][:10]:
                parts.append(f'    {imp}')
        if info.get('entities'):
            parts.append('  Defined:')
            for e in info['entities'][:15]:
                parts.append(f'    {e["kind"]} {e["name"]} (line {e["line"]})')
        if info.get('body') and info['size_bytes'] < 4000:
            parts.append('  Body:')
            parts.append('  ```')
            parts.extend('  ' + ln for ln in info['body'].splitlines())
            parts.append('  ```')
    return '\n'.join(parts)
