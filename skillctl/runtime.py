from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import tempfile

from .config import AppConfig


@dataclass(frozen=True)
class RuntimeSession:
    cli_name: str
    runtime_dir: Path
    home_dir: Path
    env: dict[str, str]

    def cleanup(self) -> None:
        shutil.rmtree(self.runtime_dir, ignore_errors=True)


def create_runtime(config: AppConfig, cli_name: str) -> RuntimeSession:
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(tempfile.mkdtemp(prefix=f"skillctl-{cli_name}-", dir=str(config.runtime_root)))
    home_dir = runtime_dir / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = runtime_dir / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    source_home = config.cli_homes[cli_name]
    if cli_name == "codex":
        _setup_subdir(home_dir / ".codex", source_home, preserve=("config.toml", "auth.json", "AGENTS.md"))
    elif cli_name == "claude":
        _setup_subdir(
            home_dir / ".claude",
            source_home,
            preserve=("config.json", "settings.json", "settings.local.json", "mcp.json", "agents", "plugins"),
        )
    elif cli_name == "gemini":
        _setup_subdir(
            home_dir / ".gemini",
            source_home,
            preserve=("settings.json", "projects.json", "trustedFolders.json", "oauth_creds.json", "GEMINI.md"),
        )
    else:
        raise ValueError(f"Unsupported CLI: {cli_name}")

    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["TMPDIR"] = str(temp_dir)
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)
    env["SKILLCTL_ACTIVE_CLI"] = cli_name
    env["SKILLCTL_RUNTIME_HOME"] = str(home_dir)
    return RuntimeSession(cli_name=cli_name, runtime_dir=runtime_dir, home_dir=home_dir, env=env)


def _setup_subdir(target_root: Path, source_root: Path, preserve: tuple[str, ...]) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for name in preserve:
        source = source_root / name
        if not source.exists():
            continue
        _link(source, target_root / name)
    (target_root / "skills").mkdir(exist_ok=True)


def _link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(source, target_is_directory=source.is_dir())
        return
    except FileExistsError:
        import sys
        sys.stderr.write(f"[skillctl] Symlink target already exists, skipping: {target}\n")
        return
    except OSError:
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
