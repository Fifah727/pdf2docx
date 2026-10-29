"""Détection des tableaux : à bordures (PyMuPDF + déduction des fusions) et sans bordures (alignement)."""
from collections import Counter
from statistics import median, pstdev

import pymupdf

from .model import Cell, Tbl
from .paras import partition_words, words_to_lines, lines_to_paras


# ---------------------------------------------------------------- helpers
def _edges(values, tol):
    vs = sorted(values)
    out = []
    for v in vs:
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _idx(edges, v):
    return min(range(len(edges)), key=lambda i: abs(edges[i] - v))


def _rgb(c):
    if c is None:
        return None
    return "".join(f"{int(round(max(0, min(1, x)) * 255)):02X}" for x in c[:3])


def fill_at(drawings, rect, max_ratio=1.6):
    """Couleur de fond (hex) d'une zone, d'après les rectangles pleins du dessin."""
    best, best_area = None, None
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    area = max((rect[2] - rect[0]) * (rect[3] - rect[1]), 1)
    for d in drawings:
        f = d.get("fill")
        if f is None or all(x > 0.97 for x in f[:3]):
            continue
        r = d["rect"]
        if r.x0 - 1 <= cx <= r.x1 + 1 and r.y0 - 1 <= cy <= r.y1 + 1:
            a = r.get_area()
            if a <= max_ratio * area and (best_area is None or a < best_area):
                best, best_area = f, a
    return _rgb(best)


def _para_cell(words, cfg):
    return lines_to_paras(words_to_lines(words), cfg)


def _vcenter(cell, rect):
    if not cell.paras:
        return False
    top = cell.paras[0].y0 - rect[1]
    bot = rect[3] - cell.paras[-1].y1
    h = rect[3] - rect[1]
    return top > 2 and bot > 2 and abs(top - bot) < 0.2 * h


# ---------------------------------------------------------------- traits (règles) du dessin
def ruling_segments(drawings):
    """Segments horizontaux/verticaux : (orientation, position, début, fin, couleur hex, épaisseur)."""
    segs = []
    for d in drawings:
        col = _rgb(d.get("color")) if d.get("color") is not None else None
        w = d.get("width") or 0.5
        fill = _rgb(d.get("fill")) if d.get("fill") is not None else None
        for it in d.get("items") or []:
            k = it[0]
            if k == "l":
                p, q = it[1], it[2]
                if abs(p.y - q.y) < 1.0 and abs(p.x - q.x) > 2:
                    segs.append(("h", (p.y + q.y) / 2, min(p.x, q.x), max(p.x, q.x), col or "000000", w))
                elif abs(p.x - q.x) < 1.0 and abs(p.y - q.y) > 2:
                    segs.append(("v", (p.x + q.x) / 2, min(p.y, q.y), max(p.y, q.y), col or "000000", w))
            elif k in ("re", "qu"):
                r = it[1] if k == "re" else it[1].rect
                c = fill or col or "000000"
                if r.height < 2.6 and r.width > 3:
                    segs.append(("h", (r.y0 + r.y1) / 2, r.x0, r.x1, c, max(r.height, 0.25)))
                elif r.width < 2.6 and r.height > 3:
                    segs.append(("v", (r.x0 + r.x1) / 2, r.y0, r.y1, c, max(r.width, 0.25)))
    return segs


def cell_borders(rect, segs, tol=2.5, outer=None, otol=8.0):
    """Bordures réellement dessinées autour d'une cellule."""
    x0, y0, x1, y1 = rect
    out = {}
    for side, orient, pos, a, b in (("top", "h", y0, x0, x1), ("bottom", "h", y1, x0, x1),
                                    ("left", "v", x0, y0, y1), ("right", "v", x1, y0, y1)):
        best = None
        for o, p, s0, s1, c, w in segs:
            t = otol if (outer and side in outer) else tol
            if o != orient or abs(p - pos) > t:
                continue
            cover = min(s1, b) - max(s0, a)
            if cover >= 0.6 * (b - a) and (best is None or w > best[1]):
                best = (c, w)
        if best:
            out[side] = (best[0], max(0.25, min(best[1], 3.0)))
    return out


def _outer(c, xs, ys):
    sides = set()
    if c.r0 == 0:
        sides.add("top")
    if c.r1 == len(ys) - 1:
        sides.add("bottom")
    if c.c0 == 0:
        sides.add("left")
    if c.c1 == len(xs) - 1:
        sides.add("right")
    return sides


def _trim_false_rows(cells, xs, ys, segs):
    """find_tables ajoute parfois une ligne fantôme (texte hors tableau) en haut ou en bas : aucune bordure réelle."""
    while len(ys) > 2:
        nr = len(ys) - 1
        for c in cells:
            c.borders = cell_borders(c.rect, segs, outer=_outer(c, xs, ys))
        top = [c for c in cells if c.r0 == 0]
        bot = [c for c in cells if c.r1 == nr]
        grid = sum(1 for c in cells if set(c.borders) & {"left", "right"})
        if grid < 0.6 * len(cells) - 1:          # tableau sans traits verticaux (filets horizontaux) : aucune ligne n'est fantôme
            break
        if top and all(not (set(c.borders) & {"top", "left", "right"}) for c in top):
            cells[:] = [c for c in cells if c.r0 != 0]
            for c in cells:
                c.r0 -= 1
                c.r1 -= 1
            ys.pop(0)
        elif bot and all(not (set(c.borders) & {"bottom", "left", "right"}) for c in bot):
            cells[:] = [c for c in cells if c.r1 != nr]
            ys.pop()
        else:
            break


def _snap_outer(cells, xs, ys, segs):
    """Cale les bords extérieurs sur les vrais traits voisins (le bord droit/bas détecté peut être trop court)."""
    y0, y1, x0, x1 = ys[0], ys[-1], xs[0], xs[-1]
    h, w = y1 - y0, x1 - x0
    vs = [p for o, p, s0, s1, c, wd in segs if o == "v" and min(s1, y1 + 4) - max(s0, y0 - 4) >= 0.8 * h]
    hs = [p for o, p, s0, s1, c, wd in segs if o == "h" and min(s1, x1 + 30) - max(s0, x0 - 30) >= 0.8 * w]
    def near(vals, ref, lo, hi):
        c = [v for v in vals if lo < v - ref < hi or lo < ref - v < hi]
        return min(c, key=lambda v: abs(v - ref)) if c else None
    new = {}
    for idx, ref, vals, lim in ((("x", len(xs) - 1), x1, vs, 25), (("x", 0), x0, vs, 25),
                                (("y", len(ys) - 1), y1, hs, 12), (("y", 0), y0, hs, 12)):
        v = near(vals, ref, 0.3, lim)
        if v is not None:
            new[idx] = v
    for (ax, i), v in new.items():
        (xs if ax == "x" else ys)[i] = v
    for c in cells:
        c.rect = pymupdf.Rect(xs[c.c0], ys[c.r0], xs[c.c1], ys[c.r1])


# ---------------------------------------------------------------- tableaux à bordures
def ruled_tables(page, lines, drawings, cfg):
    try:
        found = page.find_tables()
    except Exception:
        return lines, []
    tables = []
    segs = ruling_segments(drawings)
    for tab in found.tables:
        rects = [pymupdf.Rect(c) for c in tab.cells if c]
        if len(rects) < 2:
            continue
        xs = _edges([r.x0 for r in rects] + [r.x1 for r in rects], 2.5)
        ys = _edges([r.y0 for r in rects] + [r.y1 for r in rects], 2.5)
        if len(xs) < 2 or len(ys) < 2:
            continue
        cells = []
        for r in rects:
            c0, c1, r0, r1 = _idx(xs, r.x0), _idx(xs, r.x1), _idx(ys, r.y0), _idx(ys, r.y1)
            if c1 > c0 and r1 > r0:
                cells.append(Cell(r0, c0, r1, c1, r))
        if len(cells) < 2:
            continue
        _trim_false_rows(cells, xs, ys, segs)
        if len(cells) < 2:
            continue
        _snap_outer(cells, xs, ys, segs)

        def which(w, cells=cells):
            best = None
            for k, c in enumerate(cells):
                x0, y0, x1, y1 = c.rect
                if x0 - 0.5 <= w.cx <= x1 + 0.5 and y0 - 0.5 <= w.cy <= y1 + 0.5:
                    a = (x1 - x0) * (y1 - y0)
                    if best is None or a < best[0]:
                        best = (a, k)
            return None if best is None else best[1]

        lines, taken = partition_words(lines, which)
        for k, c in enumerate(cells):
            c.paras = _para_cell(taken.get(k, []), cfg)
            c.fill = fill_at(drawings, c.rect)
            c.vcenter = _vcenter(c, c.rect)
            c.borders = cell_borders(c.rect, segs, outer=_outer(c, xs, ys))
        t = Tbl((xs[0], ys[0], xs[-1], ys[-1]), xs, ys, cells, ruled=True)
        stroke = [d for d in drawings if d.get("color") is not None and d["rect"].intersects(pymupdf.Rect(t.bbox))
                  and (d["rect"].width < 2 or d["rect"].height < 2 or d.get("type") == "s")]
        if stroke:
            c = Counter(_rgb(d["color"]) for d in stroke).most_common(1)[0][0]
            t.stroke = c
            w = [d.get("width") or 0.5 for d in stroke]
            t.stroke_w = max(0.25, min(2.0, median(w)))
        tables.append(t)
    return lines, tables


# ---------------------------------------------------------------- tableaux sans bordures
class _Row:
    def __init__(self, base, size):
        self.base, self.size = base, size
        self.words = []
        self.cells = []

    @property
    def n(self):
        return len(self.cells)


def _cluster_rows(words):
    main = sorted([w for w in words if w.script is None], key=lambda w: (w.base, w.x0))
    rows = []
    for w in main:
        if rows and abs(w.base - rows[-1].base) <= 0.45 * min(w.size, rows[-1].size):
            rows[-1].words.append(w)
        else:
            r = _Row(w.base, w.size)
            r.words.append(w)
            rows.append(r)
    for w in words:
        if w.script is not None and rows:
            min(rows, key=lambda r: abs(r.base - w.base)).words.append(w)
    return rows


def _split_cells(row, cfg):
    ws = sorted(row.words, key=lambda w: w.x0)
    cells, cur = [], [ws[0]]
    for a, b in zip(ws, ws[1:]):
        if b.x0 - a.x1 > cfg.cell_gap_em * max(a.size, 4):
            cells.append(cur)
            cur = [b]
        else:
            cur.append(b)
    cells.append(cur)
    row.cells = cells


def _cell_x(c):
    return min(w.x0 for w in c), max(w.x1 for w in c)


def borderless_tables(lines, cfg):
    words = [w for L in lines for w in L.words]
    if len(words) < 8:
        return lines, []
    rows = _cluster_rows(words)
    for r in rows:
        _split_cells(r, cfg)
    n = len(rows)
    tables = []
    i = 0
    while i < n:
        if rows[i].n < 2:
            i += 1
            continue
        j = i
        while j + 1 < n:
            a, b = rows[j], rows[j + 1]
            if b.base - a.base > 2.4 * max(a.size, 5):
                break
            if b.n >= 2:
                j += 1
                continue
            # ligne à une seule cellule tolérée entre deux lignes de tableau (titre de groupe)
            if (j + 2 < n and rows[j + 2].n >= 2 and len(b.words) <= 6
                    and rows[j + 2].base - b.base <= 2.4 * max(b.size, 5)):
                j += 2
                continue
            break
        run = rows[i:j + 1]
        i = j + 1
        if len(run) < cfg.table_min_rows:
            continue
        t = _build_borderless(run, cfg)
        if t is not None:
            tables.append((t, run))
    if not tables:
        return lines, []
    taken_ids = set()
    for t, run in tables:
        for r in run:
            for w in r.words:
                taken_ids.add(id(w))
    lines, _ = partition_words(lines, lambda w: 1 if id(w) in taken_ids else None)
    return lines, [t for t, _ in tables]


def _build_borderless(run, cfg):
    multi = [r for r in run if r.n >= 2]
    modal = Counter(r.n for r in multi).most_common(1)[0][0]
    ref = [r for r in multi if r.n == modal]
    if modal < 2 or len(ref) < 0.5 * len(run):
        return None
    wpc = [len(c) for r in multi for c in r.cells]
    if sum(wpc) / len(wpc) > cfg.table_max_words_per_cell:
        return None
    # colonnes = union des intervalles de chaque rang de cellule dans les lignes de référence
    cols = []
    for k in range(modal):
        xs = [_cell_x(r.cells[k]) for r in ref]
        cols.append((min(a for a, _ in xs), max(b for _, b in xs)))
    for (a0, a1), (b0, b1) in zip(cols, cols[1:]):
        if b0 <= a1:
            return None
    # garde-fou : du texte en colonnes (cellules qui remplissent toute leur colonne, plusieurs mots) n'est pas un tableau
    fills = []
    for k in range(modal):
        cw = cols[k][1] - cols[k][0]
        fk = [(_cell_x(r.cells[k])[1] - _cell_x(r.cells[k])[0]) / max(cw, 1) for r in ref]
        fills.append(median(fk))
    if min(fills) >= 0.9 and sum(wpc) / len(wpc) >= 3.5:
        return None
    # du texte qui coule : lignes de plusieurs mots qui commencent le plus souvent par une minuscule
    firsts = [c[0].text[:1] for r in multi for c in r.cells[:]]
    lower = sum(1 for ch in firsts if ch.islower()) / max(len(firsts), 1)
    if lower >= 0.45 and sum(wpc) / len(wpc) >= 3.5:
        return None
    # cohérence d'alignement : au moins la moitié des colonnes alignées (gauche/droite/centre)
    good = 0
    for k in range(modal):
        cells = [_cell_x(r.cells[k]) for r in ref]
        if len(cells) < 2:
            good += 1
            continue
        s0 = pstdev([a for a, _ in cells])
        s1 = pstdev([b for _, b in cells])
        sc = pstdev([(a + b) / 2 for a, b in cells])
        if min(s0, s1, sc) <= 4.5:
            good += 1
    if good < 0.5 * modal:
        return None

    # affectation des cellules aux colonnes (avec colspan)
    grid = []
    for r in run:
        placed = []
        for c in r.cells:
            a, b = _cell_x(c)
            ov = [k for k, (x0, x1) in enumerate(cols) if min(x1, b) - max(x0, a) > 0]
            if not ov:
                k = min(range(modal), key=lambda k: abs((cols[k][0] + cols[k][1]) / 2 - (a + b) / 2))
                ov = [k]
            placed.append([ov[0], ov[-1], list(c)])
        merged = []
        for p in placed:                      # fusionne les cellules qui tombent dans la même colonne
            if merged and p[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], p[1])
                merged[-1][2] += p[2]
            else:
                merged.append(p)
        grid.append(merged)

    # arêtes
    col_edges = [cols[0][0] - 2.0]
    for (a0, a1), (b0, b1) in zip(cols, cols[1:]):
        col_edges.append((a1 + b0) / 2)
    col_edges.append(cols[-1][1] + 2.0)
    row_top = [min(w.y0 for w in r.words) for r in run]
    row_bot = [max(w.y1 for w in r.words) for r in run]
    row_edges = [row_top[0] - 1.0]
    for k in range(len(run) - 1):
        row_edges.append((row_bot[k] + row_top[k + 1]) / 2)
    row_edges.append(row_bot[-1] + 1.0)

    cells = []
    for ri, merged in enumerate(grid):
        for c0, c1, ws in merged:
            rect = (col_edges[c0], row_edges[ri], col_edges[c1 + 1], row_edges[ri + 1])
            cell = Cell(ri, c0, ri + 1, c1 + 1, rect)
            cell.paras = _para_cell(ws, cfg)
            cell.vcenter = False
            cells.append(cell)
    bbox = (col_edges[0], row_edges[0], col_edges[-1], row_edges[-1])
    return Tbl(bbox, col_edges, row_edges, cells, ruled=False)
