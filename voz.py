"""Herramientas webhook para el asistente de voz (ElevenLabs Agents + Twilio).

Separadas del contrato oficial de Frontier:
- Rutas propias (/voz/...), token propio (VOICE_TOOL_TOKEN).
- El modelo de voz solo envía la instrucción o el task_id. Las credenciales VNC las agrega el
  servidor desde sus variables de entorno (VOZ_VNC_URL, VOZ_VNC_PASSWORD) y nunca se devuelven.
- Las respuestas son frases cortas para leer en voz alta, y dicen claramente si la tarea sigue en
  curso, para que el asistente no invente que terminó.
"""

from __future__ import annotations

import os
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

router = APIRouter(prefix="/voz", tags=["voz"])
_esquema = HTTPBearer(auto_error=False)


def verificar_token_voz(cred: HTTPAuthorizationCredentials | None = Security(_esquema)) -> bool:
    esperado = os.getenv("VOICE_TOOL_TOKEN")
    if not esperado:
        raise HTTPException(status_code=503, detail="Herramientas de voz no configuradas")
    if cred is None or not secrets.compare_digest(cred.credentials, esperado):
        raise HTTPException(status_code=401, detail="No autorizado", headers={"WWW-Authenticate": "Bearer"})
    return True


class CrearTareaVoz(BaseModel):
    tarea: str = Field(min_length=3, max_length=500, description="Instrucción en lenguaje natural")


class ConsultarTareaVoz(BaseModel):
    task_id: str = Field(min_length=8, max_length=64)


def _para_voz(texto: str) -> str:
    """Quita la línea técnica 'respuesta:' y la parte de verificación para leerlo en voz alta."""
    texto = re.sub(r"(?im)^\s*respuesta\s*:.*$", "", texto or "")
    texto = texto.split(" Verificación:")[0]
    return " ".join(texto.split())[:600]


@router.post("/crear_tarea", dependencies=[Depends(verificar_token_voz)])
async def crear_tarea_voz(body: CrearTareaVoz, request: Request):
    url, password = os.getenv("VOZ_VNC_URL"), os.getenv("VOZ_VNC_PASSWORD")
    if not url or not password:
        raise HTTPException(status_code=503, detail="Escritorio para voz no configurado")
    solo_lectura = os.getenv("VOZ_SOLO_LECTURA", "1") != "0"
    task_id = request.app.state.gestor.crear(body.tarea, url, password, solo_lectura=solo_lectura)
    return {
        "task_id": task_id,
        "estado": "en_curso",
        "mensaje": "Recibí la tarea y ya estoy trabajando en ella. Puede tardar de uno a cinco minutos. "
                   "Todavía no está terminada.",
    }


@router.post("/consultar_tarea", dependencies=[Depends(verificar_token_voz)])
async def consultar_tarea_voz(body: ConsultarTareaVoz, request: Request):
    reg = request.app.state.gestor.consultar(body.task_id)
    if reg is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if reg["estado"] == "en_curso":
        return {"estado": "en_curso", "terminada": False,
                "mensaje": "Sigo trabajando en la tarea; todavía no hay resultado."}
    exito = reg["estado"] == "terminada"
    mensaje = _para_voz(reg["resumen"])
    if exito and reg.get("respuesta"):
        mensaje = f"La respuesta es {reg['respuesta']}. {mensaje}"
    return {"estado": reg["estado"], "terminada": True, "exito": exito, "respuesta": reg.get("respuesta"),
            "mensaje": mensaje}
