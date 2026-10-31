import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from gestor import GestorTareas
from seguridad import verificar_token
from voz import router as router_voz

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.gestor = GestorTareas()
    yield


# Creamos nuestro servidor
app = FastAPI(title="Frontier 2026 - Reto 2", lifespan=lifespan)

# Herramientas del asistente de voz: rutas y token propios, independientes del contrato de Frontier
app.include_router(router_voz)


# Endpoint para comprobar que funciona
@app.get("/health")
def health():
    return {"status": "ok", "ok": True}


# --- Recepción de tareas de Frontier ---

# Datos para conectarse al escritorio remoto
class Escritorio(BaseModel):
    url: str = Field(min_length=1)
    password: str


# Instrucción que recibimos de Frontier
class SolicitudTarea(BaseModel):
    tarea: str = Field(min_length=1)
    escritorio: Escritorio


@app.post("/tasks", dependencies=[Depends(verificar_token)])
async def crear_tarea(solicitud: SolicitudTarea, request: Request):
    # La tarea corre en segundo plano: respondemos de inmediato con el task_id
    task_id = request.app.state.gestor.crear(
        solicitud.tarea, solicitud.escritorio.url, solicitud.escritorio.password
    )
    return {"task_id": task_id}


# --- Consultar el estado de una tarea ---

@app.get("/tasks/{task_id}", dependencies=[Depends(verificar_token)])
async def consultar_tarea(task_id: str, request: Request):
    registro = request.app.state.gestor.consultar(task_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return {"task_id": task_id, **registro}
