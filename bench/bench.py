"""Banc d'essai de fidélité : PDF -> DOCX -> (LibreOffice) PDF, puis comparaison avec l'original.

Métriques par page :
  - text   : similarité du texte (ordre inclus) entre original et reconverti        [0..1]
  - pos    : fidélité de position des mots (distance moyenne normalisée)           [0..1]
  - visual : similarité d'image (SSIM) après rendu                                  [0..1]
  - pages  : même nombre de pages ?
Score global = 0.35*text + 0.30*pos + 0.35*visual (pénalisé si le nombre de pages diffère).

Usage :
  python bench/bench.py corpus/real corpus/synth --out out/report.json [--config cfg.json]
  python bench/bench.py --tune corpus/real corpus/synth     # recherche de seuils
"""
import argparse
import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pymupdf
from skimage.metrics import structural_similarity as ssim

from pdf2docx import Config, convert


def docx_to_pdf(docx, outdir):
    prof = tempfile.mkdtemp(prefix="lo_")
    cmd = ["soffice", f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to", "pdf",
           "--outdir", outdir, docx]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    shutil.rmtree(prof, ignore_errors=True)
    out = os.path.join(outdir, os.path.splitext(os.path.basename(docx))[0] + ".pdf")
    return out if os.path.exists(out) else None


def _norm(s):
    """Texte comparable : NFKC, minuscules, lettres/chiffres seulement (ignore puces, ponctuation, espaces)."""
    import unicodedata
    s = unicodedata.normalize("NFKC", s).lower().replace("\u00ad", "")
    return "".join(ch for ch in s if ch.isalnum())


def words_of(page):
    return [(w[4], (w[0] + w[2]) / 2, (w[1] + w[3]) / 2) for w in page.get_text("words")]


def _tokens(s):
    import unicodedata
    s = unicodedata.normalize("NFKC", s).lower().replace("\u00ad", "")
    return re.findall(r"[^\W_]+", s)


def page_text_sim(a, b):
    """0.5 x fidélité de l'ordre (séquence) + 0.5 x fidélité du contenu (sac de mots, insensible à l'ordre).
    L'ordre d'extraction d'un PDF est arbitraire (tableaux lus par colonnes, etc.) : le contenu seul ne doit pas être puni."""
    from collections import Counter
    ta, tb = _norm(a.get_text()), _norm(b.get_text())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    if len(ta) > 7000 or len(tb) > 7000:
        ga = [ta[i:i + 8] for i in range(0, len(ta), 8)]
        gb = [tb[i:i + 8] for i in range(0, len(tb), 8)]
        seq = difflib.SequenceMatcher(None, ga, gb, autojunk=False).ratio()
    else:
        seq = difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()
    ca, cb = Counter(_tokens(a.get_text())), Counter(_tokens(b.get_text()))
    inter = sum((ca & cb).values())
    bag = 2.0 * inter / max(sum(ca.values()) + sum(cb.values()), 1)
    return 0.5 * seq + 0.5 * bag


def page_pos_sim(a, b, W, H):
    wa, wb = words_of(a), words_of(b)
    if not wa:
        return 1.0 if not wb else 0.0
    if not wb:
        return 0.0
    from collections import defaultdict
    idx = defaultdict(list)
    for t, x, y in wb:
        idx[t].append((x, y))
    diag = (W * W + H * H) ** 0.5
    tot, n = 0.0, 0
    for t, x, y in wa:
        c = idx.get(t)
        if not c:
            tot += 1.0
        else:
            d = min(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for cx, cy in c) / (0.06 * diag)
            tot += min(1.0, d)
        n += 1
    return 1.0 - tot / n


def render_gray(page, W, H, scale=0.9):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return img


def page_visual(a, b, W, H):
    ia, ib = render_gray(a, W, H), render_gray(b, W, H)
    h, w = min(ia.shape[0], ib.shape[0]), min(ia.shape[1], ib.shape[1])
    ia, ib = ia[:h, :w], ib[:h, :w]
    # flou léger : on mesure la mise en page, pas l'anticrénelage
    import cv2
    ia, ib = cv2.GaussianBlur(ia, (0, 0), 1.2), cv2.GaussianBlur(ib, (0, 0), 1.2)
    return float(max(0.0, ssim(ia, ib, data_range=255)))


def evaluate(pdf, cfg, workdir, keep=True):
    name = os.path.splitext(os.path.basename(pdf))[0]
    docx = os.path.join(workdir, name + ".docx")
    t0 = time.time()
    pages_model = convert(pdf, docx, cfg)
    t_conv = time.time() - t0
    rpdf = docx_to_pdf(docx, workdir)
    res = {"name": name, "t_convert": round(t_conv, 1)}
    if not rpdf:
        res.update(score=0.0, error="LibreOffice a échoué")
        return res
    A, B = pymupdf.open(pdf), pymupdf.open(rpdf)
    tp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(pdf))), "truth", name + ".pdf")
    T = pymupdf.open(tp) if os.path.exists(tp) else A       # vérité terrain (scans) pour texte et positions
    n = len(pages_model)
    per = []
    for i in range(n):
        pa, pt = A[i], T[i]
        W, H = pa.rect.width, pa.rect.height
        if i >= len(B):
            per.append((0, 0, 0))
            continue
        pb = B[i]
        vis = page_visual(pa, pb, W, H)
        raw = pt.get_text()
        garbled = len(raw) > 40 and sum(1 for c in raw if (ord(c) < 32 and c not in "\n\r\t")) / len(raw) > 0.02
        if garbled:                      # texte source illisible (police sans table Unicode) : seule la comparaison visuelle est fiable
            per.append((vis, vis, vis))
        else:
            per.append((page_text_sim(pt, pb), page_pos_sim(pt, pb, W, H), vis))
    arr = np.array(per) if per else np.zeros((1, 3))
    text, pos, vis = arr.mean(axis=0)
    pen = 1.0 if len(B) == n else max(0.4, 1.0 - 0.1 * abs(len(B) - n) / max(n, 1) * 10)
    score = (0.35 * text + 0.30 * pos + 0.35 * vis) * min(1.0, pen)
    stats = {}
    for pm in pages_model:
        for k, v in pm.stats.items():
            stats[k] = stats.get(k, 0) + v
    res.update(pages_src=n, pages_out=len(B), text=round(text, 3), pos=round(pos, 3), visual=round(vis, 3),
               score=round(score, 3), stats=stats, per_page=[[round(x, 2) for x in p] for p in per[:40]])
    return res


def collect(paths):
    out = []
    for p in paths:
        out += sorted(glob.glob(os.path.join(p, "*.pdf"))) if os.path.isdir(p) else [p]
    return out


def run(pdfs, cfg, workdir, quiet=False):
    os.makedirs(workdir, exist_ok=True)
    results = []
    for f in pdfs:
        try:
            r = evaluate(f, cfg, workdir)
        except Exception as e:
            r = {"name": os.path.basename(f), "score": 0.0, "error": f"{type(e).__name__}: {e}"}
        results.append(r)
        if not quiet:
            if "error" in r:
                print(f"{r['name']:<28} ERREUR {r['error'][:90]}")
            else:
                print(f"{r['name']:<28} score {r['score']:.3f}  texte {r['text']:.3f}  pos {r['pos']:.3f}  "
                      f"visuel {r['visual']:.3f}  p {r['pages_src']}->{r['pages_out']}  {r['t_convert']}s")
    return results


def mean_score(results):
    return float(np.mean([r["score"] for r in results])) if results else 0.0


def tune(pdfs, base, workdir, params):
    """Recherche coordonnée (une variable à la fois) sur un petit sous-ensemble."""
    best = base
    best_s = mean_score(run(pdfs, best, workdir, quiet=True))
    print(f"départ : {best_s:.4f}")
    for name, values in params.items():
        for v in values:
            if getattr(best, name) == v:
                continue
            cand = best.with_(**{name: v})
            s = mean_score(run(pdfs, cand, workdir, quiet=True))
            print(f"  {name}={v}: {s:.4f}")
            if s > best_s + 1e-4:
                best, best_s = cand, s
        print(f"=> {name} = {getattr(best, name)} (score {best_s:.4f})")
    return best, best_s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default="out/report.json")
    ap.add_argument("--work", default="out/work")
    ap.add_argument("--config")
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--max-pages", type=int, default=0)
    a = ap.parse_args()
    cfg = Config.from_json(open(a.config).read()) if a.config else Config()
    cfg = cfg.with_(max_pages=a.max_pages or cfg.max_pages)
    pdfs = collect(a.paths)
    if a.tune:
        grid = {"para_gap": [1.4, 1.5, 1.8, 2.0], "slack": [0.004, 0.02, 0.03],
                "col_gap": [5.0, 9.0, 12.0], "cell_gap_em": [1.0, 1.2, 1.8, 2.2],
                "heading_ratio": [1.15, 1.35], "para_indent": [0.6, 1.2],
                "min_col_lines": [3, 6], "float_gap": [20.0, 60.0]}
        best, s = tune(pdfs, cfg.with_(max_pages=a.max_pages or 2), a.work + '_tune', grid)
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        open(os.path.join(os.path.dirname(a.out) or ".", "best_config.json"), "w").write(best.to_json())
        print("meilleure config enregistrée")
    else:
        res = run(pdfs, cfg, a.work)
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump(res, open(a.out, "w"), indent=1, ensure_ascii=False)
        print(f"\nSCORE MOYEN : {mean_score(res):.3f}")
