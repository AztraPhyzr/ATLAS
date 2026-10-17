from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("ATLAS_DB_PATH", ROOT / "atlas.sqlite3"))
PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 30 * 24 * 60 * 60
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_UPDATES = 12
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SESSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/history-atlas-logo.png": "history-atlas-logo.png",
    "/history-atlas-mascot.png": "history-atlas-mascot.png",
}
RESERVED_CONTENT_KEYS = {"informationPages", "deletedEvents", "eventTimelines"}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


@contextmanager
def connect_database(path: Path = DATABASE_PATH) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(path: Path = DATABASE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS site_content (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                content_json TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO site_content (id, content_json) VALUES (1, '{}');
            CREATE TABLE IF NOT EXISTS content_updates (
                id INTEGER PRIMARY KEY,
                action TEXT NOT NULL CHECK (action IN ('added', 'updated')),
                title TEXT NOT NULL,
                reference_type TEXT NOT NULL CHECK (reference_type IN ('page', 'event')),
                reference_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(site_content)")}
        if "version" not in columns:
            connection.execute(
                "ALTER TABLE site_content ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
            )


def public_content_entries(content: dict[str, object]) -> dict[tuple[str, str], tuple[str, str]]:
    entries: dict[tuple[str, str], tuple[str, str]] = {}
    pages = content.get("informationPages", [])
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_id = page.get("id")
            title = page.get("title")
            if isinstance(page_id, str) and isinstance(title, str):
                encoded = json.dumps(page, sort_keys=True, ensure_ascii=False)
                entries[("page", page_id)] = (title[:120], encoded)

    timelines = content.get("eventTimelines", {})
    if not isinstance(timelines, dict):
        timelines = {}
    for event_id, event_content in content.items():
        if event_id in RESERVED_CONTENT_KEYS or not isinstance(event_content, dict):
            continue
        if not event_content:
            continue
        event_details = event_content.get("eventDetails", {})
        if not isinstance(event_details, dict):
            event_details = {}
        title = event_details.get("name", event_id.replace("-", " ").title())
        if not isinstance(title, str):
            title = event_id.replace("-", " ").title()
        snapshot = {"content": event_content, "timeline": timelines.get(event_id, [])}
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
        entries[("event", event_id)] = (title[:120], encoded)

    for event_id, timeline in timelines.items():
        if ("event", event_id) not in entries:
            encoded = json.dumps({"timeline": timeline}, sort_keys=True, ensure_ascii=False)
            entries[("event", event_id)] = (event_id.replace("-", " ").title(), encoded)
    return entries


def find_content_changes(
    previous: dict[str, object], current: dict[str, object]
) -> list[tuple[str, str, str, str]]:
    old_entries = public_content_entries(previous)
    new_entries = public_content_entries(current)
    changes = []
    for (reference_type, reference_id), (title, snapshot) in new_entries.items():
        old_entry = old_entries.get((reference_type, reference_id))
        if old_entry is None:
            action = "added"
        elif old_entry[1] != snapshot:
            action = "updated"
        else:
            continue
        changes.append((action, title, reference_type, reference_id))
    return changes


class AtlasHTTPServer(ThreadingHTTPServer):
    database_path: Path


class AtlasHandler(BaseHTTPRequestHandler):
    server_version = "HistoryAtlas/1.0"

    @property
    def database_path(self) -> Path:
        return self.server.database_path

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/session":
            try:
                user = self._current_user()
                self._send_json(HTTPStatus.OK, {"user": {"email": user["email"]} if user else None})
            except sqlite3.Error:
                self.log_error("Could not read the current account.")
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "The server could not read the account."})
            return
        if path == "/api/content":
            try:
                with connect_database(self.database_path) as connection:
                    row = connection.execute(
                        "SELECT content_json, version FROM site_content WHERE id = 1"
                    ).fetchone()
                    content = json.loads(row["content_json"])
                    updates = self._read_updates(connection)
                self._send_json(
                    HTTPStatus.OK,
                    {"contributions": content, "updates": updates, "version": row["version"]},
                )
            except (OSError, sqlite3.Error, TypeError, ValueError):
                self.log_error("Could not read site information.")
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "The server could not read site information."})
            return
        static_name = STATIC_FILES.get(path)
        if static_name:
            self._send_static(static_name)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        try:
            self._check_origin()
            payload = self._read_json()
            if path == "/api/signup":
                self._signup(payload)
            elif path == "/api/login":
                self._login(payload)
            elif path == "/api/logout":
                self._logout()
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
        except ApiError as error:
            self._send_json(error.status, {"error": str(error)})
        except (OSError, sqlite3.Error, ValueError):
            self.log_error("Could not complete API request.")
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "The server could not complete this request."})

    def do_PUT(self) -> None:
        try:
            self._check_origin()
            if urlsplit(self.path).path != "/api/content":
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
            user = self._current_user()
            if user is None:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "Sign in to publish information.")
            payload = self._read_json()
            content = payload.get("contributions")
            if not isinstance(content, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "Content must be a JSON object.")
            version = payload.get("version")
            if type(version) is not int or version < 0:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Content version is missing or invalid.")
            encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with connect_database(self.database_path) as connection:
                row = connection.execute(
                    "SELECT content_json, version FROM site_content WHERE id = 1"
                ).fetchone()
                if row["version"] != version:
                    raise ApiError(
                        HTTPStatus.CONFLICT,
                        "Site information changed in another session. Reload the page before publishing.",
                    )
                previous = json.loads(row["content_json"])
                cursor = connection.execute(
                    "UPDATE site_content SET content_json = ?, version = version + 1 WHERE id = 1 AND version = ?",
                    (encoded, version),
                )
                if cursor.rowcount != 1:
                    raise ApiError(
                        HTTPStatus.CONFLICT,
                        "Site information changed in another session. Reload the page before publishing.",
                    )
                old_entries = public_content_entries(previous)
                new_entries = public_content_entries(content)
                for reference_type, reference_id in old_entries.keys() - new_entries.keys():
                    connection.execute(
                        """
                        DELETE FROM content_updates
                        WHERE reference_type = ? AND reference_id = ?
                        """,
                        (reference_type, reference_id),
                    )
                for action, title, reference_type, reference_id in find_content_changes(previous, content):
                    connection.execute(
                        """
                        INSERT INTO content_updates
                            (action, title, reference_type, reference_id, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (action, title, reference_type, reference_id, now),
                    )
                connection.execute(
                    """
                    DELETE FROM content_updates
                    WHERE id NOT IN (
                        SELECT id FROM content_updates ORDER BY id DESC LIMIT 500
                    )
                    """
                )
                updates = self._read_updates(connection)
            self._send_json(
                HTTPStatus.OK,
                {"contributions": content, "updates": updates, "version": version + 1},
            )
        except ApiError as error:
            self._send_json(error.status, {"error": str(error)})
        except (OSError, sqlite3.Error, ValueError, TypeError):
            self.log_error("Could not save site information.")
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "The server could not save this information."})

    def _signup(self, payload: dict[str, object]) -> None:
        email, password = self._credentials(payload)
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
        )
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        token = secrets.token_urlsafe(32)
        try:
            with connect_database(self.database_path) as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users (email, password_salt, password_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (email, salt, password_hash, created_at),
                )
                self._create_session(connection, token, cursor.lastrowid)
        except sqlite3.IntegrityError:
            raise ApiError(HTTPStatus.CONFLICT, "An account with that email already exists.") from None
        self._send_json(
            HTTPStatus.CREATED,
            {"user": {"email": email}},
            {"Set-Cookie": self._session_cookie(token)},
        )

    def _login(self, payload: dict[str, object]) -> None:
        email, password = self._credentials(payload)
        with connect_database(self.database_path) as connection:
            user = connection.execute(
                """
                SELECT id, email, password_salt, password_hash
                FROM users WHERE email = ?
                """,
                (email,),
            ).fetchone()
            if user is None:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "The email or password is incorrect.")
            password_hash = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), user["password_salt"], PASSWORD_ITERATIONS
            )
            if not hmac.compare_digest(password_hash, user["password_hash"]):
                raise ApiError(HTTPStatus.UNAUTHORIZED, "The email or password is incorrect.")
            token = secrets.token_urlsafe(32)
            self._create_session(connection, token, user["id"])
        self._send_json(
            HTTPStatus.OK,
            {"user": {"email": user["email"]}},
            {"Set-Cookie": self._session_cookie(token)},
        )

    def _logout(self) -> None:
        token = self._session_token()
        if token:
            with connect_database(self.database_path) as connection:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?",
                    (hashlib.sha256(token.encode("ascii")).hexdigest(),),
                )
        self._send_json(
            HTTPStatus.OK,
            {"user": None},
            {"Set-Cookie": "atlas_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"},
        )

    def _create_session(self, connection: sqlite3.Connection, token: str, user_id: int) -> None:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (
                hashlib.sha256(token.encode("ascii")).hexdigest(),
                user_id,
                time.time() + SESSION_SECONDS,
            ),
        )

    def _current_user(self) -> sqlite3.Row | None:
        token = self._session_token()
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with connect_database(self.database_path) as connection:
            user = connection.execute(
                """
                SELECT users.id, users.email
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (token_hash, time.time()),
            ).fetchone()
            if user is None:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return user

    def _session_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookie.get("atlas_session")
        token = morsel.value if morsel else None
        return token if token and SESSION_TOKEN_PATTERN.fullmatch(token) else None

    def _session_cookie(self, token: str) -> str:
        secure_flag = (
            "; Secure"
            if self.headers.get("X-Forwarded-Proto", "").split(",", maxsplit=1)[0].strip().lower()
            == "https"
            else ""
        )
        return (
            f"atlas_session={token}; Path=/; HttpOnly; SameSite=Strict{secure_flag}; "
            f"Max-Age={SESSION_SECONDS}"
        )

    def _credentials(self, payload: dict[str, object]) -> tuple[str, str]:
        email_value = payload.get("email")
        password_value = payload.get("password")
        if not isinstance(email_value, str) or not isinstance(password_value, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Enter an email address and password.")
        email = email_value.strip().lower()
        if len(email) > 160 or not EMAIL_PATTERN.fullmatch(email):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Enter a valid email address.")
        if not 8 <= len(password_value) <= 128:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Password must be between 8 and 128 characters.")
        return email, password_value

    def _read_json(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid request size.") from None
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request is empty or too large.")
        if self.headers.get_content_type() != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send JSON data.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid JSON data.") from None
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Send a JSON object.")
        return payload

    def _check_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed_origin = urlsplit(origin)
            except ValueError:
                raise ApiError(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed.") from None
            if (
                parsed_origin.scheme not in {"http", "https"}
                or parsed_origin.netloc.casefold() != self.headers.get("Host", "").casefold()
            ):
                raise ApiError(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed.")

    def _read_updates(self, connection: sqlite3.Connection) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT action, title, reference_type, reference_id, updated_at
            FROM content_updates ORDER BY id DESC LIMIT ?
            """,
            (MAX_UPDATES,),
        )
        return [dict(row) for row in rows]

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, name: str) -> None:
        path = ROOT / name
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "File not found."})
            return
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        super().log_message(format, *args)


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    initialize_database()
    server = AtlasHTTPServer((host, port), AtlasHandler)
    server.database_path = DATABASE_PATH
    print(f"History Atlas is available at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down History Atlas.")
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the History Atlas web app and API.")
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind (default: localhost only)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    arguments = parser.parse_args()
    run_server(arguments.host, arguments.port)
