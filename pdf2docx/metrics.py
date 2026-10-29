"""Mesure de largeur de texte avec la police de remplacement réellement utilisée (fontconfig)."""
import subprocess
from functools import lru_cache

from PIL import ImageFont

_REF = 200  # taille de référence (px) pour une mesure précise


@lru_cache(maxsize=512)
def _font(fam, bold, ital):
    pat = fam + (":bold" if bold else "") + (":italic" if ital else "")
    try:
        path = subprocess.run(["fc-match", pat, "-f", "%{file}"], capture_output=True, text=True, timeout=5).stdout
        return ImageFont.truetype(path, _REF)
    except Exception:
        return None


@lru_cache(maxsize=65536)
def width(fam, bold, ital, size, text):
    f = _font(fam, bold, ital)
    if f is None:
        return None
    return f.getlength(text) * size / _REF
