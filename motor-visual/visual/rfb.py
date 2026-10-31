"""Cliente mínimo del protocolo VNC (RFB) sobre WebSocket (websockify).

Implementa solo lo necesario:
- Versiones 3.3 / 3.7 / 3.8, seguridad "None" (1) y "VNC Authentication" (2, DES).
- Formato de píxel 32 bits true color, codificaciones Raw y CopyRect (+ DesktopSize).
- Eventos de puntero y teclado.

Un hilo de lectura (tarea asyncio) recibe actualizaciones de pantalla y mantiene una copia
local del framebuffer; justo después de cada actualización pide la siguiente (incremental),
así la copia siempre está fresca y una captura no cuesta un viaje de red.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time

from Crypto.Cipher import DES
from PIL import Image
from websockets.asyncio.client import connect as ws_connect
from websockets.typing import Subprotocol

log = logging.getLogger("visual.rfb")

ENC_RAW = 0
ENC_COPYRECT = 1
ENC_DESKTOPSIZE = -223

SEC_NONE = 1
SEC_VNC = 2


class RFBError(Exception):
    """Error de protocolo o de conexión VNC."""


class AuthError(RFBError):
    """El servidor rechazó la contraseña o no ofrece un método de autenticación soportado."""


def vnc_auth_response(password: str, challenge: bytes) -> bytes:
    """Respuesta al reto de VNC Authentication: DES con la contraseña (8 bytes, bits invertidos)."""
    key = password.encode("latin-1", errors="replace")[:8].ljust(8, b"\0")
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)
    return DES.new(key, DES.MODE_ECB).encrypt(challenge)


class _Stream:
    """Lee N bytes exactos aunque lleguen repartidos en varios mensajes WebSocket."""

    def __init__(self, ws):
        self._ws = ws
        self._buf = bytearray()
        self._pos = 0

    async def read(self, n: int) -> bytes:
        while len(self._buf) - self._pos < n:
            msg = await self._ws.recv()
            if isinstance(msg, str):
                msg = msg.encode("latin-1")
            if self._pos and self._pos > len(self._buf) // 2:
                del self._buf[: self._pos]
                self._pos = 0
            self._buf += msg
        out = bytes(self._buf[self._pos : self._pos + n])
        self._pos += n
        return out


class RFBConnection:
    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.name = ""
        self.framebuffer: Image.Image | None = None
        self.updates = 0
        self.last_update = 0.0
        self._ws = None
        self._stream: _Stream | None = None
        self._reader: asyncio.Task | None = None
        self._first_frame = asyncio.Event()
        self._error: BaseException | None = None
        self._closed = False

    # ------------------------------------------------------------------ conexión
    async def open(self, url: str, password: str, timeout: float = 15.0) -> None:
        try:
            self._ws = await ws_connect(
                url,
                subprotocols=[Subprotocol("binary")],
                max_size=None,
                compression=None,
                open_timeout=timeout,
                close_timeout=2,
            )
            self._stream = _Stream(self._ws)
            await asyncio.wait_for(self._handshake(password), timeout)
            self._reader = asyncio.create_task(self._read_loop())
            await self._request_update(incremental=False)
            await asyncio.wait_for(self._first_frame.wait(), timeout)
        except BaseException:
            await self.close()
            raise

    async def _handshake(self, password: str) -> None:
        s = self._stream
        banner = await s.read(12)
        if not banner.startswith(b"RFB "):
            raise RFBError("el servidor no habla RFB")
        major, minor = int(banner[4:7]), int(banner[8:11])
        if major != 3:
            raise RFBError(f"versión RFB no soportada: {major}.{minor}")
        minor = 8 if minor >= 8 else (7 if minor >= 7 else 3)
        await self._send(f"RFB 003.00{minor}\n".encode())

        if minor == 3:
            (sec,) = struct.unpack(">I", await s.read(4))
            if sec == 0:
                raise RFBError(await self._read_reason())
            offered = [sec]
            chosen = sec
        else:
            (n,) = struct.unpack(">B", await s.read(1))
            if n == 0:
                raise RFBError(await self._read_reason())
            offered = list(await s.read(n))
            if SEC_VNC in offered and (password or SEC_NONE not in offered):
                chosen = SEC_VNC
            elif SEC_NONE in offered:
                chosen = SEC_NONE
            else:
                raise AuthError(f"tipos de seguridad no soportados: {offered}")
            await self._send(bytes([chosen]))

        if chosen == SEC_VNC:
            challenge = await s.read(16)
            await self._send(vnc_auth_response(password, challenge))
        elif chosen != SEC_NONE:
            raise AuthError(f"tipo de seguridad no soportado: {chosen}")

        if chosen == SEC_VNC or minor == 8:
            (result,) = struct.unpack(">I", await s.read(4))
            if result != 0:
                reason = await self._read_reason() if minor == 8 else ""
                raise AuthError(f"autenticación rechazada {reason}".strip())

        await self._send(b"\x01")  # ClientInit: compartido (no expulsa a otros visores)
        w, h = struct.unpack(">HH", await s.read(4))
        await s.read(16)  # formato de píxel del servidor (lo reemplazamos)
        (name_len,) = struct.unpack(">I", await s.read(4))
        self.name = (await s.read(name_len)).decode("utf-8", errors="replace")
        self.width, self.height = w, h
        self.framebuffer = Image.new("RGB", (w, h))

        # 32 bpp, profundidad 24, little-endian, true color; R<<16 G<<8 B -> bytes B,G,R,X
        await self._send(struct.pack(">BxxxBBBBHHHBBBxxx", 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        encs = [ENC_COPYRECT, ENC_RAW, ENC_DESKTOPSIZE]
        await self._send(struct.pack(">BxH", 2, len(encs)) + b"".join(struct.pack(">i", e) for e in encs))

    async def _read_reason(self) -> str:
        (n,) = struct.unpack(">I", await self._stream.read(4))
        return (await self._stream.read(n)).decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ lectura
    async def _read_loop(self) -> None:
        s = self._stream
        try:
            while True:
                (kind,) = await s.read(1)
                if kind == 0:  # FramebufferUpdate
                    await s.read(1)
                    (nrects,) = struct.unpack(">H", await s.read(2))
                    for _ in range(nrects):
                        x, y, w, h, enc = struct.unpack(">HHHHi", await s.read(12))
                        await self._apply_rect(x, y, w, h, enc)
                    if nrects:
                        self.updates += 1
                        self.last_update = time.monotonic()
                    self._first_frame.set()
                    await asyncio.sleep(0.03)  # evita girar en vacío si el servidor responde sin cambios
                    await self._request_update(incremental=True)
                elif kind == 1:  # SetColourMapEntries (no aplica en true color)
                    await s.read(3)
                    (count,) = struct.unpack(">H", await s.read(2))
                    await s.read(count * 6)
                elif kind == 2:  # Bell
                    pass
                elif kind == 3:  # ServerCutText
                    await s.read(3)
                    (n,) = struct.unpack(">I", await s.read(4))
                    await s.read(n)
                else:
                    raise RFBError(f"mensaje de servidor desconocido: {kind}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # conexión caída o error de protocolo
            self._error = exc
            if not self._closed:
                log.warning("la conexión VNC terminó: %s", type(exc).__name__)

    async def _apply_rect(self, x: int, y: int, w: int, h: int, enc: int) -> None:
        s = self._stream
        if enc == ENC_RAW:
            data = await s.read(w * h * 4)
            if w and h:
                img = Image.frombuffer("RGB", (w, h), data, "raw", "BGRX", 0, 1)
                self.framebuffer.paste(img, (x, y))
        elif enc == ENC_COPYRECT:
            sx, sy = struct.unpack(">HH", await s.read(4))
            region = self.framebuffer.crop((sx, sy, sx + w, sy + h))
            self.framebuffer.paste(region, (x, y))
        elif enc == ENC_DESKTOPSIZE:
            self.width, self.height = w, h
            self.framebuffer = Image.new("RGB", (w, h))
        else:
            raise RFBError(f"codificación no soportada: {enc}")

    # ------------------------------------------------------------------ envío
    async def _send(self, data: bytes) -> None:
        if self._closed:
            raise RFBError("la conexión ya está cerrada")
        if self._error is not None:
            raise RFBError(f"la conexión VNC terminó: {type(self._error).__name__}")
        await self._ws.send(data)

    async def _request_update(self, incremental: bool) -> None:
        await self._send(struct.pack(">BBHHHH", 3, 1 if incremental else 0, 0, 0, self.width, self.height))

    async def pointer(self, x: int, y: int, mask: int) -> None:
        await self._send(struct.pack(">BBHH", 5, mask, x, y))

    async def key_event(self, keysym: int, down: bool) -> None:
        await self._send(struct.pack(">BBxxI", 4, 1 if down else 0, keysym))

    # ------------------------------------------------------------------ cierre
    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def alive(self) -> bool:
        return not self._closed and self._error is None

    async def close(self) -> None:
        """Corta la conexión. Idempotente; después no sale ningún evento hacia el escritorio."""
        if self._closed:
            return
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except BaseException:
                pass
        if self._ws is not None:
            try:
                await asyncio.wait_for(self._ws.close(), 3)
            except BaseException:
                transport = getattr(self._ws, "transport", None)
                if transport is not None:
                    transport.abort()
