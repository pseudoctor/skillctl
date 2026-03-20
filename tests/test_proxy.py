import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from skillctl.proxy import InputSubmissionBuffer, LazySkillInjector, ShadowBuffer, spawn_interactive
from skillctl.registry import SkillRecord
from skillctl.resolver import SkillResolver


def make_record(tmp_path: Path, name: str, text: str, summary: str = "summary") -> SkillRecord:
    path = tmp_path / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return SkillRecord(
        canonical_name=name,
        aliases=(name,),
        summary=summary,
        path=str(path),
        source_cli="codex",
        scope="global",
        hash=f"{name}-hash",
        char_count=len(text),
        estimated_tokens=max(1, (len(text) + 3) // 4),
        last_indexed_at="2026-03-19T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
#  ShadowBuffer tests
# ---------------------------------------------------------------------------


class ShadowBufferTests(unittest.TestCase):
    def test_tracks_printable_characters(self) -> None:
        buf = ShadowBuffer()
        for ch in b"hello":
            buf.track(ch)
        self.assertEqual(buf.harvest(), "hello")

    def test_handles_backspace(self) -> None:
        buf = ShadowBuffer()
        for ch in b"helo\x7fo":  # helo + DEL + o → helo → hel + o = helo
            buf.track(ch)
        self.assertEqual(buf.harvest(), "helo")

    def test_handles_backspace_del(self) -> None:
        buf = ShadowBuffer()
        for ch in b"abc\x7f\x7f":  # abc + 2x DEL → a
            buf.track(ch)
        self.assertEqual(buf.harvest(), "a")

    def test_skips_escape_sequences(self) -> None:
        buf = ShadowBuffer()
        # "hi" + ESC [ A (arrow up) + "!" → should only track "hi!"
        for ch in b"hi\x1b[A!":
            buf.track(ch)
        self.assertEqual(buf.harvest(), "hi!")

    def test_skips_multi_char_escape(self) -> None:
        buf = ShadowBuffer()
        # ESC [ 1 ; 5 C  (Ctrl+Right) → skip all
        for ch in b"\x1b[1;5C":
            buf.track(ch)
        self.assertEqual(buf.harvest(), "")

    def test_ignores_control_chars(self) -> None:
        buf = ShadowBuffer()
        # Chars below 0x20 (except ESC, BS, DEL) are not tracked
        for ch in b"\x01\x02\x0a\x0d":
            buf.track(ch)
        self.assertEqual(buf.harvest(), "")

    def test_harvest_clears_buffer(self) -> None:
        buf = ShadowBuffer()
        for ch in b"abc":
            buf.track(ch)
        self.assertEqual(buf.harvest(), "abc")
        self.assertEqual(buf.harvest(), "")

    def test_clear_resets_state(self) -> None:
        buf = ShadowBuffer()
        for ch in b"abc":
            buf.track(ch)
        buf.clear()
        self.assertEqual(buf.harvest(), "")

    def test_backspace_on_empty_is_noop(self) -> None:
        buf = ShadowBuffer()
        buf.track(0x7F)
        buf.track(0x08)
        self.assertEqual(buf.harvest(), "")


# ---------------------------------------------------------------------------
#  LazySkillInjector tests – get_injection + transform
# ---------------------------------------------------------------------------


class InjectorTests(unittest.TestCase):
    def test_get_injection_returns_bootstrap_and_skill(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))

        injection = injector.get_injection("@brainstorming solve this")

        self.assertIn("Skill index available.", injection)
        self.assertIn("[Loaded skill: brainstorming", injection)
        self.assertIn("# Brainstorming", injection)
        self.assertNotIn("solve this", injection)  # original text NOT included

    def test_get_injection_returns_empty_when_no_skill_requested(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))

        # First call: includes bootstrap
        injection = injector.get_injection("plain request")
        self.assertIn("Skill index available.", injection)

        # Second call: no bootstrap, no skill → empty
        injection = injector.get_injection("another request")
        self.assertEqual(injection, "")

    def test_get_injection_skips_escaped_mentions(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))

        injection = injector.get_injection("@@brainstorming should stay literal")

        self.assertNotIn("[Loaded skill: brainstorming", injection)

    def test_transform_includes_original_text(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))

        result = injector.transform("@brainstorming solve this")

        self.assertIn("Skill index available.", result)
        self.assertIn("[Loaded skill: brainstorming", result)
        self.assertIn("@brainstorming solve this", result)

    def test_transform_replaces_escaped_at(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))

        result = injector.transform("@@brainstorming should stay literal")

        self.assertIn("@brainstorming should stay literal", result)
        self.assertNotIn("[Loaded skill: brainstorming", result)

    def test_injector_loads_skill_only_once(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))

        first = injector.transform("@brainstorming solve this")
        second = injector.transform("@brainstorming solve again")

        self.assertIn("[Loaded skill: brainstorming", first)
        self.assertNotIn("[Loaded skill: brainstorming", second)

    def test_injector_ignores_unknown_skill(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            result = injector.transform("@unknown solve this")

        self.assertIn("@unknown solve this", result)
        self.assertIn("Unknown skill: unknown", stderr.getvalue())

    def test_suggestion_mode_hints_without_loading(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(
            tmp_path,
            "brainstorming",
            "---\nname: brainstorming\ndescription: creative planning and feature ideation\n---\n",
            summary="creative planning and feature ideation",
        )
        stderr = io.StringIO()
        injector = LazySkillInjector(SkillResolver([record]), suggestion_mode=True)

        with redirect_stderr(stderr):
            result = injector.transform("Need creative ideation for a feature rollout")

        self.assertNotIn("[Loaded skill: brainstorming", result)
        self.assertIn("Suggested skills: @brainstorming", stderr.getvalue())

    def test_suggestion_mode_does_not_repeat_same_hint(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(
            tmp_path,
            "brainstorming",
            "---\nname: brainstorming\ndescription: creative planning and feature ideation\n---\n",
            summary="creative planning and feature ideation",
        )
        stderr = io.StringIO()
        injector = LazySkillInjector(SkillResolver([record]), suggestion_mode=True)

        with redirect_stderr(stderr):
            injector.transform("Need creative ideation for a feature rollout")
            injector.transform("Need more ideation around the feature")

        self.assertEqual(stderr.getvalue().count("Suggested skills: @brainstorming"), 1)

    def test_usage_summary_reflects_loaded_skills(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        first = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        second = make_record(tmp_path, "writer", "# Writer\nbody")
        injector = LazySkillInjector(SkillResolver([first, second]))

        injector.transform("@brainstorming do this")
        stats = injector.usage_summary()

        self.assertEqual(stats["indexed_skill_count"], 2)
        self.assertEqual(stats["loaded_skills"], ["brainstorming"])
        self.assertGreaterEqual(stats["actual_injected_tokens"], first.estimated_tokens)
        self.assertGreaterEqual(stats["saved_tokens"], 0)

    def test_usage_summary_counts_bootstrap_tokens(self) -> None:
        tmp_path = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        first = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        second = make_record(tmp_path, "writer", "# Writer\nbody")
        injector = LazySkillInjector(SkillResolver([first, second]))

        result = injector.transform("plain request")
        stats = injector.usage_summary()

        self.assertIn("Skill index available.", result)
        self.assertGreater(stats["actual_injected_tokens"], 0)
        self.assertEqual(stats["loaded_skills"], [])


# ---------------------------------------------------------------------------
#  InputSubmissionBuffer tests (non-TTY path)
# ---------------------------------------------------------------------------


class InputBufferTests(unittest.TestCase):
    def test_buffer_flushes_only_after_submit_boundary(self) -> None:
        buffer = InputSubmissionBuffer()
        first = buffer.feed(b"@brain", lambda text: f"<{text}>")
        second = buffer.feed(b"storming now\r", lambda text: f"<{text}>")

        self.assertEqual(first, b"")
        self.assertEqual(second, b"<@brainstorming now>\r")

    def test_buffer_handles_multiline_paste(self) -> None:
        buffer = InputSubmissionBuffer()
        payload = buffer.feed(b"first line\r\nsecond line\n", lambda text: f"<{text}>")

        self.assertEqual(payload, b"<first line>\r\n<second line>\n")


# ---------------------------------------------------------------------------
#  Spawn interactive test
# ---------------------------------------------------------------------------


class SpawnTests(unittest.TestCase):
    def test_spawn_interactive_uses_os_exec_path(self) -> None:
        injector = LazySkillInjector(SkillResolver([]))
        with patch("skillctl.proxy.pty.fork", return_value=(0, 99)), patch(
            "skillctl.proxy.os.execvpe", side_effect=SystemExit(7)
        ) as execvpe:
            with self.assertRaises(SystemExit) as exc:
                spawn_interactive(["codex", "--help"], {"HOME": "/tmp/home"}, injector)

        self.assertEqual(exc.exception.code, 7)
        execvpe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
