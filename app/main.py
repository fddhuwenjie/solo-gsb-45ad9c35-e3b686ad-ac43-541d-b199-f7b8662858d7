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
    ReassignRequest,
    ReassignResponse,
)
from .solver import Manuscript, Reviewer, assign, reassign

app = FastAPI(
    title="Peer Review Assignment API",
    version="1.0.0",
    description=(
        "同行评审分派服务。匹配分为评审人对稿件各主题评分之和；冲突或总分为 0 的组合不可用，"
        "同一稿件不会重复使用同一评审人。完整分派依次优化：最小化评审人最大负载率 → "
        "最大化总匹配分 → 按 (稿件 ID, 评审人 ID) 取字典序最小。无解时返回最小容量缺口证明。"
        "评审人退出时可调用 /reassign 应急改派：先最大化保留其余原关系，再沿用上述优化顺序。"
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


def _build_domain(payload) -> tuple[list[Manuscript], list[Reviewer]]:
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
    return manuscripts, reviewers


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
    manuscripts, reviewers = _build_domain(payload)
    return assign(manuscripts, reviewers)


@app.post(
    "/reassign",
    response_model=ReassignResponse,
    responses={
        200: {
            "description": "应急改派完成：可行时返回完整改派及撤销/新增关系，否则返回最小容量缺口证明。",
            "model": ReassignResponse,
        },
        422: {"description": "请求体不符合约束（原分派不完整、违反冲突/容量、确认关系不属于原分派等）。"},
    },
    summary="评审人退出后的应急改派",
)
def create_reassignment(payload: ReassignRequest) -> dict:
    manuscripts, reviewers = _build_domain(payload)
    original = {
        (pair.manuscript_id, pair.reviewer_id)
        for pair in payload.original_assignments
    }
    confirmed = {
        (pair.manuscript_id, pair.reviewer_id)
        for pair in payload.confirmed_assignments
    }
    return reassign(
        manuscripts,
        reviewers,
        original,
        payload.withdrawn_reviewer_id,
        confirmed,
    )


__all__ = ["app", "FeasibleResponse", "InfeasibleResponse"]
