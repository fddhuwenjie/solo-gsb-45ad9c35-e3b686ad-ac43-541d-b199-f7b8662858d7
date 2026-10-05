"""Request/response schemas for the peer-review assignment API."""
from __future__ import annotations

import math
from fractions import Fraction
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


class PairIn(BaseModel):
    """原分派（或已确认分派）中的一条 (稿件, 评审人) 关系。"""

    manuscript_id: str = Field(description="稿件 ID")
    reviewer_id: str = Field(description="评审人 ID")


class ReassignRequest(BaseModel):
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
                    "original_assignments": [
                        {"manuscript_id": "m1", "reviewer_id": "r1"},
                        {"manuscript_id": "m1", "reviewer_id": "r3"},
                        {"manuscript_id": "m2", "reviewer_id": "r2"},
                    ],
                    "withdrawn_reviewer_id": "r2",
                    "confirmed_assignments": [
                        {"manuscript_id": "m1", "reviewer_id": "r1"}
                    ],
                }
            ]
        }
    )

    manuscripts: list[ManuscriptIn] = Field(
        min_length=1, max_length=10, description="稿件列表（1～10 篇），与 POST /assign 相同"
    )
    reviewers: list[ReviewerIn] = Field(
        min_length=1, max_length=20, description="评审人列表（1～20 名），含退出者"
    )
    original_assignments: list[PairIn] = Field(
        min_length=1, description="原完整分派的全部 (稿件, 评审人) 关系"
    )
    withdrawn_reviewer_id: str = Field(
        min_length=1, description="退出的评审人 ID（其全部关系将被撤销）"
    )
    confirmed_assignments: list[PairIn] = Field(
        default_factory=list,
        description="原分派中已经确认、必须保留的关系（退出者关系除外）",
    )

    @model_validator(mode="after")
    def _cross_check(self) -> "ReassignRequest":
        manuscript_ids = [m.id for m in self.manuscripts]
        if len(set(manuscript_ids)) != len(manuscript_ids):
            raise ValueError("稿件 ID 必须唯一")
        reviewer_ids = [r.id for r in self.reviewers]
        if len(set(reviewer_ids)) != len(reviewer_ids):
            raise ValueError("评审人 ID 必须唯一")
        known_m = set(manuscript_ids)
        reviewers_by_id = {r.id: r for r in self.reviewers}
        for reviewer in self.reviewers:
            unknown = sorted(set(reviewer.conflicts) - known_m)
            if unknown:
                raise ValueError(
                    f"评审人 {reviewer.id} 的 conflicts 包含未知稿件: {unknown}"
                )

        if self.withdrawn_reviewer_id not in reviewers_by_id:
            raise ValueError(
                f"withdrawn_reviewer_id 必须是已知评审人: {self.withdrawn_reviewer_id!r}"
            )

        original_pairs = [
            (pair.manuscript_id, pair.reviewer_id)
            for pair in self.original_assignments
        ]
        if len(set(original_pairs)) != len(original_pairs):
            raise ValueError("original_assignments 中的 (稿件, 评审人) 关系不能重复")

        required = {m.id: m.required_reviewers for m in self.manuscripts}
        topics = {m.id: m.topics for m in self.manuscripts}
        assigned_counts = {m_id: 0 for m_id in known_m}
        assigned_loads = {r_id: 0 for r_id in reviewers_by_id}
        for manuscript_id, reviewer_id in original_pairs:
            if manuscript_id not in known_m:
                raise ValueError(
                    f"original_assignments 引用了未知稿件: {manuscript_id!r}"
                )
            if reviewer_id not in reviewers_by_id:
                raise ValueError(
                    f"original_assignments 引用了未知评审人: {reviewer_id!r}"
                )
            reviewer = reviewers_by_id[reviewer_id]
            if manuscript_id in reviewer.conflicts:
                raise ValueError(
                    f"原分派关系 ({manuscript_id}, {reviewer_id}) 违反冲突约束"
                )
            score = sum(
                (
                    Fraction(str(reviewer.scores.get(topic, 0)))
                    for topic in topics[manuscript_id]
                ),
                Fraction(0),
            )
            if score <= 0:
                raise ValueError(
                    f"原分派关系 ({manuscript_id}, {reviewer_id}) 的匹配分为 0，不可分派"
                )
            assigned_counts[manuscript_id] += 1
            assigned_loads[reviewer_id] += 1
        for manuscript_id, count in assigned_counts.items():
            if count != required[manuscript_id]:
                raise ValueError(
                    f"原分派不完整：稿件 {manuscript_id} 需要 {required[manuscript_id]} 名评审人，"
                    f"实际 {count} 条关系"
                )
        for reviewer_id, load in assigned_loads.items():
            capacity = reviewers_by_id[reviewer_id].capacity
            if load > capacity:
                raise ValueError(
                    f"原分派违反容量约束：评审人 {reviewer_id} 被指派 {load} 篇，"
                    f"容量仅 {capacity}"
                )

        confirmed_pairs = [
            (pair.manuscript_id, pair.reviewer_id)
            for pair in self.confirmed_assignments
        ]
        if len(set(confirmed_pairs)) != len(confirmed_pairs):
            raise ValueError("confirmed_assignments 中的 (稿件, 评审人) 关系不能重复")
        original_set = set(original_pairs)
        for manuscript_id, reviewer_id in confirmed_pairs:
            if (manuscript_id, reviewer_id) not in original_set:
                raise ValueError(
                    f"confirmed_assignments 中的关系 ({manuscript_id}, {reviewer_id}) "
                    "不属于原分派"
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


class ReassignFeasibleResponse(FeasibleResponse):
    revoked_assignments: list[AssignmentOut] = Field(
        description="被撤销的原分派关系（退出者的全部关系，按字典序排列）"
    )
    added_assignments: list[AssignmentOut] = Field(
        description="新增的分派关系（完整分派中不属于原分派者，按字典序排列）"
    )
    withdrawn_reviewer_id: str = Field(description="退出的评审人 ID")


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
ReassignResponse = Union[ReassignFeasibleResponse, InfeasibleResponse]
