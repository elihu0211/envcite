"""閘門三：跨家族評審團，取多數。"""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from canon.db import Conn
from canon.verification import prompts
from canon.verification.gates.base import GateResult, unit_of
from canon.verification.jsonutil import object_from
from canon.verification.model_gates.common import eligible, extractor_of
from canon.verification.model_gates.render import render_annotation
from canon.verification.models import Model


def _ask_all(judges: Sequence[Model], prompt: str) -> list[str]:
    """評審同時作答，回覆順序與 `judges` 相同，票的順序因此不隨完成先後而變。

    評審之間互不相依，序列呼叫的耗時是各家延遲的總和，同時作答只剩最慢的一家。
    任一評審丟 `ModelError` 時等全部結束，再丟排在最前面的那個：其餘評審的執行緒不會被遺棄，
    丟出的例外也不取決於誰先失敗。
    """
    if len(judges) < 2:
        return [j.complete(prompt) for j in judges]
    with ThreadPoolExecutor(len(judges)) as pool:
        pending = [pool.submit(j.complete, prompt) for j in judges]
    return [f.result() for f in pending]


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
        for judge, reply in zip(ok, _ask_all(ok, prompt), strict=True):
            answer = object_from(reply)
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
