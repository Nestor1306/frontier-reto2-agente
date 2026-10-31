"""Motor del agente: observar → percibir → decidir (modelo) → revisar (guardas) → actuar → verificar.

Punto de entrada público: `run_task_async` (para FastAPI/asyncio) y `run_task` (síncrono).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from visual import AuthError, VisualController, changed_pixels

from .guards import Guardas
from .llm import LLM, LLMError, OpenRouterLLM
from .memory import MemoriaApps
from .perception import Elemento, detectar, formatear
from .prompts import system_prompt, user_prompt
from .redact import redactar, secretos_del_entorno
from .schema import Accion, Decision, DecisionInvalida, parse_decision
from .summary import resumen_fallida, resumen_terminada

log = logging.getLogger("agente")

LIMITE_POR_DEFECTO_S = 270.0  # el evaluador da 300 s; dejamos margen para cerrar y reportar
MAX_PASOS_POR_DEFECTO = 45
MARGEN_S = 12.0  # sin este tiempo restante no se pide otra decisión
PIXELES_CAMBIO = 40


@dataclass
class _Progreso:
    pasos: int = 0
    ultimo: str = "conectando al escritorio"


class _Traza:
    """Guarda capturas y decisiones de cada paso si AGENT_TRACE_DIR está definido (útil para depurar y para el video)."""

    def __init__(self, secretos: list[str]):
        base = os.environ.get("AGENT_TRACE_DIR")
        self.dir = Path(base) / time.strftime("%Y%m%d_%H%M%S") if base else None
        self.secretos = secretos
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def paso(self, n: int, img, prompt: str, respuesta: str) -> None:
        if not self.dir:
            return
        img.save(self.dir / f"paso_{n:02d}.png")
        (self.dir / f"paso_{n:02d}.json").write_text(
            json.dumps({"prompt": redactar(prompt, self.secretos), "respuesta": redactar(respuesta, self.secretos)},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _resultado(estado: str, resumen: str, respuesta: str | None = None) -> dict:
    return {"estado": estado, "resumen": resumen, "respuesta": respuesta}


def _punto(a: Accion, elementos: list[Elemento]) -> tuple[tuple[int, int] | None, str]:
    if a.elemento is not None:
        for e in elementos:
            if e.id == a.elemento:
                return e.centro, f'"{e.texto}"'
        raise ValueError(f"no existe el elemento {a.elemento} en la pantalla actual")
    if a.x is not None and a.y is not None:
        return (a.x, a.y), f"({a.x},{a.y})"
    return None, ""


async def _ejecutar(vc: VisualController, a: Accion, elementos: list[Elemento]) -> str:
    """Ejecuta una acción en el escritorio y devuelve una descripción corta para el historial."""
    p, donde = _punto(a, elementos)
    if a.tipo in ("clic", "doble_clic", "clic_derecho"):
        if p is None:
            raise ValueError("falta 'elemento' o 'x','y' para el clic")
        await vc.click(*p, double=a.tipo == "doble_clic", button="right" if a.tipo == "clic_derecho" else "left")
        return f"{a.tipo.replace('_', ' ')} en {donde}"
    if a.tipo == "escribir":
        if a.texto is None:
            raise ValueError("falta 'texto' para escribir")
        if p is not None:
            await vc.replace_text(a.texto, *p)
        else:
            await vc.replace_text(a.texto)
        if a.enter:
            await vc.key("enter")
        return f'escribir "{a.texto}"' + (f" en {donde}" if donde else "") + (" + Enter" if a.enter else "")
    if a.tipo == "tecla":
        if not a.tecla:
            raise ValueError("falta 'tecla'")
        await vc.key(a.tecla)
        return f"tecla {a.tecla}"
    if a.tipo == "desplazar":
        w, h = vc.size
        x, y = p or (w // 2, h // 2)
        cantidad = a.cantidad if a.cantidad else 3
        await vc.scroll(x, y, cantidad)
        return f"desplazar {cantidad} en ({x},{y})"
    if a.tipo == "esperar":
        s = min(max(a.segundos or 1.0, 0.5), 5.0)
        await asyncio.sleep(s)
        return f"esperar {s:.1f} s"
    raise ValueError(f"acción no ejecutable: {a.tipo}")


def _describir(d: Decision) -> str:
    a = d.accion
    extra = a.elemento if a.elemento is not None else (f"{a.x},{a.y}" if a.x is not None else (a.tecla or a.texto or ""))
    return f"{a.tipo} {extra}".strip()


async def _ciclo(
    *,
    tarea: str,
    vc: VisualController,
    llm: LLM,
    memoria: MemoriaApps,
    deadline: float,
    margen_s: float,
    max_pasos: int,
    percibir: Callable,
    progreso: _Progreso,
    secretos: list[str],
    llm_timeout_s: float,
    solo_lectura: bool,
    solo_observar: bool,
) -> dict:
    ancho, alto = vc.size
    system = system_prompt(ancho, alto)
    guardas = Guardas(tarea, solo_lectura=solo_lectura, solo_observar=solo_observar)
    traza = _Traza(secretos)
    historial: list[str] = []
    avisos: list[str] = []
    plan, app = "", None
    fallos_modelo = rechazos = sin_cambio = 0

    img = await vc.screenshot()
    for paso in range(1, max_pasos + 1):
        restante = deadline - time.monotonic()
        if restante < margen_s:
            return _resultado(
                "fallida",
                f"No se completó la tarea: se acabó el tiempo antes de terminar y verificar. Se detuvo toda "
                f"interacción con el escritorio. Último avance: {progreso.ultimo}.",
            )

        elementos = await asyncio.to_thread(percibir, img)
        prompt = user_prompt(
            tarea=tarea, paso=paso, max_pasos=max_pasos, restante_s=restante, memoria=memoria.contexto(app),
            plan=plan, historial=historial, avisos=avisos, elementos_txt=formatear(elementos),
        )
        avisos = []
        texto = ""
        try:
            texto = await llm.decidir(system, prompt, img, timeout=max(5.0, min(llm_timeout_s, restante - margen_s)))
            decision = parse_decision(texto)
            fallos_modelo = 0
        except (LLMError, DecisionInvalida, asyncio.TimeoutError) as exc:
            traza.paso(paso, img, prompt, texto or f"ERROR: {exc}")
            fallos_modelo += 1
            log.warning("paso %d: decisión no válida (%s)", paso, redactar(str(exc), secretos))
            if fallos_modelo >= 3:
                return _resultado("fallida", f"No se completó la tarea: el modelo no dio una decisión válida 3 veces seguidas ({exc}).")
            avisos.append(f"tu respuesta anterior no fue válida ({exc}). Responde SOLO con el JSON pedido.")
            continue

        traza.paso(paso, img, prompt, texto)
        app = decision.app or app
        plan = decision.plan or plan
        veredicto = guardas.revisar(decision, elementos)
        if not veredicto.ok:
            rechazos += 1
            historial.append(f"Paso {paso}: propusiste {_describir(decision)} → RECHAZADA: {veredicto.motivo}")
            log.info("paso %d: acción rechazada por guardas: %s", paso, veredicto.motivo)
            if rechazos >= 4:
                return _resultado("fallida", f"No se completó la tarea: las guardas de seguridad rechazaron 4 acciones seguidas ({veredicto.motivo}).")
            continue
        rechazos = 0

        a = decision.accion
        if a.tipo == "terminar":
            memoria.aprender(app, decision.aprendizajes, secretos)
            resumen, respuesta = resumen_terminada(a.resumen, a.verificado, a.respuesta)
            return _resultado("terminada", resumen, respuesta)
        if a.tipo == "fallar":
            memoria.aprender(app, decision.aprendizajes, secretos)
            return _resultado("fallida", resumen_fallida(a.razon, a.datos))

        commit = guardas.es_commit(decision, elementos)
        try:
            desc = await _ejecutar(vc, a, elementos)
        except ValueError as exc:
            historial.append(f"Paso {paso}: propusiste {_describir(decision)} → NO SE EJECUTÓ: {exc}")
            continue
        guardas.registrar(decision, elementos)
        progreso.pasos, progreso.ultimo = paso, desc
        log.info("paso %d: %s%s", paso, redactar(desc, secretos), " [commit]" if commit else "")

        nueva = await vc.screenshot()
        cambio = changed_pixels(img, nueva)
        estado = "la pantalla cambió" if cambio > PIXELES_CAMBIO else "la pantalla NO cambió"
        historial.append(
            f"Paso {paso}: {desc}{' [guardó/creó/exportó]' if commit else ''} → {estado}. "
            f"Antes habías visto: {decision.observacion[:160]}"
        )
        sin_cambio = sin_cambio + 1 if cambio <= PIXELES_CAMBIO and a.tipo != "esperar" else 0
        if sin_cambio >= 3:
            avisos.append("tus últimas 3 acciones no cambiaron la pantalla: cambia de estrategia.")
        img = nueva

    return _resultado("fallida", f"No se completó la tarea: se alcanzó el límite de {max_pasos} pasos. Último avance: {progreso.ultimo}.")


async def run_task_async(
    tarea: str,
    vnc_url: str,
    vnc_password: str,
    *,
    limite_s: float | None = None,
    max_pasos: int | None = None,
    llm: LLM | None = None,
    memoria: MemoriaApps | None = None,
    controller_factory: Callable[[], VisualController] | None = None,
    percibir: Callable = detectar,
    margen_s: float = MARGEN_S,
    llm_timeout_s: float = 60.0,
    solo_lectura: bool = False,
    solo_observar: bool = False,
) -> dict:
    """Ejecuta una tarea de principio a fin. Siempre cierra la conexión VNC antes de devolver.

    Devuelve {"estado": "terminada"|"fallida", "resumen": str, "respuesta": str|None,
              "pasos": int, "duracion_s": float, "modelo": str|None, "costo_usd": float|None}.
    Si la tarea (asyncio) se cancela desde fuera, también se cierra la conexión.
    """
    inicio = time.monotonic()
    limite = limite_s or float(os.environ.get("AGENT_TIME_LIMIT_S") or LIMITE_POR_DEFECTO_S)
    deadline = inicio + limite
    secretos = [vnc_password, *secretos_del_entorno()]
    progreso = _Progreso()
    memoria = memoria if memoria is not None else MemoriaApps.desde_entorno()
    llm_propio = llm is None
    vc: VisualController | None = None
    resultado: dict

    try:
        if llm is None:
            try:
                llm = OpenRouterLLM.desde_entorno()
            except LLMError as exc:
                return _final(_resultado("fallida", f"No se completó la tarea: {exc}."), inicio, progreso, None, secretos)

        vc = (controller_factory or VisualController)()
        try:
            await asyncio.wait_for(vc.connect(vnc_url, vnc_password), timeout=min(30.0, limite))
        except AuthError:
            resultado = _resultado("fallida", "No se completó la tarea: el escritorio rechazó la contraseña VNC.")
            return _final(resultado, inicio, progreso, llm, secretos)
        except Exception as exc:
            resultado = _resultado("fallida", f"No se completó la tarea: no se pudo conectar al escritorio ({type(exc).__name__}).")
            return _final(resultado, inicio, progreso, llm, secretos)

        try:
            resultado = await asyncio.wait_for(
                _ciclo(
                    tarea=tarea, vc=vc, llm=llm, memoria=memoria, deadline=deadline, margen_s=margen_s,
                    max_pasos=max_pasos or MAX_PASOS_POR_DEFECTO, percibir=percibir, progreso=progreso,
                    secretos=secretos, llm_timeout_s=llm_timeout_s, solo_lectura=solo_lectura, solo_observar=solo_observar,
                ),
                timeout=max(1.0, deadline - time.monotonic()),
            )
        except asyncio.TimeoutError:
            resultado = _resultado(
                "fallida",
                f"No se completó la tarea: se alcanzó el límite de {int(limite)} s. Se detuvo toda interacción "
                f"con el escritorio. Último avance: {progreso.ultimo}.",
            )
        except (ConnectionError, OSError) as exc:
            resultado = _resultado("fallida", f"No se completó la tarea: se perdió la conexión con el escritorio ({type(exc).__name__}).")
        return _final(resultado, inicio, progreso, llm, secretos)
    finally:
        if vc is not None:
            await vc.close()
        if llm_propio and isinstance(llm, OpenRouterLLM):
            await llm.cerrar()


def _final(resultado: dict, inicio: float, progreso: _Progreso, llm, secretos: list[str]) -> dict:
    resultado["resumen"] = redactar(resultado["resumen"], secretos)
    if resultado.get("respuesta"):
        resultado["respuesta"] = redactar(resultado["respuesta"], secretos)
    if resultado["estado"] != "terminada":
        resultado["respuesta"] = None
    resultado["pasos"] = progreso.pasos
    resultado["duracion_s"] = round(time.monotonic() - inicio, 1)
    resultado["modelo"] = getattr(llm, "modelo", None)
    costo = getattr(llm, "costo_usd", None)
    resultado["costo_usd"] = round(costo, 4) if costo is not None else None
    log.info("tarea %s en %.1f s, %d pasos", resultado["estado"], resultado["duracion_s"], progreso.pasos)
    return resultado


def run_task(tarea: str, vnc_url: str, vnc_password: str, **kwargs) -> dict:
    """Versión síncrona. No llamar desde dentro de un event loop (en FastAPI usa run_task_async)."""
    return asyncio.run(run_task_async(tarea, vnc_url, vnc_password, **kwargs))
