"""Pruebas del servidor con un motor simulado (no toca ningún escritorio real)."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import main
from gestor import GestorTareas

TOKEN = "token-equipo-prueba"
VOZ = "token-voz-prueba"
H = {"Authorization": f"Bearer {TOKEN}"}
HV = {"Authorization": f"Bearer {VOZ}"}
BODY = {"tarea": "¿Cuántas facturas?", "escritorio": {"url": "wss://escritorio-a/websockify", "password": "clave-vnc-123"}}


class MotorFalso:
    def __init__(self, demora=0.3, resultado=None):
        self.demora = demora
        self.resultado = resultado or {"estado": "terminada", "resumen": "respuesta: 45\nListo.", "respuesta": "45"}
        self.llamadas = []
        self.canceladas = 0

    async def __call__(self, tarea, url, password, **opciones):
        self.llamadas.append((tarea, url, opciones))
        try:
            await asyncio.sleep(self.demora)
        except asyncio.CancelledError:
            self.canceladas += 1
            raise
        return dict(self.resultado)


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("FRONTIER_TEAM_TOKEN", TOKEN)
    monkeypatch.setenv("VOICE_TOOL_TOKEN", VOZ)
    monkeypatch.setenv("VOZ_VNC_URL", "wss://escritorio-voz/websockify")
    monkeypatch.setenv("VOZ_VNC_PASSWORD", "clave-voz-456")
    with TestClient(main.app) as c:
        yield c


def usar_motor(c, motor, limite=290.0):
    c.app.state.gestor = GestorTareas(motor=motor, limite_duro_s=limite)


def esperar(c, task_id, headers=H, timeout=5):
    fin = time.time() + timeout
    while time.time() < fin:
        r = c.get(f"/tasks/{task_id}", headers=headers).json()
        if r["estado"] != "en_curso":
            return r
        time.sleep(0.05)
    raise AssertionError("la tarea no terminó")


def test_health(cliente):
    assert cliente.get("/health").status_code == 200


def test_post_sin_token_401(cliente):
    assert cliente.post("/tasks", json=BODY).status_code == 401
    assert cliente.post("/tasks", json=BODY, headers={"Authorization": "Bearer otro"}).status_code == 401


def test_post_responde_rapido_y_tarea_termina(cliente):
    usar_motor(cliente, MotorFalso(demora=1.5))
    t0 = time.monotonic()
    r = cliente.post("/tasks", json=BODY, headers=H)
    assert time.monotonic() - t0 < 2
    assert r.status_code == 200
    tid = r.json()["task_id"]
    assert cliente.get(f"/tasks/{tid}", headers=H).json()["estado"] == "en_curso"
    fin = esperar(cliente, tid)
    assert fin["estado"] == "terminada" and fin["resumen"].startswith("respuesta: 45")


def test_get_desconocido_404(cliente):
    assert cliente.get("/tasks/no-existe", headers=H).status_code == 404


def test_misma_tarea_en_mismo_escritorio_cancela_la_anterior(cliente):
    motor = MotorFalso(demora=2)
    usar_motor(cliente, motor)
    t1 = cliente.post("/tasks", json=BODY, headers=H).json()["task_id"]
    time.sleep(0.2)
    t2 = cliente.post("/tasks", json=BODY, headers=H).json()["task_id"]
    r1 = esperar(cliente, t1)
    assert r1["estado"] == "fallida" and "cancel" in r1["resumen"]
    assert motor.canceladas == 1
    assert esperar(cliente, t2)["estado"] == "terminada"


def test_limite_duro_de_tiempo(cliente):
    usar_motor(cliente, MotorFalso(demora=10), limite=0.5)
    tid = cliente.post("/tasks", json=BODY, headers=H).json()["task_id"]
    r = esperar(cliente, tid)
    assert r["estado"] == "fallida" and "límite" in r["resumen"]


def test_resumen_sin_contrasena(cliente):
    usar_motor(cliente, MotorFalso(resultado={"estado": "terminada", "resumen": "usé clave-vnc-123"}))
    tid = cliente.post("/tasks", json=BODY, headers=H).json()["task_id"]
    assert "clave-vnc-123" not in esperar(cliente, tid)["resumen"]


def test_error_del_motor_da_fallida(cliente):
    async def roto(*a, **k):
        raise RuntimeError("boom")
    usar_motor(cliente, roto)
    tid = cliente.post("/tasks", json=BODY, headers=H).json()["task_id"]
    r = esperar(cliente, tid)
    assert r["estado"] == "fallida" and "RuntimeError" in r["resumen"]


def test_voz_usa_su_token_y_credenciales_del_servidor(cliente):
    motor = MotorFalso(demora=0.2)
    usar_motor(cliente, motor)
    assert cliente.post("/voz/crear_tarea", json={"tarea": "hola mundo"}, headers=H).status_code == 401
    r = cliente.post("/voz/crear_tarea", json={"tarea": "¿Qué fecha es hoy?"}, headers=HV)
    assert r.status_code == 200
    body = r.json()
    assert body["estado"] == "en_curso" and "clave" not in str(body)
    tarea, url, opciones = motor.llamadas[0]
    assert url == "wss://escritorio-voz/websockify" and opciones == {"solo_lectura": True}

    c1 = cliente.post("/voz/consultar_tarea", json={"task_id": body["task_id"]}, headers=HV).json()
    assert c1["terminada"] is False
    time.sleep(0.6)
    c2 = cliente.post("/voz/consultar_tarea", json={"task_id": body["task_id"]}, headers=HV).json()
    assert c2["terminada"] is True and c2["respuesta"] == "45"
    assert "respuesta:" not in c2["mensaje"] and c2["mensaje"].startswith("La respuesta es 45")


def test_voz_no_acepta_credenciales_en_argumentos(cliente):
    usar_motor(cliente, MotorFalso())
    r = cliente.post("/voz/crear_tarea", json={"tarea": "x" * 600}, headers=HV)
    assert r.status_code == 422
