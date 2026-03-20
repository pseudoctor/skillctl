from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re

from .config import AppConfig


SKILL_FILE_CANDIDATES = ("SKILL.md", "CLAUDE.md", "GEMINI.md", "README.md")


@dataclass(frozen=True)
class SkillRecord:
    canonical_name: str
    aliases: tuple[str, ...]
    summary: str
    path: str
    source_cli: str
    scope: str
    hash: str
    char_count: int
    estimated_tokens: int
    last_indexed_at: str

    @property
    def path_obj(self) -> Path:
        return Path(self.path)

    @property
    def cache_key(self) -> str:
        return f"{self.scope}:{self.source_cli}:{self.canonical_name}"


def build_registry(config: AppConfig) -> list[SkillRecord]:
    now = datetime.now(timezone.utc).isoformat()
    records: list[SkillRecord] = []
    for cli_name, root in config.global_skill_roots.items():
        records.extend(_scan_root(root, cli_name, "global", now))
    for root in config.project_skill_roots:
        records.extend(_scan_root(root, "project", "project", now))
    return sorted(records, key=lambda item: (item.canonical_name, item.scope, item.path))


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def save_registry(records: list[SkillRecord], cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "index.json"
    payload = [asdict(item) for item in records]
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def load_registry(cache_dir: Path) -> list[SkillRecord]:
    path = cache_dir / "index.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    records: list[SkillRecord] = []
    dirty = False
    for item in payload:
        upgraded = dict(item)
        record_path = upgraded.get("path")
        if not record_path or not Path(record_path).exists():
            dirty = True
            continue
        if "char_count" not in upgraded or "estimated_tokens" not in upgraded:
            try:
                content = Path(record_path).read_text(encoding="utf-8", errors="ignore")
            except FileNotFoundError:
                dirty = True
                continue
            upgraded.setdefault("char_count", len(content))
            upgraded.setdefault("estimated_tokens", estimate_tokens(content))
            dirty = True
        try:
            records.append(SkillRecord(**upgraded))
        except TypeError:
            dirty = True
    if dirty:
        save_registry(records, cache_dir)
    return records


def _scan_root(root: Path, cli_name: str, scope: str, indexed_at: str) -> list[SkillRecord]:
    if not root.exists() or not root.is_dir():
        return []

    records: list[SkillRecord] = []
    for child in sorted(root.iterdir()):
        skill_file = _resolve_skill_file(child)
        if skill_file is None:
            continue
        content = skill_file.read_text(encoding="utf-8", errors="ignore")
        canonical_name = _extract_name(child, content)
        aliases = _extract_aliases(child.name, canonical_name, content)
        summary = _extract_summary(content)
        digest = sha256(content.encode("utf-8")).hexdigest()
        char_count = len(content)
        estimated_tokens = estimate_tokens(content)
        records.append(
            SkillRecord(
                canonical_name=canonical_name,
                aliases=aliases,
                summary=summary,
                path=str(skill_file.resolve()),
                source_cli=cli_name,
                scope=scope,
                hash=digest,
                char_count=char_count,
                estimated_tokens=estimated_tokens,
                last_indexed_at=indexed_at,
            )
        )
    return records


def _resolve_skill_file(path: Path) -> Path | None:
    if path.is_file() and path.suffix.lower() == ".md":
        return path
    if not path.is_dir():
        return None
    for candidate in SKILL_FILE_CANDIDATES:
        skill_file = path / candidate
        if skill_file.exists():
            return skill_file
    return None


def _extract_name(path: Path, content: str) -> str:
    frontmatter = _parse_frontmatter(content)
    if "name" in frontmatter:
        return _slugify(frontmatter["name"])
    heading_match = re.search(r"(?m)^#\s+(.+?)\s*$", content)
    if heading_match:
        return _slugify(heading_match.group(1))
    return _slugify(path.stem if path.is_file() else path.name)


def _extract_aliases(raw_name: str, canonical_name: str, content: str) -> tuple[str, ...]:
    aliases = {_slugify(raw_name), canonical_name}
    frontmatter = _parse_frontmatter(content)
    if "aliases" in frontmatter:
        for value in frontmatter["aliases"].strip("[]").split(","):
            aliases.add(_slugify(value.strip().strip("'\"")))
    return tuple(sorted(alias for alias in aliases if alias))


def _extract_summary(content: str) -> str:
    frontmatter = _parse_frontmatter(content)
    if "description" in frontmatter:
        return frontmatter["description"].strip()
    for line in content.splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text == "---":
            continue
        return text[:180]
    return ""


def _parse_frontmatter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip().strip("'\"")
    return metadata


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return normalized or "skill"
