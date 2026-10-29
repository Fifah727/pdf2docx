"""OCR des pages scannées (Tesseract) : redressement, lecture, reconstruction des lignes."""
import os
from collections import defaultdict
from statistics import median

import numpy as np
import pymupdf

from .model import Word, Line

_TESS_DIRS = [os.environ.get("TESSDATA_PREFIX", ""), os.path.expanduser("~/tessdata"),
              "/usr/share/tesseract-ocr/5/tessdata", "/usr/share/tesseract-ocr/4.00/tessdata",
              "/usr/share/tessdata"]


def tessdata_dir(langs):
    want = [l for l in langs.split("+") if l]
    for d in _TESS_DIRS:
        if d and os.path.isdir(d) and all(os.path.exists(os.path.join(d, l + ".traineddata")) for l in want):
            return d
    for d in _TESS_DIRS:                       # répertoire partiel : on garde ce qui existe
        if d and os.path.isdir(d):
            return d
    return None


def resolve_lang(want):
    d = tessdata_dir(want)
    avail = []
    if d:
        avail = [l for l in want.split("+") if os.path.exists(os.path.join(d, l + ".traineddata"))]
    if not avail:
        return "eng", d
    return "+".join(avail), d


def is_scanned(page, cfg):
    if cfg.ocr == "off":
        return False
    if cfg.ocr == "force":
        return True
    if len(page.get_text("text").strip()) >= cfg.ocr_min_chars:
        return False
    try:
        infos = page.get_image_info()
    except Exception:
        return False
    if not infos:
        return False
    pr = page.rect
    cov = max((pymupdf.Rect(i["bbox"]) & pr).get_area() for i in infos) / max(pr.get_area(), 1)
    return cov >= 0.5


def deskew(img):
    """Estime l'inclinaison par profil de projection et redresse. Retourne (image, angle)."""
    import cv2
    small = cv2.resize(img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    h, w = bw.shape
    if bw.sum() < 255 * 200:
        return img, 0.0

    def score(a):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
        r = cv2.warpAffine(bw, M, (w, h), flags=cv2.INTER_NEAREST)
        return float(np.var(r.sum(axis=1).astype(np.float64)))

    coarse = max(np.arange(-7, 7.01, 0.5), key=score)
    best = max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=score)
    if abs(best) < 0.15:
        return img, 0.0
    H, W = img.shape
    M = cv2.getRotationMatrix2D((W / 2, H / 2), float(best), 1.0)
    out = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=255)
    return out, float(best)


# ---------------------------------------------------------------- tableaux à traits dans une image
def _cluster(vals, tol=6):
    vals = sorted(vals)
    out = []
    for v in vals:
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [float(np.mean(g)) for g in out]


def find_grids(gray, cfg):
    """Détecte les tableaux à traits. Retourne (liste de grilles, masque de tous les traits)."""
    import cv2
    H, W = gray.shape
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(45, int(0.035 * W)), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(45, int(0.02 * H))))
    hor = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
    ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)
    hor = cv2.dilate(hor, np.ones((3, 1), np.uint8))
    ver = cv2.dilate(ver, np.ones((1, 3), np.uint8))
    lines = cv2.bitwise_or(hor, ver)
    near = cv2.dilate(lines, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(near, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    grids = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < 90 or h < 50:
            continue
        hs = hor[y:y + h, x:x + w] > 0
        vs = ver[y:y + h, x:x + w] > 0
        rows = _cluster([i for i in range(h) if hs[i].sum() >= 0.25 * w])
        cols = _cluster([j for j in range(w) if vs[:, j].sum() >= 0.25 * h])
        if len(rows) < 2 or len(cols) < 2:
            continue
        if rows[0] > 12:
            rows.insert(0, 0.0)
        if rows[-1] < h - 12:
            rows.append(float(h - 1))
        if cols[0] > 12:
            cols.insert(0, 0.0)
        if cols[-1] < w - 12:
            cols.append(float(w - 1))
        grids.append(dict(x=x, y=y, w=w, h=h, rows=rows, cols=cols, hs=hs, vs=vs))
    return grids, lines


def _merge_cells(g):
    rows, cols, hs, vs = g["rows"], g["cols"], g["hs"], g["vs"]
    R, C = len(rows) - 1, len(cols) - 1

    def h_present(ri, c0, c1):          # trait horizontal à rows[ri] sur les colonnes c0..c1-1
        y = int(round(rows[ri]))
        xa, xb = int(cols[c0]) + 3, int(cols[c1]) - 3
        if xb <= xa:
            return True
        band = hs[max(0, y - 4):y + 5, xa:xb].any(axis=0)
        return band.mean() >= 0.6

    def v_present(ci, r0, r1):
        x = int(round(cols[ci]))
        ya, yb = int(rows[r0]) + 3, int(rows[r1]) - 3
        if yb <= ya:
            return True
        band = vs[ya:yb, max(0, x - 4):x + 5].any(axis=1)
        return band.mean() >= 0.6

    covered = np.zeros((R, C), bool)
    spans = []
    for r in range(R):
        for c in range(C):
            if covered[r, c]:
                continue
            c1 = c + 1
            while c1 < C and not v_present(c1, r, r + 1):
                c1 += 1
            r1 = r + 1
            while r1 < R and not h_present(r1, c, c1):
                r1 += 1
            covered[r:r1, c:c1] = True
            spans.append((r, c, r1, c1))
    return spans


def _ocr_words_img(img, lang, psm):
    import pytesseract
    data = pytesseract.image_to_data(img, lang=lang, config=f"--psm {psm} --oem 1",
                                     output_type=pytesseract.Output.DICT)
    out = []
    for i, txt in enumerate(data["text"]):
        t = (txt or "").strip()
        if not t:
            continue
        try:
            conf = float(data["conf"][i])
        except ValueError:
            conf = -1
        out.append((t, conf, data["left"][i], data["top"][i], data["width"][i], data["height"][i],
                    (data["block_num"][i], data["par_num"][i], data["line_num"][i])))
    return out


_BULLET_OCR = {"e", "o", "°", "©", "®", "«", "»", "¢", "*", "·", "-", "–", "—", "•", "@"}


def _words_from(ocr, ox, oy, sx, sy, cfg):
    groups = defaultdict(list)
    for t, conf, l, t0, w, h, key in ocr:
        if conf < cfg.ocr_conf_min:
            continue
        groups[key].append((t, (ox + l) * sx, (oy + t0) * sy, (ox + l + w) * sx, (oy + t0 + h) * sy))
    lines = []
    for key, ws in sorted(groups.items()):
        ws.sort(key=lambda z: z[1])
        hs = sorted(z[4] - z[2] for z in ws)
        size = hs[len(hs) * 2 // 3] / 0.93          # taille robuste (ni minuscules seules, ni accents isolés)
        base = median(z[4] for z in ws)
        if len(ws) >= 2 and ws[0][0] in _BULLET_OCR and (ws[1][1] - ws[0][3]) > 0.5 * size and (ws[0][3] - ws[0][1]) <= size:
            ws[0] = ("\u2022",) + ws[0][1:]          # puce lue comme « e », « o », « ° »…
        words = [Word(t, x0, y0, x1, y1, cfg.ocr_font, size, 0, 0, base, None, k > 0, 0)
                 for k, (t, x0, y0, x1, y1) in enumerate(ws)]
        lines.append(Line(words, bid=key[0]))
    return lines


def ocr_lines(page, cfg):
    """OCR d'une page scannée. Retourne (lignes, tableaux, angle de redressement)."""
    import cv2
    from .model import Cell, Tbl
    from .paras import lines_to_paras
    lang, d = resolve_lang(cfg.ocr_lang)
    if d:
        os.environ["TESSDATA_PREFIX"] = d
    pix = page.get_pixmap(dpi=cfg.ocr_dpi, colorspace=pymupdf.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
    angle = 0.0
    if cfg.ocr_deskew:
        img, angle = deskew(img)
    sx = page.rect.width / pix.width
    sy = page.rect.height / pix.height

    grids, linemask = find_grids(img, cfg)
    clean = img.copy()
    clean[cv2.dilate(linemask, np.ones((5, 5), np.uint8)) > 0] = 255        # on efface les traits
    page_img = clean.copy()
    for g in grids:
        page_img[g["y"]:g["y"] + g["h"], g["x"]:g["x"] + g["w"]] = 255       # le contenu des tableaux est lu cellule par cellule

    lines = _words_from(_ocr_words_img(page_img, lang, 3), 0, 0, sx, sy, cfg)

    tables = []
    for g in grids:
        spans = _merge_cells(g)
        rows, cols = g["rows"], g["cols"]
        col_edges = [(g["x"] + c) * sx for c in cols]
        row_edges = [(g["y"] + r) * sy for r in rows]
        cells = []
        for r0, c0, r1, c1 in spans:
            rect = (col_edges[c0], row_edges[r0], col_edges[c1], row_edges[r1])
            cell = Cell(r0, c0, r1, c1, rect)
            cell.borders = {k: ("000000", 0.75) for k in ("top", "bottom", "left", "right")}
            xa, xb = g["x"] + int(cols[c0]) + 5, g["x"] + int(cols[c1]) - 5
            ya, yb = g["y"] + int(rows[r0]) + 5, g["y"] + int(rows[r1]) - 5
            if xb - xa > 12 and yb - ya > 12:
                crop = clean[ya:yb, xa:xb]
                if (crop < 140).mean() > 0.004:
                    ls = _words_from(_ocr_words_img(crop, lang, 6), xa, ya, sx, sy, cfg)
                    cell.paras = ls                   # lignes brutes, converties en paragraphes après normalisation
            cells.append(cell)
        bbox = (col_edges[0], row_edges[0], col_edges[-1], row_edges[-1])
        t = Tbl(bbox, col_edges, row_edges, cells, ruled=True)
        t.stroke_w = 0.75
        tables.append(t)

    # normalisation des tailles : corps dominant (±15 %)
    all_lines = list(lines) + [l for t in tables for c in t.cells for l in c.paras]
    if all_lines:
        body = median(l.size for l in lines) if lines else median(l.size for l in all_lines)
        for L in all_lines:
            s = body if abs(L.size - body) / body < 0.3 else round(L.size * 2) / 2
            for w in L.words:
                w.size = s
            L.refresh()
    for t in tables:
        for c in t.cells:
            c.paras = lines_to_paras(c.paras, cfg) if c.paras else []
    return lines, tables, angle


def ocr_page_words(page, cfg):
    """OCR de toute la page (sans redressement) -> [(texte, x0, y0, x1, y1)] en coordonnées PDF."""
    import pytesseract
    lang, d = resolve_lang(cfg.ocr_lang)
    if d:
        os.environ["TESSDATA_PREFIX"] = d
    pix = page.get_pixmap(dpi=cfg.ocr_dpi, colorspace=pymupdf.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    data = pytesseract.image_to_data(img, lang=lang, config="--psm 3 --oem 1", output_type=pytesseract.Output.DICT)
    sx, sy = page.rect.width / pix.width, page.rect.height / pix.height
    out = []
    for i, txt in enumerate(data["text"]):
        t = (txt or "").strip()
        if not t:
            continue
        try:
            conf = float(data["conf"][i])
        except ValueError:
            conf = -1
        if conf < cfg.ocr_conf_min:
            continue
        l, t0, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        out.append((t, l * sx, t0 * sy, (l + w) * sx, (t0 + h) * sy))
    return out
