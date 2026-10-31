"""Memoria por aplicación: lo que el agente aprende en una tarea lo reutiliza en la siguiente.

Se guarda en un JSON {"nombre de app": ["lección", ...]}. Las lecciones son sobre la interfaz
(rutas de menú, formatos, trampas), no sobre datos de clientes.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .guards import normalizar

MAX_LECCIONES = 25


class MemoriaApps:
    def __init__(self, ruta: str | Path | None):
        self.ruta = Path(ruta) if ruta else None
        self._lock = threading.Lock()
        self._datos: dict[str, list[str]] = {}
        if self.ruta and self.ruta.exists():
            try:
                self._datos = json.loads(self.ruta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._datos = {}

    @classmethod
    def desde_entorno(cls) -> "MemoriaApps":
        defecto = Path(__file__).resolve().parent.parent / "data" / "memoria_apps.json"
        return cls(os.environ.get("AGENT_MEMORY_PATH") or defecto)

    def _clave(self, app: str | None) -> str | None:
        if not app:
            return None
        clave = normalizar(app)
        for existente in self._datos:
            if normalizar(existente) == clave:
                return existente
        return app.strip()

    def contexto(self, app: str | None) -> dict[str, list[str]]:
        """Lecciones de la app actual; si aún no se sabe cuál es, las de todas (pocas)."""
        clave = self._clave(app)
        if clave and clave in self._datos:
            return {clave: self._datos[clave]}
        if clave:
            return {}
        return {k: v[-8:] for k, v in list(self._datos.items())[:5]}

    def aprender(self, app: str | None, lecciones: list[str], secretos: list[str]) -> None:
        clave = self._clave(app)
        if not clave or not lecciones:
            return
        from .redact import redactar

        with self._lock:
            actuales = self._datos.setdefault(clave, [])
            vistos = {normalizar(l) for l in actuales}
            for l in lecciones:
                l = redactar(str(l).strip(), secretos)[:300]
                if l and normalizar(l) not in vistos:
                    actuales.append(l)
                    vistos.add(normalizar(l))
            del actuales[:-MAX_LECCIONES]
            if self.ruta:
                self.ruta.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.ruta.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._datos, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(self.ruta)
