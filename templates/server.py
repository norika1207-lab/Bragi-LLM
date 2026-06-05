"""OpenAI-compatible server on :9090 that fronts the Bragi template stack.

Replaces the Day-1 routing-only server with the Day-3 conversation orchestrator:
  - templates retrieval (sentence-transformers if available, else TF-IDF)
  - multi-turn follow-up detection (`改一下` / `加 loading` style)
  - optional file context (custom field `bragi_files`)
  - fallback to raw Bragi on :8080 when no template fits

Routes:
  GET  /v1/health              health probe
  GET  /v1/models              model list (templates-bragi-llm)
  POST /v1/chat/completions    OpenAI-compatible chat completions

Run: python -m templates.server
Then point Code Tree at http://localhost:9090/v1
"""
from __future__ import annotations
import http.server
import json
import os
import socketserver
import sys
import time
import uuid
from urllib.parse import urlparse

from templates import conversation as conv
from templates import session as sessionmod

PORT = int(os.environ.get("BRAGI_TEMPLATE_PORT", "9090"))
MODEL_TAG = "templates-bragi-llm"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write('[server] ' + fmt % args + '\n')

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/v1/health', '/health'):
            info = conv.info()
            return self._json(200, {
                'status': 'ok',
                'model': MODEL_TAG,
                'engine': info['engine'],
                'templates': info['template_count'],
                'domains': info['domains'],
            })
        if path == '/v1/models':
            return self._json(200, {
                'object': 'list',
                'data': [{'id': MODEL_TAG, 'object': 'model', 'owned_by': 'bragi'}],
            })
        return self._json(404, {'error': 'not found'})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ('/v1/chat/completions', '/v1/chat'):
            return self._json(404, {'error': 'not found'})

        length = int(self.headers.get('Content-Length', '0'))
        try:
            payload = json.loads(self.rfile.read(length))
        except Exception:
            return self._json(400, {'error': 'invalid json'})

        messages = payload.get('messages') or []
        if not messages:
            return self._json(400, {'error': 'no messages'})

        # extract last user message
        last_user = ''
        for m in reversed(messages):
            if m.get('role') == 'user':
                c = m.get('content')
                if isinstance(c, str):
                    last_user = c
                elif isinstance(c, list):
                    last_user = '\n'.join(x.get('text') or '' for x in c if isinstance(x, dict))
                break

        # session: custom field > OpenAI user field > auto-gen
        sid = payload.get('session_id') or payload.get('user') or sessionmod.new_session_id()
        file_paths = payload.get('bragi_files') or []

        try:
            result = conv.handle(last_user, session_id=sid, file_paths=file_paths)
        except Exception as e:
            return self._json(500, {'error': str(e)})

        resp = {
            'id': 'chatcmpl-' + uuid.uuid4().hex[:16],
            'object': 'chat.completion',
            'created': int(time.time()),
            'model': MODEL_TAG,
            'choices': [{
                'index': 0,
                'message': {'role': 'assistant', 'content': result.get('code', '')},
                'finish_reason': 'stop',
            }],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
            'bragi': {
                'session_id': sid,
                'mode': result.get('mode'),
                'template_id': result.get('template_id'),
                'template_domain': result.get('template_domain'),
                'score': result.get('score'),
                'slot_values': result.get('slot_values'),
                'engine': result.get('engine'),
            },
        }
        return self._json(200, resp)


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


def main():
    print(f'=== bragi templates server starting on :{PORT} ===', flush=True)
    print('  loading index (sentence-transformers first time downloads ~120MB model)...', flush=True)
    info = conv.info()
    print(f'  engine: {info["engine"]}', flush=True)
    print(f'  templates: {info["template_count"]}', flush=True)
    print(f'  domains: {info["domains"]}', flush=True)
    print(f'  POST /v1/chat/completions  (OpenAI-compatible, supports session_id + bragi_files)', flush=True)
    print(f'  GET  /v1/health', flush=True)
    print(f'  GET  /v1/models', flush=True)
    with ReusableTCPServer(('127.0.0.1', PORT), Handler) as httpd:
        httpd.serve_forever()


if __name__ == '__main__':
    main()
