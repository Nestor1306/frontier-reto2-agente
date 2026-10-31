"""Guardas deterministas: reglas en código que el modelo no puede saltarse."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .perception import Elemento
from .schema import Decision

DESTRUCTIVAS = ("eliminar", "borrar", "suprimir", "anular", "dar de baja", "desactivar", "delete", "remove")
COMMIT_PALABRAS = ("guardar", "grabar", "exportar", "registrar", "save", "export")


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip()


@dataclass
class Veredicto:
    ok: bool
    motivo: str = ""


@dataclass
class Guardas:
    tarea: str
    solo_lectura: bool = False
    solo_observar: bool = False
    commits: dict[str, int] = field(default_factory=dict)
    commits_totales: int = 0

    def __post_init__(self):
        t = normalizar(self.tarea)
        self._tarea_destructiva = any(p in t for p in DESTRUCTIVAS)

    def objetivo(self, decision: Decision, elementos: list[Elemento]) -> str:
        a = decision.accion
        if a.elemento is not None:
            for e in elementos:
                if e.id == a.elemento:
                    return normalizar(e.texto)
        return ""

    def es_commit(self, decision: Decision, elementos: list[Elemento]) -> bool:
        if decision.accion.es_commit:
            return True
        if decision.accion.tipo not in ("clic", "doble_clic"):
            return False
        etiqueta = self.objetivo(decision, elementos)
        return any(etiqueta.startswith(p) for p in COMMIT_PALABRAS)

    def revisar(self, decision: Decision, elementos: list[Elemento]) -> Veredicto:
        a = decision.accion
        etiqueta = self.objetivo(decision, elementos)

        if a.tipo in ("clic", "doble_clic", "clic_derecho") and etiqueta and not self._tarea_destructiva:
            if any(p in etiqueta for p in DESTRUCTIVAS):
                return Veredicto(False, f'acción bloqueada: "{etiqueta}" es destructiva y la tarea no lo pide')

        if self.solo_observar and a.tipo not in ("esperar", "terminar", "fallar"):
            return Veredicto(False, "modo solo observar: no se permiten clics, teclas, escritura ni desplazamiento. "
                                    "Responde con lo que ves en pantalla o usa 'fallar' si el dato no es visible")

        if self.solo_lectura and self.es_commit(decision, elementos):
            return Veredicto(False, "modo solo lectura: no se permite guardar, crear ni exportar. Si la tarea lo exige, usa 'fallar'")

        if self.es_commit(decision, elementos):
            clave = etiqueta or f"{a.tipo}:{a.x},{a.y}:{a.tecla or ''}"
            if self.commits.get(clave, 0) >= 1 and not (a.reintento_justificado or "").strip():
                return Veredicto(
                    False,
                    f'ya ejecutaste "{clave}" (guarda/crea/exporta). Repetirlo puede duplicar. '
                    "Primero verifica en pantalla si se aplicó; si de verdad no se aplicó, explica por qué en reintento_justificado",
                )

        if a.tipo == "terminar" and not (a.verificado or "").strip():
            return Veredicto(False, "antes de terminar comprueba el resultado en la aplicación y describe la evidencia en 'verificado'")

        if a.tipo == "fallar" and not (a.razon or "").strip():
            return Veredicto(False, "explica en 'razon' por qué la tarea no se puede o no se debe hacer")

        return Veredicto(True)

    def registrar(self, decision: Decision, elementos: list[Elemento]) -> None:
        if self.es_commit(decision, elementos):
            a = decision.accion
            clave = self.objetivo(decision, elementos) or f"{a.tipo}:{a.x},{a.y}:{a.tecla or ''}"
            self.commits[clave] = self.commits.get(clave, 0) + 1
            self.commits_totales += 1
