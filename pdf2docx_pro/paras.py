"""Mots -> lignes -> paragraphes ; listes ; titres ; en-têtes / pieds de page répétés."""
import re
from collections import Counter, defaultdict

from .model import Line, Para

BULLET = re.compile(
    r"^(?:[•▪◦●■□○·‣⁃▸►▶–—*\-]|\d{1,3}[.)]|\(\d{1,3}\)|[a-z]\))$")


# ---------------------------------------------------------------- utilitaires mots
def partition_words(lines, keyfn):
    """Sépare les mots pour lesquels keyfn(mot) n'est pas None. Retourne (lignes restantes, {clé: [mots]})."""
    kept, taken = [], defaultdict(list)
    for L in lines:
        keep = []
        for w in L.words:
            k = keyfn(w)
            if k is None:
                keep.append(w)
            else:
                taken[k].append(w)
        if keep:
            kept.append(L if len(keep) == len(L.words) else Line(keep, L.bid))
    return kept, taken


def fix_spaces(ws):
    """Recalcule « espace avant » aux jonctions de fragments d'une même ligne."""
    ws[0].space = False
    for a, b in zip(ws, ws[1:]):
        if not b.space and (b.x0 - a.x1) > 0.12 * max(b.size, 4.0):
            b.space = True


def words_to_lines(words, bid=0):
    """Regroupe des mots en lignes par ligne de base."""
    if not words:
        return []
    main = sorted([w for w in words if w.script is None], key=lambda w: (w.base, w.x0))
    scripts = [w for w in words if w.script is not None]
    rows = []
    for w in main:
        if rows and abs(w.base - rows[-1][0]) <= 0.45 * min(w.size, rows[-1][1]):
            rows[-1][2].append(w)
        else:
            rows.append([w.base, w.size, [w]])
    if not rows:
        rows = [[scripts[0].base, scripts[0].size, []]]
    for w in scripts:
        best = min(rows, key=lambda r: abs(r[0] - w.base))
        best[2].append(w)
    lines = []
    for base, size, ws in rows:
        ws.sort(key=lambda w: w.x0)
        fix_spaces(ws)
        lines.append(Line(ws, bid))
    return lines


# ---------------------------------------------------------------- paragraphes
def _merge_same_baseline(ls):
    out = []
    for L in ls:
        if out and abs(out[-1].base - L.base) < 0.25 * max(L.size, out[-1].size):
            ws = sorted(out.pop().words + L.words, key=lambda w: w.x0)
            fix_spaces(ws)
            out.append(Line(ws, L.bid))
        else:
            out.append(L)
    return out


def _marker_line(ln):
    ws = ln.words
    return len(ws) >= 2 and BULLET.match(ws[0].text) is not None and (ws[1].x0 - ws[0].x1) > 0.12 * ln.size


def _new_para(prev, ln, cur, cfg):
    size = max(prev.size, ln.size)
    dy = ln.base - prev.base
    if dy > cfg.para_gap * size or dy < -0.3 * size:
        return True
    if abs(ln.size - prev.size) > 1.2 and abs(ln.size - prev.size) / size > 0.08:
        return True
    if _marker_line(ln):
        return True
    pb = all(w.fmt[1] for w in prev.words)
    lb = all(w.fmt[1] for w in ln.words)
    if pb != lb and len(cur) == 1 and (pb or len(prev.words) <= 14 and lb):
        return True                     # titre en gras seul sur sa ligne / début d'un titre
    ref = prev.x0 if len(cur) == 1 else min(l.x0 for l in cur[1:])
    if ln.x0 - ref > cfg.para_indent * size:
        return True
    return False


def lines_to_paras(lines, cfg):
    groups, order = {}, []
    for L in lines:
        if L.bid not in groups:
            groups[L.bid] = []
            order.append(L.bid)
        groups[L.bid].append(L)
    paras = []
    for bid in order:
        ls = _merge_same_baseline(groups[bid])
        cur = [ls[0]]
        for prev, ln in zip(ls, ls[1:]):
            if _new_para(prev, ln, cur, cfg):
                paras.append(Para(cur))
                cur = [ln]
            else:
                cur.append(ln)
        paras.append(Para(cur))
    for p in paras:
        _detect_marker(p)
    return paras


def _detect_marker(p):
    ln = p.lines[0]
    if _marker_line(ln):
        p.marker = ln.words[0]
        p.text_x0 = ln.words[1].x0


# ---------------------------------------------------------------- titres
def body_size(paras):
    c = Counter()
    for p in paras:
        c[round(p.size * 2) / 2] += sum(len(w.text) for w in p.words)
    return c.most_common(1)[0][0] if c else 10.0


def assign_headings(paras, body, cfg):
    for p in paras:
        if p.n > 4:
            continue
        r = p.size / body
        text = p.text.strip()
        if r >= cfg.heading_ratio and len(text) <= 160:
            p.level = 1 if r >= 1.8 else 2 if r >= 1.45 else 3
        elif (cfg.bold_headings and p.n == 1 and r >= 0.98 and len(text) <= 90
              and all(w.fmt[1] for w in p.words) and not text.endswith((".", ",", ";", ":"))
              and len(p.words) <= 14):
            p.level = 3
        if p.level:
            p.marker = None
            p.text_x0 = None


# ---------------------------------------------------------------- en-têtes / pieds
def _key(p):
    if p.kind == "img":
        return ("img", round(p.x0 / 2), round(p.y0 / 2), round(p.w / 2), round(p.h / 2), len(p.data) // 256)
    return re.sub(r"\d+", "#", p.text.lower().strip())


def _zone(it, pg):
    if it.kind not in ("para", "img"):
        return None
    if it.kind == "img" and (it.w > 0.6 * pg.W or it.h > 0.08 * pg.H):
        return None
    return "h" if it.y1 < 0.09 * pg.H else "f" if it.y0 > 0.91 * pg.H else None


def split_headers(pages, cfg):
    """Retire des pages les éléments répétés en haut/bas (texte et images). Retourne (en-têtes, pieds) de la 1re occurrence."""
    n = len(pages)
    if not cfg.headers or n < 2:
        return [], []
    cnt = {"h": Counter(), "f": Counter()}
    for pg in pages:
        seen = set()
        for it in pg.items:
            z = _zone(it, pg)
            if z and (z, _key(it)) not in seen:
                seen.add((z, _key(it)))
                cnt[z][_key(it)] += 1
    need = max(2, int(0.5 * n + 0.5))
    rep = {z: {k for k, v in cnt[z].items() if v >= need and k != ""} for z in cnt}
    hdr, ftr = [], []
    first_no = {"h": None, "f": None}
    for pg in pages:
        keep = []
        for it in pg.items:
            z = _zone(it, pg)
            if z and _key(it) in rep[z]:
                if first_no[z] is None:
                    first_no[z] = pg.number
                if first_no[z] == pg.number:
                    (hdr if z == "h" else ftr).append((pg.number, it))
                continue
            keep.append(it)
        pg.items = keep
    return hdr, ftr
