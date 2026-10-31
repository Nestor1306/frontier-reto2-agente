"""Cliente de OpenRouter (API compatible con chat completions) para un modelo con visión."""

from __future__ import annotations

import base64
import io
import os
from typing import Protocol

import httpx
from PIL import Image

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELO_POR_DEFECTO = "anthropic/claude-sonnet-5.5"


class LLM(Protocol):
    async def decidir(self, system: str, user: str, imagen: Image.Image, timeout: float) -> str: ...


class LLMError(RuntimeError):
    pass


def imagen_a_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class OpenRouterLLM:
    def __init__(self, api_key: str, modelo: str = MODELO_POR_DEFECTO, max_tokens: int = 1500):
        if not api_key:
            raise LLMError("falta OPENROUTER_API_KEY")
        self._api_key = api_key
        self.modelo = modelo
        self.max_tokens = max_tokens
        self.llamadas = 0
        self.tokens_entrada = 0
        self.tokens_salida = 0
        self.costo_usd = 0.0
        self._client = httpx.AsyncClient()

    @classmethod
    def desde_entorno(cls) -> "OpenRouterLLM":
        return cls(os.environ.get("OPENROUTER_API_KEY", ""), os.environ.get("OPENROUTER_MODEL") or MODELO_POR_DEFECTO)

    async def decidir(self, system: str, user: str, imagen: Image.Image, timeout: float) -> str:
        payload = {
            "model": self.modelo,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "usage": {"include": True},
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {"type": "image_url", "image_url": {"url": imagen_a_data_url(imagen)}},
                    ],
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "X-Title": "frontier-agente-visual"}
        try:
            r = await self._client.post(OPENROUTER_URL, json=payload, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise LLMError(f"error de red con OpenRouter: {type(exc).__name__}") from None
        if r.status_code != 200:
            raise LLMError(f"OpenRouter respondió HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        self.llamadas += 1
        uso = data.get("usage") or {}
        self.tokens_entrada += int(uso.get("prompt_tokens") or 0)
        self.tokens_salida += int(uso.get("completion_tokens") or 0)
        self.costo_usd += float(uso.get("cost") or 0)
        try:
            contenido = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("respuesta de OpenRouter sin contenido") from None
        if isinstance(contenido, list):
            contenido = "".join(p.get("text", "") for p in contenido if isinstance(p, dict))
        return contenido or ""

    async def cerrar(self) -> None:
        await self._client.aclose()
