"""Extraction native : mots (avec police, taille, ligne de base), lignes, images."""
import re
from statistics import median

import pymupdf

from .model import Word, Line, Img

_CTRL = re.compile("[\x01-\x08\x0b\x0c\x0e-\x1f\ufffd\ue000-\uf8ff]")

_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿­]")


def _raw_flags():
    f = getattr(pymupdf, "TEXTFLAGS_RAWDICT", None)
    if f is None:
        return None
    return f & ~getattr(pymupdf, "TEXT_PRESERVE_LIGATURES", 0)


def mark_scripts(words):
    """Détecte exposants / indices : petit corps + ligne de base décalée."""
    if not words:
        return
    smax = max(w.size for w in words)
    main = [w for w in words if w.size >= 0.9 * smax]
    base = median(w.base for w in main)
    for w in words:
        if w.size <= 0.86 * smax and w.size >= 0.35 * smax:
            if w.base < base - 0.16 * smax:
                w.script = "sup"
            elif w.base > base + 0.08 * smax:
                w.script = "sub"


def _line_words(ln, lid, bad_fonts=frozenset()):
    words = []
    pend = False
    for sp in ln["spans"]:
        size = float(sp["size"]) or 10.0
        font, flags, color = sp["font"], sp["flags"], sp["color"]
        base = float(sp["origin"][1])
        cur = []

        def flush(cur, pend, font=font, size=size, flags=flags, color=color, base=base):
            if not cur:
                return pend
            raw = "".join(c["c"] for c in cur)
            text = _BAD.sub("?", raw) if font in bad_fonts else _BAD.sub("", raw)   # police corrompue : on garde la géométrie
            if text.strip():
                x0 = min(c["bbox"][0] for c in cur)
                y0 = min(c["bbox"][1] for c in cur)
                x1 = max(c["bbox"][2] for c in cur)
                y1 = max(c["bbox"][3] for c in cur)
                if words:
                    sp_before = pend or (x0 - words[-1].x1) > 0.12 * words[-1].size
                else:
                    sp_before = False
                words.append(Word(text, x0, y0, x1, y1, font, size, flags, color, base,
                                  None, bool(sp_before), lid))
                return False
            return pend

        for ch in sp["chars"]:
            c = ch["c"]
            if c.isspace() or c in " ​":
                pend = flush(cur, pend)
                cur = []
                pend = True
                continue
            if cur and ch["bbox"][0] - cur[-1]["bbox"][2] > 0.3 * size:
                pend = flush(cur, pend)
                cur = []
                pend = True
            cur.append(ch)
        pend = flush(cur, pend)
    mark_scripts(words)
    return words


def garbled_fonts(d):
    """Polices dont l'extraction contient trop de caractères de contrôle : table Unicode manquante ou fausse."""
    st = {}
    for b in d["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                tot = bad = 0
                for ch in sp["chars"]:
                    c = ch["c"]
                    if not c.isspace():
                        tot += 1
                        bad += 1 if _CTRL.match(c) else 0
                a = st.setdefault(sp["font"], [0, 0])
                a[0] += bad
                a[1] += tot
    return {f for f, (bad, tot) in st.items() if tot >= 15 and bad / tot > 0.02}


def repair_garbled(page, lines, bad_fonts, cfg):
    """Remplace le texte des lignes écrites dans une police corrompue par celui de l'OCR (même emplacement, même style)."""
    from .ocr import ocr_page_words
    targets = [L for L in lines if sum(len(w.text) for w in L.words if w.font in bad_fonts) > 0.5 * sum(len(w.text) for w in L.words)]
    if not targets:
        return lines, 0
    ow = ocr_page_words(page, cfg)
    out, fixed = [], 0
    tset = set(id(t) for t in targets)
    for L in lines:
        if id(L) not in tset:
            out.append(L)
            continue
        ref = L.words[0]
        pad = 0.25 * L.size
        hit = [o for o in ow if L.x0 - 2 <= (o[1] + o[3]) / 2 <= L.x1 + 2 and L.y0 - pad <= (o[2] + o[4]) / 2 <= L.y1 + pad]
        if not hit:
            continue                     # rien de lisible : on supprime plutôt que d'écrire du charabia
        hit.sort(key=lambda o: o[1])
        words = []
        for k, (t, x0, y0, x1, y1) in enumerate(hit):
            words.append(Word(t, x0, L.y0, x1, L.y1, ref.font, ref.size, ref.flags, ref.color, L.base, None, k > 0, ref.lid))
        out.append(Line(words, L.bid))
        fixed += 1
    return out, fixed


def _crop_visible(b, r, data, ext):
    """Image rognée par un tracé de découpe : bbox = partie visible, mais les données = image entière.
    On recadre les données sur la partie visible (sinon l'image est déformée / sur-dézoomée dans Word)."""
    t = b.get("transform")
    if not t or len(t) < 6:
        return data, ext
    a, bb, c, d, e, f = t
    if abs(bb) > 1e-3 or abs(c) > 1e-3 or a <= 0 or d <= 0:
        return data, ext
    x0, y0, x1, y1 = e, f, e + a, f + d                      # rectangle complet de l'image sur la page
    if r.x0 >= x0 - 0.8 and r.y0 >= y0 - 0.8 and r.x1 <= x1 + 0.8 and r.y1 <= y1 + 0.8 and \
            (r.x0 - x0 > 1.0 or r.y0 - y0 > 1.0 or x1 - r.x1 > 1.0 or y1 - r.y1 > 1.0):
        try:
            import io
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            W, H = im.size
            box = (int(round((r.x0 - x0) / a * W)), int(round((r.y0 - y0) / d * H)),
                   int(round((r.x1 - x0) / a * W)), int(round((r.y1 - y0) / d * H)))
            box = (max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3]))
            if box[2] - box[0] < 2 or box[3] - box[1] < 2:
                return data, ext
            im = im.crop(box)
            buf = io.BytesIO()
            if im.mode in ("RGBA", "LA", "P"):
                im.convert("RGBA").save(buf, "PNG")
                return buf.getvalue(), "png"
            im.convert("RGB").save(buf, "JPEG", quality=92)
            return buf.getvalue(), "jpeg"
        except Exception:
            return data, ext
    return data, ext


def _apply_masks(page, images):
    """Les images à masque de transparence (SMask) sortent de rawdict sans leur masque : fond opaque. On les recompose en PNG RGBA."""
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return
    doc = page.parent
    for im in images:
        r = pymupdf.Rect(im.bbox)
        for inf in infos:
            if not inf.get("xref") or pymupdf.Rect(inf["bbox"]).irect != r.irect and \
                    max(abs(a - b) for a, b in zip(inf["bbox"], im.bbox)) > 1.0:
                continue
            try:
                sm = doc.extract_image(inf["xref"]).get("smask", 0)
                if not sm:
                    break
                pix = pymupdf.Pixmap(doc, inf["xref"])
                if pix.alpha or pix.colorspace is None:
                    break
                if pix.colorspace.n != 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                out = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, sm))
                im.data, im.ext = out.tobytes("png"), "png"
            except Exception:
                pass
            break


def native_lines(page, cfg):
    fl = _raw_flags()
    d = page.get_text("rawdict", flags=fl) if fl is not None else page.get_text("rawdict")
    page_area = page.rect.get_area()
    bad_fonts = garbled_fonts(d) if cfg.ocr != "off" else set()
    lines, images = [], []
    lid = 0
    has_text = False
    for bi, b in enumerate(d["blocks"]):
        if b["type"] == 1:
            data = b.get("image")
            if not data:
                continue
            r = pymupdf.Rect(b["bbox"])
            if r.width < 4 or r.height < 4:
                continue
            data, ext = _crop_visible(b, r, data, b.get("ext", "png"))
            images.append(Img(tuple(r), data, ext))
            continue
        for ln in b.get("lines", []):
            words = _line_words(ln, lid, bad_fonts)
            if words:
                lines.append(Line(words, bid=bi))
                lid += 1
                has_text = True
    _apply_masks(page, images)
    # fonds de page : image plein cadre sous du texte
    if has_text:
        images = [im for im in images if pymupdf.Rect(im.bbox).get_area() < 0.9 * page_area]
    lines = attach_scripts(_merge_same_baseline(lines))
    if bad_fonts:
        lines, n = repair_garbled(page, lines, bad_fonts, cfg)
        native_lines.last_repaired = n
    return lines, images


def attach_scripts(lines):
    """Petites lignes (indices, exposants, limites de somme) rattachées à la ligne principale du même bloc."""
    from .paras import fix_spaces
    keep = list(lines)
    for S in sorted(lines, key=lambda L: L.size):
        cands = [M for M in keep if M is not S and M.bid == S.bid and S.size <= 0.86 * M.size
                 and S.x0 >= M.x0 - 3 and S.x1 <= M.x1 + 3 and abs(S.base - M.base) <= 0.75 * M.size]
        if not cands or S not in keep:
            continue
        M = min(cands, key=lambda m: abs(m.base - S.base))
        for w in S.words:
            w.script = "sup" if S.base < M.base - 0.1 * M.size else "sub"
        ws = sorted(M.words + S.words, key=lambda w: w.x0)
        fix_spaces(ws)
        M.words = ws
        M.refresh()
        keep.remove(S)
    return keep


def _merge_same_baseline(lines):
    """Fusionne les lignes d'un même bloc qui partagent la même ligne de base."""
    out = []
    for L in lines:
        if out and out[-1].bid == L.bid and abs(out[-1].base - L.base) < 0.25 * max(L.size, out[-1].size):
            a = out.pop()
            words = sorted(a.words + L.words, key=lambda w: w.x0)
            from .paras import fix_spaces
            fix_spaces(words)
            out.append(Line(words, a.bid))
        else:
            out.append(L)
    return out
