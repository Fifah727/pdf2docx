"""Paramètres (seuils) du convertisseur. Tous réglables, tous testables par le banc d'essai."""
from dataclasses import dataclass, asdict, replace
import json


@dataclass
class Config:
    # ---- texte / titres -------------------------------------------------
    heading_ratio: float = 1.25      # taille >= ratio x corps  => titre
    bold_headings: bool = True       # ligne seule, entièrement en gras => titre
    para_gap: float = 2.0            # interligne (x taille) au-delà duquel on change de paragraphe
    para_indent: float = 0.6         # retrait de 1re ligne (x taille) => nouveau paragraphe
    # ---- mise en page ----------------------------------------------------
    col_gap: float = 7.0             # gouttière min (pt) pour séparer 2 colonnes
    row_gap: float = 0.5             # blanc vertical min (pt) pour séparer 2 blocs
    min_col_lines: int = 4           # lignes mini / colonne pour de vraies colonnes Word
    float_gap: float = 60.0          # distance max texte <-> image pour l'habillage
    # ---- tableaux --------------------------------------------------------
    borderless_tables: bool = True
    cell_gap_em: float = 1.4         # écart (x taille) qui sépare 2 cellules
    table_min_rows: int = 3
    table_max_words_per_cell: float = 6.0
    # ---- encadrés / figures ------------------------------------------------
    detect_boxes: bool = True
    detect_figures: bool = True
    figure_dpi: int = 200
    # ---- formules ----------------------------------------------------------
    formula_mode: str = "image"      # image | text
    formula_dpi: int = 300
    math_ratio: float = 0.30
    # ---- OCR -----------------------------------------------------------------
    ocr: str = "auto"                # auto | off | force
    ocr_lang: str = "fra+eng"
    ocr_dpi: int = 300
    ocr_min_chars: int = 25
    ocr_conf_min: int = 35
    ocr_deskew: bool = True
    ocr_font: str = "Arial"
    # ---- rendu -----------------------------------------------------------------
    line_mode: str = "exact"         # exact (interligne fixe, fidèle) | auto
    line_breaks: str = "sim"         # sim (césure de Word simulée, coupures forcées seulement si nécessaire) | reflow | keep
    detect_shapes: bool = True      # reproduire cadres / filets / formes libres en vectoriel
    slack: float = 0.03             # tolérance de largeur à droite (fraction)
    y_shift: float = 0.0             # décalage vertical global (pt), calibré par le banc d'essai
    headers: bool = True             # vrais en-têtes / pieds de page Word
    max_pages: int = 0               # 0 = toutes

    def with_(self, **kw):
        return replace(self, **kw)

    def to_json(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @staticmethod
    def from_json(s):
        return Config(**json.loads(s))
