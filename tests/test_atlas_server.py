import http.cookiejar
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from atlas_server import AtlasHandler, ThreadingHTTPServer, initialize_database


class QuietAtlasHandler(AtlasHandler):
    def log_message(self, format, *args):
        pass


class AtlasServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "atlas.sqlite3"
        initialize_database(self.database_path)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietAtlasHandler)
        self.server.database_path = self.database_path
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary_directory.cleanup()

    def request(self, path, method="GET", payload=None, headers=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            response = self.opener.open(request)
        except HTTPError as error:
            response = error
        return response.code, json.loads(response.read())

    def signup(self):
        return self.request(
            "/api/signup",
            method="POST",
            payload={"email": "reader@example.com", "password": "correct horse"},
        )

    def test_accounts_are_hashed_and_sessions_are_revocable(self):
        status, payload = self.signup()
        self.assertEqual(status, 201)
        self.assertEqual(payload, {"user": {"email": "reader@example.com"}})

        with closing(sqlite3.connect(self.database_path)) as connection:
            salt, password_hash = connection.execute(
                "SELECT password_salt, password_hash FROM users"
            ).fetchone()
            self.assertNotEqual(password_hash, b"correct horse")
            self.assertEqual(len(salt), 16)
            self.assertEqual(len(password_hash), 32)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)

        status, payload = self.request("/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"user": {"email": "reader@example.com"}})
        self.assertEqual(self.request("/api/logout", "POST", {})[0], 200)
        self.assertEqual(self.request("/api/session")[1], {"user": None})

    def test_public_content_updates_do_not_expose_account_information(self):
        self.signup()
        self.assertEqual(self.request("/api/content")[0], 200)
        status, payload = self.request(
            "/api/content",
            "PUT",
            {
                "version": 0,
                "contributions": {
                    "informationPages": [
                        {
                            "id": "page-1",
                            "title": "A new article",
                            "description": "Public historical information",
                        }
                    ],
                    "world-war-one": {
                        "eventDetails": {"name": "World War I", "summary": "An updated chapter"}
                    },
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(
            {(update["action"], update["title"]) for update in payload["updates"]},
            {("added", "A new article"), ("added", "World War I")},
        )
        self.assertNotIn("reader@example.com", json.dumps(payload))
        self.assertNotIn("password", json.dumps(payload).lower())

        status, payload = self.request(
            "/api/content",
            "PUT",
            {
                "version": 1,
                "contributions": {
                    "informationPages": [
                        {
                            "id": "page-1",
                            "title": "A revised article",
                            "description": "Updated public historical information",
                        }
                    ],
                    "world-war-one": {
                        "eventDetails": {"name": "World War I", "summary": "An updated chapter"}
                    },
                },
            },
        )
        self.assertEqual(status, 200)
        page_update = next(update for update in payload["updates"] if update["reference_id"] == "page-1")
        self.assertEqual(page_update["action"], "updated")
        self.assertEqual(page_update["title"], "A revised article")

    def test_publishing_requires_authentication_and_rejects_stale_versions(self):
        self.assertEqual(
            self.request("/api/content", "PUT", {"version": 0, "contributions": {}})[0],
            401,
        )
        self.signup()
        self.assertEqual(
            self.request(
                "/api/content",
                "PUT",
                {"version": 0, "contributions": {}},
                {"Origin": "http://attacker.example"},
            )[0],
            403,
        )
        self.assertEqual(
            self.request("/api/content", "PUT", {"version": 2, "contributions": {}})[0],
            409,
        )
        self.assertEqual(
            self.request(
                "/api/signup",
                "POST",
                {"email": "reader@example.com", "password": "correct horse"},
            )[0],
            409,
        )

    def test_invalid_registration_is_rejected(self):
        status, payload = self.request(
            "/api/signup",
            "POST",
            {"email": "not-an-email", "password": "short"},
        )
        self.assertEqual(status, 400)
        self.assertIn("valid email", payload["error"])


if __name__ == "__main__":
    unittest.main()
