# Honest limits

## 這是什麼

窄但能跑的開發助手。靠 template retrieval + LLM slot fill。不是 Cursor 替代品，也不是 general coding agent。

核心交易：用 1100 條手寫 template 換來「常見任務」的高命中率與低延遲，跳脫常見模式的任務就直接落到 Bragi raw model 或者乾脆答不出來。

## 它做得好的事（pattern-recognisable 常見任務）

1. 「做個登入頁」這種有萬人寫過的 UI scaffold
2. Express / FastAPI / Django 的 CRUD endpoint
3. React Native / SwiftUI / Compose 的單一畫面樣板
4. bash 批次處理、cron job、systemd unit
5. ESP32 / Arduino 讀感測器送 MQTT 的標準骨架
6. LangChain RAG、OpenAI function calling 的入門結構
7. 經典資料結構與演算法（binary search、LRU、Dijkstra）
8. Dockerfile、docker-compose、GitHub Actions yaml
9. SQL 查詢與 index 建議（單表為主）
10. nginx / Kubernetes 的標準 manifest

## 它做不到的事（誠實列）

1. 全新架構設計。沒有 template 涵蓋你公司獨有的服務拓樸
2. 跨多檔案的重構。一次只回單一 snippet 或單一檔案
3. 複雜 debug。沒有 stack trace 解讀、沒有狀態追蹤、沒有 step-through
4. 長對話記憶。每次 query 獨立，沒有 session 概念
5. 生成式創作能力。寫 paper、寫 marketing copy、寫 spec doc 不在守備範圍

## 如何擴大覆蓋

發現某種任務常被打不中：

1. 在 `templates/library/<domain>/` 加一個 JSON
2. 跑 `python -m templates.index_builder`
3. 跑 `python -m templates.run_eval` 看新模板有沒有把該類型 query 拉起來
4. commit

schema 範例見 `templates/library/web-frontend/login_page.json`。一個 template 包含：id、domain、description、keywords、slots、code skeleton、language。

## Sub-1GB 預算

| 元件 | 大小 |
| --- | --- |
| Bragi GGUF (Q4) | 786 MB |
| sentence-transformers MiniLM | 約 120 MB |
| templates 1100 條 JSON | 約 4 MB |
| framework code（index / server / cli） | < 1 MB |
| 合計 | 約 911 MB |

這是設計鐵則。任何讓總和爆 1 GB 的 PR 必須先砍掉等量的東西才能進。給沒有訂閱、沒有 API key、沒有 GPU 的獨立開發者用。
