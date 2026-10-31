"""Formato de las decisiones que devuelve el modelo y su validación."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

TIPOS = ("clic", "doble_clic", "clic_derecho", "escribir", "tecla", "desplazar", "esperar", "terminar", "fallar")


class Accion(BaseModel):
    tipo: Literal["clic", "doble_clic", "clic_derecho", "escribir", "tecla", "desplazar", "esperar", "terminar", "fallar"]
    elemento: int | None = None
    x: int | None = None
    y: int | None = None
    texto: str | None = None
    enter: bool = False
    tecla: str | None = None
    cantidad: int | None = None
    segundos: float | None = None
    es_commit: bool = False
    reintento_justificado: str | None = None
    # terminar
    resumen: str | None = None
    verificado: str | None = None
    respuesta: list[str] | None = None
    # fallar
    razon: str | None = None
    datos: list[str] | None = None

    @field_validator("respuesta", "datos", mode="before")
    @classmethod
    def _a_lista(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, (str, int, float)):
            return [str(v)]
        return [str(i) for i in v if str(i).strip()]


class Decision(BaseModel):
    app: str | None = None
    observacion: str = ""
    plan: str = ""
    accion: Accion
    aprendizajes: list[str] = Field(default_factory=list)

    @field_validator("aprendizajes", mode="before")
    @classmethod
    def _aprendizajes(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(i) for i in v]


class DecisionInvalida(ValueError):
    pass


def _extraer_json(texto: str) -> dict:
    texto = texto.strip()
    bloque = re.search(r"```(?:json)?\s*(\{.*\})\s*```", texto, re.S)
    if bloque:
        texto = bloque.group(1)
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        raise DecisionInvalida("la respuesta no contiene un objeto JSON")
    try:
        return json.loads(texto[inicio : fin + 1])
    except json.JSONDecodeError as exc:
        raise DecisionInvalida(f"JSON mal formado: {exc.msg}") from exc


def parse_decision(texto: str) -> Decision:
    data = _extraer_json(texto)
    if "accion" not in data and "tipo" in data:  # el modelo puso la acción en el nivel superior
        data = {"accion": data}
    try:
        return Decision.model_validate(data)
    except ValidationError as exc:
        primer = exc.errors()[0]
        raise DecisionInvalida(f"campo {'.'.join(map(str, primer['loc']))}: {primer['msg']}") from exc
