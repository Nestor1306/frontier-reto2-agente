"""Elimina secretos de cualquier texto que salga del agente (resumen, logs, memoria)."""

from __future__ import annotations

import os

VARIABLES_SECRETAS = ("OPENROUTER_API_KEY", "VNC_PASSWORD", "AGENT_TOKEN", "API_TOKEN", "FRONTIER_TOKEN", "FRONTIER_TEAM_TOKEN", "DESKTOP_TOKEN")


def secretos_del_entorno() -> list[str]:
    return [v for k in VARIABLES_SECRETAS if (v := os.environ.get(k))]


def redactar(texto: str, secretos: list[str]) -> str:
    if not texto:
        return texto
    for s in sorted({s for s in secretos if s and len(s) >= 3}, key=len, reverse=True):
        texto = texto.replace(s, "[oculto]")
    return texto
