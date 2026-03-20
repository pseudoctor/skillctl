from __future__ import annotations

import argparse
import subprocess
import sys

from .config import default_config
from .proxy import LazySkillInjector, spawn_interactive
from .registry import build_registry, load_registry, save_registry
from .resolver import SkillNotFoundError, SkillResolver
from .runtime import create_runtime
from .shims import collect_status, default_shim_dir, install_shims, print_install_hint, remove_shims, resolve_shims


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args, unknown = parser.parse_known_args(argv)
    if getattr(args, "command", None) in {"claude", "codex", "gemini"}:
        args.args = list(unknown)
    config = default_config()
    _validate_scope_args(parser, args)

    if args.command == "index" and args.index_command == "rebuild":
        records = build_registry(config)
        path = save_registry(records, config.cache_dir)
        print(f"Indexed {len(records)} skills into {path}")
        return 0

    if args.command == "list":
        records = _visible_records(config, args)
        for record in records:
            print(
                f"{record.canonical_name}\t{record.scope}\t{record.source_cli}\t"
                f"tokens={record.estimated_tokens}\t{record.summary}"
            )
        return 0

    if args.command == "stats":
        records = _visible_records(config, args)
        total = sum(record.estimated_tokens for record in records)
        print(f"indexed_skills={len(records)}")
        print(f"estimated_full_load_tokens={total}")
        for record in records[: args.limit]:
            print(
                f"{record.canonical_name}\t{record.scope}\t{record.source_cli}\t"
                f"tokens={record.estimated_tokens}\tchars={record.char_count}"
            )
        return 0

    if args.command == "inspect":
        resolver = SkillResolver(_visible_records(config, args))
        try:
            record = resolver.resolve(args.skill_name)
        except SkillNotFoundError:
            print(f"Skill not found: {args.skill_name}", file=sys.stderr)
            return 1
        try:
            content = record.path_obj.read_text(encoding="utf-8", errors="ignore")
        except FileNotFoundError:
            print(f"Skill file missing: {record.path}", file=sys.stderr)
            return 1
        print(f"name: {record.canonical_name}")
        print(f"scope: {record.scope}")
        print(f"source: {record.source_cli}")
        print(f"path: {record.path}")
        print()
        print(content)
        return 0

    if args.command == "shim":
        shim_dir = args.dir.resolve() if args.dir else default_shim_dir()
        if args.shim_command == "check":
            specs = resolve_shims(shim_dir)
            print(f"Validated {len(specs)} CLI binaries for shim installation")
            for spec in specs:
                print(f"{spec.cli_name}\t{spec.real_bin}")
            return 0
        if args.shim_command == "install":
            specs = install_shims(shim_dir)
            print(f"Installed {len(specs)} shims into {shim_dir}")
            print_install_hint(shim_dir)
            return 0
        if args.shim_command == "remove":
            removed = remove_shims(shim_dir)
            print(f"Removed {len(removed)} shims from {shim_dir}")
            return 0
        if args.shim_command == "status":
            for row in collect_status(shim_dir):
                print(
                    f"{row['cli']}\tinstalled={row['installed']}\ton_path={row['on_path']}\t"
                    f"shim={row['shim_path']}\tsystem={row['system_target']}"
                )
            return 0

    if args.command in {"claude", "codex", "gemini"}:
        if _should_passthrough(args.args):
            return _run_passthrough(_command_for_cli(config, args.command), args.args)
        records = _session_records(config, args, args.command)
        resolver = SkillResolver(records)
        injector = LazySkillInjector(resolver, suggestion_mode=args.suggest_skills)
        runtime = create_runtime(config, args.command)
        try:
            command = [_command_for_cli(config, args.command), *args.args]
            return spawn_interactive(command, runtime.env, injector)
        finally:
            runtime.cleanup()

    parser.error("Unsupported command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skillctl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index")
    index_subparsers = index_parser.add_subparsers(dest="index_command", required=True)
    index_subparsers.add_parser("rebuild")

    list_parser = subparsers.add_parser("list")
    _add_scope_flags(list_parser)

    stats_parser = subparsers.add_parser("stats")
    stats_parser.add_argument("--limit", type=int, default=20)
    _add_scope_flags(stats_parser)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("skill_name")
    _add_scope_flags(inspect_parser)

    shim_parser = subparsers.add_parser("shim")
    shim_subparsers = shim_parser.add_subparsers(dest="shim_command", required=True)
    for name in ("check", "install", "remove", "status"):
        subparser = shim_subparsers.add_parser(name)
        subparser.add_argument("--dir", type=_path_arg, default=None)

    for cli_name in ("claude", "codex", "gemini"):
        cli_parser = subparsers.add_parser(cli_name, add_help=False)
        cli_parser.add_argument("--suggest-skills", action="store_true")
        _add_scope_flags(cli_parser)
        cli_parser.set_defaults(args=[])

    return parser


def _ensure_registry(config):
    records = load_registry(config.cache_dir)
    if records:
        return records
    records = build_registry(config)
    save_registry(records, config.cache_dir)
    return records


def _path_arg(value: str):
    from pathlib import Path

    return Path(value).expanduser()


def _add_scope_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--global-only", action="store_true")
    parser.add_argument("--project-only", action="store_true")


def _validate_scope_args(parser: argparse.ArgumentParser, args) -> None:
    if getattr(args, "global_only", False) and getattr(args, "project_only", False):
        parser.error("--global-only and --project-only cannot be used together")


def _visible_records(config, args):
    records = _ensure_registry(config)
    if getattr(args, "global_only", False):
        return [record for record in records if record.scope == "global"]
    if getattr(args, "project_only", False):
        return [record for record in records if record.scope == "project"]
    return records


def _session_records(config, args, cli_name: str):
    records = _visible_records(config, args)
    return [
        record
        for record in records
        if record.scope == "project" or record.source_cli in {cli_name, "project"}
    ]


def _command_for_cli(config, cli_name: str) -> str:
    real_env_name = f"SKILLCTL_REAL_{cli_name.upper()}_BIN"
    import os

    return os.environ.get(real_env_name, config.cli_commands[cli_name])


def _should_passthrough(provider_args: list[str]) -> bool:
    if not provider_args:
        return False
    if any(arg in {"-h", "--help", "-V", "--version"} for arg in provider_args):
        return True
    return bool(provider_args[0]) and not provider_args[0].startswith("-")


def _run_passthrough(command: str, provider_args: list[str]) -> int:
    completed = subprocess.run([command, *provider_args])
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
