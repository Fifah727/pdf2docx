"""Diagnostic : décalage dx/dy médian entre mots uniques de l'original et de la conversion, par tiers de page."""
import sys, pymupdf, numpy as np
from collections import Counter, defaultdict
name, pg = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0
d = "synth" if name.startswith("s") and name[1].isdigit() else "real"
A = pymupdf.open(f"corpus/{d}/{name}.pdf"); B = pymupdf.open(f"out/work/{name}.pdf")
wa, wb = A[pg].get_text("words"), B[pg].get_text("words")
ca, cb = Counter(w[4] for w in wa), Counter(w[4] for w in wb)
ib = {w[4]: w for w in wb if cb[w[4]] == 1}
rows = []
for w in wa:
    if ca[w[4]] == 1 and w[4] in ib:
        v = ib[w[4]]
        rows.append(((w[0]+w[2])/2, (w[1]+w[3])/2, (v[0]+v[2])/2-(w[0]+w[2])/2, (v[1]+v[3])/2-(w[1]+w[3])/2, w[4]))
r = np.array([x[:4] for x in rows])
print(name, "page", pg, "mots appariés:", len(r), "/", len(wa), "| pages out:", len(B))
H = A[pg].rect.height
for i in range(5):
    m = (r[:,1] >= i*H/5) & (r[:,1] < (i+1)*H/5)
    if m.any(): print(f"  y {int(i*H/5):3d}-{int((i+1)*H/5):3d}: n={m.sum():3d}  dx={np.median(r[m,2]):6.1f}  dy={np.median(r[m,3]):6.1f}")
