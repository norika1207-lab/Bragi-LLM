"""槽位填充器。

把 template + user query 丟給 localhost:8080 的 OpenAI-compatible LLM，
要它回 JSON {slot_name: value}，再 substitute 進 template 的 code_template 字串。

endpoint 預設 http://localhost:8080/v1/chat/completions，可用環境變數 BRAGI_LLM_URL 蓋掉。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

LLM_URL = os.environ.get("BRAGI_LLM_URL", "http://localhost:8080/v1/chat/completions")
LLM_MODEL = os.environ.get("BRAGI_LLM_MODEL", "local-model")
LLM_TIMEOUT = int(os.environ.get("BRAGI_LLM_TIMEOUT", "60"))


def _build_prompt(template: dict[str, Any], query: str) -> list[dict[str, str]]:
    slots = template.get("slots", {})
    slot_specs = []
    for name, spec in slots.items():
        line = f"- {name} ({spec.get('type', 'string')}): {spec.get('description', '')}"
        if "default" in spec:
            line += f"  [預設 {spec['default']}]"
        if spec.get("required"):
            line += "  [必填]"
        slot_specs.append(line)

    system = (
        "你是樣板槽位抽取器。讀使用者需求，從中抽取每個槽位的值，回傳純 JSON。\n"
        "規則：\n"
        "1. 只回 JSON，不要任何說明、markdown code block、前後綴文字。\n"
        "2. 抽不到的槽位用 template 的 default；沒 default 又非必填就略過該 key。\n"
        "3. boolean 用 true/false，不要用字串。\n"
        "4. 不要編造使用者沒講的細節。"
    )
    user = (
        f"樣板 ID: {template['id']}\n"
        f"樣板說明: {template.get('description', '')}\n\n"
        f"可填槽位：\n" + "\n".join(slot_specs) + "\n\n"
        f"使用者需求：{query}\n\n"
        "回 JSON："
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _call_llm(messages: list[dict[str, str]]) -> str:
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 512,
    }
    resp = requests.post(LLM_URL, json=payload, timeout=LLM_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _extract_json(text: str) -> dict[str, Any]:
    """從 LLM 回應抽 JSON，容忍 markdown code fence 或前後雜訊。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM 沒回 JSON：{text!r}")
    return json.loads(text[start : end + 1])


def _apply_defaults(template: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for name, spec in template.get("slots", {}).items():
        if name in values:
            merged[name] = values[name]
        elif "default" in spec:
            merged[name] = spec["default"]
        elif spec.get("required"):
            merged[name] = ""
    return merged


def _render(code_template: str, slots: dict[str, Any]) -> str:
    """用 {{slot_name}} 佔位符做替換，跳過程式碼裡常見的單 { 大括號。"""
    out = code_template
    for name, value in slots.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif value is None:
            rendered = ""
        else:
            rendered = str(value)
        out = out.replace("{{" + name + "}}", rendered)
    return out


def fill_slots(template: dict[str, Any], query: str) -> dict[str, Any]:
    """主要 API：吃 template + query，回 {code, slots}。

    LLM 失敗時退回全用 default，方便離線測試。
    """
    messages = _build_prompt(template, query)
    try:
        raw = _call_llm(messages)
        values = _extract_json(raw)
    except Exception as e:
        values = {}
        llm_error = str(e)
    else:
        llm_error = None

    slots = _apply_defaults(template, values)
    code = _render(template["code_template"], slots)
    return {"code": code, "slots": slots, "llm_error": llm_error}
