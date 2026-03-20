from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile


@dataclass(frozen=True)
class AppConfig:
    workspace_root: Path
    cache_dir: Path
    runtime_root: Path
    global_skill_roots: dict[str, Path]
    project_skill_roots: tuple[Path, ...]
    cli_homes: dict[str, Path]
    cli_commands: dict[str, str]


def default_config(workspace_root: Path | None = None) -> AppConfig:
    workspace = (workspace_root or Path.cwd()).resolve()
    home = Path.home()
    cache_dir = workspace / ".skillctl"
    runtime_root = Path(
        os.environ.get("SKILLCTL_RUNTIME_ROOT", str(Path(tempfile.gettempdir()) / "skillctl-runtime"))
    ).resolve()

    global_skill_roots = {
        "codex": home / ".codex" / "skills",
        "claude": home / ".claude" / "skills",
        "gemini": home / ".gemini" / "skills",
    }
    project_skill_roots = (
        workspace / "skills",
        workspace / ".codex" / "skills",
        workspace / ".claude" / "skills",
        workspace / ".gemini" / "skills",
    )
    cli_homes = {
        "codex": home / ".codex",
        "claude": home / ".claude",
        "gemini": home / ".gemini",
    }
    cli_commands = {
        "codex": os.environ.get("SKILLCTL_CODEX_BIN", "codex"),
        "claude": os.environ.get("SKILLCTL_CLAUDE_BIN", "claude"),
        "gemini": os.environ.get("SKILLCTL_GEMINI_BIN", "gemini"),
    }
    return AppConfig(
        workspace_root=workspace,
        cache_dir=cache_dir,
        runtime_root=runtime_root,
        global_skill_roots=global_skill_roots,
        project_skill_roots=project_skill_roots,
        cli_homes=cli_homes,
        cli_commands=cli_commands,
    )
