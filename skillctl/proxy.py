from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import fcntl
import os
import pty
import re
import select
import shutil
import signal
import sys
import termios
import tty

from .registry import estimate_tokens
from .resolver import SkillNotFoundError, SkillResolver


SKILL_PATTERN = re.compile(r"(?<![\w/-])@([A-Za-z0-9._-]+)")
ESCAPED_AT_PATTERN = re.compile(r"@@([A-Za-z0-9._-]+)")
ESCAPED_AT_SENTINEL = "\0SKILLCTL_ESCAPED_AT\0"
SUBMIT_DELIMITERS = b"\r\n"


# ---------------------------------------------------------------------------
#  ShadowBuffer – tracks forwarded input without buffering/delaying it
# ---------------------------------------------------------------------------


@dataclass
class ShadowBuffer:
    """Tracks characters already forwarded to the child PTY.

    Unlike ``InputSubmissionBuffer`` this class never *holds back* bytes.
    It merely remembers printable text so that ``harvest()`` can return
    the accumulated line when the caller detects a submit boundary.
    """

    pending: bytearray = field(default_factory=bytearray)
    _in_escape: bool = False
    _in_csi: bool = False

    def track(self, byte_val: int) -> None:
        """Record a single byte into the shadow buffer."""
        # Inside a CSI sequence: ESC [ <params> <final>
        # Parameter bytes: 0x20–0x3F, final byte: 0x40–0x7E
        if self._in_csi:
            if 0x40 <= byte_val <= 0x7E:  # final byte terminates CSI
                self._in_csi = False
            return
        # ESC seen, waiting for next byte
        if self._in_escape:
            self._in_escape = False
            if byte_val == 0x5B:  # '[' → start of CSI sequence
                self._in_csi = True
                return
            # Other ESC sequences (e.g. ESC O): consume the next byte and done
            return
        # Start of escape
        if byte_val == 0x1B:
            self._in_escape = True
            return
        # Backspace / DEL
        if byte_val in (0x7F, 0x08):
            if self.pending:
                self.pending.pop()
            return
        # Only accumulate printable bytes (incl. high bytes for UTF-8)
        if byte_val >= 0x20:
            self.pending.append(byte_val)

    def harvest(self) -> str:
        """Return accumulated text and clear the buffer."""
        text = bytes(self.pending).decode("utf-8", errors="ignore")
        self.pending.clear()
        return text

    def clear(self) -> None:
        self.pending.clear()
        self._in_escape = False
        self._in_csi = False


# ---------------------------------------------------------------------------
#  InputSubmissionBuffer – retained for non-TTY (pipe) fallback
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
#  LazySkillInjector
# ---------------------------------------------------------------------------


@dataclass
class LazySkillInjector:
    resolver: SkillResolver
    loaded_hashes: set[str] = field(default_factory=set)
    bootstrap_sent: bool = False
    suggestion_mode: bool = False
    suggested_names: set[str] = field(default_factory=set)
    loaded_skills: list[str] = field(default_factory=list)
    actual_injected_tokens: int = 0

    # -- Public API ----------------------------------------------------------

    def get_injection(self, raw_text: str) -> str:
        """Return *only* the content to inject (bootstrap + skills).

        Does NOT include the original user text—the caller is responsible
        for ensuring ``raw_text`` has already been forwarded to the child.
        """
        prefix_parts: list[str] = []
        self._maybe_bootstrap(raw_text, prefix_parts)
        self._resolve_and_load(raw_text, prefix_parts)
        self._maybe_suggest(raw_text)
        if not prefix_parts:
            return ""
        return "\n".join(prefix_parts) + "\n"

    def transform(self, raw_text: str) -> str:
        """Full transform: injection + original text (for non-TTY / tests)."""
        injection = self.get_injection(raw_text)
        protected = ESCAPED_AT_PATTERN.sub(
            lambda match: f"{ESCAPED_AT_SENTINEL}{match.group(1)}", raw_text,
        )
        restored = protected.replace(ESCAPED_AT_SENTINEL, "@")
        if not injection:
            return restored
        return injection + restored

    # -- Internal ------------------------------------------------------------

    def _maybe_bootstrap(self, raw_text: str, parts: list[str]) -> None:
        if self.bootstrap_sent or not raw_text.strip():
            return
        names = ", ".join(sorted({r.canonical_name for r in self.resolver.list_records()}))
        bootstrap_text = (
            "Skill index available. Request a skill explicitly with @skill_name. "
            f"Indexed skills: {names if names else 'none'}."
        )
        parts.append(bootstrap_text)
        self.actual_injected_tokens += estimate_tokens(bootstrap_text)
        if self.suggestion_mode:
            suggestion_text = "Suggestion mode enabled. Matching skills will be hinted locally without auto-loading."
            parts.append(suggestion_text)
            self.actual_injected_tokens += estimate_tokens(suggestion_text)
        self.bootstrap_sent = True

    def _resolve_and_load(self, raw_text: str, parts: list[str]) -> None:
        protected_text = ESCAPED_AT_PATTERN.sub(
            lambda match: f"{ESCAPED_AT_SENTINEL}{match.group(1)}", raw_text,
        )
        requested = []
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
            parts.append(
                f"\n[Loaded skill: {skill.canonical_name} | scope={skill.scope} | source={skill.source_cli}]\n"
                f"{content}\n[End loaded skill]\n"
            )
            self.loaded_hashes.add(skill.hash)
            self.loaded_skills.append(skill.canonical_name)
            self.actual_injected_tokens += skill.estimated_tokens

    def _maybe_suggest(self, raw_text: str) -> None:
        if not self.suggestion_mode or not raw_text.strip():
            return
        protected_text = ESCAPED_AT_PATTERN.sub(
            lambda match: f"{ESCAPED_AT_SENTINEL}{match.group(1)}", raw_text,
        )
        # Only suppress suggestions when a skill was actually loaded in this turn.
        resolved = []
        for match in SKILL_PATTERN.findall(protected_text):
            try:
                resolved.append(self.resolver.resolve(match))
            except SkillNotFoundError:
                pass
        if any(r.hash in self.loaded_hashes for r in resolved):
            return  # explicit skill was loaded, no suggestion needed
        suggestions = self.resolver.suggest(protected_text)
        new_suggestions = [s for s in suggestions if s.canonical_name not in self.suggested_names]
        if new_suggestions:
            hint = ", ".join(f"@{s.canonical_name}" for s in new_suggestions)
            sys.stderr.write(f"[skillctl] Suggested skills: {hint}\n")
            sys.stderr.flush()
            self.suggested_names.update(s.canonical_name for s in new_suggestions)

    # -- Stats ---------------------------------------------------------------

    def usage_summary(self) -> dict[str, object]:
        records = self.resolver.list_records()
        baseline_tokens = sum(r.estimated_tokens for r in records)
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


# ---------------------------------------------------------------------------
#  spawn_interactive – entry point
# ---------------------------------------------------------------------------


def spawn_interactive(command: list[str], env: dict[str, str], injector: LazySkillInjector) -> int:
    argv = [shutil.which(command[0]) or command[0], *command[1:]]

    pid, master_fd = pty.fork()
    if pid == 0:
        os.execvpe(argv[0], argv, env)

    stdin_fd = sys.stdin.fileno()
    is_tty = os.isatty(stdin_fd)

    if is_tty:
        old_settings = termios.tcgetattr(stdin_fd)
    try:
        if is_tty:
            tty.setraw(stdin_fd)
            _sync_winsize(master_fd)
            _install_sigwinch(master_fd)
            return _pump_io_tty(pid, master_fd, stdin_fd, injector)
        else:
            input_buffer = InputSubmissionBuffer()
            return _pump_io_buffered(pid, master_fd, stdin_fd, input_buffer, injector.transform)
    finally:
        if is_tty:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
        os.close(master_fd)
        injector.print_usage_summary()


# ---------------------------------------------------------------------------
#  TTY I/O loop – transparent forwarding + shadow buffer
# ---------------------------------------------------------------------------


def _pump_io_tty(pid: int, master_fd: int, stdin_fd: int, injector: LazySkillInjector) -> int:
    shadow = ShadowBuffer()
    watch_fds = [master_fd, stdin_fd]

    while True:
        try:
            read_fds, _, _ = select.select(watch_fds, [], [])
        except InterruptedError:
            continue  # SIGWINCH may interrupt select

        if master_fd in read_fds:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            os.write(sys.stdout.fileno(), data)

        if stdin_fd in read_fds:
            chunk = os.read(stdin_fd, 1024)
            if not chunk:
                watch_fds = [master_fd]  # stdin EOF
                continue

            i = 0
            n = len(chunk)
            while i < n:
                byte_val = chunk[i]
                if byte_val == 0x0D:  # CR = submit boundary
                    text = shadow.harvest()
                    injection = injector.get_injection(text)
                    if injection:
                        # Use \n (not \r) inside injected content to avoid
                        # triggering the child CLI's submit.
                        os.write(master_fd, injection.encode("utf-8"))
                    os.write(master_fd, b"\r")
                    i += 1
                elif byte_val == 0x03:  # Ctrl+C
                    shadow.clear()
                    os.write(master_fd, b"\x03")
                    i += 1
                elif byte_val == 0x04:  # Ctrl+D
                    shadow.clear()
                    os.write(master_fd, b"\x04")
                    i += 1
                else:
                    # Batch consecutive non-special bytes into one write
                    start = i
                    while i < n and chunk[i] not in (0x0D, 0x03, 0x04):
                        shadow.track(chunk[i])
                        i += 1
                    os.write(master_fd, chunk[start:i])

    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


# ---------------------------------------------------------------------------
#  Non-TTY (pipe) I/O loop – uses InputSubmissionBuffer
# ---------------------------------------------------------------------------


def _pump_io_buffered(pid: int, master_fd: int, stdin_fd: int, input_buffer: InputSubmissionBuffer, transform) -> int:
    watch_fds = [master_fd, stdin_fd]

    while True:
        read_fds, _, _ = select.select(watch_fds, [], [])

        if master_fd in read_fds:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            os.write(sys.stdout.fileno(), data)

        if stdin_fd in read_fds:
            chunk = os.read(stdin_fd, 1024)
            if not chunk:
                flushed = input_buffer.feed(b"", transform)
                if flushed:
                    os.write(master_fd, flushed)
                watch_fds = [master_fd]
                continue
            data = input_buffer.feed(chunk, transform)
            if data:
                os.write(master_fd, data)

    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


# ---------------------------------------------------------------------------
#  Terminal window size helpers
# ---------------------------------------------------------------------------


def _sync_winsize(master_fd: int) -> None:
    """Copy the real terminal's window size to the child PTY."""
    try:
        buf = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, buf)
    except OSError:
        pass


def _install_sigwinch(master_fd: int) -> None:
    """Relay SIGWINCH to the child PTY so it sees size changes."""

    def _handler(signum: int, frame: object) -> None:
        _sync_winsize(master_fd)

    signal.signal(signal.SIGWINCH, _handler)
