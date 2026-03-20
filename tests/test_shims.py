import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillctl.shims import collect_status, default_shim_dir, install_shims, remove_shims, resolve_shims


class ShimTests(unittest.TestCase):
    def test_install_shims_creates_executables(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        real_bin_dir = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-bin-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(real_bin_dir, ignore_errors=True))
        for name in ("claude", "codex", "gemini"):
            candidate = real_bin_dir / name
            candidate.write_text("#!/bin/sh\n", encoding="utf-8")
            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)

        with patch.dict(os.environ, {"PATH": f"{real_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            specs = install_shims(tmp_path)

        self.assertEqual(len(specs), 3)
        for spec in specs:
            self.assertTrue(spec.path.exists())
            content = spec.path.read_text(encoding="utf-8")
            self.assertIn(f"-m skillctl {spec.cli_name}", content)
            self.assertIn(f'SKILLCTL_REAL_{spec.cli_name.upper()}_BIN', content)
            self.assertTrue(spec.path.stat().st_mode & stat.S_IXUSR)

    def test_resolve_shims_does_not_write_files(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        real_bin_dir = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-bin-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(real_bin_dir, ignore_errors=True))
        for name in ("claude", "codex", "gemini"):
            candidate = real_bin_dir / name
            candidate.write_text("#!/bin/sh\n", encoding="utf-8")
            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)

        with patch.dict(os.environ, {"PATH": f"{real_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            specs = resolve_shims(tmp_path)

        self.assertEqual(len(specs), 3)
        self.assertEqual(list(tmp_path.iterdir()) if tmp_path.exists() else [], [])

    def test_status_reports_installation_and_path(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        install_shims(tmp_path)
        resolved_dir = tmp_path.resolve()

        with patch.dict(os.environ, {"PATH": f"{resolved_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            rows = collect_status(resolved_dir)

        self.assertEqual({row["installed"] for row in rows}, {"yes"})
        self.assertEqual({row["on_path"] for row in rows}, {"yes"})

    def test_remove_shims_deletes_files(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        install_shims(tmp_path)

        removed = remove_shims(tmp_path)

        self.assertEqual(len(removed), 3)
        for path in removed:
            self.assertFalse(path.exists())

    def test_remove_shims_keeps_unmanaged_files(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        unmanaged = tmp_path / "claude"
        unmanaged.write_bytes(b"\xcf\xfa\xed\xfe")
        unmanaged.chmod(unmanaged.stat().st_mode | stat.S_IXUSR)

        removed = remove_shims(tmp_path)

        self.assertEqual(removed, [])
        self.assertTrue(unmanaged.exists())

    def test_install_shims_resolves_real_binary_outside_shim_dir(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        real_bin_dir = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-bin-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(real_bin_dir, ignore_errors=True))
        real_codex = real_bin_dir / "codex"
        real_codex.write_text("#!/bin/sh\n", encoding="utf-8")
        real_codex.chmod(real_codex.stat().st_mode | stat.S_IXUSR)

        with patch.dict(os.environ, {"PATH": f"{tmp_path}{os.pathsep}{real_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            specs = install_shims(tmp_path)

        codex_spec = next(spec for spec in specs if spec.cli_name == "codex")
        self.assertEqual(Path(codex_spec.real_bin).resolve(), real_codex.resolve())

    def test_resolve_shims_skips_unmanaged_real_binary_in_target_dir(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        real_codex = tmp_path / "codex"
        real_codex.write_text("#!/bin/sh\n", encoding="utf-8")
        real_codex.chmod(real_codex.stat().st_mode | stat.S_IXUSR)

        # When PATH only contains the shim dir itself, no real binary
        # can be found outside it, so resolve_shims raises FileNotFoundError.
        with patch.dict(os.environ, {"PATH": str(tmp_path)}):
            with self.assertRaises(FileNotFoundError):
                resolve_shims(tmp_path)

    def test_install_shims_skips_existing_skillctl_shims_on_path(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        old_shim_dir = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-old-"))
        real_bin_dir = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-bin-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(old_shim_dir, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(real_bin_dir, ignore_errors=True))

        old_shim = old_shim_dir / "claude"
        old_shim.write_text(
            '#!/bin/sh\nexport SKILLCTL_REAL_CLAUDE_BIN="/tmp/fake"\nexec "python3" -m skillctl claude "$@"\n',
            encoding="utf-8",
        )
        old_shim.chmod(old_shim.stat().st_mode | stat.S_IXUSR)

        real_claude = real_bin_dir / "claude"
        real_claude.write_text("#!/bin/sh\n", encoding="utf-8")
        real_claude.chmod(real_claude.stat().st_mode | stat.S_IXUSR)

        with patch.dict(os.environ, {"PATH": f"{old_shim_dir}{os.pathsep}{real_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            specs = install_shims(tmp_path)

        claude_spec = next(spec for spec in specs if spec.cli_name == "claude")
        self.assertEqual(Path(claude_spec.real_bin).resolve(), real_claude.resolve())

    def test_windows_shims_are_not_supported(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))

        with self.assertRaises(NotImplementedError):
            install_shims(tmp_path, platform_name="nt")
        with self.assertRaises(NotImplementedError):
            default_shim_dir(platform_name="nt")

    def test_status_reports_real_system_target_not_existing_shim(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        old_shim_dir = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-oldstatus-"))
        real_bin_dir = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-statusbin-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(old_shim_dir, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(real_bin_dir, ignore_errors=True))

        old_shim = old_shim_dir / "claude"
        old_shim.write_text(
            '#!/bin/sh\nexport SKILLCTL_REAL_CLAUDE_BIN="/tmp/fake"\nexec "python3" -m skillctl claude "$@"\n',
            encoding="utf-8",
        )
        old_shim.chmod(old_shim.stat().st_mode | stat.S_IXUSR)

        real_claude = real_bin_dir / "claude"
        real_claude.write_text("#!/bin/sh\n", encoding="utf-8")
        real_claude.chmod(real_claude.stat().st_mode | stat.S_IXUSR)

        with patch.dict(os.environ, {"PATH": f"{tmp_path}{os.pathsep}{old_shim_dir}{os.pathsep}{real_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            install_shims(tmp_path)
            rows = collect_status(tmp_path)

        claude_row = next(row for row in rows if row["cli"] == "claude")
        self.assertEqual(Path(claude_row["system_target"]).resolve(), real_claude.resolve())

    def test_status_marks_unmanaged_file_as_not_installed(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        unmanaged = tmp_path / "claude"
        unmanaged.write_bytes(b"\xcf\xfa\xed\xfe")
        unmanaged.chmod(unmanaged.stat().st_mode | stat.S_IXUSR)

        rows = collect_status(tmp_path)
        claude_row = next(row for row in rows if row["cli"] == "claude")

        self.assertEqual(claude_row["installed"], "no")

    def test_install_shims_fails_when_real_binary_is_missing(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))

        with patch.dict(os.environ, {"PATH": str(tmp_path)}):
            with self.assertRaises(FileNotFoundError):
                install_shims(tmp_path)

        self.assertEqual(list(tmp_path.iterdir()) if tmp_path.exists() else [], [])

    def test_install_shims_partially_installs_available_clis(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        real_bin_dir = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-bin-"))
        self.addCleanup(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(real_bin_dir, ignore_errors=True))
        for name in ("codex", "gemini"):
            candidate = real_bin_dir / name
            candidate.write_text("#!/bin/sh\n", encoding="utf-8")
            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)

        with patch.dict(os.environ, {"PATH": str(real_bin_dir)}):
            specs = install_shims(tmp_path)

        self.assertEqual({spec.cli_name for spec in specs}, {"codex", "gemini"})
        self.assertFalse((tmp_path / "claude").exists())


if __name__ == "__main__":
    unittest.main()
