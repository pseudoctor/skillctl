from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import stat
import sys


SUPPORTED_CLIS = ("claude", "codex", "gemini")


@dataclass(frozen=True)
class ShimSpec:
    cli_name: str
    path: Path
    real_bin: str
    target_module: str = "skillctl"


def default_shim_dir(platform_name: str | None = None) -> Path:
    platform_name = platform_name or os.name
    if platform_name == "nt":
        raise NotImplementedError("Windows is not supported")
    return Path.home() / ".local" / "bin"


def install_shims(shim_dir: Path, platform_name: str | None = None) -> list[ShimSpec]:
    platform_name = platform_name or os.name
    if platform_name == "nt":
        raise NotImplementedError("Windows is not supported")
    resolved_specs = resolve_shims(shim_dir, platform_name)
    shim_dir.mkdir(parents=True, exist_ok=True)
    for spec in resolved_specs:
        spec.path.write_text(_render_shim(spec, platform_name), encoding="utf-8")
        _make_executable(spec.path)
    return resolved_specs


def remove_shims(shim_dir: Path, platform_name: str | None = None) -> list[Path]:
    platform_name = platform_name or os.name
    if platform_name == "nt":
        raise NotImplementedError("Windows is not supported")
    removed: list[Path] = []
    for cli_name in SUPPORTED_CLIS:
        for path in _shim_paths(shim_dir, cli_name, platform_name):
            if path.exists():
                path.unlink()
                removed.append(path)
    return removed


def collect_status(shim_dir: Path, platform_name: str | None = None) -> list[dict[str, str]]:
    platform_name = platform_name or os.name
    if platform_name == "nt":
        raise NotImplementedError("Windows is not supported")
    resolved_shim_dir = shim_dir.resolve()
    path_entries = {
        str(Path(entry).expanduser().resolve()) if entry else entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
    }
    rows = []
    for cli_name in SUPPORTED_CLIS:
        primary_path = _shim_paths(resolved_shim_dir, cli_name, platform_name)[0]
        rows.append(
            {
                "cli": cli_name,
                "shim_path": str(primary_path),
                "installed": "yes" if any(path.exists() for path in _shim_paths(resolved_shim_dir, cli_name, platform_name)) else "no",
                "on_path": "yes" if str(resolved_shim_dir) in path_entries else "no",
                "system_target": _find_real_bin(cli_name, resolved_shim_dir, platform_name) or "",
            }
        )
    return rows


def print_install_hint(shim_dir: Path, platform_name: str | None = None) -> None:
    platform_name = platform_name or os.name
    if platform_name == "nt":
        raise NotImplementedError("Windows is not supported")
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if str(shim_dir) in path_entries:
        print(f"Shim directory already on PATH: {shim_dir}")
        return
    shell = Path(os.environ.get("SHELL", "")).name or "shell"
    export_line = f'export PATH="{shim_dir}:$PATH"'
    print(f"Add this to your {shell} profile:")
    print(export_line)


def resolve_shims(shim_dir: Path, platform_name: str | None = None) -> list[ShimSpec]:
    platform_name = platform_name or os.name
    if platform_name == "nt":
        raise NotImplementedError("Windows is not supported")
    specs: list[ShimSpec] = []
    for cli_name in SUPPORTED_CLIS:
        real_bin = _resolve_real_bin(cli_name, shim_dir, platform_name)
        for shim_path in _shim_paths(shim_dir, cli_name, platform_name):
            specs.append(ShimSpec(cli_name=cli_name, path=shim_path, real_bin=real_bin))
    return specs


def _render_shim(spec: ShimSpec, platform_name: str) -> str:
    python_bin = sys.executable or "python3"
    env_name = f"SKILLCTL_REAL_{spec.cli_name.upper()}_BIN"
    return "\n".join(
        [
            "#!/bin/sh",
            f'export {env_name}="{spec.real_bin}"',
            f'exec "{python_bin}" -m {spec.target_module} {spec.cli_name} "$@"',
            "",
        ]
    )


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _resolve_real_bin(cli_name: str, shim_dir: Path, platform_name: str) -> str:
    resolved = _find_real_bin(cli_name, str(shim_dir.resolve()), platform_name)
    if resolved:
        return resolved
    raise FileNotFoundError(f"Could not find real CLI binary for '{cli_name}' on PATH")


def _find_real_bin(cli_name: str, shim_dir: str, platform_name: str) -> str | None:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        resolved_entry = str(Path(entry).expanduser().resolve())
        if resolved_entry == shim_dir:
            continue
        for candidate in _candidate_bins(Path(resolved_entry), cli_name, platform_name):
            if not candidate.exists() or not os.access(candidate, os.X_OK):
                continue
            if _looks_like_skillctl_shim(candidate):
                continue
            return str(candidate)
    return None


def _shim_paths(shim_dir: Path, cli_name: str, platform_name: str) -> list[Path]:
    return [shim_dir / cli_name]


def _candidate_bins(base_dir: Path, cli_name: str, platform_name: str) -> list[Path]:
    return [base_dir / cli_name]


def _looks_like_skillctl_shim(path: Path) -> bool:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "SKILLCTL_REAL_" in content and "-m skillctl " in content
