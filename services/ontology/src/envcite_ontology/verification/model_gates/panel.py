"""閘門三：跨家族評審團，取多數。"""

from dataclasses import dataclass
from typing import Any

from envcite_ontology.db import Conn
from envcite_ontology.verification import prompts
from envcite_ontology.verification.gates import GateResult, unit_of
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.model_gates.common import eligible, extractor_of
from envcite_ontology.verification.model_gates.render import render_annotation
from envcite_ontology.verification.models import Model


@dataclass(frozen=True)
class PanelGate:
    """不同家族的模型各自判斷標註是否忠於原文，取多數；與抽取者同家族的評審不採計。

    有效票不足兩票、或贊成與反對都未達 `quorum`，為無法判定。評審一致不等於正確，只是多個方法之一。
    """

    judges: tuple[Model, ...]
    quorum: float = 2 / 3
    allow_same_family: bool = False
    min_families: int = 2
    method: str = "panel"

    @property
    def verifier(self) -> str:
        """記入紀錄的驗證者識別，含全體評審的名稱。"""
        return "gate:panel@1:" + "+".join(j.name for j in self.judges)

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        """贊成票比率達 `quorum` 為 pass，反對票比率達 `quorum` 為 fail，其餘為 inconclusive。

        合格評審的家族少於 `min_families`，或有效票少於兩票，也是 inconclusive。任一評審丟
        `ModelError` 時不在此接住，整個閘門交由呼叫端記為 inconclusive。
        """
        ok, skipped = eligible(
            self.judges, extractor_of(conn, statement_id), self.allow_same_family
        )
        if len({m.family for m in ok}) < self.min_families:
            return GateResult(
                "inconclusive",
                {"reason": "fewer than the required model families", "skipped": skipped},
            )
        u = unit_of(conn, statement_id)
        prompt = prompts.judge(
            context=u.context,
            text=u.text,
            quote=u.quoted,
            description=render_annotation(conn, statement_id, for_reader=True),
        )
        votes: list[dict[str, Any]] = []
        for judge in ok:
            answer = object_from(judge.complete(prompt))
            if answer is not None and isinstance(answer.get("faithful"), bool):
                votes.append(
                    {
                        "judge": judge.name,
                        "faithful": answer["faithful"],
                        "issues": answer.get("issues", []),
                    }
                )
        if len(votes) < 2:
            return GateResult(
                "inconclusive", {"reason": "fewer than two valid votes", "votes": votes}
            )
        yes = sum(v["faithful"] for v in votes)
        share = yes / len(votes)
        evidence = {"votes": votes, "skipped": skipped}
        if share >= self.quorum:
            return GateResult("pass", evidence)
        if (1 - share) >= self.quorum:
            return GateResult("fail", evidence)
        return GateResult("inconclusive", evidence)
