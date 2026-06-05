# Bragi-LLM ↔ Code Tree 整合指南

## 整體架構

```
┌──────────────────────┐
│ Code Tree (CLI/app) │ ← norika 既有
│ ─ bin/cosmos-tree   │
│ ─ src/cli/local-llm │  ← 已是 OpenAI 相容 client
│ ─ local-detect      │  ← 改一行加上 8080 偵測
└─────────┬────────────┘
          │
          │ OpenAI-compatible (POST :8080/v1/chat/completions)
          ▼
┌──────────────────────┐
│  bragi-server.js     │ ← Bragi-LLM repo 新增 (本文件配對)
│  - intercept router  │   - formula 類直接答 engine_lib
│  - LLM fallback      │   - 其他轉發到 llama-server
└─────────┬────────────┘
          │ POST :8081/v1/chat/completions
          ▼
┌──────────────────────┐
│  llama-server (8081) │ ← 載 c15v-q3km-imat.gguf (786 MB)
└──────────────────────┘
```

## Bragi-LLM 端 (這 repo, 已就緒)

```
Bragi-LLM/
├── c15v-q3km-imat.gguf       # 模型, 從 HF 下載
├── llama-server (llama.cpp)  # 用戶自己 build
├── bragi-server.js           # ← 新增 (Node proxy on :8080)
├── engine_lib.js             # ← 新增 (JS port of engine_lib.py)
├── engine_lib.py             # ← 既有 (Python, 用戶專案會 import 這個)
├── start-bragi.sh            # ← 新增 (一鍵啟動)
└── solve_intercept2.py       # 既有 (MBPP 評測用)
```

**啟動指令**:

```bash
cd ~/Documents/Bragi-LLM
./start-bragi.sh
# 預設 llama-server on :8081, proxy on :8080
# 可用 LLAMA_BIN / MODEL_PATH / NGL=99 / PROXY_PORT=... 環境變數覆寫
```

## Code Tree 端 (norika 的 repo, 需改一行)

### 改動 1: `src/cli/local-detect.js` 加 8080 偵測

```diff
   // 2) generic OpenAI-compatible server (vLLM / llama.cpp / LM Studio)
-  for (const base of ['http://localhost:8000/v1', 'http://localhost:1234/v1']) {
+  for (const base of ['http://localhost:8080/v1', 'http://localhost:8000/v1', 'http://localhost:1234/v1']) {
     const models = await getJson(base + '/models');
```

Bragi-server 在 `/v1/models` 回 `{data: [{id: 'bragi-llm', ...}]}`,Code Tree 的 `pickModel` 會選中(`CODER_HINT` regex 沒匹配但是唯一選項就會用它)。

要更精準(讓 Bragi 在 Ollama 已開的情況下也被優先選),建議在 `local-detect.js` 把 Bragi 探測放最前面:

```diff
 export async function detectLocalLLM({ preferModel } = {}) {
   // 0) env explicitly set → trust it, no probing
   const envUrl = process.env.CODETREE_LOCAL_URL;
   if (envUrl) { ... }
+
+  // 0.5) Bragi-LLM proxy on :8080 (preferred when available)
+  const bragiHealth = await getJson('http://localhost:8080/v1/health', 1500);
+  if (bragiHealth && bragiHealth.model === 'bragi-llm') {
+    return {
+      baseURL: 'http://localhost:8080/v1',
+      model: 'bragi-llm',
+      provider: 'bragi',
+      models: ['bragi-llm'],
+    };
+  }

   // 1) Ollama (the main path...)
```

### 改動 2 (可選, 顯示用): `bin/cosmos-tree.js` 標出 Bragi

如果 norika 想在 CLI footer 顯示「目前用 Bragi-LLM」,在偵測結果裡 `provider === 'bragi'` 時加 badge。這非必要,核心功能不依賴。

## 用戶 flow

```
1. 用戶下載 Code Tree.app + Bragi-LLM bundle (~1GB total)
   - Code Tree.app   ~200 MB
   - c15v-q3km-imat.gguf 786 MB
   - engine_lib.py / .js / bragi-server.js < 50 KB
2. 用戶開 Bragi-LLM:
     ./start-bragi.sh
3. 用戶開 Code Tree:
     code-tree ~/my-project
4. Code Tree 自動偵測到 :8080 上的 Bragi-server
   左下 footer 顯示 "engine: bragi-llm (local)"
5. 用戶在 Code Tree 對 agent 講:
     "寫一個 octagonal number 函式給我"
6. Code Tree agent 把這個 prompt 送給 :8080
7. bragi-server 識別「octagonal」keyword → 路由到 engine_lib.octagonal
   直接回 ```python from engine_lib import octagonal as _eng \n def ... ```
   (llama-server 連叫都沒叫, 零 LLM token)
8. Code Tree agent 收到 code, write_file 到專案
9. Code Tree 視覺化樹的對應格子亮起來, 程式逐行塞進去
10. 用戶看到一個小程式, 完全本地, 完全離線, 零 API 費
```

**對「比較複雜、不在 engine_lib 涵蓋範圍」的任務**(例如重構某個既有檔案、加 logging、改 CSS),bragi-server 不命中 router,自動 fallback 到 llama-server 上的 1.5B 模型寫。

## engine_lib 部署到用戶專案

用戶寫 octagonal 函式時, bragi-server 回的 code 是:

```python
from engine_lib import octagonal as _eng
def is_octagonal(*args, **kwargs):
    return _eng(*args, **kwargs)
```

這假設用戶的專案根目錄有 `engine_lib.py`。Code Tree 啟動時應該:

1. 偵測到 provider === 'bragi'
2. 自動複製 `engine_lib.py` 到當前專案根 (若還沒)
3. 加到 `.gitignore` 或保留在 repo 由用戶決定

建議在 Code Tree 寫一個 helper:

```js
// src/cli/bragi-setup.js
import { copyFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

export function ensureEngineLib(projectRoot) {
  const target = join(projectRoot, 'engine_lib.py');
  if (existsSync(target)) return;
  // Bragi-LLM ship 的 engine_lib.py 位置 (待定)
  const source = process.env.BRAGI_ENGINE_LIB || '/path/to/bragi/engine_lib.py';
  if (existsSync(source)) {
    copyFileSync(source, target);
    console.log(`[bragi] copied engine_lib.py to ${target}`);
  }
}
```

## 測試整合

啟動 Bragi:

```bash
cd ~/Documents/Bragi-LLM
./start-bragi.sh
```

另開 terminal 測試 proxy:

```bash
curl -s http://localhost:8080/v1/health
# → {"status":"ok","upstream":"up","model":"bragi-llm"}

curl -s http://localhost:8080/v1/models
# → {"object":"list","data":[{"id":"bragi-llm",...}]}

# 測試 intercept
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"bragi-llm",
    "messages":[{"role":"user","content":"Write a Python function to find the nth octagonal number. Test: assert is_octagonal(10) == 280"}],
    "stream":false
  }' | python3 -c "import json,sys;print(json.load(sys.stdin)['choices'][0]['message']['content'])"
# 應該直接回 from engine_lib import octagonal ... (bragi 標記為 routed: true)

# 測試 fallback (用 LLM)
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"bragi-llm",
    "messages":[{"role":"user","content":"Write a function to parse a JSON line and return the email field."}],
    "stream":false
  }' | python3 -c "import json,sys;print(json.load(sys.stdin)['choices'][0]['message']['content'])"
# 應該是 llama-server 寫的程式碼 (沒 routed 標記)
```

然後啟動 Code Tree 連 Bragi:

```bash
export CODETREE_LOCAL_URL=http://localhost:8080/v1
export CODETREE_LOCAL_MODEL=bragi-llm
node ~/Dropbox/Code\ Tree/bin/cosmos-tree.js --local ~/test-project
```

agent 動的時候,看 bragi-server 終端機:
- 看到 `[bragi] INTERCEPT octagonal → is_octagonal` 代表攔截成功,零 LLM token
- 看到 `[bragi] forward to llama-server` 代表轉給 1.5B 模型寫

## 未來方向 (可不做但加分)

1. **engine_lib auto-update**: Bragi-server 自動掃描專案常見題型,生成對應 helper 加進 engine_lib
2. **embedding-based router**: 現在 regex 命中率有限,換 sentence-transformer 25MB 模型做語意 routing
3. **multi-domain engine_lib**: SQL helper / web scraping / data analysis 各一個 lib,用戶選載入
4. **bundle 成單一 binary**: 用 pkg/nexe 把 Node + bragi-server 打成 macOS app,跟 Code Tree.app 一起 ship

## 整合進 Code Tree.app 的 bundling

Code Tree 既然要做成 macOS app,理想流程是:

```
Code Tree.app/
├── Contents/
│   ├── MacOS/Code Tree
│   ├── Resources/
│   │   ├── bragi/
│   │   │   ├── c15v-q3km-imat.gguf       (786 MB, 模型)
│   │   │   ├── llama-server              (預編譯 macOS binary)
│   │   │   ├── bragi-server.js
│   │   │   ├── engine_lib.js
│   │   │   ├── engine_lib.py
│   │   │   └── start-bragi.sh
│   │   └── app/                          (Code Tree 本體, ~200 MB)
└── ...
```

Electron 啟動時 `startCore()` 之前先 `spawn` 起 start-bragi.sh, 跟 llama-server 一起活著。退出時 cleanup。

這樣用戶體驗:
1. 拖 Code Tree.app 到 /Applications
2. 雙擊
3. 一切跑起來,沒看到任何「另外安裝」步驟
4. 在 Code Tree 裡寫程式,所有事都 local

**1GB 一次下載, 從此免訂閱。** 這就是使命 (HANDOFF.md §0) 的具體落地。
