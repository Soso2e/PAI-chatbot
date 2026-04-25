import json
import httpx
from pathlib import Path
from typing import AsyncGenerator

CONFIG_PATH = Path(__file__).parent.parent / "config" / "llm.json"


def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


class LLMClient:
    def __init__(self):
        self._cfg = _load_config()

    def reload(self):
        self._cfg = _load_config()

    async def chat(self, messages: list[dict]) -> str:
        provider = self._cfg.get("provider", "ollama")
        if provider == "ollama":
            return await self._chat_ollama(messages)
        elif provider == "openai":
            return await self._chat_openai(messages)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    # ── Ollama ──────────────────────────────────────────────────────────────

    async def _chat_ollama(self, messages: list[dict]) -> str:
        url = self._cfg["base_url"].rstrip("/") + "/api/chat"
        payload = {
            "model": self._cfg["model"],
            "messages": messages,
            "stream": False,
            "options": self._cfg.get("options", {}),
        }
        async with httpx.AsyncClient(timeout=self._cfg.get("timeout", 60)) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]

    # ── OpenAI-compatible ────────────────────────────────────────────────────

    async def _chat_openai(self, messages: list[dict]) -> str:
        url = self._cfg["base_url"].rstrip("/") + "/v1/chat/completions"
        headers = {}
        api_key = self._cfg.get("api_key", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        opts = self._cfg.get("options", {})
        payload = {
            "model": self._cfg["model"],
            "messages": messages,
            "temperature": opts.get("temperature", 0.7),
        }
        async with httpx.AsyncClient(timeout=self._cfg.get("timeout", 60)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
