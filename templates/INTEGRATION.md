# Bragi 模板系統 integration guide

從零裝起來、跑單一查詢、跑 eval、接進 Code Tree 的完整步驟。

## 1. 安裝 deps

```bash
cd ~/Documents/Bragi-LLM
python -m venv .venv
source .venv/bin/activate
pip install sentence-transformers numpy requests
```

sentence-transformers 預設拉的 all-MiniLM-L6-v2 約 120 MB，這是「sub-1GB by design」預算內的一部分。

## 2. Build index

```bash
python -m templates.index_builder
```

會掃 `templates/library/<domain>/*.json`，算 embedding，輸出 `templates/index.bin` + `templates/index_meta.json`。約 1100 個 template 大概跑 30-60 秒（看機器）。

## 3. 跑單一查詢（CLI）

```bash
python -m templates.cli "做個登入頁"
```

會印：top-1 template id、domain、score、slot 填完的程式碼。如果分數低於門檻（預設 0.45）會回 fallback 標記，由上層決定要不要送去 Bragi。

## 4. 跑 eval

```bash
python -m templates.run_eval
```

讀 `templates/test_scenarios.json`（50 條），印每個 domain 的 hit rate、slot 成功率、語法通過率，以及分數最低的 5 條 unmatched query。新增 template 後重跑這個確認沒退步。

## 5. 起 OpenAI-compatible server

```bash
python -m templates.server  # 預設 port 9090
```

server 行為：
- 收到 `/v1/chat/completions` 先抽 user message 跑 template retrieval
- 命中（score >= 門檻）→ 直接回 slot-filled code
- 沒命中 → forward 給 Bragi raw model on `localhost:8080`

Bragi raw model server 要先在 8080 起好（llama.cpp / llama-server / 任何 OpenAI-compatible runner）。

## 6. 接進 Code Tree

Code Tree 預設打 `localhost:8080`。改打 9090 讓它走 template-or-fallback：

```bash
export CODETREE_LOCAL_URL=http://localhost:9090
```

或在 Code Tree 設定檔寫死：

```json
{
  "local_llm_url": "http://localhost:9090"
}
```

9090 拿不到就降級回 8080 的邏輯放在 Code Tree 那邊（已有 retry on connection refused），這邊不重複實作。

## 7. 新增 template

1. 在 `templates/library/<domain>/` 放一個 JSON（schema 看現有檔案）
2. `python -m templates.index_builder` 重建索引
3. `python -m templates.run_eval` 確認 hit rate 沒退步
4. commit

## 8. troubleshooting

- index_builder OOM：用 `--batch 32` 縮 batch
- server 502：檢查 Bragi on 8080 有沒有起來，`curl http://localhost:8080/v1/models`
- 全部 query 都打不中：確認 `index.bin` 存在，沒有就重跑 builder
