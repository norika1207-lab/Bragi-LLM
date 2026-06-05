# 樣板 JSON Schema

每個 `.json` 一個樣板。檔名隨意，建議用 kebab-case。放在 `library/<domain>/` 下，`<domain>` 會自動當成檢索 metadata 的一個欄位。

## 必填欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | string | 全域唯一，建議 `<domain>.<name>`，例如 `react.login-form` |
| `code_template` | string | 最終要輸出的 code，用 `{{slot_name}}` 當佔位符 |

## 建議欄位 (沒有就檢索不到)

| 欄位 | 型別 | 說明 |
|---|---|---|
| `intents_zh` | string[] | 中文需求講法，5 到 15 句最佳 |
| `intents_en` | string[] | 英文需求講法 |
| `description` | string | 一句話描述這個樣板做什麼，會一起餵 embedding |
| `slots` | object | 槽位定義，見下 |

## slots 物件

```json
{
  "slot_name": {
    "type": "string" | "boolean" | "number",
    "description": "給 LLM 看的說明，講清楚這個槽位是什麼",
    "required": true,
    "default": "...",
    "hints": ["關鍵字 1", "keyword2"]
  }
}
```

- `description` 是丟給 LLM 的 prompt 一部分，要白話。
- `required` 標 true 的槽位，retrieval 階段會檢查 `hints` 有沒有一個出現在 user query 裡，沒命中的樣板會被排到後面。`hints` 沒列就跳過這層檢查。
- `default` 在 LLM 失敗或沒抽到時使用。required 但沒 default 會塞空字串。
- `hints` 是純字串 contains 比對，case-insensitive，中英都可以列。

## 替換規則

`code_template` 裡的 `{{slot_name}}` 會被字串替換掉。`{ }` 單大括號 (例如 JS 物件、JSX expression) 不會被動。boolean 會 render 成 `true` / `false`。

## 範例

看 `EXAMPLE.json`。
