"""Construction du modèle d'une page : OCR/natif -> tableaux -> encadrés -> figures -> formules -> paragraphes."""
import pymupdf

from .model import PageModel, Img
from .native import native_lines
from .ocr import is_scanned, ocr_lines
from .overlay import apply_underlines, detect_shapes
from .paras import lines_to_paras, words_to_lines
from .special import safe_drawings, detect_boxes, detect_figures, detect_formulas
from .tables import ruled_tables, borderless_tables, _rgb


def _filled(drawings, page_area):
    out = []
    for d in drawings:
        f = d.get("fill")
        if f is None or all(x > 0.97 for x in f[:3]):
            continue
        r = d["rect"]
        if r.get_area() >= 0.7 * page_area or r.width < 3 or r.height < 3:
            continue
        out.append((r, f))
    return out


def _bg_at(filled, x0, y0, x1, y1, lo, hi):
    """Couleur du plus petit aplat qui recouvre la zone (ni un point, ni un fond de page)."""
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    area = max((x1 - x0) * (y1 - y0), 1.0)
    best = None
    for r, f in filled:
        if r.x0 - 1 <= cx <= r.x1 + 1 and r.y0 - 1 <= cy <= r.y1 + 1 and r.x0 - 3 <= x0 and x1 <= r.x1 + 3:
            a = r.get_area()
            if lo * area <= a <= hi * area and (best is None or a < best[0]):
                best = (a, f)
    return _rgb(best[1]) if best else None


def assign_backgrounds(items, drawings, page_area):
    filled = _filled(drawings, page_area)
    if not filled:
        return

    def run(paras, inherit):
        for p in paras:
            if p.kind == "para":
                bg = _bg_at(filled, p.x0, p.y0, p.x1, p.y1, 1.0, 40.0)
                if bg and bg != inherit:
                    p.bg = bg
    top = [i for i in items if i.kind == "para"]
    run(top, None)
    for it in items:
        if it.kind == "box":
            ps = [c for c in it.children if c.kind == "para"]
            for p in ps:
                p.bg = _bg_at(filled, p.x0, p.y0, p.x1, p.y1, 1.0, 1e9)
            weight = {}
            for p in ps:
                if p.bg:
                    weight[p.bg] = weight.get(p.bg, 0) + len(p.text)
            outer = it.fill
            if weight:
                it.fill = max(weight, key=weight.get)           # fond majoritaire = fond du corps de l'encadré
            for p in ps:
                if p.bg == it.fill:
                    p.bg = None
            if outer and outer != it.fill and not it.stroke:
                it.stroke, it.stroke_w = outer, 1.0             # le cadre tcolorbox est un aplat de la couleur du cadre


def build_page(page, cfg, number):
    W, H = page.rect.width, page.rect.height
    pm = PageModel(W, H, number)
    scanned = is_scanned(page, cfg)
    pm.scanned = scanned
    if scanned:
        lines, otbls, angle = ocr_lines(page, cfg)
        images, drawings = [], []
        pm.stats["ocr_lines"] = len(lines)
        pm.stats["deskew_x100"] = int(round(angle * 100))
    else:
        lines, images = native_lines(page, cfg)
        drawings = safe_drawings(page)
        consumed = apply_underlines(lines, drawings)

    items = []
    claimed = []
    if scanned:
        items += otbls
        pm.stats["tables_scanned"] = len(otbls)

    # 1. tableaux à bordures
    if not scanned:
        lines, tbls = ruled_tables(page, lines, drawings, cfg)
        for t in tbls:
            claimed.append(pymupdf.Rect(t.bbox))
        items += tbls
        pm.stats["tables_ruled"] = len(tbls)

    # 2. encadrés
    if cfg.detect_boxes and not scanned:
        boxes, lines, images, claimed = detect_boxes(page, drawings, lines, images, claimed, cfg)
        for box, words, imgs in boxes:
            box.children_src = (words, imgs)
            items.append(box)
        pm.stats["boxes"] = len(boxes)

    # 3. figures vectorielles
    if cfg.detect_figures and not scanned:
        figs, lines, images = detect_figures(page, drawings, lines, images, claimed, cfg)
        def inside(a, b):      # a contenu dans b (tolérance 4 pt)
            return a[0] >= b[0] - 4 and a[1] >= b[1] - 4 and a[2] <= b[2] + 4 and a[3] <= b[3] + 4
        items = [it for it in items if not (it.kind == "box" and any(inside(it.bbox, f.bbox) for f in figs))]
        # un encadré qui contient une figure importante est lui-même une figure (schéma dans un cadre) : une seule image
        for bx in [it for it in items if it.kind == "box"]:
            fin = [f for f in figs if inside(f.bbox, bx.bbox)]
            if fin and sum(f.w * f.h for f in fin) >= 0.2 * bx.w * bx.h:
                rr = pymupdf.Rect(bx.bbox) & page.rect
                pix = page.get_pixmap(clip=rr, dpi=cfg.figure_dpi, alpha=False)
                items.remove(bx)
                figs = [f for f in figs if f not in fin] + [Img(tuple(rr), pix.tobytes("png"), "png")]
        items += figs
        pm.skip_rects += [f.bbox for f in figs]
        pm.stats["figures"] = len(figs)

    # 3b. tracés libres (cadres, filets, formes) non revendiqués : vectoriel derrière le texte
    if cfg.detect_shapes and not scanned:
        pm.overlays = detect_shapes(page, drawings, consumed, claimed, pm.skip_rects, cfg)
        pm.stats["shapes"] = len(pm.overlays)

    # 4. formules
    if cfg.formula_mode == "image" and not scanned:
        fmls, lines = detect_formulas(page, lines, cfg)
        items += fmls
        pm.skip_rects += [f.bbox for f in fmls]
        pm.stats["formulas"] = len(fmls)

    # 5. tableaux sans bordures
    if cfg.borderless_tables:
        lines, btbls = borderless_tables(lines, cfg)
        items += btbls
        pm.stats["tables_borderless"] = len(btbls)

    # 6. paragraphes et images
    paras = lines_to_paras(lines, cfg)
    items += paras
    items += images
    pm.stats["paras"] = len(paras)
    pm.stats["images"] = len(images)

    # contenu des encadrés
    for it in items:
        if it.kind == "box":
            words, imgs = it.children_src
            ps = lines_to_paras(words_to_lines(words), cfg) if words else []
            it.children = ps + list(imgs)
            del it.children_src
    if not scanned:
        assign_backgrounds(items, drawings, page.rect.get_area())
    pm.items = items
    return pm
