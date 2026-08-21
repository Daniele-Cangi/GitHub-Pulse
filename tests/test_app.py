import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


def user(login: str) -> dict[str, str]:
    return {
        "login": login,
        "avatar_url": f"https://avatars.example/{login}",
        "html_url": f"https://github.com/{login}",
    }


class RelationshipTests(unittest.TestCase):
    def test_classifies_relationships_case_insensitively(self) -> None:
        result = app.classify_relationships(
            [user("Alice"), user("Bob")],
            [user("alice"), user("Carol")],
        )

        self.assertEqual([item["login"] for item in result["mutual"]], ["Alice"])
        self.assertEqual(
            [item["login"] for item in result["not_following_back"]], ["Carol"]
        )
        self.assertEqual(
            [item["login"] for item in result["followers_not_followed"]], ["Bob"]
        )
        self.assertEqual(len(result["all"]), 3)

    def test_empty_relationships(self) -> None:
        result = app.classify_relationships([], [])
        self.assertTrue(all(items == [] for items in result.values()))


class ValidationTests(unittest.TestCase):
    def test_accepts_valid_repo(self) -> None:
        self.assertEqual(
            app.validate_repo("octocat/hello-world"),
            "octocat/hello-world",
        )

    def test_rejects_path_or_query_injection(self) -> None:
        for invalid in ("", "owner", "../repo", "owner/repo?x=1", "owner/repo/extra"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    app.validate_repo(invalid)

    def test_account_database_is_scoped_by_login(self) -> None:
        root = Path("data")
        self.assertEqual(
            app.account_database_path("Octo-Cat", root),
            root / "github-pulse-octo-cat.sqlite3",
        )
        with self.assertRaises(app.GitHubCLIError):
            app.account_database_path("../invalid", root)


class SignalTests(unittest.TestCase):
    def test_percentage_change_handles_growth_and_new_signal(self) -> None:
        self.assertEqual(app.percentage_change(15, 10), 50.0)
        self.assertEqual(app.percentage_change(0, 0), 0.0)
        self.assertIsNone(app.percentage_change(5, 0))


class OpportunityTests(unittest.TestCase):
    def test_finds_foundation_conversion_and_developer_experience_opportunities(
        self,
    ) -> None:
        repositories = [
            {
                "full_name": "octocat/hello-world",
                "name": "hello-world",
                "private": False,
                "archived": False,
                "fork": False,
                "html_url": "https://github.com/octocat/hello-world",
                "description": "",
                "homepage": "",
                "topics": [],
                "license": "",
                "pushed_at": app.utc_now(),
            }
        ]
        signals = [
            {
                "repo": "octocat/hello-world",
                "unique_views_7d": 24,
                "previous_unique_views": 12,
                "unique_clones_7d": 10,
                "stars_delta": 0,
                "intent_rate": 90.0,
            }
        ]

        opportunities, health = app.analyze_opportunities(repositories, signals)

        self.assertEqual(health[0]["repo"], "octocat/hello-world")
        self.assertLess(health[0]["score"], 100)
        self.assertTrue(
            {"foundation", "conversion", "developer_experience"}.issubset(
                {item["kind"] for item in opportunities}
            )
        )

    def test_builds_markdown_digest(self) -> None:
        signals = {
            "totals": {
                "unique_views_7d": 42,
                "unique_clones_7d": 12,
                "stars_delta": 3,
                "forks_delta": 1,
            },
            "relationship_delta": {"followers": 2},
            "repository_ranking": [
                {
                    "name": "hello-world",
                    "unique_views_7d": 24,
                    "unique_clones_7d": 10,
                    "signal_score": 88,
                }
            ],
            "notifications": [
                {"title": "Traffic spike", "detail": "24 unique visitors"}
            ],
        }
        center = {
            "opportunities": [
                {
                    "title": "Improve the README",
                    "action": "Add a quick start.",
                    "metric": "24 visitors",
                }
            ]
        }

        digest = app.build_digest_markdown(
            "octocat",
            signals,
            center,
            generated_at="2026-08-21T12:00:00+00:00",
        )

        self.assertIn("# GitHub Pulse Weekly Digest", digest)
        self.assertIn("@octocat", digest)
        self.assertIn("Improve the README", digest)
        self.assertIn("Traffic spike", digest)


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = app.DB_PATH
        app.DB_PATH = Path(self.tempdir.name) / "github-pulse-test.sqlite3"
        app.ensure_database()

    def tearDown(self) -> None:
        app.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_relationship_movements_start_after_initial_snapshot(self) -> None:
        initial = app.classify_relationships([user("Alice")], [user("Bob")])
        app.save_relation_snapshot(initial, "2026-08-20T08:00:00+00:00")
        self.assertEqual(app.get_relation_movements(), [])

        changed = app.classify_relationships([user("Carol")], [user("Bob")])
        app.save_relation_snapshot(changed, "2026-08-20T09:00:00+00:00")
        movements = app.get_relation_movements()

        self.assertEqual(
            {(item["login"], item["event_type"]) for item in movements},
            {("Alice", "lost_follower"), ("Carol", "new_follower")},
        )


if __name__ == "__main__":
    unittest.main()
