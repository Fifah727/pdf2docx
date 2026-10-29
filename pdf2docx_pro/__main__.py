import argparse
import sys

from . import Config, convert


def main():
    if len(sys.argv) == 1:                      # sans argument : interface graphique
        from .gui import main as gui_main
        sys.argv = [sys.argv[0]]
        return gui_main()
    ap = argparse.ArgumentParser(prog="pdf2docx", description="PDF -> DOCX sans Word")
    ap.add_argument("pdf", help="sans argument : lance l'interface graphique")
    ap.add_argument("docx")
    ap.add_argument("--ocr", choices=["auto", "off", "force"], default="auto")
    ap.add_argument("--lang", default="fra+eng")
    ap.add_argument("--formulas", choices=["image", "text"], default="image")
    ap.add_argument("--keep-line-breaks", action="store_true", help="conserver les retours à la ligne du PDF")
    ap.add_argument("--config", help="fichier JSON de seuils (produit par le banc d'essai)")
    a = ap.parse_args()
    cfg = Config.from_json(open(a.config).read()) if a.config else Config()
    cfg = cfg.with_(ocr=a.ocr, ocr_lang=a.lang, formula_mode=a.formulas,
                    line_breaks="keep" if a.keep_line_breaks else cfg.line_breaks)
    convert(a.pdf, a.docx, cfg, progress=lambda i, n: print(f"\rpage {i}/{n}", end="", file=sys.stderr))
    print("\nOK ->", a.docx)


if __name__ == "__main__":
    main()
