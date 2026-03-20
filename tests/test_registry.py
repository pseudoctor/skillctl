import unittest
from pathlib import Path
import tempfile
from unittest import mock

from skillctl.config import default_config
from skillctl.registry import build_registry, load_registry, save_registry
from skillctl.resolver import SkillResolver


def write_skill(root: Path, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


class RegistryTests(unittest.TestCase):
    def test_registry_scans_global_and_project_skills(self) -> None:
        with mock.patch("pathlib.Path.home") as mocked_home:
            tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
            self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
            workspace = tmp_path / "workspace"
            workspace.mkdir(parents=True)
            home = tmp_path / "home"
            mocked_home.return_value = home
            (home / ".codex" / "skills").mkdir(parents=True)
            (home / ".claude" / "skills").mkdir(parents=True)
            (home / ".gemini" / "skills").mkdir(parents=True)
            write_skill(home / ".codex" / "skills", "brainstorming", "global brainstorming")
            write_skill(workspace / "skills", "local-helper", "project helper")

            config = default_config(workspace)
            records = build_registry(config)
            self.assertEqual({record.canonical_name for record in records}, {"brainstorming", "local-helper"})

    def test_resolver_prefers_project_scope(self) -> None:
        with mock.patch("pathlib.Path.home") as mocked_home:
            tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
            self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
            workspace = tmp_path / "workspace"
            workspace.mkdir(parents=True)
            home = tmp_path / "home"
            mocked_home.return_value = home
            (home / ".codex" / "skills").mkdir(parents=True)
            (home / ".claude" / "skills").mkdir(parents=True)
            (home / ".gemini" / "skills").mkdir(parents=True)
            write_skill(home / ".codex" / "skills", "shared", "global shared")
            write_skill(workspace / "skills", "shared", "project shared")

            config = default_config(workspace)
            resolver = SkillResolver(build_registry(config))
            resolved = resolver.resolve("shared")
            self.assertEqual(resolved.scope, "project")

    def test_load_registry_skips_missing_cached_files(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        cache_dir = tmp_path / ".skillctl"
        cache_dir.mkdir(parents=True)
        records = [
            {
                "canonical_name": "missing",
                "aliases": ["missing"],
                "summary": "summary",
                "path": str(tmp_path / "missing.md"),
                "source_cli": "codex",
                "scope": "global",
                "hash": "abc",
                "last_indexed_at": "2026-03-19T00:00:00+00:00",
            }
        ]
        (cache_dir / "index.json").write_text(__import__("json").dumps(records), encoding="utf-8")

        loaded = load_registry(cache_dir)

        self.assertEqual(loaded, [])

    def test_load_registry_drops_complete_records_when_file_was_deleted(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        cache_dir = tmp_path / ".skillctl"
        cache_dir.mkdir(parents=True)
        missing_path = tmp_path / "missing.md"
        records = [
            {
                "canonical_name": "missing",
                "aliases": ["missing"],
                "summary": "summary",
                "path": str(missing_path),
                "source_cli": "codex",
                "scope": "global",
                "hash": "abc",
                "char_count": 10,
                "estimated_tokens": 3,
                "last_indexed_at": "2026-03-19T00:00:00+00:00",
            }
        ]
        (cache_dir / "index.json").write_text(__import__("json").dumps(records), encoding="utf-8")

        loaded = load_registry(cache_dir)

        self.assertEqual(loaded, [])


if __name__ == "__main__":
    unittest.main()
