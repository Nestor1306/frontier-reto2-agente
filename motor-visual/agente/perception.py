"""Percepción: detecta los textos visibles con OCR (Tesseract) y les da un id con coordenadas exactas.

El modelo elige "clic en el elemento 12" y nosotros calculamos el centro: los clics no dependen de
que el modelo adivine píxeles. Si Tesseract no está instalado, devuelve una lista vacía y el modelo
usa coordenadas sobre la imagen.
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
from dataclasses import dataclass

from PIL import Image

log = logging.getLogger("agente.percepcion")


@dataclass(frozen=True)
class Elemento:
    id: int
    texto: str
    x: int  # esquina superior izquierda
    y: int
    w: int
    h: int

    @property
    def centro(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


@functools.cache
def _tesseract():
    if not shutil.which("tesseract"):
        log.warning("Tesseract no está instalado: el agente trabajará solo con coordenadas")
        return None
    try:
        import pytesseract

        langs = set(pytesseract.get_languages(config=""))
    except Exception:
        return None
    lang = "+".join(l for l in ("spa", "eng") if l in langs) or "eng"
    return pytesseract, lang


def ocr_disponible() -> bool:
    return _tesseract() is not None


def detectar(img: Image.Image, max_elementos: int = 220) -> list[Elemento]:
    tess = _tesseract()
    if tess is None:
        return []
    pytesseract, lang = tess
    escala = float(os.environ.get("OCR_ESCALA", "2"))
    gris = img.convert("L")
    if escala != 1:
        gris = gris.resize((int(img.width * escala), int(img.height * escala)), Image.Resampling.LANCZOS)
    data = pytesseract.image_to_data(
        gris, lang=lang, config="--psm 11", output_type=pytesseract.Output.DICT
    )

    palabras = []
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not txt or conf < 35:
            continue
        x, y = data["left"][i] / escala, data["top"][i] / escala
        w, h = data["width"][i] / escala, data["height"][i] / escala
        palabras.append([x, y, w, h, txt])

    # 1) Agrupa palabras en líneas por su centro vertical.
    palabras.sort(key=lambda p: p[1] + p[3] / 2)
    lineas: list[list] = []
    for p in palabras:
        yc = p[1] + p[3] / 2
        if lineas and abs(yc - lineas[-1][0]) < max(p[3], 8) * 0.6:
            lineas[-1][1].append(p)
        else:
            lineas.append([yc, [p]])

    # 2) Dentro de cada línea, une palabras cercanas (mismo botón o etiqueta).
    grupos: list[list] = []
    for _, ps in lineas:
        ps.sort(key=lambda p: p[0])
        actual = list(ps[0])
        for p in ps[1:]:
            if p[0] - (actual[0] + actual[2]) < max(p[3], actual[3]) * 0.9:
                x0, y0 = min(actual[0], p[0]), min(actual[1], p[1])
                x1, y1 = max(actual[0] + actual[2], p[0] + p[2]), max(actual[1] + actual[3], p[1] + p[3])
                actual = [x0, y0, x1 - x0, y1 - y0, actual[4] + " " + p[4]]
            else:
                grupos.append(actual)
                actual = list(p)
        grupos.append(actual)

    grupos.sort(key=lambda g: (round(g[1] / 8), g[0]))
    return [
        Elemento(i, g[4], int(g[0]), int(g[1]), max(1, int(g[2])), max(1, int(g[3])))
        for i, g in enumerate(grupos[:max_elementos])
    ]


def formatear(elementos: list[Elemento]) -> str:
    return "\n".join(f'[{e.id}] "{e.texto}" ({e.centro[0]},{e.centro[1]})' for e in elementos)
