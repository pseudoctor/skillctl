import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillctl.config import default_config
from skillctl.runtime import _link, create_runtime


class RuntimeTests(unittest.TestCase):
    def test_runtime_creates_isolated_codex_home(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))

        home = tmp_path / "home"
        codex_home = home / ".codex"
        codex_home.mkdir(parents=True)
        (codex_home / "config.toml").write_text('model = "gpt-5.4"\n', encoding="utf-8")
        (codex_home / "auth.json").write_text("{}", encoding="utf-8")

        config = default_config(workspace)
        config = config.__class__(
            workspace_root=config.workspace_root,
            cache_dir=config.cache_dir,
            runtime_root=tmp_path / "runtime",
            global_skill_roots=config.global_skill_roots,
            project_skill_roots=config.project_skill_roots,
            cli_homes={
                "codex": codex_home,
                "claude": home / ".claude",
                "gemini": home / ".gemini",
            },
            cli_commands=config.cli_commands,
        )
        config.runtime_root.mkdir(parents=True, exist_ok=True)

        session = create_runtime(config, "codex")
        try:
            target = session.home_dir / ".codex"
            self.assertTrue(target.exists())
            self.assertTrue((target / "skills").is_dir())
            self.assertTrue((target / "config.toml").is_symlink())
            self.assertEqual(session.env["HOME"], str(session.home_dir))
        finally:
            session.cleanup()

    def test_link_falls_back_to_copy_when_symlink_fails(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))

        source = tmp_path / "config.toml"
        target = tmp_path / "copied" / "config.toml"
        source.write_text('model = "gpt-5.4"\n', encoding="utf-8")

        with patch.object(Path, "symlink_to", side_effect=OSError("blocked")):
            _link(source, target)

        self.assertTrue(target.exists())
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
