"""Guard suite selection without dependencies, services, or provider calls."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scope import changed_paths, classify, main, needs_stats_validation


class ScopeTests(unittest.TestCase):
    def test_docs_and_pr_templates_skip_both_suites(self):
        self.assertEqual(
            classify(
                [
                    "CONTRIBUTING.md",
                    "docs/architecture.png",
                    "frontend/README.md",
                    "src/asaree/AGENTS.md",
                    ".github/ISSUE_TEMPLATE/bug.yml",
                    ".github/CODEOWNERS",
                ]
            ),
            (False, False),
        )

    def test_backend_sources_dependencies_and_scripts_skip_frontend(self):
        for path in [
            "src/asaree/api.py",
            "tests/test_api.py",
            "sdk/src/client.py",
            "workspace-core/pyproject.toml",
            "mcp-servers/server.py",
            "scripts/seed_row_test_users.py",
            "personas/prompt.txt",
            "uv.lock",
            "pyproject.toml",
        ]:
            with self.subTest(path=path):
                self.assertEqual(classify([path]), (True, False))

    def test_frontend_sources_assets_and_lockfiles_skip_backend(self):
        for path in ["frontend/src/App.tsx", "frontend/public/logo.png", "frontend/package-lock.json"]:
            with self.subTest(path=path):
                self.assertEqual(classify([path]), (False, True))

    def test_shared_workflow_build_and_unknown_files_run_both(self):
        for path in [
            ".github/workflows/ci.yml",
            ".github/ci/scope.py",
            "Dockerfile",
            "compose.yml",
            ".dockerignore",
            "new-area/file",
        ]:
            with self.subTest(path=path):
                self.assertEqual(classify([path]), (True, True))

    def test_docs_do_not_hide_code_changes(self):
        self.assertEqual(classify(["README.md", "src/asaree/api.py"]), (True, False))
        self.assertEqual(classify(["frontend/src/App.tsx", "tests/test_api.py"]), (True, True))

    def test_empty_diff_skips_suites(self):
        self.assertEqual(classify([]), (False, False))


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q", "-b", "main")
        self.write("src/app.py", "initial")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=CI test", "-c", "user.email=ci@example.test", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_pr_diff_excludes_unrelated_changes_on_base(self):
        self.git("switch", "-qc", "feature")
        self.write("frontend/app.ts", "feature")
        head = self.commit()
        self.git("switch", "-q", "main")
        self.write("src/app.py", "base advanced")
        base = self.commit()
        paths = changed_paths(
            "pull_request", {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}, cwd=self.root
        )
        self.assertEqual(classify(paths), (False, True))

    def test_push_includes_all_commits(self):
        self.write("frontend/app.ts", "feature")
        self.commit()
        self.write("README.md", "documentation")
        head = self.commit()
        self.assertEqual(
            classify(changed_paths("push", {"before": self.base, "after": head}, cwd=self.root)), (False, True)
        )

    def test_rename_selects_old_and_new_suites(self):
        self.git("mv", "src", "frontend")
        head = self.commit()
        paths = changed_paths("push", {"before": self.base, "after": head}, cwd=self.root)
        self.assertEqual(classify(paths), (True, True))

    def test_initial_push_cannot_be_mistaken_for_an_empty_diff(self):
        with self.assertRaises(ValueError):
            changed_paths("push", {"before": "0" * 40, "after": self.base}, cwd=self.root)

    def test_moving_statistics_code_out_of_its_directory_keeps_validation(self):
        self.write("src/asaree/stats/engine.py", "numerical implementation")
        base = self.commit()
        self.git("mv", "src/asaree/stats/engine.py", "src/moved.py")
        head = self.commit()
        paths = changed_paths("push", {"before": base, "after": head}, cwd=self.root)
        self.assertTrue(needs_stats_validation(paths))


class StatsValidationTests(unittest.TestCase):
    def test_numerical_sources_tests_dependencies_and_shared_config_select_validation(self):
        for path in [
            "src/asaree/stats/covariance.py",
            "src/asaree/services/stats_source.py",
            "src/asaree/api/stats.py",
            "src/asaree/worker/stats_task.py",
            "src/asaree/models/stats_analysis.py",
            "scripts/stats_validate_beta.py",
            "tests/test_stats_automatic.py",
            "tests/test_beta_hc3.py",
            "tests/conftest.py",
            "tests/fixtures/data.json",
            "pyproject.toml",
            "uv.lock",
            ".github/workflows/ci.yml",
            ".github/ci/scope.py",
            "unknown/file",
        ]:
            with self.subTest(path=path):
                self.assertTrue(needs_stats_validation([path]))

    def test_unrelated_code_and_documentation_skip_validation(self):
        for path in [
            "src/asaree/api/agents.py",
            "tests/test_agents.py",
            "frontend/src/components/stats/StatisticsReport.tsx",
            "frontend/package-lock.json",
            "src/asaree/stats/README.md",
            "docs/statistics.md",
        ]:
            with self.subTest(path=path):
                self.assertFalse(needs_stats_validation([path]))
        self.assertFalse(needs_stats_validation([]))

    def test_mixed_changes_keep_validation(self):
        self.assertTrue(needs_stats_validation(["README.md", "src/asaree/stats/analysis.py"]))


class OutputTests(unittest.TestCase):
    def invoke(self, paths=None, error=None, event_name="push"):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            output = Path(directory) / "output"
            event.write_text(json.dumps({}))
            with (
                patch.dict(
                    os.environ,
                    {"GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output)},
                ),
                patch("scope.changed_paths", return_value=paths, side_effect=error),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main()
            return output.read_text()

    def test_docs_only_emits_skip_outputs_for_github(self):
        self.assertEqual(
            self.invoke(["CONTRIBUTING.md"]), "backend=false\nfrontend=false\nstats_validation=false\n"
        )

    def test_failed_comparison_emits_both_jobs_instead_of_skipping(self):
        self.assertEqual(
            self.invoke(error=subprocess.CalledProcessError(128, "git")),
            "backend=true\nfrontend=true\nstats_validation=true\n",
        )

    def test_manual_run_selects_every_suite_without_a_diff(self):
        self.assertEqual(
            self.invoke(event_name="workflow_dispatch"), "backend=true\nfrontend=true\nstats_validation=true\n"
        )


if __name__ == "__main__":
    unittest.main()
