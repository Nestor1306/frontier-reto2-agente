"""Controlador visual: lo único que el agente necesita para "ver" y "tocar" el escritorio.

Reglas del escritorio que este módulo cumple por su cuenta:
- Los clics se mantienen presionados >= 120 ms (los de menos de 100 ms se ignoran).
- `screenshot()` espera a que la pantalla deje de cambiar después de la última acción
  (repintar puede tardar hasta 1 s; una captura inmediata trae la pantalla vieja).
- `replace_text()` selecciona todo (Ctrl+A) antes de escribir (escribir encima concatena).
- `close()` corta la conexión; cualquier acción posterior lanza error en lugar de llegar al escritorio.
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

from PIL import Image, ImageChops

from .keys import keysym_for_char, parse_combo
from .rfb import RFBConnection

log = logging.getLogger("visual")

BUTTONS = {"left": 1, "middle": 2, "right": 4}
WHEEL_UP, WHEEL_DOWN = 8, 16


def changed_pixels(a: Image.Image, b: Image.Image, threshold: int = 24) -> int:
    """Cuántos píxeles cambiaron de forma visible entre dos capturas."""
    if a.size != b.size:
        return a.size[0] * a.size[1]
    diff = ImageChops.difference(a.convert("L"), b.convert("L"))
    return diff.point(lambda p: 255 if p > threshold else 0).histogram()[255]


class ControllerClosed(RuntimeError):
    """Se intentó usar el controlador después de cerrarlo."""


class VisualController:
    def __init__(
        self,
        *,
        click_hold: float = 0.15,
        settle_min: float = 1.0,
        settle_interval: float = 0.3,
        settle_timeout: float = 4.0,
        stable_pixels: int = 60,
        type_delay: float = 0.02,
    ) -> None:
        if click_hold < 0.1:
            raise ValueError("click_hold debe ser >= 0.1 s: el escritorio ignora clics más cortos")
        self.click_hold = click_hold
        self.settle_min = settle_min
        self.settle_interval = settle_interval
        self.settle_timeout = settle_timeout
        self.stable_pixels = stable_pixels
        self.type_delay = type_delay
        self._rfb: RFBConnection | None = None
        self._last_action = 0.0
        self._closed = False

    # ------------------------------------------------------------------ ciclo de vida
    async def connect(self, url: str, password: str, timeout: float = 15.0) -> None:
        log.info("conectando a %s", urlparse(url).hostname)  # nunca se registra la contraseña
        rfb = RFBConnection()
        await rfb.open(url, password, timeout=timeout)
        self._rfb = rfb
        log.info("conectado: %sx%s", rfb.width, rfb.height)

    async def close(self) -> None:
        self._closed = True
        if self._rfb is not None:
            await self._rfb.close()

    async def __aenter__(self) -> "VisualController":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @property
    def size(self) -> tuple[int, int]:
        r = self._live()
        return r.width, r.height

    def _live(self) -> RFBConnection:
        if self._closed or self._rfb is None or self._rfb.closed:
            raise ControllerClosed("el controlador visual está cerrado")
        if not self._rfb.alive:
            raise ConnectionError("se perdió la conexión con el escritorio")
        return self._rfb

    def _check_point(self, x: int, y: int) -> tuple[int, int]:
        w, h = self.size
        x, y = int(round(x)), int(round(y))
        if not (0 <= x < w and 0 <= y < h):
            raise ValueError(f"coordenada fuera de la pantalla: ({x}, {y}) en {w}x{h}")
        return x, y

    def _mark_action(self) -> None:
        self._last_action = time.monotonic()

    # ------------------------------------------------------------------ ver
    def snapshot(self) -> Image.Image:
        """Copia inmediata del framebuffer (puede no reflejar todavía la última acción)."""
        return self._live().framebuffer.copy()

    async def screenshot(self) -> Image.Image:
        """Captura estable: espera >= settle_min desde la última acción y a que la pantalla deje de cambiar."""
        since = time.monotonic() - self._last_action
        if since < self.settle_min:
            await asyncio.sleep(self.settle_min - since)
        prev = self.snapshot()
        deadline = time.monotonic() + self.settle_timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(self.settle_interval)
            cur = self.snapshot()
            if changed_pixels(prev, cur) <= self.stable_pixels:
                return cur
            prev = cur
        log.info("la pantalla no se estabilizó en %.1f s; se usa la última captura", self.settle_timeout)
        return prev

    # ------------------------------------------------------------------ mouse
    async def move(self, x: int, y: int) -> None:
        x, y = self._check_point(x, y)
        await self._live().pointer(x, y, 0)
        self._mark_action()

    async def click(self, x: int, y: int, *, button: str = "left", double: bool = False) -> None:
        if button not in BUTTONS:
            raise ValueError(f"botón desconocido: {button}")
        x, y = self._check_point(x, y)
        rfb, mask = self._live(), BUTTONS[button]
        await rfb.pointer(x, y, 0)
        await asyncio.sleep(0.05)
        for i in range(2 if double else 1):
            if i:
                await asyncio.sleep(0.08)
            await rfb.pointer(x, y, mask)
            await asyncio.sleep(self.click_hold if not double else 0.12)
            await rfb.pointer(x, y, 0)
        self._mark_action()

    async def scroll(self, x: int, y: int, amount: int) -> None:
        """amount > 0 baja, < 0 sube (una unidad = un paso de rueda)."""
        x, y = self._check_point(x, y)
        rfb = self._live()
        mask = WHEEL_DOWN if amount > 0 else WHEEL_UP
        for _ in range(abs(int(amount))):
            await rfb.pointer(x, y, mask)
            await asyncio.sleep(0.03)
            await rfb.pointer(x, y, 0)
            await asyncio.sleep(0.05)
        self._mark_action()

    # ------------------------------------------------------------------ teclado
    async def key(self, combo: str) -> None:
        """Teclas o combinaciones: 'enter', 'tab', 'escape', 'ctrl+a', 'shift+tab', 'pagedown'..."""
        mods, main = parse_combo(combo)
        rfb = self._live()
        for m in mods:
            await rfb.key_event(m, True)
        await rfb.key_event(main, True)
        await asyncio.sleep(0.03)
        await rfb.key_event(main, False)
        for m in reversed(mods):
            await rfb.key_event(m, False)
        self._mark_action()

    async def type_text(self, text: str) -> None:
        """Escribe el texto en el foco actual, tal cual (no borra lo que ya había)."""
        rfb = self._live()
        for ch in text:
            sym = keysym_for_char(ch)
            await rfb.key_event(sym, True)
            await rfb.key_event(sym, False)
            await asyncio.sleep(self.type_delay)
        self._mark_action()

    async def replace_text(self, text: str, x: int | None = None, y: int | None = None) -> None:
        """Reemplaza el contenido del campo: clic opcional en (x, y), Ctrl+A y escribir."""
        if (x is None) != (y is None):
            raise ValueError("indica x e y juntos, o ninguno")
        if x is not None:
            await self.click(x, y)
            await asyncio.sleep(0.2)
        await self.key("ctrl+a")
        await asyncio.sleep(0.05)
        if text:
            await self.type_text(text)
        else:
            await self.key("backspace")
