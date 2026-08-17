from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "github-pulse.sqlite3"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_USER = "Daniele-Cangi"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubCLIError(RuntimeError):
    pass


class MemoryCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, max_age: int) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if not item or time.time() - item[0] > max_age:
                return None
            return item[1]

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = (time.time(), value)


CACHE = MemoryCache()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_gh_json(
    endpoint: str,
    *,
    params: dict[str, str | int] | None = None,
    paginate: bool = False,
    timeout: int = 45,
) -> Any:
    """Read GitHub data through the already-authenticated GitHub CLI."""
    command = [
        "gh",
        "api",
        "--method",
        "GET",
        endpoint,
        "-H",
        "Accept: application/vnd.github+json",
    ]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    for key, value in (params or {}).items():
        command.extend(["-f", f"{key}={value}"])

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise GitHubCLIError(
            "GitHub CLI (gh) non è installata o non è disponibile nel PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitHubCLIError("GitHub non ha risposto entro il tempo previsto.") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Errore GitHub CLI").strip()
        raise GitHubCLIError(detail)

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GitHubCLIError("GitHub CLI ha restituito una risposta non valida.") from exc

    if paginate:
        if not isinstance(payload, list):
            return []
        flattened: list[Any] = []
        for page in payload:
            if isinstance(page, list):
                flattened.extend(page)
            else:
                flattened.append(page)
        return flattened
    return payload


def ensure_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS relation_snapshots (
                collected_at TEXT PRIMARY KEY,
                followers INTEGER NOT NULL,
                following INTEGER NOT NULL,
                mutual INTEGER NOT NULL,
                not_following_back INTEGER NOT NULL,
                followers_not_followed INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS traffic_daily (
                repo TEXT NOT NULL,
                day TEXT NOT NULL,
                views INTEGER NOT NULL DEFAULT 0,
                unique_views INTEGER NOT NULL DEFAULT 0,
                clones INTEGER NOT NULL DEFAULT 0,
                unique_clones INTEGER NOT NULL DEFAULT 0,
                collected_at TEXT NOT NULL,
                PRIMARY KEY (repo, day)
            );
            """
        )


def compact_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "login": user.get("login", ""),
        "avatar_url": user.get("avatar_url", ""),
        "html_url": user.get("html_url", ""),
    }


def classify_relationships(
    followers: list[dict[str, Any]], following: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    follower_map = {
        str(item.get("login", "")).casefold(): compact_user(item)
        for item in followers
        if item.get("login")
    }
    following_map = {
        str(item.get("login", "")).casefold(): compact_user(item)
        for item in following
        if item.get("login")
    }

    follower_keys = set(follower_map)
    following_keys = set(following_map)

    def users(keys: set[str], source: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            (source[key] for key in keys),
            key=lambda item: item["login"].casefold(),
        )

    union = follower_keys | following_keys
    all_users: list[dict[str, Any]] = []
    for key in union:
        item = dict(follower_map.get(key) or following_map[key])
        item["follows_you"] = key in follower_keys
        item["you_follow"] = key in following_keys
        all_users.append(item)
    all_users.sort(key=lambda item: item["login"].casefold())

    return {
        "all": all_users,
        "followers": users(follower_keys, follower_map),
        "following": users(following_keys, following_map),
        "mutual": users(follower_keys & following_keys, follower_map),
        "not_following_back": users(following_keys - follower_keys, following_map),
        "followers_not_followed": users(follower_keys - following_keys, follower_map),
    }


def save_relation_snapshot(categories: dict[str, list[dict[str, Any]]]) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO relation_snapshots (
                collected_at, followers, following, mutual,
                not_following_back, followers_not_followed
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                len(categories["followers"]),
                len(categories["following"]),
                len(categories["mutual"]),
                len(categories["not_following_back"]),
                len(categories["followers_not_followed"]),
            ),
        )


def build_dashboard(*, force: bool = False) -> dict[str, Any]:
    cached = None if force else CACHE.get("dashboard", 60)
    if cached is not None:
        return cached

    with ThreadPoolExecutor(max_workers=4) as executor:
        jobs = {
            "profile": executor.submit(
                run_gh_json,
                f"users/{DEFAULT_USER}",
            ),
            "followers": executor.submit(
                run_gh_json,
                f"users/{DEFAULT_USER}/followers",
                params={"per_page": 100},
                paginate=True,
            ),
            "following": executor.submit(
                run_gh_json,
                f"users/{DEFAULT_USER}/following",
                params={"per_page": 100},
                paginate=True,
            ),
            "repos": executor.submit(
                run_gh_json,
                "user/repos",
                params={
                    "per_page": 100,
                    "affiliation": "owner",
                    "sort": "updated",
                },
                paginate=True,
            ),
        }
        result = {name: job.result() for name, job in jobs.items()}

    categories = classify_relationships(result["followers"], result["following"])
    repositories = [
        {
            "full_name": repo.get("full_name", ""),
            "name": repo.get("name", ""),
            "private": bool(repo.get("private")),
            "html_url": repo.get("html_url", ""),
            "description": repo.get("description") or "",
            "updated_at": repo.get("updated_at", ""),
        }
        for repo in result["repos"]
        if repo.get("permissions", {}).get("push") and repo.get("full_name")
    ]

    save_relation_snapshot(categories)
    payload = {
        "collected_at": utc_now(),
        "profile": {
            "login": result["profile"].get("login", DEFAULT_USER),
            "name": result["profile"].get("name") or result["profile"].get("login", ""),
            "avatar_url": result["profile"].get("avatar_url", ""),
            "html_url": result["profile"].get("html_url", ""),
            "bio": result["profile"].get("bio") or "",
        },
        "counts": {name: len(items) for name, items in categories.items()},
        "relationships": categories,
        "repositories": repositories,
    }
    CACHE.set("dashboard", payload)
    return payload


def validate_repo(repo: str) -> str:
    if not REPO_PATTERN.fullmatch(repo):
        raise ValueError("Nome repository non valido.")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise ValueError("Nome repository non valido.")
    return repo


def _safe_traffic_call(endpoint: str, default: Any) -> Any:
    try:
        return run_gh_json(endpoint)
    except GitHubCLIError:
        return default


def save_traffic(repo: str, views: dict[str, Any], clones: dict[str, Any]) -> None:
    collected_at = utc_now()
    view_days = {
        str(item.get("timestamp", ""))[:10]: item
        for item in views.get("views", [])
        if item.get("timestamp")
    }
    clone_days = {
        str(item.get("timestamp", ""))[:10]: item
        for item in clones.get("clones", [])
        if item.get("timestamp")
    }

    with sqlite3.connect(DB_PATH) as connection:
        for day in sorted(set(view_days) | set(clone_days)):
            view = view_days.get(day, {})
            clone = clone_days.get(day, {})
            connection.execute(
                """
                INSERT INTO traffic_daily (
                    repo, day, views, unique_views, clones, unique_clones, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo, day) DO UPDATE SET
                    views = excluded.views,
                    unique_views = excluded.unique_views,
                    clones = excluded.clones,
                    unique_clones = excluded.unique_clones,
                    collected_at = excluded.collected_at
                """,
                (
                    repo,
                    day,
                    int(view.get("count", 0)),
                    int(view.get("uniques", 0)),
                    int(clone.get("count", 0)),
                    int(clone.get("uniques", 0)),
                    collected_at,
                ),
            )


def get_traffic_history(repo: str) -> list[dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT day, views, unique_views, clones, unique_clones
            FROM traffic_daily
            WHERE repo = ?
            ORDER BY day ASC
            """,
            (repo,),
        ).fetchall()
    return [dict(row) for row in rows]


def build_traffic(repo: str, *, force: bool = False) -> dict[str, Any]:
    repo = validate_repo(repo)
    cache_key = f"traffic:{repo.casefold()}"
    cached = None if force else CACHE.get(cache_key, 300)
    if cached is not None:
        return cached

    endpoints = {
        "views": f"repos/{repo}/traffic/views",
        "clones": f"repos/{repo}/traffic/clones",
        "referrers": f"repos/{repo}/traffic/popular/referrers",
        "paths": f"repos/{repo}/traffic/popular/paths",
    }
    defaults: dict[str, Any] = {
        "views": {"count": 0, "uniques": 0, "views": []},
        "clones": {"count": 0, "uniques": 0, "clones": []},
        "referrers": [],
        "paths": [],
    }

    data: dict[str, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {
            executor.submit(run_gh_json, endpoint): name
            for name, endpoint in endpoints.items()
        }
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                data[name] = future.result()
            except GitHubCLIError as exc:
                data[name] = defaults[name]
                errors.append(f"{name}: {exc}")

    if len(errors) == len(endpoints):
        raise GitHubCLIError(
            "Impossibile leggere il traffico. Verifica di avere accesso in scrittura "
            "al repository e che il token GitHub CLI includa l’accesso al repository."
        )

    save_traffic(repo, data["views"], data["clones"])
    payload = {
        "repo": repo,
        "collected_at": utc_now(),
        "views": data["views"],
        "clones": data["clones"],
        "referrers": data["referrers"],
        "paths": data["paths"],
        "history": get_traffic_history(repo),
        "partial_errors": errors,
    }
    CACHE.set(cache_key, payload)
    return payload


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "GitHubPulse/1.0"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/api/health":
            self.send_json({"ok": True, "app": "GitHub Pulse"})
            return
        if parsed.path == "/api/dashboard":
            self.handle_api(lambda: build_dashboard(force=query.get("refresh") == ["1"]))
            return
        if parsed.path == "/api/traffic":
            repo = query.get("repo", [""])[0]
            self.handle_api(
                lambda: build_traffic(repo, force=query.get("refresh") == ["1"])
            )
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        self.serve_static(parsed.path)

    def handle_api(self, callback: Any) -> None:
        try:
            self.send_json(callback())
        except (GitHubCLIError, ValueError) as exc:
            self.send_json(
                {"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY
            )
        except Exception as exc:  # Keep the local UI usable and avoid a broken socket.
            self.send_json(
                {"error": f"Errore inatteso: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_types.get(candidate.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {message % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dashboard GitHub locale")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="Non aprire il browser")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not shutil.which("gh"):
        raise SystemExit("GitHub CLI (gh) non trovata nel PATH.")
    ensure_database()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"GitHub Pulse è disponibile su {url}")
    print("Premi Ctrl+C per arrestare il server.")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArresto GitHub Pulse…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
