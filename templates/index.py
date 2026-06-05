"""Bragi 樣板檢索主入口。

流程：
1. 載 multilingual MiniLM embedding model
2. 載已建好的樣板索引 (index_builder.py 產出)
3. 算 user query embedding，cosine similarity 取 top-5
4. 規則挑選：分數最高、且 required slots 都能從 query 抽到的那個
5. 呼叫 slot_filler 補完 code

這不是聊天模型，是檢索式 code generator。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

from templates.slot_filler import fill_slots

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
INDEX_DIR = Path(__file__).parent / "library" / "_index"
LIBRARY_DIR = Path(__file__).parent / "library"

_model_cache: SentenceTransformer | None = None
_index_cache: dict[str, Any] | None = None


def _get_model() -> SentenceTransformer:
    global _model_cache
    if _model_cache is None:
        _model_cache = SentenceTransformer(MODEL_NAME)
    return _model_cache


def _load_index() -> dict[str, Any]:
    """載 numpy embedding 矩陣 + metadata。"""
    global _index_cache
    if _index_cache is not None:
        return _index_cache

    emb_path = INDEX_DIR / "embeddings.npy"
    meta_path = INDEX_DIR / "metadata.json"
    if not emb_path.exists() or not meta_path.exists():
        raise FileNotFoundError(
            f"索引不存在於 {INDEX_DIR}，先跑 python -m templates.index_builder"
        )

    embeddings = np.load(emb_path)
    with open(meta_path, encoding="utf-8") as f:
        metadata = json.load(f)

    _index_cache = {"embeddings": embeddings, "metadata": metadata}
    return _index_cache


def _load_template(template_path: str) -> dict[str, Any]:
    full_path = LIBRARY_DIR / template_path
    with open(full_path, encoding="utf-8") as f:
        return json.load(f)


def _cosine_topk(query_vec: np.ndarray, corpus: np.ndarray, k: int = 5) -> list[tuple[int, float]]:
    q = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    c = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-9)
    scores = c @ q
    idx = np.argsort(-scores)[:k]
    return [(int(i), float(scores[i])) for i in idx]


def _has_required_slot_hints(template: dict[str, Any], query: str) -> bool:
    """檢查 query 是否大致包含 required slots 的關鍵字提示。

    這是粗篩，真正抽 slot 值靠 LLM。這裡只擋掉明顯對不上的樣板。
    每個 required slot 可在 template 的 slot_hints 列關鍵字 (中英任一命中即可)。
    沒列 hints 的 slot 預設 pass。
    """
    slots = template.get("slots", {})
    required = [name for name, spec in slots.items() if spec.get("required")]
    query_lower = query.lower()

    for slot_name in required:
        hints = slots[slot_name].get("hints", [])
        if not hints:
            continue
        if not any(h.lower() in query_lower for h in hints):
            return False
    return True


def generate(query: str, top_k: int = 5, min_score: float = 0.25) -> dict[str, Any]:
    """主函式：query 進 code 出。

    回傳 dict：
      {
        "code": str,
        "template_id": str,
        "score": float,
        "candidates": [(template_id, score), ...],
        "slots": {...},
      }
    """
    model = _get_model()
    index = _load_index()

    query_vec = model.encode(query, convert_to_numpy=True)
    topk = _cosine_topk(query_vec, index["embeddings"], k=top_k)

    metadata = index["metadata"]
    candidates: list[tuple[dict[str, Any], float]] = []
    for idx, score in topk:
        if score < min_score:
            continue
        entry = metadata[idx]
        tpl = _load_template(entry["path"])
        candidates.append((tpl, score))

    if not candidates:
        return {
            "code": "",
            "template_id": None,
            "score": 0.0,
            "candidates": [(metadata[i]["id"], s) for i, s in topk],
            "slots": {},
            "error": "沒有任何樣板分數超過 min_score，可能 library 太小或 query 太偏",
        }

    chosen = None
    chosen_score = 0.0
    for tpl, score in candidates:
        if _has_required_slot_hints(tpl, query):
            chosen = tpl
            chosen_score = score
            break
    if chosen is None:
        chosen, chosen_score = candidates[0]

    filled = fill_slots(chosen, query)

    return {
        "code": filled["code"],
        "template_id": chosen["id"],
        "score": chosen_score,
        "candidates": [(metadata[i]["id"], s) for i, s in topk],
        "slots": filled["slots"],
    }


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "做個登入頁面要記住帳號"
    result = generate(q)
    print(result["code"])
