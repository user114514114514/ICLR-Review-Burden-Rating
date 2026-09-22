"""Local read-only query UI over the SQLite cache."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .constants import SCORE_VERSION, SCORE_YEAR_MIN
from .database import (DEFAULT_DATABASE, connect_database, database_status, get_author_report,
                       score_distribution, scored_years, search_authors, top_a, top_s)
from .name_search import load_name_index
from .dataset_source import YEARS
from .errors import BurdenError
from .profiles import public_output
from .storage import canonical_json

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
RANK_K = 100
SEARCH_LIMIT = 30


def api_dispatch(path, query, db_path=DEFAULT_DATABASE):
    if path == "/api/meta":
        status = database_status(db_path)
        return {"score_version": SCORE_VERSION,
                "years": status.get("years") or scored_years({"years": list(YEARS)}),
                "score_year_min": SCORE_YEAR_MIN,
                "source_date": status.get("source_date"), "source_snapshot": status.get("source_snapshot")}
    if path == "/api/search":
        q = (query.get("q") or [""])[0]
        return {"query": q, "results": search_authors(q, db_path, limit=SEARCH_LIMIT)}
    if path == "/api/author":
        author_id = (query.get("id") or [""])[0]
        if not author_id:
            raise BurdenError("Author id is required")
        return get_author_report(author_id, db_path)
    if path == "/api/rank":
        metric = ((query.get("metric") or ["S"])[0] or "S").upper()
        if metric == "S":
            return top_s(k=RANK_K, db_path=db_path)
        if metric == "A":
            try:
                year = int((query.get("year") or [""])[0])
            except (TypeError, ValueError) as exc:
                raise BurdenError("Ranking by A requires an integer year") from exc
            return top_a(year, k=RANK_K, db_path=db_path)
        raise BurdenError("Ranking metric must be S or A")
    if path == "/api/stats":
        metric = ((query.get("metric") or ["S"])[0] or "S").upper()
        if metric == "S":
            return score_distribution("S", db_path=db_path)
        if metric == "A":
            try:
                year = int((query.get("year") or [""])[0])
            except (TypeError, ValueError) as exc:
                raise BurdenError("Stats by A requires an integer year") from exc
            return score_distribution("A", year=year, db_path=db_path)
        raise BurdenError("Stats metric must be S or A")
    raise BurdenError(f"Unknown API path: {path}")


def _safe_static(rel):
    root = WEB_ROOT.resolve()
    target = (root / unquote(rel).lstrip("/")).resolve()
    if target != root and root not in target.parents:
        return None
    if target.is_dir():
        target = target / "index.html"
    return target if target.is_file() else None


def make_handler(db_path):
    database = str(db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print("%s - %s" % (self.address_string(), fmt % args), flush=True)

        def _send(self, status, body, content_type):
            payload = body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path or "/"
            if path.startswith("/api/"):
                try:
                    data = public_output(api_dispatch(path, parse_qs(parsed.query), database))
                    self._send(200, canonical_json(data), "application/json; charset=utf-8")
                except BurdenError as exc:
                    self._send(400, canonical_json({"error": str(exc)}), "application/json; charset=utf-8")
                except (OSError, json.JSONDecodeError) as exc:
                    self._send(500, canonical_json({"error": str(exc)}), "application/json; charset=utf-8")
                return
            if path in {"/", "/index.html"}:
                rel = "index.html"
            else:
                rel = path
            static = _safe_static(rel)
            if static is None:
                self._send(404, canonical_json({"error": "Not found"}), "application/json; charset=utf-8")
                return
            content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                            ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                            ".woff2": "font/woff2", ".woff": "font/woff"}.get(
                static.suffix, "application/octet-stream")
            self._send(200, static.read_bytes(), content_type)

    return Handler


def serve_app(host="127.0.0.1", port=8765, db_path=DEFAULT_DATABASE):
    conn = connect_database(db_path)
    try:
        load_name_index(conn)
    finally:
        conn.close()
    httpd = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"ICLR Review Burden UI: http://{host}:{port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
