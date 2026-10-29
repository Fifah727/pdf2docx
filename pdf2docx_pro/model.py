"""Modèle intermédiaire : mots -> lignes -> paragraphes / tableaux / images / encadrés / colonnes."""
from collections import Counter
from statistics import median

from .fonts import style_of


class Word:
    __slots__ = ("text", "x0", "y0", "x1", "y1", "font", "size", "flags", "color",
                 "base", "script", "space", "lid", "under")

    def __init__(self, text, x0, y0, x1, y1, font="Arial", size=10.0, flags=0, color=0,
                 base=None, script=None, space=True, lid=0):
        self.text = text
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.font, self.size, self.flags, self.color = font, size, flags, color
        self.base = y1 if base is None else base
        self.script = script          # None | "sup" | "sub"
        self.space = space            # espace avant ce mot ?
        self.lid = lid
        self.under = False

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    @property
    def fmt(self):
        fam, b, i = style_of(self.font, self.flags)
        return fam, b, i


class Line:
    def __init__(self, words, bid=0):
        self.words = words
        self.bid = bid
        self.refresh()

    def refresh(self):
        ws = self.words
        self.x0 = min(w.x0 for w in ws)
        self.y0 = min(w.y0 for w in ws)
        self.x1 = max(w.x1 for w in ws)
        self.y1 = max(w.y1 for w in ws)
        c = Counter()
        for w in ws:
            c[round(w.size * 2) / 2] += len(w.text)
        self.size = c.most_common(1)[0][0]
        main = [w for w in ws if w.script is None] or ws
        self.base = median(w.base for w in main)

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def text(self):
        return " ".join(w.text for w in self.words)


class Item:
    kind = ""

    def __init__(self, bbox):
        self.bbox = tuple(float(v) for v in bbox)

    x0 = property(lambda s: s.bbox[0])
    y0 = property(lambda s: s.bbox[1])
    x1 = property(lambda s: s.bbox[2])
    y1 = property(lambda s: s.bbox[3])
    w = property(lambda s: s.bbox[2] - s.bbox[0])
    h = property(lambda s: s.bbox[3] - s.bbox[1])

    # position du haut / du bas de la "boîte" occupée dans le flux Word
    @property
    def top(self):
        return self.y0

    @property
    def bottom(self):
        return self.y1


class Para(Item):
    kind = "para"

    def __init__(self, lines):
        self.lines = lines
        self.level = 0
        self.marker = None        # Word du marqueur de liste
        self.text_x0 = None
        self.floats = []          # images flottantes ancrées dans ce paragraphe
        self.eff_L = None         # bord gauche effectif (texte à droite d'une image flottante)
        self.eff_R = None
        self.near_float = False
        self.bg = None
        self.refresh()

    def refresh(self):
        ls = self.lines
        self.bbox = (min(l.x0 for l in ls), min(l.y0 for l in ls),
                     max(l.x1 for l in ls), max(l.y1 for l in ls))
        c = Counter()
        for l in ls:
            for w in l.words:
                c[round(w.size * 2) / 2] += len(w.text)
        self.size = c.most_common(1)[0][0]
        n = len(ls)
        if n >= 2:
            d = [ls[i + 1].base - ls[i].base for i in range(n - 1)]
            self.pitch = min(max(median(d), 0.8 * self.size), 3.2 * self.size)
        else:
            self.pitch = max(ls[0].height, 1.05 * self.size)
        self.pitch = round(self.pitch * 20) / 20     # valeur réellement écrite (twips)

    @property
    def n(self):
        return len(self.lines)

    @property
    def words(self):
        return [w for l in self.lines for w in l.words]

    @property
    def text(self):
        return " ".join(l.text for l in self.lines)

    @property
    def top(self):
        return self.y0 - (self.pitch - self.lines[0].height) / 2.0

    @property
    def bottom(self):
        return self.top + self.n * self.pitch


class Img(Item):
    kind = "img"

    def __init__(self, bbox, data, ext="png", formula=False):
        super().__init__(bbox)
        self.data = data
        self.ext = ext
        self.formula = formula
        self.floating = False
        self.host = None


class Shape(Item):
    """Tracé vectoriel libre (cadre, filet, forme courbe) ancré derrière le texte à sa position absolue."""
    kind = "shape"

    def __init__(self, bbox, paths, stroke, width, fill, dash=False):
        super().__init__(bbox)
        self.paths = paths          # liste de sous-chemins : [("M",x,y) | ("L",x,y) | ("C",x1,y1,x2,y2,x,y) | ("Z",)]
        self.stroke, self.width, self.fill, self.dash = stroke, width, fill, dash


class Cell:
    def __init__(self, r0, c0, r1, c1, rect):
        self.r0, self.c0, self.r1, self.c1 = r0, c0, r1, c1   # r1/c1 exclusifs
        self.rect = tuple(rect)
        self.paras = []
        self.fill = None
        self.vcenter = False
        self.borders = {}      # {'top'|'bottom'|'left'|'right': (hex, épaisseur pt)}


class Tbl(Item):
    kind = "tbl"

    def __init__(self, bbox, col_edges, row_edges, cells, ruled):
        super().__init__(bbox)
        self.col_edges, self.row_edges = col_edges, row_edges
        self.cells = cells
        self.ruled = ruled
        self.stroke = "000000"
        self.stroke_w = 0.5

    @property
    def top(self):
        return self.row_edges[0]

    @property
    def bottom(self):
        return self.row_edges[-1]


class Box(Item):
    """Encadré : cellule unique avec fond / bordure contenant des éléments."""
    kind = "box"

    def __init__(self, bbox, children, fill=None, stroke=None, stroke_w=0.5):
        super().__init__(bbox)
        self.children = children      # nœuds ordonnés (rempli par layout)
        self.fill, self.stroke, self.stroke_w = fill, stroke, stroke_w


class Cols(Item):
    """Éléments côte à côte : vraies colonnes Word ou tableau de mise en page."""
    kind = "cols"

    def __init__(self, groups, boxes):
        self.groups = groups          # list[list[nœud]]
        self.gboxes = boxes           # list[(x0,y0,x1,y1)]
        super().__init__((min(b[0] for b in boxes), min(b[1] for b in boxes),
                          max(b[2] for b in boxes), max(b[3] for b in boxes)))
        self.real = False

    @property
    def top(self):
        return min(node_top(g[0]) for g in self.groups if g)

    @property
    def bottom(self):
        return max(node_bottom(g[-1]) for g in self.groups if g)


def node_top(n):
    return n.top


def node_bottom(n):
    return n.bottom


class PageModel:
    def __init__(self, W, H, number):
        self.W, self.H, self.number = W, H, number
        self.items = []     # éléments à plat (avant ordonnancement)
        self.nodes = []     # éléments ordonnés (après layout)
        self.scanned = False
        self.overlays = []     # Shape ancrées derrière le texte
        self.skip_rects = []   # zones converties en image (figures/formules) pour le banc d'essai
        self.stats = Counter()
