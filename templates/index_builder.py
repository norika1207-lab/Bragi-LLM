"""建索引：把 library/(domain)/*.json 全掃一遍，算 embedding 存 numpy。

用法：
    python -m templates.index_builder

產出：
    templates/library/_index/embeddings.npy
    templates/library/_index/metadata.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
LIBRARY_DIR = Path(__file__).parent / "library"
INDEX_DIR = LIBRARY_DIR / "_index"


def _collect_templates() -> list[dict]:
    """掃 library/<domain>/*.json，跳過 _index 與 schema/example 檔。"""
    entries = []
    for path in sorted(LIBRARY_DIR.rglob("*.json")):
        if "_index" in path.parts:
            continue
        if path.name.startswith("_"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                tpl = json.load(f)
        except json.JSONDecodeError as e:
            print(f"略過 {path}: {e}")
            continue
        if "id" not in tpl or "code_template" not in tpl:
            print(f"略過 {path}: 缺 id 或 code_template")
            continue
        rel = path.relative_to(LIBRARY_DIR)
        entries.append({
            "id": tpl["id"],
            "path": str(rel),
            "domain": rel.parts[0] if len(rel.parts) > 1 else "misc",
            "intents_zh": tpl.get("intents_zh", []),
            "intents_en": tpl.get("intents_en", []),
            "description": tpl.get("description", ""),
        })
    return entries


def _build_text(entry: dict) -> str:
    parts = entry["intents_zh"] + entry["intents_en"]
    if entry["description"]:
        parts.append(entry["description"])
    return " | ".join(parts)


def build() -> None:
    entries = _collect_templates()
    if not entries:
        print("library 裡沒任何樣板，先放幾個 .json 再來")
        return

    print(f"找到 {len(entries)} 個樣板，載 embedding model 中⋯")
    model = SentenceTransformer(MODEL_NAME)

    texts = [_build_text(e) for e in entries]
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=True)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(INDEX_DIR / "embeddings.npy", embeddings)
    with open(INDEX_DIR / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"索引寫到 {INDEX_DIR}")
    print(f"  embeddings.npy shape = {embeddings.shape}")
    print(f"  metadata.json 筆數 = {len(entries)}")


if __name__ == "__main__":
    build()
