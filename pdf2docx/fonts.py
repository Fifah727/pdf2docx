"""Normalisation des noms de polices PDF -> polices Word."""
import re

_SUBSET = re.compile(r"^[A-Z]{6}\+")
_SUFFIX = re.compile(r"(PSMT|MT|PS)$")

FONT_MAP = {
    "timesnewroman": "Times New Roman", "times": "Times New Roman", "timesroman": "Times New Roman",
    "timesnewromanps": "Times New Roman", "nimbusromno9l": "Times New Roman", "nimbusrom": "Times New Roman",
    "liberationserif": "Times New Roman", "txfonts": "Times New Roman", "ptserif": "Georgia",
    "arial": "Arial", "helvetica": "Arial", "helv": "Arial", "nimbussanl": "Arial", "nimbussan": "Arial",
    "liberationsans": "Arial", "arialnarrow": "Arial Narrow", "arialunicode": "Arial",
    "courier": "Courier New", "couriernew": "Courier New", "cour": "Courier New", "nimbusmonl": "Courier New",
    "liberationmono": "Courier New", "lucidaconsole": "Courier New",
    "symbol": "Symbol", "symb": "Symbol", "zapfdingbats": "Wingdings", "wingdings": "Wingdings",
    "calibri": "Calibri", "cambria": "Cambria", "cambriamath": "Cambria Math", "georgia": "Georgia",
    "verdana": "Verdana", "tahoma": "Tahoma", "trebuchetms": "Trebuchet MS", "segoeui": "Segoe UI",
    "garamond": "Garamond", "palatino": "Palatino Linotype", "palatinolinotype": "Palatino Linotype",
    "bookantiqua": "Book Antiqua", "centurygothic": "Century Gothic", "comicsansms": "Comic Sans MS",
    "dejavusans": "DejaVu Sans", "dejavuserif": "DejaVu Serif", "dejavusansmono": "DejaVu Sans Mono",
    "consolas": "Consolas", "impact": "Impact", "candara": "Candara", "constantia": "Constantia",
    "corbel": "Corbel", "franklingothic": "Franklin Gothic Book", "opensans": "Open Sans",
    "roboto": "Roboto", "lato": "Lato",
}
# LaTeX / Computer Modern et dérivés : pas de police Word équivalente => serif générique
_CM = re.compile(r"^(cm|lm|ec|tc|rm|sf|qtm|qpl|qbk|qag|qhv|qcr|qzc|qcs|texgyre)", re.I)
_SANS_HINT = re.compile(r"(sans|helv|arial|gothic|grotesk|cmss|lmss|qhv|qag|sf\d)", re.I)


def clean_name(raw: str):
    """Retourne (famille Word, gras, italique) déduits du nom de police PDF."""
    n = _SUBSET.sub("", raw or "")
    low = n.lower()
    bold = any(k in low for k in ("bold", "black", "heavy", "semibold", "demi")) or low.endswith(("-bd", ",bd"))
    ital = any(k in low for k in ("italic", "oblique", "slanted")) or low.endswith(("-it", ",it", "-ital"))
    base = re.split(r"[-,]", n)[0]
    base = _SUFFIX.sub("", base)
    key = re.sub(r"[^a-z0-9]", "", base.lower())
    if key in FONT_MAP:
        return FONT_MAP[key], bold, ital
    # CMBX10, CMTI10, CMSS10 ...
    if _CM.match(key):
        fam = "Arial" if _SANS_HINT.search(key) else "Times New Roman"
        if re.match(r"^(cmbx|cmb)", key):
            bold = True
        if re.match(r"^(cmti|cmsl|cmmi|lmroman\d+italic)", key):
            ital = True
        if key.startswith(("cmtt", "lmmono")):
            fam = "Courier New"
        return fam, bold, ital
    fam = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base).strip() or "Arial"
    return fam, bold, ital




_GENERIC = re.compile(r"^(f|tt|r|c|t|g|font|type|ft|fnt|ttf)\d+$", re.I)
OVERRIDE = {}          # nom de police PDF -> (famille Word, gras, italique) déduit des largeurs mesurées


def is_known(raw):
    n = _SUBSET.sub("", raw or "")
    base = re.split(r"[-,]", n)[0]
    key = re.sub(r"[^a-z0-9]", "", _SUFFIX.sub("", base).lower())
    if key in FONT_MAP or _CM.match(key):
        return True
    return len(re.sub(r"[^A-Za-z]", "", base)) >= 5 and not _GENERIC.match(re.sub(r"[^A-Za-z0-9]", "", base))


def style_of(font: str, flags: int):
    if font in OVERRIDE:
        fam, b, i = OVERRIDE[font]
        return fam, b or bool(flags & 16), i or bool(flags & 2)
    fam, b, i = clean_name(font)
    return fam, bool(flags & 16) or b, bool(flags & 2) or i


def fit_unknown(words_by_font, measure, tex=False):
    """Pour chaque police sans nom exploitable, retient la famille dont les largeurs mesurées collent le mieux."""
    out = {}
    for font, ws in words_by_font.items():
        if is_known(font):
            continue
        sample = [w for w in ws if len(w.text) >= 3 and w.script is None and w.x1 - w.x0 > 4][:400]
        if len(sample) < 5:
            continue
        b = any(w.flags & 16 for w in sample) or "bold" in font.lower()
        i = any(w.flags & 2 for w in sample) or "ital" in font.lower()
        best = None
        candidates = ("Times New Roman", "Courier New") if tex else ("Times New Roman", "Arial", "Courier New")   # pdfTeX : Computer Modern, jamais Arial
        for fam in candidates:
            err = 0.0
            for w in sample:
                m = measure(fam, b, i, w.size, w.text)
                if not m:
                    err = 1e9
                    break
                err += abs(m - (w.x1 - w.x0)) / (w.x1 - w.x0)
            if best is None or err < best[0]:
                best = (err, fam)
        out[font] = (best[1], b, i)
    return out
