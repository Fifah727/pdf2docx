"""Éléments spéciaux : encadrés (fond/bordure), figures vectorielles, formules."""
import re

import pymupdf

from .model import Img, Box
from .paras import partition_words
from .tables import _rgb


def safe_drawings(page):
    try:
        return page.get_drawings()
    except Exception:
        return []


def _near_white(c):
    return c is None or all(x > 0.97 for x in c[:3])


def _rect_like(d):
    """Rectangle (pymupdf.Rect) si le tracé est un rectangle / quadrilatère aligné, sinon None."""
    items = d.get("items") or []
    if not items:
        return None
    kinds = [it[0] for it in items]
    if kinds == ["re"]:
        return pymupdf.Rect(items[0][1])
    if kinds == ["qu"]:
        return items[0][1].rect
    if kinds.count("l") >= 4 and set(kinds) <= {"l"} and len(items) <= 5:
        r = d["rect"]
        for it in items:
            p, q = it[1], it[2]
            if abs(p.x - q.x) > 0.8 and abs(p.y - q.y) > 0.8:
                return None
        return pymupdf.Rect(r)
    # rectangle à coins arrondis : 4 lignes + 4 courbes
    if kinds.count("l") + kinds.count("c") == len(items) and kinds.count("c") == 4 and kinds.count("l") in (4, 3):
        return pymupdf.Rect(d["rect"])
    return None


def _inside(w, r, tol=0.5):
    return r.x0 - tol <= w.cx <= r.x1 + tol and r.y0 - tol <= w.cy <= r.y1 + tol


def _expand(r, d):
    return pymupdf.Rect(r.x0 - d, r.y0 - d, r.x1 + d, r.y1 + d)


# ---------------------------------------------------------------- encadrés
def detect_boxes(page, drawings, lines, images, claimed, cfg):
    """Retourne (boxes, lines_restantes, images_restantes, rects_revendiqués)."""
    pr = page.rect
    cands = []
    for d in drawings:
        r = _rect_like(d)
        if r is None:
            continue
        typ = d.get("type") or ""
        fill, stroke, width = d.get("fill"), d.get("color"), d.get("width") or 0
        has_fill = fill is not None and not _near_white(fill)
        has_stroke = "s" in typ and stroke is not None and width >= 0.3
        if not (has_fill or has_stroke):
            continue
        if r.width < 36 or r.height < 16 or r.get_area() > 0.85 * pr.get_area():
            continue
        if any(_expand(c, 2).contains(r) for c in claimed):
            continue
        inner = sum(1 for o in drawings if o is not d and r.contains(o["rect"]) and o["rect"].get_area() < 0.5 * r.get_area())
        if inner >= 12:
            continue                      # beaucoup de tracés à l'intérieur : c'est un graphique / une figure, pas un encadré
        cands.append((r, fill if has_fill else None, stroke if has_stroke else None, width))
    # diagramme : plusieurs petites boîtes reliées par des connecteurs => une figure, pas des encadrés
    small = [c for c in cands if c[0].width < 220 and c[0].height < 90]
    if len(small) >= 3:
        u = small[0][0]
        for c in small[1:]:
            u = u | c[0]
        zone = _expand(u, 30)
        conn = [d for d in drawings if _rect_like(d) is None and zone.intersects(d["rect"]) and d["rect"].get_area() < 0.5 * pr.get_area()]
        if len(conn) >= 3:
            cands = [c for c in cands if c not in small]
    cands.sort(key=lambda c: -c[0].get_area())

    accepted = []
    for r, fill, stroke, width in cands:
        if any((r & a[0]).get_area() >= 0.9 * r.get_area() for a in accepted):
            continue
        ws = [w for L in lines for w in L.words if _inside(w, r)]
        ims = [im for im in images if pymupdf.Rect(im.bbox).get_area() > 0
               and (pymupdf.Rect(im.bbox) & r).get_area() >= 0.8 * pymupdf.Rect(im.bbox).get_area()]
        if not ws and not ims:
            continue
        if ws and stroke is None:       # surlignage d'une ligne : pas un encadré
            hmax = max(w.y1 - w.y0 for w in ws)
            if r.height < 1.5 * hmax:
                continue
        accepted.append((r, fill, stroke, width))

    boxes = []
    for r, fill, stroke, width in accepted:
        rect = r

        def keyfn(w, rect=rect):
            return 1 if _inside(w, rect) else None

        lines, taken = partition_words(lines, keyfn)
        inner_img = [im for im in images if (pymupdf.Rect(im.bbox) & rect).get_area() >= 0.8 * pymupdf.Rect(im.bbox).get_area()]
        images = [im for im in images if im not in inner_img]
        boxes.append((Box(tuple(rect), [], _rgb(fill), _rgb(stroke), max(0.5, width)),
                      taken.get(1, []), inner_img))
        claimed.append(rect)
    return boxes, lines, images, claimed


# ---------------------------------------------------------------- figures vectorielles
def detect_figures(page, drawings, lines, images, claimed, cfg):
    pr = page.rect
    use = []
    for d in drawings:
        r = d["rect"]
        if r.width < 2 and r.height < 2:
            continue
        if (r.width < 3 or r.height < 3) and max(r.width, r.height) > 0.6 * pr.width:
            continue                                   # filets pleine largeur (règles de page)
        if any(_expand(c, 2).contains(r) for c in claimed):
            continue
        if r.get_area() > 0.7 * pr.get_area():
            continue
        use.append(d)
    if len(use) < 1:
        return [], lines, images
    try:
        rects = page.cluster_drawings(drawings=use)
    except Exception:
        return [], lines, images
    figs = []
    for r in rects:
        r = pymupdf.Rect(r) & pr
        if r.width < 30 or r.height < 24 or r.get_area() > 0.7 * pr.get_area():
            continue
        n = sum(1 for d in use if r.contains(d["rect"]) or (d["rect"] & r).get_area() > 0.5 * d["rect"].get_area())
        has_words = any(_inside(w, r) for L in lines for w in L.words)
        if n < 4 and not (n >= 1 and r.get_area() >= 900 and not has_words):
            continue
        figs.append(r)
    out = []
    for r in figs:
        rr = _expand(r, 1.5) & pr
        near = _expand(rr, 14) & pr
        for L in lines:
            if len(L.words) <= 6 and L.size <= 11:
                for w in L.words:
                    if near.intersects(pymupdf.Rect(w.x0, w.y0, w.x1, w.y1)) and not (r.get_area() < 0 or False):
                        rr = rr | pymupdf.Rect(w.x0 - 1, w.y0 - 1, w.x1 + 1, w.y1 + 1)
        rr = rr & pr

        def keyfn(w, rr=rr):
            return 1 if _inside(w, rr) else None

        lines, _ = partition_words(lines, keyfn)
        images = [im for im in images if (pymupdf.Rect(im.bbox) & rr).get_area() < 0.8 * max(pymupdf.Rect(im.bbox).get_area(), 1)]
        pix = page.get_pixmap(clip=rr, dpi=cfg.figure_dpi, alpha=False)
        out.append(Img(tuple(rr), pix.tobytes("png"), "png"))
        claimed.append(rr)
    return out, lines, images


# ---------------------------------------------------------------- formules
MATH_FONT = re.compile(r"(cmmi|cmsy|cmex|cmbsy|cmmib|msam|msbm|mathematica|mtmi|mtsy|mtex|mtsyn|euclid|"
                       r"eufm|rsfs|lmmath|math|stix|xits|mathjax|latinmodern-math|cmr10.*math)", re.I)
OPS = set("∑∏∫∮∬∭√∛∜∂∇∞≈≠≡≤≥≪≫±∓×÷∈∉∋⊂⊃⊆⊇∪∩∧∨¬∀∃∅→←↔⇒⇐⇔↦∝∠⊥∥′″⁄⌈⌉⌊⌋〈〉⟨⟩∘⊗⊕⊙∼≃≅∖⋅−∕")
GREEK = set("αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΘΛΞΠΣΦΨΩϕϑϖϱϵ")
BIG = set("∑∏∫∮∬∭√")
NOT_MATH = set("∗•†‡*·‣▪◦○●■□▸►‣⁃–—-{}()[]|⃝")      # puces et marqueurs de notes : jamais des formules


def _math_word(w):
    """Mot manifestement mathématique : opérateur, symbole privé (police math), ou police math."""
    if MATH_FONT.search(w.font) and w.text not in NOT_MATH and not all(ch in NOT_MATH for ch in w.text):
        return len(w.text) <= 12
    return any(ch in OPS and ch not in NOT_MATH for ch in w.text)


def _is_text_word(w):
    t = w.text.strip("(),.;:")
    return len(t) >= 3 and t.isalpha() and not _math_word(w)


def detect_formulas(page, lines, cfg):
    """Équations hors texte -> images. Grappes de mots mathématiques ; les petits voisins (indices, fractions)
    sont inclus ; un voisinage de vrais mots de texte signale du calcul en ligne, laissé en texte."""
    words = [w for L in lines for w in L.words]
    seeds = [w for w in words if _math_word(w)]
    big_lines = [L for L in lines if any(ch in BIG for w in L.words for ch in w.text) and len(L.text) <= 200]
    if not seeds and not big_lines:
        return [], lines
    boxes = []
    for w in seeds:
        ex, ey = 0.8 * w.size, 0.9 * w.size
        boxes.append([w.x0 - ex, w.y0 - ey, w.x1 + ex, w.y1 + ey, [w]])
    for L in big_lines:                      # ligne contenant ∑ ∫ √ : on la traite en entier
        boxes.append([L.x0 - 2, L.y0 - 2, L.x1 + 2, L.y1 + 2, list(L.words)])
    def merge(boxes):
        changed = True
        while changed:
            changed = False
            out = []
            for b in boxes:
                for o in out:
                    if b[0] < o[2] and o[0] < b[2] and b[1] < o[3] and o[1] < b[3]:
                        o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                        o[4] += b[4]
                        changed = True
                        break
                else:
                    out.append(b)
            boxes = out
        return boxes

    boxes = merge(boxes)
    # une équation affichée est faite de lignes entières sans vrai mot de texte : on étend la grappe à ces lignes
    pure = [L for L in lines if not any(_is_text_word(w) for w in L.words) and len(L.text) <= 90]
    for _ in range(3):
        grown = False
        for b in boxes:
            for L in pure:
                if L.y1 > b[1] + 1 and L.y0 < b[3] - 1 and L.x1 > b[0] - 40 and L.x0 < b[2] + 40:
                    if not (b[0] <= L.x0 and L.x1 <= b[2] and b[1] <= L.y0 and L.y1 <= b[3]):
                        b[0], b[1], b[2], b[3] = min(b[0], L.x0), min(b[1], L.y0), max(b[2], L.x1), max(b[3], L.y1)
                        b[4] += list(L.words)
                        grown = True
        boxes = merge(boxes)
        if not grown:
            break
    pr = page.rect
    imgs, taken = [], set()
    for b in boxes:
        sw = b[4]
        x0, y0 = min(w.x0 for w in sw) - 2.0, min(w.y0 for w in sw) - 2.0
        x1, y1 = max(w.x1 for w in sw) + 2.0, max(w.y1 for w in sw) + 2.0
        region = pymupdf.Rect(x0, y0, x1, y1)
        inside = [w for w in words if id(w) not in taken
                  and x0 - 1 <= (w.x0 + w.x1) / 2 <= x1 + 1 and y0 - 1 <= (w.y0 + w.y1) / 2 <= y1 + 1]
        big = any(ch in BIG for w in sw for ch in w.text)
        if big and sum(1 for w in inside if _is_text_word(w)) >= 1:
            # phrase contenant ∑ ∫ √ : on prend toute la bande de ligne contiguë (indices et limites compris)
            band = [w for w in words if id(w) not in taken and y0 - 1 <= (w.y0 + w.y1) / 2 <= y1 + 1]
            band.sort(key=lambda w: w.x0)
            lo, hi = x0, x1
            changed = True
            while changed:
                changed = False
                for w in band:
                    gap = max(lo - w.x1, w.x0 - hi)
                    if gap < 2.5 * max(w.size, 6) and (w.x0 < lo or w.x1 > hi):
                        lo, hi = min(lo, w.x0), max(hi, w.x1)
                        changed = True
            x0, x1 = lo - 1.0, hi + 1.0
            region = pymupdf.Rect(x0, y0, x1, y1)
            inside = [w for w in band if x0 - 1 <= (w.x0 + w.x1) / 2 <= x1 + 1]
        elif not big and sum(1 for w in inside if _is_text_word(w)) >= 2:
            continue                         # calcul en ligne dans une phrase : on garde le texte
        if len(inside) < 2 and not big:
            continue
        strong = big or any(ch in OPS and ch not in NOT_MATH for w in inside for ch in w.text)
        if not strong:
            continue                         # symboles de police math isolés (accolades, ©, indices) : pas une équation
        if len(inside) < 1 or region.width < 6 or region.height < 6 or region.height > 0.35 * pr.height:
            continue
        rr = (_expand(region, 1.0) & pr)
        pix = page.get_pixmap(clip=rr, dpi=cfg.formula_dpi, alpha=False)
        imgs.append(Img(tuple(rr), pix.tobytes("png"), "png", formula=True))
        taken |= {id(w) for w in inside}
    if not taken:
        return [], lines
    lines, _ = partition_words(lines, lambda w: 1 if id(w) in taken else None)
    return imgs, lines
