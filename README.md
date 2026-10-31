# Frontier 2026 · Reto 2: agente que opera lo que no tiene API

Servidor HTTP que recibe tareas en lenguaje natural y las ejecuta en un escritorio remoto (VNC) con un agente visual. El agente mira la pantalla, decide con un modelo de visión, actúa con mouse y teclado, y verifica. No usa APIs internas de la aplicación.

## Contrato (Frontier)

Todas las llamadas llevan `Authorization: Bearer <FRONTIER_TEAM_TOKEN>`.

| Endpoint | Qué hace |
|---|---|
| `GET /health` | 200 cuando el servicio está listo |
| `POST /tasks` | `{"tarea": "...", "escritorio": {"url": "wss://…/websockify", "password": "…"}}` → `{"task_id": "…"}` en milisegundos; la tarea corre en segundo plano |
| `GET /tasks/{task_id}` | `{"task_id", "estado": "en_curso"\|"terminada"\|"fallida", "resumen", "respuesta"}` |

**Garantías:**
- Una sola tarea por escritorio: si llega otra para el mismo escritorio, la anterior se cancela y cierra su conexión.
- Límite interno de 270 s más un límite duro de 290 s. Al terminar, fallar o cancelarse, siempre se cierra la conexión VNC.
- El resumen nunca incluye la contraseña ni otros secretos.

## Arquitectura

```
POST /tasks ─► gestor.py (asyncio, candado por escritorio, límite) ─► agente (motor-visual/agente)
                                                                        │ captura → OCR → modelo (OpenRouter)
                                                                        │ → guardas → acción → verificar
                                                                        ▼
                                                              motor-visual/visual (cliente VNC/RFB propio)
```

- `main.py`: los tres endpoints del contrato.
- `gestor.py`: ejecución en segundo plano, estados, cancelación, límite y borrado de secretos.
- `seguridad.py`: autenticación Bearer.
- `voz.py`: herramientas webhook para el asistente de voz (opcional, independientes del contrato).
- `motor-visual/`: agente (`agente/`) y controlador VNC (`visual/`). Viene de `frontier-visual`, donde está el código fuente con sus pruebas.

## Variables de entorno

| Variable | Obligatoria | Uso |
|---|---|---|
| `FRONTIER_TEAM_TOKEN` | sí | Token del equipo (Bearer) |
| `OPENROUTER_API_KEY` | sí | Modelo de visión |
| `OPENROUTER_MODEL` | no | Por defecto `anthropic/claude-sonnet-5.5` |
| `VOICE_TOOL_TOKEN` | solo voz | Token de las herramientas `/voz/*` (distinto del de Frontier) |
| `VOZ_VNC_URL`, `VOZ_VNC_PASSWORD` | solo voz | Escritorio que opera el asistente de voz |
| `VOZ_SOLO_LECTURA` | no | `1` (por defecto) bloquea guardar, crear y exportar desde la voz; `0` lo permite |

## Correr en local

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt ./motor-visual
brew install tesseract tesseract-lang     # OCR (macOS)
export FRONTIER_TEAM_TOKEN=... OPENROUTER_API_KEY=...
.venv/bin/uvicorn main:app --port 8000
.venv/bin/python -m pytest -q             # pruebas con motor simulado
```

## Docker / Railway

```bash
docker build -t frontier-agente .
docker run -p 8000:8000 -e FRONTIER_TEAM_TOKEN=... -e OPENROUTER_API_KEY=... frontier-agente
```

La imagen incluye Tesseract con español. `.dockerignore` impide que entren `.env` y entornos locales. En Railway: `railway up` desde esta carpeta y configurar las variables en el panel.

## Asistente de voz (opcional)

Llamada a Twilio → agente conversacional de ElevenLabs (en español) → herramientas `POST /voz/crear_tarea` y `POST /voz/consultar_tarea` → el mismo agente visual. El modelo de voz nunca ve credenciales: el servidor agrega las del escritorio. La configuración está en [docs/voz.md](docs/voz.md).
