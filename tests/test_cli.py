import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from skillctl.cli import _build_parser, _session_records, _should_passthrough, _validate_scope_args, _visible_records, main
from skillctl.registry import SkillRecord


def record(name: str, scope: str) -> SkillRecord:
    return SkillRecord(
        canonical_name=name,
        aliases=(name,),
        summary="summary",
        path=f"/tmp/{name}.md",
        source_cli="codex" if scope == "global" else "project",
        scope=scope,
        hash=f"{name}-hash",
        char_count=100,
        estimated_tokens=25,
        last_indexed_at="2026-03-19T00:00:00+00:00",
    )


class CliTests(unittest.TestCase):
    def test_scope_flags_filter_records(self) -> None:
        records = [record("global-one", "global"), record("project-one", "project")]

        class DummyConfig:
            pass

        args = argparse.Namespace(global_only=True, project_only=False)
        self.assertEqual([item.canonical_name for item in _visible_records_with_records(records, args)], ["global-one"])

        args = argparse.Namespace(global_only=False, project_only=True)
        self.assertEqual([item.canonical_name for item in _visible_records_with_records(records, args)], ["project-one"])

        args = argparse.Namespace(global_only=False, project_only=False)
        self.assertEqual(len(_visible_records_with_records(records, args)), 2)

    def test_scope_flags_are_mutually_exclusive(self) -> None:
        parser = _build_parser()
        args = argparse.Namespace(global_only=True, project_only=True)
        with self.assertRaises(SystemExit):
            _validate_scope_args(parser, args)

    def test_session_records_keep_project_and_current_cli_globals_only(self) -> None:
        records = [
            record("global-codex", "global"),
            SkillRecord(
                canonical_name="global-claude",
                aliases=("global-claude",),
                summary="summary",
                path="/tmp/global-claude.md",
                source_cli="claude",
                scope="global",
                hash="global-claude-hash",
                char_count=100,
                estimated_tokens=25,
                last_indexed_at="2026-03-19T00:00:00+00:00",
            ),
            record("project-one", "project"),
        ]
        args = argparse.Namespace(global_only=False, project_only=False)

        visible = _session_records_with_records(records, args, "codex")

        self.assertEqual([item.canonical_name for item in visible], ["global-codex", "project-one"])

    def test_should_passthrough_for_help_version_and_subcommand(self) -> None:
        self.assertTrue(_should_passthrough(["--help"]))
        self.assertTrue(_should_passthrough(["--version"]))
        self.assertTrue(_should_passthrough(["exec", "--json"]))
        self.assertFalse(_should_passthrough(["--suggest-skills"]))
        self.assertFalse(_should_passthrough([]))

    def test_main_passthroughs_provider_help(self) -> None:
        with patch("skillctl.cli._run_passthrough", return_value=0) as runner:
            code = main(["codex", "--help"])

        self.assertEqual(code, 0)
        runner.assert_called_once_with("codex", ["--help"])

    def test_inspect_reports_missing_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix=f"{self._testMethodName}-") as tmpdir:
            tmp_path = Path(tmpdir)
            missing = tmp_path / "missing.md"
            record = SkillRecord(
                canonical_name="missing",
                aliases=("missing",),
                summary="summary",
                path=str(missing),
                source_cli="codex",
                scope="global",
                hash="missing-hash",
                char_count=10,
                estimated_tokens=3,
                last_indexed_at="2026-03-19T00:00:00+00:00",
            )
            stderr = io.StringIO()
            stdout = io.StringIO()
            with redirect_stderr(stderr):
                with redirect_stdout(stdout):
                    with patch("skillctl.cli._visible_records", return_value=[record]):
                        code = main(["inspect", "missing"])

        self.assertEqual(code, 1)
        self.assertIn("Skill file missing", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_shim_check_runs_resolution(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with patch("skillctl.cli.resolve_shims") as resolver:
                resolver.return_value = [
                    type("Spec", (), {"cli_name": "codex", "real_bin": "/opt/homebrew/bin/codex"})(),
                ]
                code = main(["shim", "check", "--dir", "/tmp/skillctl-check-test"])

        self.assertEqual(code, 0)
        resolver.assert_called_once()
        self.assertIn("Validated 1 CLI binaries", stdout.getvalue())


def _visible_records_with_records(records, args):
    from skillctl import cli as cli_module

    original = cli_module._ensure_registry
    try:
        cli_module._ensure_registry = lambda config: records
        return _visible_records(object(), args)
    finally:
        cli_module._ensure_registry = original


def _session_records_with_records(records, args, cli_name):
    from skillctl import cli as cli_module

    original = cli_module._ensure_registry
    try:
        cli_module._ensure_registry = lambda config: records
        return _session_records(object(), args, cli_name)
    finally:
        cli_module._ensure_registry = original


if __name__ == "__main__":
    unittest.main()
