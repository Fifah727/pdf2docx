"""Image comparative original | conversion pour des documents : python bench/cmp.py nom1 nom2 ... (page 1)."""
import sys, pymupdf
from PIL import Image, ImageDraw
rows = []
for name in sys.argv[1:]:
    d = "synth" if name.startswith("s") and name[1].isdigit() else "real"
    A = pymupdf.open(f"corpus/{d}/{name}.pdf"); B = pymupdf.open(f"out/work/{name}.pdf")
    ims = []
    for D in (A, B):
        D[0].get_pixmap(dpi=62).save("/tmp/_t.png"); ims.append(Image.open("/tmp/_t.png").copy())
    rows.append(ims)
W = sum(i.width for i in rows[0]) + 10; H = max(i.height for r in rows for i in r)
c = Image.new("RGB", (W * len(rows), H), "white")
for k, ims in enumerate(rows):
    c.paste(ims[0], (k * W, 0)); c.paste(ims[1], (k * W + ims[0].width + 10, 0))
    ImageDraw.Draw(c).line([(k * W + ims[0].width + 5, 0), (k * W + ims[0].width + 5, H)], fill=(200, 0, 0))
c.save("out/cmp.png"); print(c.size)
