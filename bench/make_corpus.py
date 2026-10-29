"""Génère un corpus synthétique ciblé : une difficulté par fichier (LaTeX + scans simulés)."""
import os
import subprocess
import tempfile

import numpy as np
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "corpus", "synth")
os.makedirs(OUT, exist_ok=True)

FR = ("Le traitement automatique des documents consiste à reconstruire la structure logique d'une page "
      "à partir de simples glyphes positionnés. Un fichier PDF ne connaît ni paragraphe, ni tableau, ni colonne : "
      "il décrit seulement où dessiner chaque caractère. Reconstituer une mise en page éditable demande donc "
      "d'analyser les alignements, les espacements et les polices. ")

PRE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[T1]{fontenc}\usepackage[utf8]{inputenc}
\usepackage[margin=2cm]{geometry}\usepackage{multicol,wrapfig,booktabs,multirow,amsmath,amssymb,graphicx,xcolor,tcolorbox,array,lipsum}
\setlength{\parindent}{1em}
"""


def tex(name, body, pre=PRE):
    src = pre + "\\begin{document}\n" + body + "\n\\end{document}\n"
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "a.tex"), "w").write(src)
        r = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "a.tex"], cwd=d,
                           capture_output=True, text=True, timeout=120)
        if not os.path.exists(os.path.join(d, "a.pdf")):
            print("ECHEC", name, r.stdout[-600:])
            return
        os.replace(os.path.join(d, "a.pdf"), os.path.join(OUT, name + ".pdf"))
        print("ok", name)


# 1. trois colonnes + titre pleine largeur
tex("s1_trois_colonnes", r"""
\begin{center}{\LARGE\bfseries Bulletin trimestriel de la recherche}\\[4pt]{\large Département d'informatique}\end{center}
\begin{multicols}{3}
\section*{Introduction}\lipsum[1-2]
\section*{Méthode}\lipsum[3-4]
\subsection*{Données}\lipsum[5]
\section*{Résultats}\lipsum[6-8]
\end{multicols}
\lipsum[9]
""")

# 2. encadrés (fond + bordure) et texte autour d'une image
tex("s2_encadres_image", r"""
\section*{Rapport d'activité}
\begin{tcolorbox}[colback=blue!5!white,colframe=blue!60!black,title=À retenir]
Les encadrés contiennent du texte avec un fond coloré et une bordure. Ils doivent rester des blocs distincts.
\end{tcolorbox}
\begin{wrapfigure}{r}{0.35\textwidth}
\centering\fbox{\rule{0pt}{3.2cm}\rule{4.2cm}{0pt}}\caption{Une image à droite}
\end{wrapfigure}
\lipsum[1-3]
\begin{tcolorbox}[colback=yellow!15!white,colframe=orange!70!black]
\textbf{Attention :} un second encadré, avec une autre couleur de fond et de bordure.
\end{tcolorbox}
\lipsum[4]
""")

# 3. tableaux sans bordures (booktabs), alignements et colspan
tex("s3_tableaux_sans_bordure", r"""
\section*{Ventes par région}
\begin{center}\begin{tabular}{lrrr}
\toprule Région & T1 & T2 & T3 \\ \midrule
Nord & 1\,250 & 1\,310 & 1\,402 \\ Sud & 980 & 1\,044 & 1\,120 \\ Est & 1\,515 & 1\,498 & 1\,630 \\ Ouest & 760 & 802 & 845 \\
\bottomrule\end{tabular}\end{center}
Texte entre deux tableaux. \lipsum[1]
\begin{center}\begin{tabular}{llcc}
\toprule Nom & Fonction & \multicolumn{2}{c}{Ancienneté} \\ \cmidrule(lr){3-4}
 & & Années & Mois \\ \midrule
Alice Martin & Directrice & 12 & 4 \\ Bruno Petit & Ingénieur & 7 & 9 \\ Chloé Durand & Analyste & 3 & 1 \\
\bottomrule\end{tabular}\end{center}
""")

# 4. tableau à bordures avec cellules fusionnées
tex("s4_cellules_fusionnees", r"""
\section*{Planning}
\begin{center}\renewcommand{\arraystretch}{1.6}
\begin{tabular}{|l|c|c|c|}\hline
\multirow{2}{*}{Projet} & \multicolumn{3}{c|}{Trimestre} \\ \cline{2-4}
 & T1 & T2 & T3 \\ \hline
Alpha & \multicolumn{2}{c|}{Phase 1} & Phase 2 \\ \hline
Bêta & Étude & \multicolumn{2}{c|}{Développement et tests} \\ \hline
\multirow{2}{*}{Gamma} & Cadrage & Pilote & Déploiement \\ \cline{2-4} & \multicolumn{3}{c|}{Support continu} \\ \hline
\end{tabular}\end{center}
\lipsum[2]
""")

# 5. formules (en ligne et affichées)
tex("s5_formules", r"""
\section*{Résultats théoriques}
Soit $f(x)=\sum_{k=0}^{n} a_k x^k$ un polynôme de degré $n$. On a, pour tout $x\in\mathbb{R}$ :
\begin{equation} \int_0^{\infty} e^{-x^2}\,dx=\frac{\sqrt{\pi}}{2}\end{equation}
\lipsum[1]
\[ \lim_{n\to\infty}\left(1+\frac{1}{n}\right)^n = e,\qquad \nabla\cdot\mathbf{E}=\frac{\rho}{\varepsilon_0}\]
\begin{align} x_{n+1} &= x_n-\frac{f(x_n)}{f'(x_n)}\\ \sigma^2 &= \frac{1}{N}\sum_{i=1}^{N}(x_i-\mu)^2\end{align}
\lipsum[2]
""")

# 6. document « Word-like » classique : titres, listes, en-têtes/pieds, 3 pages
tex("s6_rapport_classique", r"""
\usepackage{fancyhdr}\pagestyle{fancy}\fancyhf{}\lhead{Société Exemple}\rhead{Rapport 2026}\cfoot{Page \thepage}
""".replace("\\usepackage", "\\usepackage", 1) and r"""
\section{Contexte}\lipsum[1-2]
\subsection{Objectifs}
\begin{itemize}\item Améliorer la qualité de service\item Réduire les délais de traitement\item Former les équipes\end{itemize}
\begin{enumerate}\item Première étape\item Deuxième étape\item Troisième étape\end{enumerate}
\section{Analyse}\lipsum[3-6]
\section{Conclusion}\lipsum[7-8]
""", pre=PRE + "\\usepackage{fancyhdr}\\pagestyle{fancy}\\fancyhf{}\\lhead{Société Exemple}\\rhead{Rapport 2026}\\cfoot{Page \\thepage}\n")


# ----------------------------------------------------------------- scans simulés
def make_scan(name, src_pdf, page=0, angle=1.6, noise=10, dpi=200, blur=0.7):
    import cv2
    doc = pymupdf.open(src_pdf)
    pix = doc[page].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()
    h, w = img.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    img = cv2.warpAffine(img, M, (w, h), borderValue=235)
    img = cv2.GaussianBlur(img, (0, 0), blur)
    rng = np.random.default_rng(1)
    img = np.clip(img.astype(np.float32) + rng.normal(0, noise, img.shape) - 6, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    out = pymupdf.open()
    pg = out.new_page(width=doc[page].rect.width, height=doc[page].rect.height)
    pg.insert_image(pg.rect, stream=buf.tobytes())
    out.save(os.path.join(OUT, name + ".pdf"))
    print("ok", name)


tex("_src_fr", r"""
\section*{Compte rendu de la réunion}
""" + FR * 3 + r"""
\begin{itemize}\item Valider le budget\item Nommer un responsable\item Fixer la prochaine date\end{itemize}
""" + FR * 2 + r"""
\begin{center}\begin{tabular}{|l|r|}\hline Poste & Montant \\\hline Matériel & 1\,200 \\\hline Services & 800 \\\hline\end{tabular}\end{center}
""" + FR)
import shutil
TRUTH = os.path.join(os.path.dirname(HERE), "corpus", "truth")
os.makedirs(TRUTH, exist_ok=True)
make_scan("s7_scan_fr_incline", os.path.join(OUT, "_src_fr.pdf"), angle=1.6)
make_scan("s8_scan_fr_droit_propre", os.path.join(OUT, "_src_fr.pdf"), angle=0.0, noise=4, blur=0.5)
for n in ("s7_scan_fr_incline", "s8_scan_fr_droit_propre"):
    shutil.copy(os.path.join(OUT, "_src_fr.pdf"), os.path.join(TRUTH, n + ".pdf"))   # vérité terrain vectorielle
os.remove(os.path.join(OUT, "_src_fr.pdf"))
print("corpus synthétique terminé :", sorted(os.listdir(OUT)))
