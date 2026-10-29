"""Interface locale : limites (taille, pages, mot de passe), validation des réglages, sécurité, cycle complet."""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import pymupdf
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pdf2docx.gui import Limits, Refused, check_pdf, make_config, make_server, safe_name  # noqa: E402

SYNTH = os.path.join(ROOT, "corpus", "synth", "s4_cellules_fusionnees.pdf")


def make_pdf(pages=1, password=None):
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 100), f"Page {i + 1}")
    kw = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": password, "owner_pw": password + "x"} if password else {}
    return d.tobytes(**kw)


def test_pdf_valide_retourne_le_nombre_de_pages():
    assert check_pdf(make_pdf(3), Limits(max_pages=5)) == 3


@pytest.mark.parametrize("data,code,mot", [
    (make_pdf(6), 422, "6 pages"),
    (make_pdf(1, password="secret"), 422, "mot de passe"),
    (b"ceci n'est pas un pdf", 400, "pas un PDF"),
    (b"%PDF-1.4\n" + b"\x00" * 50, 422, "illisible"),
])
def test_pdf_refuse(data, code, mot):
    with pytest.raises(Refused) as e:
        check_pdf(data, Limits(max_pages=5))
    assert e.value.code == code and mot in e.value.message


def test_pdf_trop_lourd():
    lim = Limits(max_mb=1)
    lim.max_mb = 0.00001            # ~10 octets : tout PDF dépasse la limite
    with pytest.raises(Refused) as e:
        check_pdf(make_pdf(1), lim)
    assert e.value.code == 413


def test_reglages_valides_et_inconnus_ignores():
    lim = Limits(max_pages=10)
    cfg = make_config({"ocr": ["rm -rf"], "lang": ["fra; evil"], "formulas": ["x"], "pages": ["999"]}, lim)
    assert cfg.ocr == "auto" and cfg.ocr_lang == "fra+eng" and cfg.formula_mode == "image" and cfg.max_pages == 0
    cfg = make_config({"ocr": ["off"], "lang": ["fra"], "pages": ["1"], "shapes": ["0"], "keep": ["1"]}, lim)
    assert (cfg.ocr, cfg.ocr_lang, cfg.max_pages, cfg.detect_shapes, cfg.line_breaks) == ("off", "fra", 1, False, "keep")


def test_nom_de_fichier_nettoye():
    assert safe_name("../../etc/passwd") == "passwd"
    assert "/" not in safe_name("a/b\\c.pdf") and safe_name("") == "document.pdf"


@pytest.fixture()
def server():
    srv = make_server(0, Limits(max_mb=5, max_pages=3))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.jobs.shutdown()


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_serveur_page_limites_et_en_tetes(server):
    code, body, h = call(server + "/")
    assert code == 200 and b"FIFALIANA Sarobidy" in body and b"Made with curiosity" in body
    assert "default-src 'self'" in h["Content-Security-Policy"] and h["X-Frame-Options"] == "DENY"
    info = json.loads(call(server + "/info")[1])
    assert info["limits"]["max_pages"] == 3 and info["limits"]["max_mb"] == 5


def test_serveur_refuse_trop_de_pages_et_autres_origines(server):
    code, body, _ = call(server + "/convert", make_pdf(4), {"X-Filename": "a.pdf"})
    assert code == 422 and "Limite : 3 pages" in json.loads(body)["error"]
    code, _, _ = call(server + "/convert", make_pdf(1), {"X-Filename": "a.pdf", "Origin": "https://evil.example"})
    assert code == 403
    code, _, _ = call(server + "/info", None, {"Host": "evil.example"})
    assert code == 403


@pytest.mark.skipif(not os.path.exists(SYNTH), reason="corpus synthétique absent")
def test_serveur_cycle_complet(server):
    with open(SYNTH, "rb") as f:
        code, body, _ = call(server + "/convert?ocr=off", f.read(), {"X-Filename": "tableau.pdf"})
    assert code == 200
    jid = json.loads(body)["id"]
    for _ in range(120):
        st = json.loads(call(f"{server}/status?id={jid}")[1])
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert st["state"] == "done" and st["stats"]["pages"] == 1
    code, docx, h = call(f"{server}/download?id={jid}")
    assert code == 200 and docx[:2] == b"PK" and "tableau.docx" in h["Content-Disposition"]
    assert json.loads(call(f"{server}/delete?id={jid}", b"")[1])["ok"] is True
    assert call(f"{server}/status?id={jid}")[0] == 404
