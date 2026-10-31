"""Gestor de tareas: ejecuta el agente en segundo plano y garantiza las reglas del reto.

- POST responde enseguida: la tarea corre como tarea asyncio.
- Una sola tarea por escritorio: si llega otra para el mismo escritorio, la anterior se cancela
  (y al cancelarse, el agente cierra su conexión VNC).
- Límite duro de tiempo por encima del límite interno del agente (270 s).
- El resumen nunca contiene la contraseña del escritorio ni secretos del entorno.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable

from agente.redact import redactar, secretos_del_entorno

log = logging.getLogger("gestor")

LIMITE_DURO_S = 290.0  # el evaluador corta a los 300 s

Motor = Callable[..., Awaitable[dict]]


def _motor_por_defecto(*args, **kwargs):
    from agente import run_task_async

    return run_task_async(*args, **kwargs)


class GestorTareas:
    def __init__(self, motor: Motor | None = None, limite_duro_s: float = LIMITE_DURO_S):
        self.motor = motor or _motor_por_defecto
        self.limite_duro_s = limite_duro_s
        self._tareas: dict[str, dict] = {}
        self._por_escritorio: dict[str, asyncio.Task] = {}
        self._vivas: set[asyncio.Task] = set()

    def crear(self, tarea: str, vnc_url: str, vnc_password: str, **opciones) -> str:
        task_id = str(uuid.uuid4())
        self._tareas[task_id] = {
            "estado": "en_curso",
            "resumen": "El agente está trabajando en la tarea.",
            "respuesta": None,
            "_inicio": time.time(),
        }
        t = asyncio.create_task(self._ejecutar(task_id, tarea, vnc_url, vnc_password, opciones))
        self._vivas.add(t)
        t.add_done_callback(self._vivas.discard)
        return task_id

    def consultar(self, task_id: str) -> dict | None:
        reg = self._tareas.get(task_id)
        if reg is None:
            return None
        return {k: v for k, v in reg.items() if not k.startswith("_")}

    async def _ejecutar(self, task_id: str, tarea: str, url: str, password: str, opciones: dict) -> None:
        reg = self._tareas[task_id]
        yo = asyncio.current_task()
        anterior = self._por_escritorio.get(url)
        self._por_escritorio[url] = yo
        secretos = [password, *secretos_del_entorno()]
        try:
            if anterior is not None and not anterior.done():
                log.info("tarea %s: cancelando la tarea anterior del mismo escritorio", task_id[:8])
                anterior.cancel()
                await asyncio.wait({anterior}, timeout=10)

            r = await asyncio.wait_for(self.motor(tarea, url, password, **opciones), timeout=self.limite_duro_s)
            estado = r.get("estado")
            if estado not in ("terminada", "fallida"):
                raise ValueError(f"estado no válido: {estado!r}")
            reg["resumen"] = redactar(str(r.get("resumen") or "Sin resumen."), secretos)
            reg["respuesta"] = redactar(str(r["respuesta"]), secretos) if r.get("respuesta") else None
            reg["estado"] = estado
        except asyncio.CancelledError:
            reg.update(estado="fallida", respuesta=None,
                       resumen="No se completó la tarea: llegó una nueva tarea para el mismo escritorio y esta se "
                               "canceló. Se detuvo toda interacción con el escritorio.")
        except TimeoutError:
            reg.update(estado="fallida", respuesta=None,
                       resumen=f"No se completó la tarea: se superó el límite de {int(self.limite_duro_s)} s. "
                               "Se detuvo toda interacción con el escritorio.")
        except Exception as exc:
            log.warning("tarea %s: error interno %s", task_id[:8], type(exc).__name__)
            reg.update(estado="fallida", respuesta=None,
                       resumen=f"No se completó la tarea: error interno del agente ({type(exc).__name__}). "
                               "No se confirmó el resultado.")
        finally:
            reg["_fin"] = time.time()
            if self._por_escritorio.get(url) is yo:
                del self._por_escritorio[url]
            log.info("tarea %s: %s en %.1f s", task_id[:8], reg["estado"], reg["_fin"] - reg["_inicio"])
