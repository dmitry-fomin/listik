"""Журнал событий стенда роя: диспетчер пишет сюда, тесты читают отсюда."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Journal:
    events: list[dict] = field(default_factory=list)
    discrepancies: list[dict] = field(default_factory=list)
    _seq: int = field(default=0, init=False, repr=False)

    def add(self, event: str, **fields) -> dict:
        self._seq += 1
        record = {"seq": self._seq, "event": event}
        record.update(fields)
        self.events.append(record)
        return record

    def of(self, event: str, **match) -> list[dict]:
        result = []
        for record in self.events:
            if record["event"] != event:
                continue
            if all(record.get(key) == value for key, value in match.items()):
                result.append(record)
        return result

    def last(self, event: str, **match) -> dict | None:
        matches = self.of(event, **match)
        return matches[-1] if matches else None

    def names(self) -> list[str]:
        return [record["event"] for record in self.events]
