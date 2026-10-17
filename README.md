
# History Atlas

History Atlas is a static HTML history site served with a small Python standard-library backend. The backend stores account records, login sessions, published site information, and a public feed of recent information changes in SQLite.

## Run locally

Use Python 3.10 or newer; no third-party packages are required.

```powershell
python atlas_server.py
```

Open <http://127.0.0.1:8000>. Do not open `index.html` directly: accounts and publishing require the backend.

The database is created as `atlas.sqlite3` beside `atlas_server.py`. Set `ATLAS_DB_PATH` to use another database file. The server binds to `127.0.0.1` by default; `--host` and `--port` can be used to change the listening address and port.

## Accounts and published information

Sign-up and login are handled by the server. It stores normalized email addresses and salted PBKDF2-HMAC-SHA256 password hashes, never plaintext passwords. Login sessions use random, HttpOnly, SameSite cookies. Account records are private and are not included in the published content or the sidebar activity feed.

Signed-in users can publish and edit information pages and chapter content. Published information is stored centrally and shared with site visitors. The sidebar shows the latest additions and edits to public information only; it never lists account details.

This built-in HTTP server is intended for local development and demos, not direct production exposure. If placing it behind a TLS-terminating reverse proxy, configure the proxy to overwrite `X-Forwarded-Proto` with `https` and preserve the public host; this enables secure session cookies and same-origin checks. Add operational controls such as rate limiting, backups, and monitoring before making it public.

## Tests

```powershell
python -m unittest discover -s tests
```
