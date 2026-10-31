"""Traducción de teclas a "keysyms" de X11, que es lo que entiende el protocolo VNC."""

NAMED = {
    "enter": 0xFF0D, "return": 0xFF0D,
    "tab": 0xFF09,
    "escape": 0xFF1B, "esc": 0xFF1B,
    "backspace": 0xFF08,
    "delete": 0xFFFF, "supr": 0xFFFF,
    "home": 0xFF50, "inicio": 0xFF50,
    "end": 0xFF57, "fin": 0xFF57,
    "left": 0xFF51, "up": 0xFF52, "right": 0xFF53, "down": 0xFF54,
    "pageup": 0xFF55, "pagedown": 0xFF56,
    "space": 0x0020, "espacio": 0x0020,
    **{f"f{i}": 0xFFBE + i - 1 for i in range(1, 13)},
}

MODIFIERS = {
    "ctrl": 0xFFE3, "control": 0xFFE3,
    "shift": 0xFFE1,
    "alt": 0xFFE9,
    "super": 0xFFEB, "meta": 0xFFEB,
}


def keysym_for_char(ch: str) -> int:
    """Keysym de un carácter. Latin-1 (incluye á, é, ñ, ¿, ¡) se mapea directo; el resto es Unicode."""
    if ch == "\n":
        return NAMED["enter"]
    if ch == "\t":
        return NAMED["tab"]
    code = ord(ch)
    if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF:
        return code
    return 0x01000000 + code


def parse_combo(combo: str) -> tuple[list[int], int]:
    """'ctrl+shift+a' -> ([keysym ctrl, keysym shift], keysym 'a')."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("combinación de teclas vacía")
    *mods, main = parts
    mod_syms = []
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"modificador desconocido: {m}")
        mod_syms.append(MODIFIERS[m])
    if main in NAMED:
        return mod_syms, NAMED[main]
    if main in MODIFIERS:
        return mod_syms, MODIFIERS[main]
    if len(main) == 1:
        return mod_syms, keysym_for_char(main)
    raise ValueError(f"tecla desconocida: {main}")
