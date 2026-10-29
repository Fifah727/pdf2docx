"""Pipeline complet : PDF -> modèle de pages -> DOCX."""
import pymupdf

from .build import build_page
from .config import Config
from .layout import order_page
from .paras import assign_headings, body_size, split_headers
from .render import Renderer
from . import fonts, metrics
from collections import defaultdict


def convert(pdf_path, docx_path, cfg=None, progress=None):
    cfg = cfg or Config()
    src = pymupdf.open(pdf_path)
    n = len(src)
    if cfg.max_pages:
        n = min(n, cfg.max_pages)
    pages = []
    for i in range(n):
        pages.append(build_page(src[i], cfg, i + 1))
        if progress:
            progress(i + 1, n)
    fonts.OVERRIDE.clear()
    by_font = defaultdict(list)
    def walk(it):
        if it.kind == "para":
            for w in it.words:
                by_font[w.font].append(w)
        elif it.kind == "tbl":
            for c in it.cells:
                for x in c.paras:
                    walk(x)
        elif it.kind == "box":
            for x in it.children:
                walk(x)
    for pg in pages:
        for it in pg.items:
            walk(it)
    fonts.OVERRIDE.update(fonts.fit_unknown(by_font, metrics.width, tex="tex" in ((src.metadata or {}).get("producer", "") + (src.metadata or {}).get("creator", "")).lower()))
    allparas = [it for pg in pages for it in pg.items if it.kind == "para"]
    body = body_size(allparas)
    assign_headings(allparas, body, cfg)
    hdr, ftr = split_headers(pages, cfg)
    for pg in pages:
        order_page(pg, cfg)
    r = Renderer(cfg)
    if pages:
        W, H = pages[0].W, pages[0].H
        all_nodes = [x for pg in pages for x in pg.nodes]
        L = min((x.x0 for x in all_nodes), default=36.0)
        R = max((x.x1 for x in all_nodes), default=W - 36.0)
        r.make_hf(hdr, ftr, n, W, H, L, R)
    for pg in pages:
        r.render_page(pg)
    r.save(docx_path)
    return pages
