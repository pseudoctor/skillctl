from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import pty
import re
import select
import shutil
import sys

from .resolver import SkillNotFoundError, SkillResolver


SKILL_PATTERN = re.compile(r"(?<![\w/-])@([A-Za-z0-9._-]+)")
ESCAPED_AT_PATTERN = re.compile(r"@@([A-Za-z0-9._-]+)")
ESCAPED_AT_SENTINEL = "\0SKILLCTL_ESCAPED_AT\0"
SUBMIT_DELIMITERS = b"\r\n"


@dataclass
class InputSubmissionBuffer:
    pending: bytearray = field(default_factory=bytearray)

    def feed(self, chunk: bytes, transform) -> bytes:
        if not chunk:
            if not self.pending:
                return b""
            return self._flush(transform)

        self.pending.extend(chunk)
        ready = bytearray()
        while True:
            boundary = self._find_boundary()
            if boundary is None:
                break
            end_index, delimiter = boundary
            payload = bytes(self.pending[:end_index])
            del self.pending[: end_index + len(delimiter)]
            text = payload.decode("utf-8", errors="ignore")
            ready.extend(transform(text).encode("utf-8"))
            ready.extend(delimiter)
        return bytes(ready)

    def _flush(self, transform) -> bytes:
        payload = bytes(self.pending)
        self.pending.clear()
        text = payload.decode("utf-8", errors="ignore")
        return transform(text).encode("utf-8")

    def _find_boundary(self) -> tuple[int, bytes] | None:
        for index, byte in enumerate(self.pending):
            if byte not in SUBMIT_DELIMITERS:
                continue
            if byte == ord("\r") and index + 1 < len(self.pending) and self.pending[index + 1] == ord("\n"):
                return index, b"\r\n"
            return index, bytes([byte])
        return None


@dataclass
class LazySkillInjector:
    resolver: SkillResolver
    loaded_hashes: set[str] = field(default_factory=set)
    bootstrap_sent: bool = False
    suggestion_mode: bool = False
    suggested_names: set[str] = field(default_factory=set)
    loaded_skills: list[str] = field(default_factory=list)
    actual_injected_tokens: int = 0

    def transform(self, raw_text: str) -> str:
        prefix_parts: list[str] = []
        if not self.bootstrap_sent and raw_text.strip():
            names = ", ".join(sorted({record.canonical_name for record in self.resolver.list_records()}))
            bootstrap_text = (
                "Skill index available. Request a skill explicitly with @skill_name. "
                f"Indexed skills: {names if names else 'none'}."
            )
            prefix_parts.append(bootstrap_text)
            self.actual_injected_tokens += _estimate_tokens(bootstrap_text)
            if self.suggestion_mode:
                suggestion_text = "Suggestion mode enabled. Matching skills will be hinted locally without auto-loading."
                prefix_parts.append(suggestion_text)
                self.actual_injected_tokens += _estimate_tokens(suggestion_text)
            self.bootstrap_sent = True

        requested = []
        protected_text = ESCAPED_AT_PATTERN.sub(lambda match: f"{ESCAPED_AT_SENTINEL}{match.group(1)}", raw_text)
        for match in SKILL_PATTERN.findall(protected_text):
            try:
                requested.append(self.resolver.resolve(match))
            except SkillNotFoundError:
                sys.stderr.write(f"[skillctl] Unknown skill: {match}\n")
                sys.stderr.flush()
        for skill in requested:
            if skill.hash in self.loaded_hashes:
                continue
            try:
                content = Path(skill.path).read_text(encoding="utf-8", errors="ignore")
            except FileNotFoundError:
                sys.stderr.write(f"[skillctl] Skill file disappeared: {skill.path}\n")
                sys.stderr.flush()
                continue
            prefix_parts.append(
                f"\n[Loaded skill: {skill.canonical_name} | scope={skill.scope} | source={skill.source_cli}]\n"
                f"{content}\n[End loaded skill]\n"
            )
            self.loaded_hashes.add(skill.hash)
            self.loaded_skills.append(skill.canonical_name)
            self.actual_injected_tokens += skill.estimated_tokens

        if self.suggestion_mode and raw_text.strip() and not requested:
            suggestions = self.resolver.suggest(protected_text)
            new_suggestions = [item for item in suggestions if item.canonical_name not in self.suggested_names]
            if new_suggestions:
                hint = ", ".join(f"@{item.canonical_name}" for item in new_suggestions)
                sys.stderr.write(f"[skillctl] Suggested skills: {hint}\n")
                sys.stderr.flush()
                self.suggested_names.update(item.canonical_name for item in new_suggestions)

        restored_text = protected_text.replace(ESCAPED_AT_SENTINEL, "@")
        if not prefix_parts:
            return restored_text
        return "\n".join(prefix_parts) + "\n" + restored_text

    def usage_summary(self) -> dict[str, object]:
        records = self.resolver.list_records()
        baseline_tokens = sum(record.estimated_tokens for record in records)
        saved_tokens = max(0, baseline_tokens - self.actual_injected_tokens)
        saved_ratio = 0.0 if baseline_tokens == 0 else saved_tokens / baseline_tokens
        return {
            "indexed_skill_count": len(records),
            "baseline_tokens": baseline_tokens,
            "actual_injected_tokens": self.actual_injected_tokens,
            "saved_tokens": saved_tokens,
            "saved_ratio": saved_ratio,
            "loaded_skills": list(self.loaded_skills),
        }

    def print_usage_summary(self) -> None:
        stats = self.usage_summary()
        loaded = ", ".join(stats["loaded_skills"]) if stats["loaded_skills"] else "none"
        sys.stderr.write(
            "[skillctl] Token estimate: "
            f"baseline={stats['baseline_tokens']} "
            f"actual={stats['actual_injected_tokens']} "
            f"saved={stats['saved_tokens']} "
            f"saved_ratio={stats['saved_ratio']:.1%} "
            f"loaded={loaded}\n"
        )
        sys.stderr.flush()


def spawn_interactive(command: list[str], env: dict[str, str], injector: LazySkillInjector) -> int:
    argv = [shutil.which(command[0]) or command[0], *command[1:]]
    input_buffer = InputSubmissionBuffer()

    def read_stdin(fd: int) -> bytes:
        chunk = os.read(fd, 1024)
        return input_buffer.feed(chunk, injector.transform)

    def read_master(fd: int) -> bytes:
        return os.read(fd, 1024)

    pid, master_fd = pty.fork()
    if pid == 0:
        os.execvpe(argv[0], argv, env)

    try:
        return _pump_io(pid, master_fd, read_stdin, read_master)
    finally:
        os.close(master_fd)
        injector.print_usage_summary()


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _pump_io(pid: int, master_fd: int, read_stdin, read_master) -> int:
    while True:
        read_fds, _, _ = select.select([master_fd, sys.stdin.fileno()], [], [])
        if master_fd in read_fds:
            try:
                data = read_master(master_fd)
            except OSError:
                break
            if not data:
                break
            os.write(sys.stdout.fileno(), data)
        if sys.stdin.fileno() in read_fds:
            data = read_stdin(sys.stdin.fileno())
            if data:
                os.write(master_fd, data)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)
