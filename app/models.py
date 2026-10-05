"""Request/response schemas for the peer-review assignment API."""
from __future__ import annotations

import math
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ManuscriptIn(BaseModel):
    id: str = Field(min_length=1, description="稿件 ID")
    topics: list[str] = Field(min_length=1, description="稿件主题列表（至少一个，且不重复）")
    required_reviewers: int = Field(ge=1, description="所需评审人数")

    @field_validator("topics")
    @classmethod
    def _topics_ok(cls, value: list[str]) -> list[str]:
        if any(not topic.strip() for topic in value):
            raise ValueError("topics 不能包含空白字符串")
        if len(set(value)) != len(value):
            raise ValueError("topics 不能重复")
        return value


class ReviewerIn(BaseModel):
    id: str = Field(min_length=1, description="评审人 ID")
    scores: dict[str, float] = Field(
        default_factory=dict, description="各主题评分（有限非负数，缺省主题按 0 计）"
    )
    capacity: int = Field(ge=0, description="最多可评审的稿件数")
    conflicts: list[str] = Field(
        default_factory=list, description="存在利益冲突、不可指派的稿件 ID 列表"
    )

    @field_validator("scores")
    @classmethod
    def _scores_ok(cls, value: dict[str, float]) -> dict[str, float]:
        for topic, score in value.items():
            if not topic.strip():
                raise ValueError("评分的主题名不能为空白字符串")
            if not math.isfinite(score) or score < 0:
                raise ValueError("评分必须为有限非负数")
        return value

    @field_validator("conflicts")
    @classmethod
    def _conflicts_ok(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("conflicts 不能重复")
        return value


class AssignRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "manuscripts": [
                        {"id": "m1", "topics": ["nlp", "ml"], "required_reviewers": 2},
                        {"id": "m2", "topics": ["vision"], "required_reviewers": 1},
                    ],
                    "reviewers": [
                        {
                            "id": "r1",
                            "scores": {"nlp": 5, "ml": 4},
                            "capacity": 2,
                            "conflicts": ["m2"],
                        },
                        {
                            "id": "r2",
                            "scores": {"nlp": 3, "vision": 5},
                            "capacity": 1,
                            "conflicts": [],
                        },
                        {
                            "id": "r3",
                            "scores": {"ml": 4, "vision": 2},
                            "capacity": 1,
                            "conflicts": [],
                        },
                    ],
                }
            ]
        }
    )

    manuscripts: list[ManuscriptIn] = Field(
        min_length=1, max_length=10, description="稿件列表（1～10 篇）"
    )
    reviewers: list[ReviewerIn] = Field(
        min_length=1, max_length=20, description="评审人列表（1～20 名）"
    )

    @model_validator(mode="after")
    def _cross_check(self) -> "AssignRequest":
        manuscript_ids = [m.id for m in self.manuscripts]
        if len(set(manuscript_ids)) != len(manuscript_ids):
            raise ValueError("稿件 ID 必须唯一")
        reviewer_ids = [r.id for r in self.reviewers]
        if len(set(reviewer_ids)) != len(reviewer_ids):
            raise ValueError("评审人 ID 必须唯一")
        known = set(manuscript_ids)
        for reviewer in self.reviewers:
            unknown = sorted(set(reviewer.conflicts) - known)
            if unknown:
                raise ValueError(
                    f"评审人 {reviewer.id} 的 conflicts 包含未知稿件: {unknown}"
                )
        return self


class AssignmentOut(BaseModel):
    manuscript_id: str = Field(description="稿件 ID")
    reviewer_id: str = Field(description="评审人 ID")
    score: float = Field(description="该 (稿件, 评审人) 组合的匹配分")


class FeasibleResponse(BaseModel):
    status: Literal["feasible"] = "feasible"
    assignments: list[AssignmentOut] = Field(
        description="分派结果，按 (稿件 ID, 评审人 ID) 字典序排列"
    )
    total_score: float = Field(description="总匹配分")
    max_load_ratio: float = Field(description="评审人最大负载率（已最小化）")
    loads: dict[str, int] = Field(description="每名评审人被指派的稿件数")


class CapacityGapProof(BaseModel):
    manuscripts: list[str] = Field(description="需求无法被满足的稿件子集（按 ID 排序）")
    reviewers: list[str] = Field(description="该子集的全部可用评审人（按 ID 排序）")
    demand: int = Field(description="子集的评审需求总数")
    capacity: int = Field(
        description="可用评审人对着子集的有效容量和：每人取 min(容量, 子集中其可评审稿件数) 后求和"
    )
    gap: int = Field(description="容量缺口，即 demand - capacity")


class InfeasibleResponse(BaseModel):
    status: Literal["infeasible"] = "infeasible"
    proof: CapacityGapProof = Field(description="最小容量缺口证明")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


AssignResponse = Union[FeasibleResponse, InfeasibleResponse]
