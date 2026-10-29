"""Soulignements (vrais attributs de texte) et tracés vectoriels libres (cadres, filets, formes) ancrés derrière le texte."""
import pymupdf

from .model import Shape
from .tables import _rgb


def _hsegments(d):
    """Segments horizontaux d'un dessin ne contenant que des traits fins (trait « l » ou petit rectangle plein) : [(y, x0, x1)]."""
    out = []
    for it in d.get("items") or []:
        if it[0] == "l" and abs(it[1].y - it[2].y) < 0.8 and abs(it[1].x - it[2].x) > 3:
            out.append(((it[1].y + it[2].y) / 2, min(it[1].x, it[2].x), max(it[1].x, it[2].x)))
        elif it[0] == "re" and it[1].height < 1.6 and it[1].width > 3:
            r = it[1]
            out.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
        else:
            return []
    return out


def apply_underlines(lines, drawings):
    """Un trait horizontal juste sous la ligne de base, qui ne dépasse pas des mots qu'il souligne, devient un soulignement."""
    consumed = set()
    for k, d in enumerate(drawings):
        segs = _hsegments(d)
        if not segs or len(segs) != len(d["items"]):
            continue
        if d.get("fill") is not None and not all(it[0] == "re" for it in d["items"]):
            continue
        for y, x0, x1 in segs:
            best = None
            for L in lines:
                for wd in L.words:
                    if wd.script is not None:
                        continue
                    dy = y - wd.base
                    if -0.6 <= dy <= 0.32 * wd.size and min(x1, wd.x1) - max(x0, wd.x0) > 0.5 * (wd.x1 - wd.x0):
                        best = L
                        break
                if best:
                    break
            if not best:
                continue
            hit = [wd for wd in best.words if min(x1, wd.x1) - max(x0, wd.x0) > 0.5 * (wd.x1 - wd.x0)
                   and -0.6 <= y - wd.base <= 0.32 * wd.size]
            if not hit:
                continue
            lo, hi = min(wd.x0 for wd in hit), max(wd.x1 for wd in hit)
            if x0 < lo - 4 or x1 > hi + 4:         # trait plus large que les mots : c'est un filet de mise en page
                continue
            for wd in hit:
                wd.under = True
            consumed.add(k)
    return consumed


def _path_of(d):
    sub, cur = [], None
    for it in d.get("items") or []:
        k = it[0]
        if k == "l":
            p, q = it[1], it[2]
            if cur is None or abs(cur.x - p.x) > 0.01 or abs(cur.y - p.y) > 0.01:
                sub.append(("M", p.x, p.y))
            sub.append(("L", q.x, q.y))
            cur = q
        elif k == "c":
            p1, c1, c2, p2 = it[1], it[2], it[3], it[4]
            if cur is None or abs(cur.x - p1.x) > 0.01 or abs(cur.y - p1.y) > 0.01:
                sub.append(("M", p1.x, p1.y))
            sub.append(("C", c1.x, c1.y, c2.x, c2.y, p2.x, p2.y))
            cur = p2
        elif k == "re":
            r = it[1]
            sub += [("M", r.x0, r.y0), ("L", r.x1, r.y0), ("L", r.x1, r.y1), ("L", r.x0, r.y1), ("Z",)]
            cur = None
        elif k == "qu":
            q = it[1]
            sub += [("M", q.ul.x, q.ul.y), ("L", q.ur.x, q.ur.y), ("L", q.lr.x, q.lr.y), ("L", q.ll.x, q.ll.y), ("Z",)]
            cur = None
    if d.get("closePath"):
        sub.append(("Z",))
    return sub


def detect_shapes(page, drawings, consumed, claimed, skip_rects, cfg, max_shapes=400):
    """Tracés non revendiqués par un tableau / encadré / figure : reproduits en vectoriel derrière le texte."""
    pr = page.rect
    area = pr.get_area()
    cl = [pymupdf.Rect(c) + (-2, -2, 2, 2) for c in list(claimed) + [tuple(r) for r in skip_rects]]
    out = []
    for k, d in enumerate(drawings):
        if k in consumed:
            continue
        r = d["rect"]
        if r.get_area() > 0.7 * area:
            continue
        if any(c.contains(r) for c in cl):
            continue
        stroke = d.get("color")
        fill = d.get("fill")
        if fill is not None and all(x > 0.97 for x in fill[:3]):
            fill = None
        items = d.get("items") or []
        # aplat rectangulaire seul : déjà rendu comme fond de paragraphe / d'encadré
        if stroke is None and all(it[0] in ("re", "qu") for it in items):
            continue
        if stroke is None and fill is None:
            continue
        path = _path_of(d)
        if not path:
            continue
        w = float(d.get("width") or 0.5)
        out.append(Shape((r.x0, r.y0, r.x1, r.y1), path, _rgb(stroke) if stroke is not None else None,
                         max(0.25, min(w, 6.0)), _rgb(fill) if fill is not None else None,
                         bool(d.get("dashes") and d["dashes"].strip() not in ("", "[] 0"))))
    if len(out) > max_shapes:
        return []
    return out
