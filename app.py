from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
LEGACY_DB_PATH = DATA_DIR / "github-pulse.sqlite3"
DB_PATH = LEGACY_DB_PATH
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,39}$")
COLLECTION_INTERVAL_SECONDS = 24 * 60 * 60
COLLECTION_STALE_SECONDS = 20 * 60 * 60
ACCOUNT_LOGIN: str | None = None


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
COLLECTION_LOCK = threading.Lock()
COLLECTION_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "completed_at": None,
    "current_repo": None,
    "repos_total": 0,
    "repos_completed": 0,
    "errors": [],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def account_database_path(login: str, data_dir: Path | None = None) -> Path:
    if not LOGIN_PATTERN.fullmatch(login):
        raise GitHubCLIError("GitHub CLI returned an invalid account name.")
    root = data_dir or DATA_DIR
    return root / f"github-pulse-{login.casefold()}.sqlite3"


def configure_account(login: str) -> str:
    """Select a per-account database and preserve data from the legacy version."""
    global ACCOUNT_LOGIN, DB_PATH
    target = account_database_path(login)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not target.exists() and LEGACY_DB_PATH.exists():
        shutil.copy2(LEGACY_DB_PATH, target)
    ACCOUNT_LOGIN = login
    DB_PATH = target
    return login


@contextmanager
def database_connection() -> Any:
    connection = sqlite3.connect(DB_PATH)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def run_gh_json(
    endpoint: str,
    *,
    params: dict[str, str | int] | None = None,
    paginate: bool = False,
    timeout: int = 45,
    accept: str = "application/vnd.github+json",
) -> Any:
    """Read GitHub data through the already-authenticated GitHub CLI."""
    command = [
        "gh",
        "api",
        "--method",
        "GET",
        endpoint,
        "-H",
        f"Accept: {accept}",
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
            "GitHub CLI (gh) is not installed or is not available in PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitHubCLIError("GitHub did not respond before the request timed out.") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "GitHub CLI error").strip()
        raise GitHubCLIError(detail)

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GitHubCLIError("GitHub CLI returned an invalid response.") from exc

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


def get_account_login() -> str:
    if ACCOUNT_LOGIN:
        return ACCOUNT_LOGIN
    profile = run_gh_json("user")
    login = str(profile.get("login") or "")
    if not login:
        raise GitHubCLIError(
            "Unable to determine the account authenticated in GitHub CLI."
        )
    return configure_account(login)


def ensure_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with database_connection() as connection:
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

            CREATE TABLE IF NOT EXISTS relation_memberships (
                login TEXT NOT NULL,
                kind TEXT NOT NULL,
                avatar_url TEXT NOT NULL DEFAULT '',
                html_url TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (login, kind)
            );

            CREATE TABLE IF NOT EXISTS relation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collected_at TEXT NOT NULL,
                login TEXT NOT NULL,
                avatar_url TEXT NOT NULL DEFAULT '',
                html_url TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL,
                UNIQUE (collected_at, login, event_type)
            );

            CREATE TABLE IF NOT EXISTS repo_snapshots (
                repo TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                stars INTEGER NOT NULL DEFAULT 0,
                forks INTEGER NOT NULL DEFAULT 0,
                watchers INTEGER NOT NULL DEFAULT 0,
                open_issues INTEGER NOT NULL DEFAULT 0,
                private INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                language TEXT NOT NULL DEFAULT '',
                pushed_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (repo, collected_at)
            );

            CREATE TABLE IF NOT EXISTS collection_runs (
                started_at TEXT PRIMARY KEY,
                completed_at TEXT,
                repos_total INTEGER NOT NULL DEFAULT 0,
                repos_completed INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_relation_events_time
                ON relation_events (collected_at DESC);
            CREATE INDEX IF NOT EXISTS idx_repo_snapshots_repo_time
                ON repo_snapshots (repo, collected_at DESC);
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


def save_relation_snapshot(
    categories: dict[str, list[dict[str, Any]]], collected_at: str | None = None
) -> list[dict[str, Any]]:
    """Persist counts and turn membership changes into a local event timeline."""
    timestamp = collected_at or utc_now()
    event_names = {
        "followers": ("new_follower", "lost_follower"),
        "following": ("started_following", "stopped_following"),
    }
    saved_events: list[dict[str, Any]] = []

    with database_connection() as connection:
        initialized = bool(
            connection.execute("SELECT 1 FROM relation_memberships LIMIT 1").fetchone()
        )
        for kind, (added_type, removed_type) in event_names.items():
            current = {
                item["login"].casefold(): item
                for item in categories[kind]
                if item.get("login")
            }
            previous_rows = connection.execute(
                """
                SELECT login, avatar_url, html_url
                FROM relation_memberships
                WHERE kind = ? AND active = 1
                """,
                (kind,),
            ).fetchall()
            previous = {
                str(row[0]).casefold(): {
                    "login": row[0],
                    "avatar_url": row[1],
                    "html_url": row[2],
                }
                for row in previous_rows
            }

            changes: list[tuple[dict[str, Any], str]] = []
            if initialized:
                changes.extend((current[key], added_type) for key in current.keys() - previous.keys())
                changes.extend((previous[key], removed_type) for key in previous.keys() - current.keys())

            for item, event_type in changes:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO relation_events (
                        collected_at, login, avatar_url, html_url, event_type
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp,
                        item.get("login", ""),
                        item.get("avatar_url", ""),
                        item.get("html_url", ""),
                        event_type,
                    ),
                )
                saved_events.append(
                    {
                        "collected_at": timestamp,
                        "login": item.get("login", ""),
                        "avatar_url": item.get("avatar_url", ""),
                        "html_url": item.get("html_url", ""),
                        "event_type": event_type,
                    }
                )

            for item in current.values():
                connection.execute(
                    """
                    INSERT INTO relation_memberships (
                        login, kind, avatar_url, html_url,
                        first_seen_at, last_seen_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(login, kind) DO UPDATE SET
                        avatar_url = excluded.avatar_url,
                        html_url = excluded.html_url,
                        last_seen_at = excluded.last_seen_at,
                        active = 1
                    """,
                    (
                        item["login"],
                        kind,
                        item.get("avatar_url", ""),
                        item.get("html_url", ""),
                        timestamp,
                        timestamp,
                    ),
                )

            removed_logins = [previous[key]["login"] for key in previous.keys() - current.keys()]
            if removed_logins:
                connection.executemany(
                    """
                    UPDATE relation_memberships
                    SET active = 0, last_seen_at = ?
                    WHERE login = ? AND kind = ?
                    """,
                    ((timestamp, login, kind) for login in removed_logins),
                )

        connection.execute(
            """
            INSERT OR REPLACE INTO relation_snapshots (
                collected_at, followers, following, mutual,
                not_following_back, followers_not_followed
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                len(categories["followers"]),
                len(categories["following"]),
                len(categories["mutual"]),
                len(categories["not_following_back"]),
                len(categories["followers_not_followed"]),
            ),
        )
    return saved_events


def save_repo_snapshots(repositories: list[dict[str, Any]], collected_at: str) -> None:
    with database_connection() as connection:
        connection.executemany(
            """
            INSERT OR REPLACE INTO repo_snapshots (
                repo, collected_at, stars, forks, watchers, open_issues,
                private, archived, language, pushed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    repo["full_name"],
                    collected_at,
                    int(repo.get("stars", 0)),
                    int(repo.get("forks", 0)),
                    int(repo.get("watchers", 0)),
                    int(repo.get("open_issues", 0)),
                    int(bool(repo.get("private"))),
                    int(bool(repo.get("archived"))),
                    repo.get("language") or "",
                    repo.get("pushed_at") or "",
                )
                for repo in repositories
            ),
        )


def get_relation_movements(limit: int = 50) -> list[dict[str, Any]]:
    with database_connection() as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT collected_at, login, avatar_url, html_url, event_type
            FROM relation_events
            ORDER BY collected_at DESC, id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_relation_history(days: int = 30) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with database_connection() as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT collected_at, followers, following, mutual,
                   not_following_back, followers_not_followed
            FROM relation_snapshots
            WHERE collected_at >= ?
            ORDER BY collected_at ASC
            """,
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def build_dashboard(*, force: bool = False) -> dict[str, Any]:
    cached = None if force else CACHE.get("dashboard", 60)
    if cached is not None:
        return cached
    account = get_account_login()

    with ThreadPoolExecutor(max_workers=4) as executor:
        jobs = {
            "profile": executor.submit(
                run_gh_json,
                f"users/{account}",
            ),
            "followers": executor.submit(
                run_gh_json,
                f"users/{account}/followers",
                params={"per_page": 100},
                paginate=True,
            ),
            "following": executor.submit(
                run_gh_json,
                f"users/{account}/following",
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
            "archived": bool(repo.get("archived")),
            "fork": bool(repo.get("fork")),
            "html_url": repo.get("html_url", ""),
            "description": repo.get("description") or "",
            "homepage": repo.get("homepage") or "",
            "topics": [
                str(topic)
                for topic in (repo.get("topics") or [])
                if isinstance(topic, str)
            ],
            "license": (
                (repo.get("license") or {}).get("spdx_id")
                if isinstance(repo.get("license"), dict)
                else ""
            )
            or "",
            "default_branch": repo.get("default_branch") or "main",
            "updated_at": repo.get("updated_at", ""),
            "pushed_at": repo.get("pushed_at", ""),
            "language": repo.get("language") or "",
            "stars": int(repo.get("stargazers_count", 0)),
            "forks": int(repo.get("forks_count", 0)),
            "watchers": int(repo.get("subscribers_count", repo.get("watchers_count", 0))),
            "open_issues": int(repo.get("open_issues_count", 0)),
            "size": int(repo.get("size", 0)),
        }
        for repo in result["repos"]
        if repo.get("permissions", {}).get("push") and repo.get("full_name")
    ]

    collected_at = utc_now()
    save_relation_snapshot(categories, collected_at)
    save_repo_snapshots(repositories, collected_at)
    payload = {
        "collected_at": collected_at,
        "profile": {
            "login": result["profile"].get("login", account),
            "name": result["profile"].get("name") or result["profile"].get("login", ""),
            "avatar_url": result["profile"].get("avatar_url", ""),
            "html_url": result["profile"].get("html_url", ""),
            "bio": result["profile"].get("bio") or "",
            "public_repos": int(result["profile"].get("public_repos", 0)),
            "created_at": result["profile"].get("created_at", ""),
        },
        "counts": {name: len(items) for name, items in categories.items()},
        "relationships": categories,
        "relationship_movements": get_relation_movements(),
        "relationship_history": get_relation_history(),
        "repositories": repositories,
    }
    CACHE.set("dashboard", payload)
    return payload


def validate_repo(repo: str) -> str:
    if not REPO_PATTERN.fullmatch(repo):
        raise ValueError("Invalid repository name.")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise ValueError("Invalid repository name.")
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

    with database_connection() as connection:
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
    with database_connection() as connection:
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


def percentage_change(current: int, previous: int) -> float | None:
    if previous == 0:
        return 0.0 if current == 0 else None
    return round(((current - previous) / previous) * 100, 1)


def get_repository_signal_rows() -> list[dict[str, Any]]:
    with database_connection() as connection:
        connection.row_factory = sqlite3.Row
        traffic_rows = connection.execute(
            """
            SELECT repo,
                SUM(CASE WHEN day >= date('now', '-6 days') THEN views ELSE 0 END) AS views_7d,
                SUM(CASE WHEN day >= date('now', '-6 days') THEN unique_views ELSE 0 END) AS unique_views_7d,
                SUM(CASE WHEN day >= date('now', '-6 days') THEN clones ELSE 0 END) AS clones_7d,
                SUM(CASE WHEN day >= date('now', '-6 days') THEN unique_clones ELSE 0 END) AS unique_clones_7d,
                SUM(CASE WHEN day BETWEEN date('now', '-13 days') AND date('now', '-7 days') THEN unique_views ELSE 0 END) AS previous_unique_views,
                SUM(CASE WHEN day BETWEEN date('now', '-13 days') AND date('now', '-7 days') THEN unique_clones ELSE 0 END) AS previous_unique_clones,
                SUM(CASE WHEN day >= date('now', '-13 days') THEN views ELSE 0 END) AS views_14d,
                SUM(CASE WHEN day >= date('now', '-13 days') THEN unique_views ELSE 0 END) AS unique_views_14d,
                SUM(CASE WHEN day >= date('now', '-13 days') THEN clones ELSE 0 END) AS clones_14d,
                SUM(CASE WHEN day >= date('now', '-13 days') THEN unique_clones ELSE 0 END) AS unique_clones_14d,
                MAX(collected_at) AS traffic_collected_at
            FROM traffic_daily
            GROUP BY repo
            """
        ).fetchall()
        snapshot_rows = connection.execute(
            """
            SELECT repo, collected_at, stars, forks, watchers, open_issues,
                   private, archived, language, pushed_at
            FROM repo_snapshots
            WHERE collected_at >= datetime('now', '-8 days')
               OR collected_at = (
                   SELECT MAX(inner_snapshot.collected_at)
                   FROM repo_snapshots AS inner_snapshot
                   WHERE inner_snapshot.repo = repo_snapshots.repo
               )
            ORDER BY repo, collected_at ASC
            """
        ).fetchall()

    traffic = {row["repo"]: dict(row) for row in traffic_rows}
    snapshots: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot_rows:
        snapshots.setdefault(row["repo"], []).append(dict(row))

    rows: list[dict[str, Any]] = []
    for repo in sorted(set(traffic) | set(snapshots), key=str.casefold):
        repo_traffic = traffic.get(repo, {})
        repo_snapshots = snapshots.get(repo, [])
        latest = repo_snapshots[-1] if repo_snapshots else {}
        baseline = repo_snapshots[0] if repo_snapshots else {}
        unique_views = int(repo_traffic.get("unique_views_7d") or 0)
        unique_clones = int(repo_traffic.get("unique_clones_7d") or 0)
        star_delta = int(latest.get("stars", 0)) - int(baseline.get("stars", 0))
        fork_delta = int(latest.get("forks", 0)) - int(baseline.get("forks", 0))
        score = round(
            min(
                100,
                math.log1p(unique_views) * 10
                + math.log1p(unique_clones) * 17
                + max(0, star_delta) * 12
                + max(0, fork_delta) * 16,
            )
        )
        rows.append(
            {
                "repo": repo,
                "name": repo.split("/", 1)[-1],
                "views_7d": int(repo_traffic.get("views_7d") or 0),
                "unique_views_7d": unique_views,
                "clones_7d": int(repo_traffic.get("clones_7d") or 0),
                "unique_clones_7d": unique_clones,
                "previous_unique_views": int(repo_traffic.get("previous_unique_views") or 0),
                "previous_unique_clones": int(repo_traffic.get("previous_unique_clones") or 0),
                "views_14d": int(repo_traffic.get("views_14d") or 0),
                "unique_views_14d": int(repo_traffic.get("unique_views_14d") or 0),
                "clones_14d": int(repo_traffic.get("clones_14d") or 0),
                "unique_clones_14d": int(repo_traffic.get("unique_clones_14d") or 0),
                "traffic_collected_at": repo_traffic.get("traffic_collected_at"),
                "stars": int(latest.get("stars", 0)),
                "stars_delta": star_delta,
                "forks": int(latest.get("forks", 0)),
                "forks_delta": fork_delta,
                "watchers": int(latest.get("watchers", 0)),
                "open_issues": int(latest.get("open_issues", 0)),
                "private": bool(latest.get("private", 0)),
                "archived": bool(latest.get("archived", 0)),
                "language": latest.get("language") or "",
                "pushed_at": latest.get("pushed_at") or "",
                "intent_rate": round((unique_clones / unique_views) * 100, 1)
                if unique_views
                else None,
                "signal_score": score,
            }
        )
    rows.sort(
        key=lambda item: (
            item["signal_score"],
            item["unique_views_7d"],
            item["stars"],
        ),
        reverse=True,
    )
    return rows


def days_since_timestamp(value: str, *, now: datetime | None = None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return max(0, (reference - parsed.astimezone(timezone.utc)).days)


def repository_health(repo: dict[str, Any]) -> dict[str, Any]:
    score = 100
    gaps: list[str] = []

    if not str(repo.get("description") or "").strip():
        score -= 20
        gaps.append("description")
    if len(repo.get("topics") or []) < 2:
        score -= 15
        gaps.append("topics")
    license_id = str(repo.get("license") or "")
    if not repo.get("private") and license_id in {"", "NOASSERTION"}:
        score -= 20
        gaps.append("license")
    if not str(repo.get("homepage") or "").strip():
        score -= 5
        gaps.append("homepage")

    pushed_days_ago = days_since_timestamp(str(repo.get("pushed_at") or ""))
    if pushed_days_ago is None:
        score -= 10
        gaps.append("recent activity")
    elif pushed_days_ago > 180:
        score -= 20
        gaps.append("recent activity")
    elif pushed_days_ago > 90:
        score -= 10
        gaps.append("recent activity")

    return {
        "score": max(0, score),
        "gaps": gaps,
        "pushed_days_ago": pushed_days_ago,
    }


def analyze_opportunities(
    repositories: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    signals = {str(row["repo"]).casefold(): row for row in signal_rows}
    opportunities: list[dict[str, Any]] = []
    health_rows: list[dict[str, Any]] = []
    priority_rank = {"high": 3, "medium": 2, "low": 1}

    for repo in repositories:
        full_name = str(repo.get("full_name") or "")
        if not full_name or repo.get("archived") or repo.get("fork"):
            continue
        signal = signals.get(full_name.casefold(), {})
        health = repository_health(repo)
        health_rows.append(
            {
                "repo": full_name,
                "name": repo.get("name") or full_name.split("/", 1)[-1],
                "score": health["score"],
                "gaps": health["gaps"],
                "url": repo.get("html_url") or f"https://github.com/{full_name}",
            }
        )

        repo_name = str(repo.get("name") or full_name.split("/", 1)[-1])
        repo_url = str(repo.get("html_url") or f"https://github.com/{full_name}")
        unique_views = int(signal.get("unique_views_7d") or 0)
        unique_clones = int(signal.get("unique_clones_7d") or 0)
        star_delta = int(signal.get("stars_delta") or 0)
        previous_views = int(signal.get("previous_unique_views") or 0)
        intent_rate = signal.get("intent_rate")
        growth = percentage_change(unique_views, previous_views)

        essential_gaps = [
            gap for gap in health["gaps"] if gap in {"description", "topics", "license"}
        ]
        if essential_gaps:
            missing = ", ".join(essential_gaps)
            opportunities.append(
                {
                    "kind": "foundation",
                    "priority": "high" if "license" in essential_gaps else "medium",
                    "repo": full_name,
                    "title": f"Complete {repo_name}'s essentials",
                    "detail": f"Missing or weak repository metadata: {missing}.",
                    "action": "Add the missing metadata so visitors understand and trust the project faster.",
                    "metric": f"Health {health['score']}/100",
                    "score": 95 - health["score"],
                    "url": repo_url,
                }
            )

        if unique_views >= 10 and star_delta <= 0:
            opportunities.append(
                {
                    "kind": "conversion",
                    "priority": "high" if unique_views >= 20 else "medium",
                    "repo": full_name,
                    "title": f"Turn {repo_name}'s attention into validation",
                    "detail": f"{unique_views} unique visitors arrived this week without a new star.",
                    "action": "Sharpen the README opening, demo and primary call to action.",
                    "metric": f"{unique_views} visitors · {star_delta:+d} stars",
                    "score": 70 + min(unique_views, 30),
                    "url": repo_url,
                }
            )

        if unique_clones >= 5 and (intent_rate or 0) >= 80:
            opportunities.append(
                {
                    "kind": "developer_experience",
                    "priority": "medium",
                    "repo": full_name,
                    "title": f"Make {repo_name} easier to evaluate",
                    "detail": f"{unique_clones} unique cloners show strong hands-on intent.",
                    "action": "Add a quick start, expected output and a minimal runnable example.",
                    "metric": f"{intent_rate:g}% clone intent",
                    "score": 55 + min(unique_clones, 30),
                    "url": repo_url,
                }
            )

        is_new_traffic = growth is None and unique_views >= 5
        if is_new_traffic or (growth is not None and growth >= 50 and unique_views >= 5):
            growth_label = "new traffic" if growth is None else f"+{growth:g}% traffic"
            opportunities.append(
                {
                    "kind": "momentum",
                    "priority": "low",
                    "repo": full_name,
                    "title": f"Capture {repo_name}'s momentum",
                    "detail": f"{growth_label} is creating a short window for discovery.",
                    "action": "Publish a small release or update while attention is elevated.",
                    "metric": f"{unique_views} visitors · {growth_label}",
                    "score": 40 + min(unique_views, 30),
                    "url": repo_url,
                }
            )

        pushed_days_ago = health["pushed_days_ago"]
        if pushed_days_ago is not None and pushed_days_ago > 120 and unique_views >= 3:
            opportunities.append(
                {
                    "kind": "freshness",
                    "priority": "medium",
                    "repo": full_name,
                    "title": f"Refresh {repo_name} while people still visit",
                    "detail": f"The repository still attracts traffic but was last pushed {pushed_days_ago} days ago.",
                    "action": "Confirm compatibility, refresh examples and publish maintenance notes.",
                    "metric": f"{unique_views} visitors · {pushed_days_ago}d since push",
                    "score": 60 + min(unique_views, 20),
                    "url": repo_url,
                }
            )

    opportunities.sort(
        key=lambda item: (priority_rank[item["priority"]], int(item["score"])),
        reverse=True,
    )
    health_rows.sort(key=lambda item: (int(item["score"]), str(item["name"]).casefold()))
    return opportunities, health_rows


def build_opportunity_center(*, force: bool = False) -> dict[str, Any]:
    cached = None if force else CACHE.get("opportunities", 120)
    if cached is not None:
        return cached
    dashboard = build_dashboard()
    repositories = get_repository_signal_rows()
    opportunities, health = analyze_opportunities(
        dashboard["repositories"], repositories
    )
    average_health = (
        round(sum(int(item["score"]) for item in health) / len(health))
        if health
        else 0
    )
    payload = {
        "generated_at": utc_now(),
        "summary": {
            "total": len(opportunities),
            "high": sum(1 for item in opportunities if item["priority"] == "high"),
            "medium": sum(
                1 for item in opportunities if item["priority"] == "medium"
            ),
            "health_average": average_health,
            "repositories_analyzed": len(health),
        },
        "opportunities": opportunities[:40],
        "health": health,
        "repositories": [
            {"repo": row["repo"], "name": row["name"]}
            for row in repositories
            if not row["archived"]
        ],
    }
    CACHE.set("opportunities", payload)
    return payload


def build_repository_comparison(selected_repos: list[str]) -> dict[str, Any]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in selected_repos:
        repo = validate_repo(value.strip())
        key = repo.casefold()
        if key not in seen:
            selected.append(repo)
            seen.add(key)
    if not 2 <= len(selected) <= 4:
        raise ValueError("Choose between 2 and 4 different repositories.")

    available = {
        str(row["repo"]).casefold(): row for row in get_repository_signal_rows()
    }
    items: list[dict[str, Any]] = []
    for requested in selected:
        row = available.get(requested.casefold())
        if row is None:
            raise ValueError(f"Repository is not available for comparison: {requested}")
        unique_views = int(row["unique_views_7d"])
        star_delta = int(row["stars_delta"])
        item = dict(row)
        item.update(
            {
                "visitor_growth": percentage_change(
                    unique_views, int(row["previous_unique_views"])
                ),
                "validation_rate": round((star_delta / unique_views) * 100, 1)
                if unique_views
                else None,
                "history": get_traffic_history(str(row["repo"]))[-30:],
            }
        )
        items.append(item)
    return {"generated_at": utc_now(), "repositories": items}


def build_digest_markdown(
    account: str,
    signals: dict[str, Any],
    opportunity_center: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> str:
    reference = datetime.fromisoformat((generated_at or utc_now()).replace("Z", "+00:00"))
    period_end = reference.date()
    period_start = period_end - timedelta(days=6)
    totals = signals["totals"]
    relationship_delta = signals.get("relationship_delta") or {}
    lines = [
        "# GitHub Pulse Weekly Digest",
        "",
        f"**Account:** @{account}",
        f"**Period:** {period_start.isoformat()} to {period_end.isoformat()}",
        "",
        "## This week",
        "",
        f"- {totals['unique_views_7d']} unique visitors across tracked repositories",
        f"- {totals['unique_clones_7d']} unique cloners",
        f"- {totals['stars_delta']:+d} stars and {totals['forks_delta']:+d} forks",
        f"- {int(relationship_delta.get('followers', 0)):+d} followers",
        "",
        "## Top repositories",
        "",
    ]
    for row in signals["repository_ranking"][:5]:
        lines.append(
            f"- **{row['name']}** — {row['unique_views_7d']} visitors, "
            f"{row['unique_clones_7d']} cloners, pulse {row['signal_score']}"
        )
    if not signals["repository_ranking"]:
        lines.append("- No repository traffic collected yet.")

    lines.extend(["", "## Opportunities", ""])
    opportunities = opportunity_center["opportunities"][:5]
    for item in opportunities:
        lines.append(
            f"- **{item['title']}** — {item['action']} ({item['metric']})"
        )
    if not opportunities:
        lines.append("- No urgent opportunities detected.")

    lines.extend(["", "## Alerts", ""])
    notifications = signals["notifications"][:5]
    for item in notifications:
        lines.append(f"- **{item['title']}** — {item['detail']}")
    if not notifications:
        lines.append("- No important alerts this week.")

    lines.extend(["", "---", "Generated locally by GitHub Pulse.", ""])
    return "\n".join(lines)


def build_weekly_digest(*, force: bool = False) -> dict[str, Any]:
    generated_at = utc_now()
    signals = build_signals()
    opportunity_center = build_opportunity_center(force=force)
    markdown = build_digest_markdown(
        get_account_login(),
        signals,
        opportunity_center,
        generated_at=generated_at,
    )
    reference = datetime.fromisoformat(generated_at)
    return {
        "generated_at": generated_at,
        "period": {
            "from": (reference.date() - timedelta(days=6)).isoformat(),
            "to": reference.date().isoformat(),
        },
        "totals": signals["totals"],
        "relationship_delta": signals["relationship_delta"],
        "top_repositories": signals["repository_ranking"][:5],
        "opportunities": opportunity_center["opportunities"][:5],
        "alerts": signals["notifications"][:5],
        "markdown": markdown,
    }


def get_latest_relation_counts() -> tuple[dict[str, int], dict[str, int]]:
    with database_connection() as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT collected_at, followers, following, mutual,
                   not_following_back, followers_not_followed
            FROM relation_snapshots
            ORDER BY collected_at DESC
            LIMIT 300
            """
        ).fetchall()
    if not rows:
        empty = {
            "followers": 0,
            "following": 0,
            "mutual": 0,
            "not_following_back": 0,
            "followers_not_followed": 0,
        }
        return empty, empty

    latest = dict(rows[0])
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    baseline = dict(rows[-1])
    for row in rows:
        try:
            row_time = datetime.fromisoformat(str(row["collected_at"]))
        except ValueError:
            continue
        if row_time <= cutoff:
            baseline = dict(row)
            break
    keys = (
        "followers",
        "following",
        "mutual",
        "not_following_back",
        "followers_not_followed",
    )
    return (
        {key: int(latest[key]) for key in keys},
        {key: int(baseline[key]) for key in keys},
    )


def collection_status() -> dict[str, Any]:
    with COLLECTION_LOCK:
        status = dict(COLLECTION_STATE)
        status["errors"] = list(COLLECTION_STATE["errors"])
    if not status["running"] and not status["completed_at"]:
        with database_connection() as connection:
            row = connection.execute(
                """
                SELECT started_at, completed_at, repos_total, repos_completed, errors, status
                FROM collection_runs
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row:
            status.update(
                {
                    "started_at": row[0],
                    "completed_at": row[1],
                    "repos_total": row[2],
                    "repos_completed": row[3],
                    "last_error_count": row[4],
                    "last_status": row[5],
                }
            )
    return status


def build_signals() -> dict[str, Any]:
    repositories = get_repository_signal_rows()
    latest_counts, baseline_counts = get_latest_relation_counts()
    movements = get_relation_movements(30)

    totals = {
        key: sum(int(repo[key]) for repo in repositories)
        for key in (
            "views_7d",
            "unique_views_7d",
            "clones_7d",
            "unique_clones_7d",
            "previous_unique_views",
            "previous_unique_clones",
            "stars",
            "stars_delta",
            "forks",
            "forks_delta",
        )
    }
    follower_delta = latest_counts["followers"] - baseline_counts["followers"]
    cards = [
        {
            "key": "reach",
            "label": "Reach",
            "value": totals["unique_views_7d"],
            "unit": "unique visitors · 7d",
            "delta": percentage_change(
                totals["unique_views_7d"], totals["previous_unique_views"]
            ),
        },
        {
            "key": "intent",
            "label": "Intent",
            "value": totals["unique_clones_7d"],
            "unit": "unique cloners · 7d",
            "delta": percentage_change(
                totals["unique_clones_7d"], totals["previous_unique_clones"]
            ),
        },
        {
            "key": "validation",
            "label": "Validation",
            "value": totals["stars"],
            "unit": "total stars",
            "delta_absolute": totals["stars_delta"],
        },
        {
            "key": "community",
            "label": "Community",
            "value": latest_counts["followers"],
            "unit": "follower",
            "delta_absolute": follower_delta,
        },
    ]

    notifications: list[dict[str, Any]] = []
    movement_copy = {
        "new_follower": ("New follower", "positive"),
        "lost_follower": ("No longer follows you", "warning"),
        "started_following": ("You started following", "info"),
        "stopped_following": ("You stopped following", "info"),
    }
    for movement in movements[:8]:
        title, tone = movement_copy.get(movement["event_type"], ("Network change", "info"))
        notifications.append(
            {
                "type": movement["event_type"],
                "tone": tone,
                "title": title,
                "detail": f'@{movement["login"]}',
                "occurred_at": movement["collected_at"],
                "url": movement["html_url"],
            }
        )

    for repo in repositories:
        current = repo["unique_views_7d"]
        previous = repo["previous_unique_views"]
        if current >= 5 and current >= max(3, previous * 1.5):
            change = percentage_change(current, previous)
            detail = (
                f"{current} unique visitors · +{change:g}%"
                if change is not None
                else f"{current} unique visitors · new traffic"
            )
            notifications.append(
                {
                    "type": "traffic_spike",
                    "tone": "positive",
                    "title": f'Traffic spike on {repo["name"]}',
                    "detail": detail,
                    "occurred_at": repo["traffic_collected_at"],
                    "url": f'https://github.com/{repo["repo"]}',
                }
            )
        if repo["stars_delta"] > 0:
            notifications.append(
                {
                    "type": "new_stars",
                    "tone": "positive",
                    "title": f'New stars on {repo["name"]}',
                    "detail": f'+{repo["stars_delta"]} in the last 7 days',
                    "occurred_at": repo["traffic_collected_at"],
                    "url": f'https://github.com/{repo["repo"]}/stargazers',
                }
            )

    notifications.sort(
        key=lambda item: str(item.get("occurred_at") or ""), reverse=True
    )
    active_repositories = [repo for repo in repositories if repo["unique_views_14d"] or repo["unique_clones_14d"]]
    return {
        "generated_at": utc_now(),
        "cards": cards,
        "totals": totals,
        "repository_ranking": repositories,
        "notifications": notifications[:16],
        "important_signals": sum(
            1
            for item in notifications
            if item["type"] in {"traffic_spike", "new_stars", "new_follower"}
        ),
        "tracked_repositories": len(active_repositories),
        "relationship_counts": latest_counts,
        "relationship_delta": {
            key: latest_counts[key] - baseline_counts[key] for key in latest_counts
        },
        "collection": collection_status(),
    }


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
            "Unable to read repository traffic. Make sure you have push access and "
            "that the GitHub CLI token can access the repository."
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


def build_star_timeline(repo: str, *, force: bool = False) -> dict[str, Any]:
    repo = validate_repo(repo)
    cache_key = f"stars:{repo.casefold()}"
    cached = None if force else CACHE.get(cache_key, 600)
    if cached is not None:
        return cached
    rows = run_gh_json(
        f"repos/{repo}/stargazers",
        params={"per_page": 100},
        paginate=True,
        accept="application/vnd.github.star+json",
    )
    stars = []
    for row in rows:
        user = row.get("user") if isinstance(row, dict) else None
        user = user if isinstance(user, dict) else row
        if not isinstance(user, dict) or not user.get("login"):
            continue
        stars.append(
            {
                "login": user.get("login", ""),
                "avatar_url": user.get("avatar_url", ""),
                "html_url": user.get("html_url", ""),
                "starred_at": row.get("starred_at") if isinstance(row, dict) else None,
            }
        )
    stars.sort(key=lambda item: str(item.get("starred_at") or ""), reverse=True)
    payload = {"repo": repo, "count": len(stars), "stars": stars[:100]}
    CACHE.set(cache_key, payload)
    return payload


def normalize_activity_event(event: dict[str, Any]) -> dict[str, Any]:
    event_type = event.get("type", "Event")
    payload = event.get("payload") or {}
    repo = (event.get("repo") or {}).get("name", "")
    title = {
        "PushEvent": "Push",
        "PullRequestEvent": "Pull request",
        "IssuesEvent": "Issue",
        "IssueCommentEvent": "Comment",
        "CreateEvent": "Created",
        "ReleaseEvent": "Release",
        "ForkEvent": "Fork",
        "WatchEvent": "Starred",
        "PublicEvent": "Repository made public",
        "DeleteEvent": "Reference deleted",
    }.get(event_type, event_type.removesuffix("Event"))
    detail = repo
    if event_type == "PushEvent":
        size = payload.get("size", len(payload.get("commits") or []))
        detail = f"{repo} · {size} commit"
    elif event_type in {"PullRequestEvent", "IssuesEvent"}:
        detail = f'{repo} · {payload.get("action", "updated")}'
    elif event_type == "CreateEvent":
        detail = f'{repo} · {payload.get("ref_type", "resource")}'
    elif event_type == "ReleaseEvent":
        detail = f'{repo} · {(payload.get("release") or {}).get("tag_name", "release")}'
    return {
        "id": str(event.get("id", "")),
        "type": event_type,
        "title": title,
        "detail": detail,
        "repo": repo,
        "created_at": event.get("created_at", ""),
        "url": f"https://github.com/{repo}" if repo else "https://github.com",
    }


def build_activity(*, force: bool = False) -> dict[str, Any]:
    cached = None if force else CACHE.get("activity", 600)
    if cached is not None:
        return cached
    account = get_account_login()
    with ThreadPoolExecutor(max_workers=2) as executor:
        events_job = executor.submit(
            run_gh_json,
            f"users/{account}/events/public",
            params={"per_page": 100},
        )
        merged_job = executor.submit(
            run_gh_json,
            "search/issues",
            params={"q": f"author:{account} is:pr is:merged", "per_page": 1},
        )
        events = events_job.result()
        merged_result = merged_job.result()

    normalized = [
        normalize_activity_event(event)
        for event in events
        if isinstance(event, dict)
    ]
    counts: dict[str, int] = {}
    for item in normalized:
        counts[item["type"]] = counts.get(item["type"], 0) + 1

    quickdraw_candidates = []
    for event in events:
        if event.get("type") not in {"IssuesEvent", "PullRequestEvent"}:
            continue
        payload = event.get("payload") or {}
        subject = payload.get("issue") or payload.get("pull_request") or {}
        if payload.get("action") != "closed":
            continue
        try:
            created = datetime.fromisoformat(str(subject["created_at"]).replace("Z", "+00:00"))
            closed = datetime.fromisoformat(str(subject["closed_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        if (closed - created).total_seconds() <= 300:
            quickdraw_candidates.append(subject.get("html_url", ""))

    repo_rows = get_repository_signal_rows()
    top_starred = max(repo_rows, key=lambda item: item["stars"], default=None)
    top_stars = int(top_starred["stars"]) if top_starred else 0
    merged_prs = int(merged_result.get("total_count", 0))
    achievements = [
        {
            "name": "Starstruck",
            "status": "candidate" if top_stars >= 16 else "progress",
            "progress": min(top_stars, 16),
            "target": 16,
            "detail": (
                f'{top_starred["name"]}: {top_stars} stars'
                if top_starred
                else "No repositories detected"
            ),
            "confidence": "high",
        },
        {
            "name": "Pull Shark",
            "status": "candidate" if merged_prs >= 2 else "progress",
            "progress": min(merged_prs, 2),
            "target": 2,
            "detail": f"{merged_prs} merged pull requests found",
            "confidence": "medium",
        },
        {
            "name": "Quickdraw",
            "status": "candidate" if quickdraw_candidates else "open",
            "progress": 1 if quickdraw_candidates else 0,
            "target": 1,
            "detail": (
                "Found an item closed within 5 minutes in recent events"
                if quickdraw_candidates
                else "Close an issue or pull request within 5 minutes of creation"
            ),
            "confidence": "medium",
        },
        {
            "name": "Pair Extraordinaire",
            "status": "open",
            "progress": 0,
            "target": 1,
            "detail": "Co-authored commits require a dedicated check",
            "confidence": "manual",
        },
    ]
    payload = {
        "generated_at": utc_now(),
        "events": normalized,
        "counts": counts,
        "achievements": achievements,
        "achievement_note": (
            "These are eligibility indicators, not an authoritative record of "
            "achievements awarded by GitHub."
        ),
    }
    CACHE.set("activity", payload)
    return payload


def collect_all_data() -> None:
    started_at = utc_now()
    with COLLECTION_LOCK:
        if COLLECTION_STATE["running"]:
            return
        COLLECTION_STATE.update(
            {
                "running": True,
                "started_at": started_at,
                "completed_at": None,
                "current_repo": None,
                "repos_total": 0,
                "repos_completed": 0,
                "errors": [],
            }
        )

    completed = 0
    total = 0
    errors: list[str] = []
    status = "completed"
    try:
        dashboard = build_dashboard(force=True)
        repositories = [
            repo for repo in dashboard["repositories"] if not repo.get("archived")
        ]
        total = len(repositories)
        with COLLECTION_LOCK:
            COLLECTION_STATE["repos_total"] = total
        for repository in repositories:
            repo = repository["full_name"]
            with COLLECTION_LOCK:
                COLLECTION_STATE["current_repo"] = repo
            try:
                build_traffic(repo, force=True)
            except (GitHubCLIError, ValueError) as exc:
                errors.append(f"{repo}: {exc}")
            finally:
                completed += 1
                with COLLECTION_LOCK:
                    COLLECTION_STATE["repos_completed"] = completed
                    COLLECTION_STATE["errors"] = errors[-20:]
    except Exception as exc:
        status = "failed"
        errors.append(str(exc))
    completed_at = utc_now()
    if errors and status == "completed":
        status = "partial"
    with database_connection() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO collection_runs (
                started_at, completed_at, repos_total, repos_completed, errors, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (started_at, completed_at, total, completed, len(errors), status),
        )
    with COLLECTION_LOCK:
        COLLECTION_STATE.update(
            {
                "running": False,
                "completed_at": completed_at,
                "current_repo": None,
                "repos_total": total,
                "repos_completed": completed,
                "errors": errors[-20:],
            }
        )


def start_collection() -> bool:
    with COLLECTION_LOCK:
        if COLLECTION_STATE["running"]:
            return False
    threading.Thread(target=collect_all_data, daemon=True, name="github-pulse-collector").start()
    return True


def automatic_collection_loop() -> None:
    time.sleep(20)
    while True:
        with database_connection() as connection:
            row = connection.execute(
                """
                SELECT completed_at
                FROM collection_runs
                WHERE completed_at IS NOT NULL
                ORDER BY completed_at DESC
                LIMIT 1
                """
            ).fetchone()
        stale = True
        if row and row[0]:
            try:
                last_run = datetime.fromisoformat(str(row[0]))
                stale = (
                    datetime.now(timezone.utc) - last_run
                ).total_seconds() >= COLLECTION_STALE_SECONDS
            except ValueError:
                stale = True
        if stale:
            start_collection()
        time.sleep(60 * 60)


def build_export_payload() -> dict[str, Any]:
    with database_connection() as connection:
        connection.row_factory = sqlite3.Row
        traffic = [
            dict(row)
            for row in connection.execute(
                """
                SELECT repo, day, views, unique_views, clones, unique_clones, collected_at
                FROM traffic_daily
                ORDER BY day DESC, repo
                """
            ).fetchall()
        ]
    return {
        "exported_at": utc_now(),
        "account": get_account_login(),
        "signals": build_signals(),
        "movements": get_relation_movements(200),
        "relationship_history": get_relation_history(3650),
        "traffic": traffic,
    }


def build_csv_export(dataset: str) -> tuple[str, bytes]:
    output = io.StringIO(newline="")
    if dataset == "movements":
        rows = get_relation_movements(200)
        fields = ["collected_at", "event_type", "login", "html_url"]
        filename = "github-pulse-movements.csv"
    else:
        with database_connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT repo, day, views, unique_views, clones, unique_clones, collected_at
                    FROM traffic_daily
                    ORDER BY day DESC, repo
                    """
                ).fetchall()
            ]
        fields = [
            "repo",
            "day",
            "views",
            "unique_views",
            "clones",
            "unique_clones",
            "collected_at",
        ]
        filename = "github-pulse-traffic.csv"
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return filename, output.getvalue().encode("utf-8-sig")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "GitHubPulse/2.1"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/api/health":
            self.send_json(
                {"ok": True, "app": "GitHub Pulse", "account": get_account_login()}
            )
            return
        if parsed.path == "/api/dashboard":
            self.handle_api(lambda: build_dashboard(force=query.get("refresh") == ["1"]))
            return
        if parsed.path == "/api/signals":
            self.handle_api(build_signals)
            return
        if parsed.path == "/api/opportunities":
            self.handle_api(
                lambda: build_opportunity_center(
                    force=query.get("refresh") == ["1"]
                )
            )
            return
        if parsed.path == "/api/compare":
            self.handle_api(
                lambda: build_repository_comparison(query.get("repos", []))
            )
            return
        if parsed.path == "/api/digest":
            self.handle_api(
                lambda: build_weekly_digest(force=query.get("refresh") == ["1"])
            )
            return
        if parsed.path == "/api/activity":
            self.handle_api(
                lambda: build_activity(force=query.get("refresh") == ["1"])
            )
            return
        if parsed.path == "/api/collection":
            self.send_json(collection_status())
            return
        if parsed.path == "/api/traffic":
            repo = query.get("repo", [""])[0]
            self.handle_api(
                lambda: build_traffic(repo, force=query.get("refresh") == ["1"])
            )
            return
        if parsed.path == "/api/stars":
            repo = query.get("repo", [""])[0]
            self.handle_api(
                lambda: build_star_timeline(
                    repo, force=query.get("refresh") == ["1"]
                )
            )
            return
        if parsed.path == "/api/export":
            dataset = query.get("dataset", ["traffic"])[0]
            if dataset == "digest":
                body = build_weekly_digest()["markdown"].encode("utf-8")
                self.send_download(
                    body,
                    "text/markdown; charset=utf-8",
                    "github-pulse-weekly-digest.md",
                )
                return
            if dataset == "summary":
                body = json.dumps(
                    build_export_payload(), ensure_ascii=False, indent=2
                ).encode("utf-8")
                self.send_download(
                    body, "application/json; charset=utf-8", "github-pulse-export.json"
                )
                return
            if dataset not in {"traffic", "movements"}:
                self.send_json(
                    {"error": "Invalid export dataset."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            filename, body = build_csv_export(dataset)
            self.send_download(body, "text/csv; charset=utf-8", filename)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        self.serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path == "/api/collect":
            started = start_collection()
            self.send_json(
                {
                    "started": started,
                    "message": (
                        "Collection started."
                        if started
                        else "A collection is already running."
                    ),
                    "collection": collection_status(),
                },
                status=HTTPStatus.ACCEPTED if started else HTTPStatus.OK,
            )
            return
        self.send_json(
            {"error": "Endpoint not found."}, status=HTTPStatus.NOT_FOUND
        )

    def handle_api(self, callback: Any) -> None:
        try:
            self.send_json(callback())
        except (GitHubCLIError, ValueError) as exc:
            self.send_json(
                {"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY
            )
        except Exception as exc:  # Keep the local UI usable and avoid a broken socket.
            self.send_json(
                {"error": f"Unexpected error: {exc}"},
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

    def send_download(self, body: bytes, content_type: str, filename: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {message % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local-first GitHub dashboard")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not shutil.which("gh"):
        raise SystemExit("GitHub CLI (gh) was not found in PATH.")
    account = get_account_login()
    ensure_database()
    threading.Thread(
        target=automatic_collection_loop,
        daemon=True,
        name="github-pulse-scheduler",
    ).start()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"GitHub Pulse is available at {url} for @{account}")
    print("Press Ctrl+C to stop the server.")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping GitHub Pulse…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
