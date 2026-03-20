from __future__ import annotations

from collections import defaultdict
import re

from .registry import SkillRecord


class SkillNotFoundError(KeyError):
    pass


class SkillResolver:
    def __init__(self, records: list[SkillRecord]) -> None:
        self._records = records
        self._alias_map: dict[str, list[SkillRecord]] = defaultdict(list)
        for record in records:
            for alias in record.aliases:
                self._alias_map[alias].append(record)

    def resolve(self, name: str) -> SkillRecord:
        key = name.strip().lower()
        candidates = self._alias_map.get(key, [])
        if not candidates:
            raise SkillNotFoundError(name)
        candidates = sorted(candidates, key=_priority_key)
        return candidates[0]

    def list_records(self) -> list[SkillRecord]:
        return list(self._records)

    def suggest(self, text: str, limit: int = 3) -> list[SkillRecord]:
        tokens = _tokenize(text)
        if not tokens:
            return []

        scored: list[tuple[int, SkillRecord]] = []
        for record in self._records:
            haystack_tokens = _tokenize(" ".join((record.canonical_name, " ".join(record.aliases), record.summary)))
            overlap = tokens & haystack_tokens
            if not overlap:
                continue
            score = len(overlap)
            if record.scope == "project":
                score += 1
            scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], _priority_key(item[1])))

        suggestions: list[SkillRecord] = []
        seen: set[str] = set()
        for _, record in scored:
            if record.canonical_name in seen:
                continue
            seen.add(record.canonical_name)
            suggestions.append(record)
            if len(suggestions) >= limit:
                break
        return suggestions


def _priority_key(record: SkillRecord) -> tuple[int, str, str]:
    scope_priority = 0 if record.scope == "project" else 1
    return (scope_priority, record.source_cli, record.path)


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}", text.lower())}
