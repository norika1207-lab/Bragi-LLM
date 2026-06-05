#!/usr/bin/env node
// bragi-server.js — OpenAI-compatible proxy that sits in front of llama-server.
//
// Architecture:
//
//   Code Tree (or any OpenAI-compatible client)
//        │
//        │  POST :8080/v1/chat/completions
//        ▼
//   ┌────────────────────────────────────┐
//   │  bragi-server (this file)          │
//   │  - intercept router                 │
//   │  - if a formula-class prompt        │
//   │    matches, synthesise an answer    │
//   │    from engine_lib directly         │
//   │    (LLM is not called)              │
//   │  - otherwise forward to llama-server│
//   └─────────────┬──────────────────────┘
//                 │
//                 │  POST :8081/v1/chat/completions
//                 ▼
//   ┌────────────────────────────────────┐
//   │  llama-server with c15v-q3km-imat   │
//   │  (Bragi-LLM base, 786 MB Q3_K_M)    │
//   └────────────────────────────────────┘
//
// Why this design:
//  - Code Tree's local-llm.js already speaks OpenAI-compatible.  Standing this proxy in front of
//    llama-server makes Bragi a drop-in option (Code Tree just needs to detect port 8080).
//  - Intercept happens transparently: matched problems never reach the LLM, saving latency
//    and wall-clock on formula-class tasks where the small model would mis-recall.
//  - Code Tree's agent (read_file / write_file tool loop) does not need to change.  When the agent
//    asks the LLM "write a function to find the nth octagonal number", the proxy responds with a
//    ```python block containing `from engine_lib import octagonal as _eng / def is_octagonal(...)`,
//    which Code Tree's write_file tool then writes to disk.  The Python `engine_lib.py` file
//    needs to be present in the user's project (we provide an install command in the README).

import http from 'node:http';
import { route, LIB_FNS } from './engine_lib.js';

const PROXY_PORT = Number(process.env.BRAGI_PORT || 8080);
const LLAMA_URL = process.env.BRAGI_LLAMA_URL || 'http://localhost:8081/v1/chat/completions';
const LLAMA_HEALTH = (process.env.BRAGI_LLAMA_URL || 'http://localhost:8081').replace(/\/v1\/.*$/, '') + '/health';
const MODEL_TAG = 'bragi-llm';

// === health ===
async function llamaHealth() {
  try {
    const r = await fetch(LLAMA_HEALTH, { signal: AbortSignal.timeout(2000) });
    return r.ok;
  } catch { return false; }
}

// === intercept response ===
// When the router matches, synthesise an OpenAI-compatible chat completion that returns
// a ```python block wrapping engine_lib (Python file the user has installed in the project).
// The agent will then write_file this code to disk; verification will run it against
// the test, which imports engine_lib successfully.
function buildInterceptCode(routedFn, targetName) {
  return [
    '```python',
    `from engine_lib import ${routedFn} as _eng`,
    `def ${targetName || routedFn}(*args, **kwargs):`,
    `    return _eng(*args, **kwargs)`,
    '```'
  ].join('\n');
}

// Parse the *target* function name from a prompt that contains "assert <name>(...)".
function parseTargetFn(text) {
  const m = text.match(/assert\s+(\w+)\s*\(/);
  return m ? m[1] : null;
}

// Extract the user-facing prompt text from an OpenAI messages array (last user content).
function lastUserText(messages) {
  if (!Array.isArray(messages)) return '';
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== 'user') continue;
    if (typeof m.content === 'string') return m.content;
    if (Array.isArray(m.content)) {
      return m.content.map((c) => (typeof c === 'string' ? c : c.text || c.content || '')).join('\n');
    }
  }
  return '';
}

// === streaming SSE emission (for stream: true requests) ===
function sseFrame(payload) {
  return 'data: ' + JSON.stringify(payload) + '\n\n';
}
function sseDone() { return 'data: [DONE]\n\n'; }

function streamInterceptResponse(res, model, text) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  });
  const id = 'bragi-' + Math.random().toString(36).slice(2, 10);
  const ts = Math.floor(Date.now() / 1000);
  // first chunk: role
  res.write(sseFrame({
    id, object: 'chat.completion.chunk', created: ts, model,
    choices: [{ index: 0, delta: { role: 'assistant', content: '' }, finish_reason: null }],
  }));
  // content in one chunk (short enough that streaming-per-token brings no benefit)
  res.write(sseFrame({
    id, object: 'chat.completion.chunk', created: ts, model,
    choices: [{ index: 0, delta: { content: text }, finish_reason: null }],
  }));
  // final chunk
  res.write(sseFrame({
    id, object: 'chat.completion.chunk', created: ts, model,
    choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
  }));
  res.write(sseDone());
  res.end();
}

function jsonInterceptResponse(res, model, text) {
  const id = 'bragi-' + Math.random().toString(36).slice(2, 10);
  const ts = Math.floor(Date.now() / 1000);
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({
    id, object: 'chat.completion', created: ts, model,
    choices: [{ index: 0, message: { role: 'assistant', content: text }, finish_reason: 'stop' }],
    usage: { prompt_tokens: 0, completion_tokens: text.length / 4 | 0, total_tokens: text.length / 4 | 0 },
    bragi: { routed: true },
  }));
}

// === forward to llama-server ===
async function forwardToLlama(req, res, body) {
  try {
    const upstream = await fetch(LLAMA_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    res.writeHead(upstream.status, Object.fromEntries(upstream.headers.entries()));
    if (upstream.body) {
      for await (const chunk of upstream.body) res.write(chunk);
    }
    res.end();
  } catch (err) {
    res.writeHead(502, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'llama-server unreachable: ' + err.message } }));
  }
}

// === main HTTP handler ===
const server = http.createServer(async (req, res) => {
  // CORS preflight for browser-based clients
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    });
    return res.end();
  }

  // health endpoint (used by Code Tree's local-detect)
  if (req.url === '/health' || req.url === '/v1/health') {
    const up = await llamaHealth();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ status: 'ok', upstream: up ? 'up' : 'down', model: MODEL_TAG }));
  }

  // models endpoint (Code Tree's local-detect probes /v1/models)
  if (req.url === '/v1/models') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({
      object: 'list',
      data: [{ id: MODEL_TAG, object: 'model', owned_by: 'bragi-llm', created: 0 }],
    }));
  }

  if (req.url !== '/v1/chat/completions') {
    res.writeHead(404, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ error: { message: 'not found' } }));
  }

  // read full body
  let body = '';
  for await (const chunk of req) body += chunk;

  let data;
  try { data = JSON.parse(body); }
  catch {
    res.writeHead(400, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ error: { message: 'invalid JSON' } }));
  }

  const stream = !!data.stream;
  const model = data.model || MODEL_TAG;

  // try intercept
  const prompt = lastUserText(data.messages);
  const routedFn = prompt ? route(prompt) : null;
  if (routedFn && LIB_FNS.has(routedFn)) {
    const targetFn = parseTargetFn(prompt) || routedFn;
    const code = buildInterceptCode(routedFn, targetFn);
    console.log(`[bragi] INTERCEPT ${routedFn} → ${targetFn}`);
    if (stream) return streamInterceptResponse(res, model, code);
    return jsonInterceptResponse(res, model, code);
  }

  // fallback to llama-server
  console.log('[bragi] forward to llama-server');
  await forwardToLlama(req, res, body);
});

server.listen(PROXY_PORT, () => {
  console.log(`bragi-server listening on http://localhost:${PROXY_PORT}`);
  console.log(`  upstream llama-server expected at ${LLAMA_URL}`);
  console.log(`  model tag: ${MODEL_TAG}`);
});
