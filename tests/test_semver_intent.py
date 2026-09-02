from __future__ import annotations

import unittest

from automation import semver_intent


class SemVerIntentTests(unittest.TestCase):
    def test_issue_intent_is_authoritative(self) -> None:
        resolved = semver_intent.resolve_intent(
            "Feature request\n\n+semver: MINOR\n",
            explicit="minor",
            repository_default="none",
        )
        self.assertEqual(resolved.intent, "minor")
        self.assertEqual(resolved.source, "issue")

    def test_conflicting_explicit_override_fails_closed(self) -> None:
        with self.assertRaisesRegex(semver_intent.SemVerIntentError, "conflicts"):
            semver_intent.resolve_intent(
                "+semver: minor",
                explicit="patch",
            )

    def test_explicit_override_wins_when_issue_has_no_directive(self) -> None:
        resolved = semver_intent.resolve_intent(
            "No release directive here",
            explicit="none",
            repository_default="minor",
        )
        self.assertEqual(resolved, semver_intent.ResolvedSemVerIntent("none", "explicit"))

    def test_repository_default_precedes_builtin_default(self) -> None:
        resolved = semver_intent.resolve_intent(
            "No directive",
            repository_default="major",
        )
        self.assertEqual(resolved, semver_intent.ResolvedSemVerIntent("major", "repository-default"))

    def test_builtin_default_is_patch(self) -> None:
        self.assertEqual(
            semver_intent.resolve_intent("No directive"),
            semver_intent.ResolvedSemVerIntent("patch", "built-in-default"),
        )

    def test_duplicate_same_or_conflicting_issue_directives_are_rejected(self) -> None:
        for text in (
            "+semver: patch\n+semver: patch\n",
            "+semver: patch\n+semver: major\n",
        ):
            with self.subTest(text=text):
                with self.assertRaisesRegex(semver_intent.SemVerIntentError, "duplicate/conflicting"):
                    semver_intent.resolve_intent(text)

    def test_invalid_explicit_and_repository_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(semver_intent.SemVerIntentError, "invalid explicit"):
            semver_intent.resolve_intent("", explicit="feature")
        with self.assertRaisesRegex(semver_intent.SemVerIntentError, "repository-default"):
            semver_intent.resolve_intent("", repository_default="banana")

    def test_pr_rendering_can_remove_source_directive_before_appending_canonical_one(self) -> None:
        source = "Heading\n\n+semver: minor\n\nDetails\n"
        stripped = semver_intent.without_directives(source)
        rendered = stripped + "\n\n" + semver_intent.directive("minor") + "\n"
        self.assertEqual(semver_intent.explicit_intents(rendered), ["minor"])
        self.assertIn("Details", rendered)


if __name__ == "__main__":
    unittest.main()
