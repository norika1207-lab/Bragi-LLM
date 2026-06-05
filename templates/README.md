# Bragi 樣板系統

這不是聊天模型，也不是 code LLM。是一個檢索式的 code generator。

## 做什麼

1. 你寫一句自然語言需求 (中或英)。
2. 系統用 multilingual MiniLM 算 embedding，從 `library/` 裡的樣板挑最像的 top-5。
3. 規則挑出分數最高、且必填槽位的關鍵字提示能對上 query 的那一個。
4. 把樣板跟 query 丟給 localhost:8080 的 OpenAI-compatible LLM (例如 llama.cpp server、LM Studio、Ollama 的 OpenAI-compat layer)，要它回 JSON 槽位值。
5. 用 `{{slot}}` 字串替換把值塞進 `code_template`，輸出最終 code。

## 為什麼這樣設計

直接用 LLM 生 code 有兩個問題：慢、不穩定。

樣板檢索 + 窄槽位填充把「結構」鎖在你寫過的 code 裡，LLM 只負責抽幾個變數。LLM 答錯一個 boolean 不會炸整支 code。樣板品質就是你預先 review 過的品質。

## 安裝

```
pip install sentence-transformers numpy requests
```

第一次跑會下載 embedding model (約 118 MB) 到 `~/.cache/huggingface/`。

LLM 端任何 OpenAI-compatible `/v1/chat/completions` 都行，預設 `http://localhost:8080`。要換用：

```
export BRAGI_LLM_URL=http://localhost:11434/v1/chat/completions
export BRAGI_LLM_MODEL=qwen2.5-coder:7b
```

## 用法

```
python -m templates.index_builder              # 建索引 (改 library 後要重跑)
python -m templates.cli "做個登入頁面要記住帳號"
python -m templates.cli --verbose "login form with remember me"
```

## 新增樣板

1. 在 `library/<domain>/` 放一個 `.json`，schema 看 `library/SCHEMA.md`，範例看 `library/EXAMPLE.json`。
2. 重跑 `python -m templates.index_builder`。
3. 寫幾句不同講法的 `intents_zh` / `intents_en`，越多檢索越準。

## 已知限制

- 樣板沒涵蓋的需求生不出來，會挑一個分數最高的硬塞，可能離題。用 `--verbose` 看候選清單判斷有沒有信心。
- LLM 抽槽位有時會回非 JSON 或亂編值。Fallback 是全用 default，所以樣板的 default 要設成合理可跑的 baseline。
- embedding 是句子等級語意，不是程式語意。「登入」「登錄」「sign in」會被當成同類，但「登入頁」跟「登入 API」也會混在一起。靠多寫幾個 intents 區分。
- 索引是純線性 cosine，幾千個樣板以內沒問題；上萬筆要換 faiss。
- 沒有對話上下文。每次 query 是獨立的。要連續修改生出來的 code 是另一層的事。

## 結構

```
templates/
  index.py             # 檢索 + 規則挑選
  slot_filler.py       # 呼 LLM 抽槽位、字串替換
  index_builder.py     # 一次性建索引
  cli.py               # python -m templates.cli
  library/
    SCHEMA.md          # 樣板 JSON 規格
    EXAMPLE.json       # 範例 (React login)
    <domain>/*.json    # 你的樣板
    _index/            # 自動產出，不要手動編
      embeddings.npy
      metadata.json
```
