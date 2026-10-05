"""FastAPI application exposing the peer-review assignment solver."""
from __future__ import annotations

from fractions import Fraction

from fastapi import FastAPI

from .models import (
    AssignRequest,
    AssignResponse,
    FeasibleResponse,
    HealthResponse,
    InfeasibleResponse,
)
from .solver import Manuscript, Reviewer, assign

app = FastAPI(
    title="Peer Review Assignment API",
    version="1.0.0",
    description=(
        "同行评审分派服务。匹配分为评审人对稿件各主题评分之和；冲突或总分为 0 的组合不可用，"
        "同一稿件不会重复使用同一评审人。完整分派依次优化：最小化评审人最大负载率 → "
        "最大化总匹配分 → 按 (稿件 ID, 评审人 ID) 取字典序最小。无解时返回最小容量缺口证明。"
    ),
)


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["meta"],
    summary="健康检查",
)
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/assign",
    response_model=AssignResponse,
    responses={
        200: {
            "description": "求解完成：可行时返回最优分派，否则返回最小容量缺口证明。",
            "model": AssignResponse,
        },
        422: {"description": "请求体不符合约束（数量范围、ID 唯一性、评分非负等）。"},
    },
    summary="求解同行评审分派",
)
def create_assignment(payload: AssignRequest) -> dict:
    manuscripts = [
        Manuscript(
            id=manuscript.id,
            topics=tuple(manuscript.topics),
            required=manuscript.required_reviewers,
        )
        for manuscript in payload.manuscripts
    ]
    reviewers = [
        Reviewer(
            id=reviewer.id,
            scores={topic: Fraction(score) for topic, score in reviewer.scores.items()},
            capacity=reviewer.capacity,
            conflicts=frozenset(reviewer.conflicts),
        )
        for reviewer in payload.reviewers
    ]
    return assign(manuscripts, reviewers)


__all__ = ["app", "FeasibleResponse", "InfeasibleResponse"]
