"""Interface locale (navigateur) : glisser-déposer des PDF, réglages, progression, téléchargement des DOCX.

    python -m pdf2docx.gui                       # http://127.0.0.1:8765
    python -m pdf2docx.gui --max-pages 40 --max-mb 20 --no-browser

Aucune dépendance en plus (bibliothèque standard). Le serveur n'écoute que sur 127.0.0.1 : les fichiers ne quittent
jamais l'ordinateur. Les PDF trop lourds, trop longs, protégés ou illisibles sont refusés avant toute conversion.
"""
import argparse
import io
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
import zipfile
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from .config import Config

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC = {"/": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/favicon.svg": "favicon.svg"}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ------------------------------------------------------------------ limites
@dataclass
class Limits:
    """Garde-fous. Une page se convertit en 1 à 3 s : 60 pages = 1 à 3 minutes."""
    max_mb: int = 30              # taille maximale d'un PDF
    max_pages: int = 60           # nombre de pages maximal d'un PDF
    max_active: int = 10          # PDF en attente + en cours (tous utilisateurs confondus)
    ttl_minutes: int = 60         # durée de conservation des fichiers temporaires

    @property
    def max_bytes(self):
        return self.max_mb * 1024 * 1024

    @staticmethod
    def from_env():
        def get(name, default):
            try:
                return max(1, int(os.environ.get(name, default)))
            except ValueError:
                return default
        return Limits(get("PDF2DOCX_MAX_MB", 30), get("PDF2DOCX_MAX_PAGES", 60),
                      get("PDF2DOCX_MAX_ACTIVE", 10), get("PDF2DOCX_TTL_MINUTES", 60))


class Refused(Exception):
    """Fichier refusé : (code HTTP, message affichable tel quel à l'utilisateur)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def check_pdf(data, limits):
    """Valide un PDF reçu. Retourne son nombre de pages ou lève Refused."""
    if len(data) > limits.max_bytes:
        raise Refused(413, f"Fichier trop lourd ({len(data) / 1048576:.1f} Mo). Limite : {limits.max_mb} Mo.")
    if not data.lstrip()[:1024].startswith(b"%PDF"):
        raise Refused(400, "Ce fichier n'est pas un PDF.")
    try:
        import pymupdf
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise Refused(422, "PDF illisible ou corrompu.") from None
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise Refused(422, "PDF protégé par un mot de passe : retirez la protection puis réessayez.")
        n = doc.page_count
    finally:
        doc.close()
    if n < 1:
        raise Refused(422, "Ce PDF ne contient aucune page.")
    if n > limits.max_pages:
        raise Refused(422, f"Ce PDF compte {n} pages. Limite : {limits.max_pages} pages. "
                           f"Découpez-le en plusieurs fichiers.")
    return n


# ------------------------------------------------------------------ environnement (Tesseract)
def find_tesseract():
    """Retrouve tesseract même hors du PATH (Windows) et renvoie (chemin, langues installées)."""
    try:
        import pytesseract
    except ImportError:
        return None, []
    candidates = [shutil.which("tesseract"),
                  r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")]
    for path in candidates:
        if path and os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            try:
                langs = [x for x in pytesseract.get_languages(config="") if x != "osd"]
            except Exception:
                langs = []
            return path, langs
    return None, []


# ------------------------------------------------------------------ tâches
class Jobs:
    """Registre des conversions (mémoire + dossier temporaire). Une conversion à la fois : le moteur partage
    des tables de polices globales."""

    def __init__(self, limits):
        self.limits = limits
        self.root = tempfile.mkdtemp(prefix="pdf2docx_gui_")
        self.items = {}
        self.lock = threading.Lock()
        self.run_lock = threading.Lock()

    def active(self):
        with self.lock:
            return sum(1 for j in self.items.values() if j["state"] in ("queued", "running"))

    def add(self, filename, data, pages, cfg):
        if self.active() >= self.limits.max_active:
            raise Refused(429, "Trop de conversions en cours. Réessayez dans quelques instants.")
        jid = uuid.uuid4().hex[:12]
        folder = os.path.join(self.root, jid)
        os.makedirs(folder)
        pdf = os.path.join(folder, "in.pdf")
        with open(pdf, "wb") as f:
            f.write(data)
        name = safe_name(filename)
        job = {"id": jid, "name": name, "out_name": os.path.splitext(name)[0] + ".docx", "pdf": pdf,
               "docx": os.path.join(folder, "out.docx"), "cfg": cfg, "state": "queued",
               "page": 0, "total": pages, "created": time.time()}
        with self.lock:
            self.items[jid] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, jid):
        with self.lock:
            return self.items.get(jid)

    def remove(self, jid):
        with self.lock:
            job = self.items.pop(jid, None)
        if job:
            shutil.rmtree(os.path.dirname(job["pdf"]), ignore_errors=True)
        return job is not None

    def purge_expired(self):
        limit = time.time() - self.limits.ttl_minutes * 60
        with self.lock:
            old = [j["id"] for j in self.items.values() if j["created"] < limit and j["state"] not in ("queued", "running")]
        for jid in old:
            self.remove(jid)

    def cleanup_forever(self):
        while True:
            time.sleep(60)
            self.purge_expired()

    def _run(self, job):
        from . import convert
        with self.run_lock:
            job["state"] = "running"
            t0 = time.time()
            try:
                def progress(i, n):
                    job["page"], job["total"] = i, n
                pages = convert(job["pdf"], job["docx"], job["cfg"], progress=progress)
                job["stats"] = summarize(pages)
                job["state"] = "done"
            except Exception as e:                                # noqa: BLE001 - tout échec est rapporté à l'utilisateur
                job["state"] = "error"
                job["error"] = f"La conversion a échoué ({type(e).__name__}). Essayez sans les réglages avancés."
                job["trace"] = traceback.format_exc(limit=4)
            job["seconds"] = round(time.time() - t0, 1)

    def shutdown(self):
        shutil.rmtree(self.root, ignore_errors=True)


def safe_name(name):
    name = os.path.basename(name or "document.pdf")
    name = re.sub(r"[^\w.\- ()]", "_", name)
    return name or "document.pdf"


def summarize(pages):
    st = {}
    for pm in pages:
        for k, v in pm.stats.items():
            st[k] = st.get(k, 0) + v
    return {"pages": len(pages),
            "tableaux": st.get("tables_ruled", 0) + st.get("tables_borderless", 0) + st.get("tables_scanned", 0),
            "encadrés": st.get("boxes", 0), "figures": st.get("figures", 0), "formules": st.get("formulas", 0),
            "formes": st.get("shapes", 0), "lignes OCR": st.get("ocr_lines", 0),
            "pages scannées": sum(1 for pm in pages if pm.scanned)}


def make_config(opts, limits):
    """Réglages de la page web -> Config (valeurs validées : rien n'est transmis tel quel au moteur)."""
    def flag(key, default=True):
        return opts.get(key, ["1" if default else "0"])[0] == "1"

    def pick(key, allowed, default):
        v = opts.get(key, [default])[0]
        return v if v in allowed else default
    lang = pick("lang", ("fra+eng", "fra", "eng"), "fra+eng")
    cfg = Config().with_(
        ocr=pick("ocr", ("auto", "off", "force"), "auto"), ocr_lang=lang,
        formula_mode=pick("formulas", ("image", "text"), "image"),
        line_breaks="keep" if flag("keep", False) else "sim",
        detect_boxes=flag("boxes"), detect_figures=flag("figures"), borderless_tables=flag("borderless"),
        detect_shapes=flag("shapes"), headers=flag("headers"))
    first = opts.get("pages", ["0"])[0]
    if first.isdigit() and 0 < int(first) <= limits.max_pages:
        cfg = cfg.with_(max_pages=int(first))
    return cfg


def public(job):
    keys = ("id", "name", "out_name", "state", "page", "total", "error", "stats", "seconds")
    return {k: job[k] for k in keys if k in job}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "pdf2docx"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    @property
    def jobs(self):
        return self.server.jobs

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None, close=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
                         "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _guard(self):
        """Refuse tout ce qui ne vient pas de cette machine (DNS rebinding) ou d'un autre site web (CSRF)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, {"error": "Accès refusé."}, close=True)
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            self._send(403, {"error": "Origine refusée."}, close=True)
            return False
        return True

    def _job(self, q):
        return self.jobs.get((q.get("id") or [""])[0])

    def do_GET(self):
        if not self._guard():
            return None
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in STATIC:
            return self._static(STATIC[u.path])
        if u.path == "/info":
            lim = self.jobs.limits
            return self._send(200, {"tesseract": self.server.tess[0], "langs": self.server.tess[1],
                                    "limits": asdict(lim) | {"max_bytes": lim.max_bytes}})
        job = self._job(q)
        if u.path in ("/status", "/preview", "/download") and not job:
            return self._send(404, {"error": "Fichier introuvable (expiré ou supprimé)."})
        if u.path == "/status":
            return self._send(200, public(job))
        if u.path == "/preview":
            return self._preview(job, q)
        if u.path == "/download":
            if job["state"] != "done":
                return self._send(409, {"error": "Conversion pas encore terminée."})
            with open(job["docx"], "rb") as f:
                data = f.read()
            return self._send(200, data, DOCX_MIME,
                              {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(job['out_name'])}"})
        if u.path == "/download_all":
            return self._download_all(q)
        return self._send(404, {"error": "Introuvable."})

    def _static(self, name):
        path = os.path.join(WEB_DIR, name)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return self._send(404, {"error": "Introuvable."})
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "svg+xml")):
            ctype += "; charset=utf-8"
        return self._send(200, data, ctype)

    def _preview(self, job, q):
        try:
            import pymupdf
            with pymupdf.open(job["pdf"]) as doc:
                page = doc[min(max(int((q.get("p") or ["0"])[0]), 0), len(doc) - 1)]
                return self._send(200, page.get_pixmap(dpi=80).tobytes("png"), "image/png")
        except Exception:                                         # noqa: BLE001
            return self._send(500, {"error": "Aperçu indisponible."})

    def _download_all(self, q):
        ids = [i for i in (q.get("ids") or [""])[0].split(",") if i]
        buf, used = io.BytesIO(), set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for jid in ids[:50]:
                job = self.jobs.get(jid)
                if job and job["state"] == "done":
                    name, k = job["out_name"], 1
                    while name in used:
                        k += 1
                        name = f"{os.path.splitext(job['out_name'])[0]} ({k}).docx"
                    used.add(name)
                    z.write(job["docx"], name)
        if not used:
            return self._send(404, {"error": "Aucun fichier prêt."})
        return self._send(200, buf.getvalue(), "application/zip",
                          {"Content-Disposition": "attachment; filename*=UTF-8''documents_word.zip"})

    def do_POST(self):
        if not self._guard():
            return None
        u = urlparse(self.path)
        if u.path == "/delete":
            q = parse_qs(u.query)
            return self._send(200, {"ok": self.jobs.remove((q.get("id") or [""])[0])})
        if u.path != "/convert":
            return self._send(404, {"error": "Introuvable."})
        lim = self.jobs.limits
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self._send(411, {"error": "Fichier vide."}, close=True)
        if length > lim.max_bytes + 4096:            # refusé avant de lire le corps
            return self._send(413, {"error": f"Fichier trop lourd ({length / 1048576:.1f} Mo). Limite : {lim.max_mb} Mo."},
                              close=True)
        data = self.rfile.read(length)
        try:
            pages = check_pdf(data, lim)
            cfg = make_config(parse_qs(u.query), lim)
            job = self.jobs.add(unquote(self.headers.get("X-Filename", "document.pdf")), data, pages, cfg)
        except Refused as e:
            return self._send(e.code, {"error": e.message})
        return self._send(200, public(job))


def make_server(port, limits):
    srv = None
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit("Aucun port libre.")
    srv.jobs = Jobs(limits)
    srv.tess = find_tesseract()
    threading.Thread(target=srv.jobs.cleanup_forever, daemon=True).start()
    return srv


def main(argv=None):
    env = Limits.from_env()
    ap = argparse.ArgumentParser(prog="pdf2docx.gui", description="Interface graphique (navigateur local)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--max-mb", type=int, default=env.max_mb, help="taille maximale d'un PDF (Mo)")
    ap.add_argument("--max-pages", type=int, default=env.max_pages, help="nombre de pages maximal d'un PDF")
    a = ap.parse_args(argv)
    srv = make_server(a.port, Limits(max(1, a.max_mb), max(1, a.max_pages), env.max_active, env.ttl_minutes))
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    print(f"pdf2docx — interface sur {url}  (Ctrl+C pour quitter)")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.jobs.shutdown()


if __name__ == "__main__":
    main()
