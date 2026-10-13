"""整批驗收（env-law-docs 03-03 §7）。

單一法規的問題只讓該部不寫入、記入問題清單；整批看比例，越線代表來源格式改了、
解析規則跟不上，整批不寫入。上限取自 2026-09-18 全量檔（11,798 部）的實測再放寬。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from env_law_ontology.ingest.law import ParsedLaw

#: 名稱 → (分母說明, 上限)。實測：不可寫入 0.01%、切分退回 0.23%、認不得的事件 1.8%、
#: 待確認 4.0%。
LIMITS = {
    "rejected_laws": ("不可寫入的法規占法規", 0.005),
    "degraded_articles": ("切分退回整條的條占條", 0.01),
    "unknown_events": ("認不得的沿革事件占事件", 0.05),
    "unconfirmed_articles": ("生效日待確認的現行條占現行條", 0.08),
}


@dataclass
class Metrics:
    laws: int = 0
    rejected_laws: int = 0
    articles: int = 0
    live_articles: int = 0
    degraded_articles: int = 0
    events: int = 0
    unknown_events: int = 0
    unconfirmed_articles: int = 0
    by_order_articles: int = 0
    problems: Counter[tuple[str, str]] = field(default_factory=Counter)

    def add(self, law: ParsedLaw) -> None:
        self.laws += 1
        self.rejected_laws += not law.ok
        self.events += len(law.events)
        self.unknown_events += sum(e.kind == "unknown" for e in law.events)
        self.degraded_articles += law.degraded_articles
        for u in law.units:
            if u.type == "article":
                self.articles += 1
                # 已廢止的法規各條皆已失效，生效日待不待確認不影響任何答案。
                if law.abandoned_on is None:
                    self.live_articles += 1
                    self.unconfirmed_articles += u.rule == "unconfirmed"
                self.by_order_articles += u.rule == "by_order"
        self.problems.update((p.level, p.code) for p in law.problems)

    def ratio(self, name: str) -> float:
        denominator = {
            "rejected_laws": self.laws,
            "degraded_articles": self.articles,
            "unknown_events": self.events,
            "unconfirmed_articles": self.live_articles,
        }[name]
        return getattr(self, name) / denominator if denominator else 0.0

    def violations(self) -> list[str]:
        if self.laws == 0:
            return ["沒有任何法規"]
        return [
            f"{LIMITS[n][0]} {self.ratio(n):.2%}，上限 {LIMITS[n][1]:.2%}"
            for n in LIMITS
            if self.ratio(n) > LIMITS[n][1]
        ]

    def lines(self) -> list[str]:
        out = [
            f"法規 {self.laws}，條 {self.articles}，事件 {self.events}",
            *(f"{LIMITS[n][0]} {getattr(self, n)}（{self.ratio(n):.2%}）" for n in LIMITS),
            f"等命令定施行日的條 {self.by_order_articles}",
        ]
        out += [f"{level} {code}: {n}" for (level, code), n in sorted(self.problems.items())]
        return out
