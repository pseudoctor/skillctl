import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from skillctl.proxy import InputSubmissionBuffer, LazySkillInjector, spawn_interactive
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


class ProxyTests(unittest.TestCase):
    def test_injector_bootstraps_and_loads_skill_once(self) -> None:
        tmp_path = Path(f"{self._testMethodName}-tmp").resolve()
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))

        first = injector.transform("@brainstorming solve this")
        second = injector.transform("@brainstorming solve again")

        self.assertIn("Skill index available.", first)
        self.assertIn("[Loaded skill: brainstorming", first)
        self.assertIn("# Brainstorming", first)
        self.assertNotIn("[Loaded skill: brainstorming", second)

    def test_injector_ignores_unknown_skill(self) -> None:
        tmp_path = Path(f"{self._testMethodName}-tmp").resolve()
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            result = injector.transform("@unknown solve this")

        self.assertIn("@unknown solve this", result)
        self.assertIn("Unknown skill: unknown", stderr.getvalue())

    def test_injector_supports_escaped_skill_mentions(self) -> None:
        tmp_path = Path(f"{self._testMethodName}-tmp").resolve()
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        record = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        injector = LazySkillInjector(SkillResolver([record]))

        result = injector.transform("@@brainstorming should stay literal")

        self.assertIn("@brainstorming should stay literal", result)
        self.assertNotIn("[Loaded skill: brainstorming", result)

    def test_input_buffer_flushes_only_after_submit_boundary(self) -> None:
        buffer = InputSubmissionBuffer()

        first = buffer.feed(b"@brain", lambda text: f"<{text}>")
        second = buffer.feed(b"storming now\r", lambda text: f"<{text}>")

        self.assertEqual(first, b"")
        self.assertEqual(second, b"<@brainstorming now>\r")

    def test_input_buffer_handles_multiline_paste(self) -> None:
        buffer = InputSubmissionBuffer()

        payload = buffer.feed(b"first line\r\nsecond line\n", lambda text: f"<{text}>")

        self.assertEqual(payload, b"<first line>\r\n<second line>\n")

    def test_suggestion_mode_hints_without_loading(self) -> None:
        tmp_path = Path(f"{self._testMethodName}-tmp").resolve()
        tmp_path.mkdir(parents=True, exist_ok=True)
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
        tmp_path = Path(f"{self._testMethodName}-tmp").resolve()
        tmp_path.mkdir(parents=True, exist_ok=True)
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
        tmp_path = Path(f"{self._testMethodName}-tmp").resolve()
        tmp_path.mkdir(parents=True, exist_ok=True)
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
        tmp_path = Path(f"{self._testMethodName}-tmp").resolve()
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))
        first = make_record(tmp_path, "brainstorming", "# Brainstorming\nbody")
        second = make_record(tmp_path, "writer", "# Writer\nbody")
        injector = LazySkillInjector(SkillResolver([first, second]))

        result = injector.transform("plain request")
        stats = injector.usage_summary()

        self.assertIn("Skill index available.", result)
        self.assertGreater(stats["actual_injected_tokens"], 0)
        self.assertEqual(stats["loaded_skills"], [])

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
