"""各版本單元的效力上界：由新往舊，止於較新版本同路徑單元的起點。"""

from dataclasses import dataclass, replace
from datetime import date

from envcite_ontology.ingest.backfill.models import Anchor, Planned, Version
from envcite_ontology.ingest.law import ParsedLaw, ParsedUnit


@dataclass
class _Later:
    starts: dict[str, date | None]
    #: 從未生效、不寫列的路徑：較舊的版本越過它往更新的找
    skipped: set[str]

    @property
    def law_start(self) -> date | None:
        """法規層的起點：各條已知起點的最小值，都未知為 None。"""
        known = [s for s in self.starts.values() if s is not None]
        return min(known) if known else None


def _end(later: list[_Later], path: str) -> date | None:
    for v in later:
        if path in v.skipped:
            continue
        if path in v.starts:
            if v.starts[path] is not None:
                return v.starts[path]
            continue
        if v.law_start is not None:
            return v.law_start
    return None


def effect_intervals(anchor: Anchor, accepted: list[tuple[Version, ParsedLaw]]) -> list[Planned]:
    """由新往舊定每一版各單元的效力上界（`accepted` 是新到舊）。"""
    later = [_Later(dict(s), set()) for s in (anchor.starts, *anchor.after)]
    planned: list[Planned] = []
    until = anchor.version_date
    for version, parsed in accepted:
        units: list[ParsedUnit] = []
        starts: dict[str, date | None] = {}
        skipped: set[str] = set()
        for u in parsed.units:
            if u.starts is None:
                units.append(u)
                starts[u.path] = None
                continue
            end = _end(later, u.path)
            if anchor.repealed_on is not None and (end is None or end > anchor.repealed_on):
                end = anchor.repealed_on
            if end is not None and end <= u.starts:
                skipped.add(u.path)
                continue
            units.append(replace(u, ends=end))
            starts[u.path] = u.starts
        later.insert(0, _Later(starts, skipped))
        assert parsed.version_date is not None
        planned.append(Planned(parsed.version_date, version.title, until, units))
        until = parsed.version_date
    planned.reverse()
    return planned
