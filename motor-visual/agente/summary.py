"""Construye el resumen final con el formato que exige el evaluador."""

from __future__ import annotations

import re

LINEA_RESPUESTA = re.compile(r"^\s*respuesta\s*:.*$", re.I | re.M)


def quitar_lineas_respuesta(texto: str) -> str:
    return re.sub(r"\n{2,}", "\n", LINEA_RESPUESTA.sub("", texto or "")).strip()


def formatear_respuesta(valores: list[str] | None) -> str | None:
    """['45'] -> 'respuesta: 45'; ['3', 'Bogotá'] -> 'respuesta: 3; Bogotá'."""
    if not valores:
        return None
    limpios = [" ".join(str(v).replace(";", ",").split()) for v in valores]
    limpios = [v for v in limpios if v]
    return "respuesta: " + "; ".join(limpios) if limpios else None


def resumen_terminada(resumen: str | None, verificado: str | None, respuesta: list[str] | None) -> tuple[str, str | None]:
    linea = formatear_respuesta(respuesta)
    cuerpo = quitar_lineas_respuesta(resumen or "Tarea completada.")
    if verificado:
        cuerpo += f" Verificación: {quitar_lineas_respuesta(verificado)}"
    texto = f"{linea}\n{cuerpo}" if linea else cuerpo
    return texto, (linea[len("respuesta: "):] if linea else None)


def resumen_fallida(razon: str | None, datos: list[str] | None) -> str:
    texto = f"No se completó la tarea: {quitar_lineas_respuesta(razon or 'motivo no especificado')}"
    if datos:
        texto += f" Datos: {', '.join(quitar_lineas_respuesta(d) for d in datos)}."
    return texto
