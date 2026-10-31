# Asistente de voz: ElevenLabs Agents + Twilio

Flujo: persona llama al número de Twilio → agente conversacional de ElevenLabs (español) → herramienta webhook `crear_tarea` → nuestro servidor lanza el agente visual → herramienta `consultar_tarea` → ElevenLabs lee el resultado.

El modelo de voz **solo** envía la instrucción o el `task_id`. Las credenciales del escritorio están en las variables del servidor (`VOZ_VNC_URL`, `VOZ_VNC_PASSWORD`) y nunca se devuelven.

## 1. Token de las herramientas

Genera un token largo en tu terminal. No lo pegues en ningún chat.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Ponlo en **dos** lugares:
- **Railway**, como variable `VOICE_TOOL_TOKEN`.
- **ElevenLabs**, como secreto (*Secrets*) para el header de las herramientas.

## 2. Agente en ElevenLabs

Agents → crear agente en blanco.

- **Idioma:** español.
- **Primer mensaje:** `Hola, soy el asistente de operaciones de SIGECOM. ¿Qué necesitas que haga?`
- **Prompt del sistema:**

```
Eres un asistente telefónico en español que opera el sistema SIGECOM mediante un agente visual.
- Cuando la persona pida algo, repite la tarea en una frase y pide confirmación antes de enviarla.
- Envía la tarea con la herramienta crear_tarea, con la instrucción completa y precisa (nombres, números, fechas tal como los dijo la persona).
- Después de crearla, di que estás trabajando y que puede tardar de uno a cinco minutos.
- Consulta el avance con consultar_tarea usando el task_id. Si "terminada" es false, di que sigues trabajando y vuelve a consultar en unos 20 segundos. NUNCA digas que la tarea terminó si la herramienta no lo indica.
- Cuando termine, lee el campo "mensaje". Si "exito" es false, explica el motivo sin inventar.
- No pidas ni menciones contraseñas, tokens o datos de acceso.
```

## 3. Herramientas webhook (Tools → Add tool → Webhook)

| | `crear_tarea` | `consultar_tarea` |
|---|---|---|
| Método | POST | POST |
| URL | `https://<tu-servicio>.up.railway.app/voz/crear_tarea` | `https://<tu-servicio>.up.railway.app/voz/consultar_tarea` |
| Header | `Authorization: Bearer <secreto VOICE_TOOL_TOKEN>` | igual |
| Body | `tarea` (string): "Instrucción completa para operar SIGECOM" | `task_id` (string): "Identificador que devolvió crear_tarea" |
| Descripción | "Envía una tarea al agente que opera SIGECOM. Devuelve task_id; la tarea NO está terminada al crearla." | "Consulta si la tarea terminó y su resultado." |

## 4. Número de teléfono (integración nativa con Twilio)

ElevenLabs → Phone Numbers → Import → Twilio. Ingresa el número, el Account SID y el Auth Token de Twilio (están en la consola de Twilio), y asigna el agente del paso 2.

## 5. Variables del servidor para voz (Railway)

```
VOICE_TOOL_TOKEN=<el del paso 1>
VOZ_VNC_URL=wss://<tu escritorio>/websockify
VOZ_VNC_PASSWORD=<contraseña del escritorio>
VOZ_SOLO_LECTURA=1        # 0 para permitir que la voz guarde o cree registros
```

## Prueba sin teléfono

```bash
curl -s -X POST https://<tu-servicio>.up.railway.app/voz/crear_tarea \
  -H "Authorization: Bearer $VOICE_TOOL_TOKEN" -H "Content-Type: application/json" \
  -d '{"tarea":"¿Qué fecha muestra la aplicación como hoy?"}'
```
