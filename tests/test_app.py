import sys
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
            app.validate_repo("Daniele-Cangi/Daniele-Cangi"),
            "Daniele-Cangi/Daniele-Cangi",
        )

    def test_rejects_path_or_query_injection(self) -> None:
        for invalid in ("", "owner", "../repo", "owner/repo?x=1", "owner/repo/extra"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    app.validate_repo(invalid)


if __name__ == "__main__":
    unittest.main()
