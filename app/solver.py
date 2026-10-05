"""Peer-review assignment solver built on NetworkX flow algorithms.

Rules implemented here:

* The match score of a (manuscript, reviewer) pair is the sum of the
  reviewer's scores over the manuscript's topics.
* A pair is usable only when the reviewer is not in conflict with the
  manuscript and the pair score is strictly positive.
* A reviewer may be assigned to the same manuscript at most once and may
  review at most ``capacity`` manuscripts in total.
* A complete assignment first minimises the maximum reviewer load ratio
  (assigned / capacity), then maximises the total match score, and breaks
  remaining ties by choosing the lexicographically smallest sorted list of
  ``(manuscript_id, reviewer_id)`` pairs.
* When no complete assignment exists, a Hall-style capacity-gap proof is
  produced: the subset of manuscripts (fewest first, then lexicographic by
  id) whose demand exceeds the effective capacity of its usable reviewers.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from math import lcm

import networkx as nx

_SOURCE = ("__source__",)
_SINK = ("__sink__",)


@dataclass(frozen=True)
class Manuscript:
    id: str
    topics: tuple[str, ...]
    required: int


@dataclass(frozen=True)
class Reviewer:
    id: str
    scores: dict[str, Fraction]
    capacity: int
    conflicts: frozenset[str]


def _m_node(manuscript_id: str) -> tuple[str, str]:
    return ("m", manuscript_id)


def _r_node(reviewer_id: str) -> tuple[str, str]:
    return ("r", reviewer_id)


def _floor(value: Fraction) -> int:
    return value.numerator // value.denominator


def _as_number(value: Fraction):
    """Render an exact fraction as int when integral, else float."""
    if value.denominator == 1:
        return value.numerator
    return float(value)


def allowed_pairs(
    manuscripts: list[Manuscript], reviewers: list[Reviewer]
) -> dict[tuple[str, str], Fraction]:
    """Usable (manuscript_id, reviewer_id) pairs with their match score.

    Conflicted pairs and pairs whose total topic score is 0 are excluded.
    """
    pairs: dict[tuple[str, str], Fraction] = {}
    for manuscript in manuscripts:
        for reviewer in reviewers:
            if manuscript.id in reviewer.conflicts:
                continue
            score = sum(
                (reviewer.scores.get(topic, Fraction(0)) for topic in manuscript.topics),
                Fraction(0),
            )
            if score > 0:
                pairs[(manuscript.id, reviewer.id)] = score
    return pairs


def _build_flow_network(
    manuscripts: list[Manuscript],
    reviewers: list[Reviewer],
    pairs: dict[tuple[str, str], Fraction],
    capacities: dict[str, int],
) -> nx.DiGraph:
    """Bipartite b-matching network: source -> manuscripts -> reviewers -> sink."""
    graph = nx.DiGraph()
    graph.add_nodes_from((_SOURCE, _SINK))
    for manuscript in manuscripts:
        graph.add_edge(_SOURCE, _m_node(manuscript.id), capacity=manuscript.required)
    for manuscript_id, reviewer_id in pairs:
        # Capacity 1: a reviewer may serve the same manuscript at most once.
        graph.add_edge(_m_node(manuscript_id), _r_node(reviewer_id), capacity=1)
    for reviewer in reviewers:
        graph.add_edge(_r_node(reviewer.id), _SINK, capacity=capacities[reviewer.id])
    return graph


def _max_flow_value(
    manuscripts: list[Manuscript],
    reviewers: list[Reviewer],
    pairs: dict[tuple[str, str], Fraction],
    capacities: dict[str, int],
) -> int:
    graph = _build_flow_network(manuscripts, reviewers, pairs, capacities)
    value, _ = nx.maximum_flow(graph, _SOURCE, _SINK)
    return value


def _minimal_max_load_ratio(
    manuscripts: list[Manuscript],
    reviewers: list[Reviewer],
    pairs: dict[tuple[str, str], Fraction],
    demand: int,
) -> Fraction:
    """Smallest t such that capping every reviewer at floor(t * capacity)
    still admits a complete assignment. The caller guarantees the instance
    is feasible at full capacity."""
    candidates: set[Fraction] = {Fraction(0)}
    for reviewer in reviewers:
        if reviewer.capacity <= 0:
            continue
        # No reviewer can take more manuscripts than exist.
        for load in range(1, min(reviewer.capacity, len(manuscripts)) + 1):
            candidates.add(Fraction(load, reviewer.capacity))
    ordered = sorted(candidates)

    def feasible(ratio: Fraction) -> bool:
        caps = {r.id: _floor(ratio * r.capacity) for r in reviewers}
        return _max_flow_value(manuscripts, reviewers, pairs, caps) >= demand

    # ordered[-1] is feasible: it allows every reviewer at least
    # min(capacity, #manuscripts) assignments, which full capacity never exceeds.
    lo, hi = 0, len(ordered) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if feasible(ordered[mid]):
            hi = mid
        else:
            lo = mid + 1
    return ordered[lo]


def _solve_optimal(
    manuscripts: list[Manuscript],
    reviewers: list[Reviewer],
    pairs: dict[tuple[str, str], Fraction],
    demand: int,
    ratio: Fraction,
) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Complete assignment under the load-ratio cap that first maximises the
    total score, then is lexicographically smallest.

    Implemented as a single min-cost flow: each pair edge costs
    ``-(scaled_score * 2^M) - 2^(M-1-rank)`` where ``rank`` is the pair's
    position in lexicographic order. The score term dominates any sum of
    lexicographic weights. Reading the assignment as a bit vector over the
    ordered pairs, the lexicographically smallest sorted pair list has the
    *largest* exponential weight sum (its most significant differing bit is
    1), so minimising the negated sum yields it.
    """
    caps = {r.id: _floor(ratio * r.capacity) for r in reviewers}

    # Scale possibly fractional scores to integers for exact arithmetic.
    denom = 1
    for score in pairs.values():
        denom = lcm(denom, score.denominator)
    int_score = {
        pair: score.numerator * (denom // score.denominator)
        for pair, score in pairs.items()
    }

    ordered_pairs = sorted(pairs)  # lexicographic by (manuscript_id, reviewer_id)
    n_pairs = len(ordered_pairs)
    score_weight = 1 << n_pairs  # dominates the sum of all lexicographic weights

    reviewers_in_pairs = {reviewer_id for _, reviewer_id in ordered_pairs}

    graph = nx.DiGraph()
    graph.add_node(_SOURCE, demand=-demand)
    graph.add_node(_SINK, demand=demand)
    for manuscript in manuscripts:
        graph.add_edge(
            _SOURCE, _m_node(manuscript.id), capacity=manuscript.required, weight=0
        )
    for rank, (manuscript_id, reviewer_id) in enumerate(ordered_pairs):
        lex_weight = 1 << (n_pairs - 1 - rank)
        weight = -(int_score[(manuscript_id, reviewer_id)] * score_weight) - lex_weight
        graph.add_edge(
            _m_node(manuscript_id), _r_node(reviewer_id), capacity=1, weight=weight
        )
    for reviewer in reviewers:
        if reviewer.id in reviewers_in_pairs:
            graph.add_edge(
                _r_node(reviewer.id), _SINK, capacity=caps[reviewer.id], weight=0
            )

    _, flow = nx.network_simplex(graph)

    assignments = [
        (manuscript_id, reviewer_id)
        for manuscript_id, reviewer_id in ordered_pairs
        if flow.get(_m_node(manuscript_id), {}).get(_r_node(reviewer_id), 0)
    ]
    loads = {
        reviewer.id: flow.get(_r_node(reviewer.id), {}).get(_SINK, 0)
        for reviewer in reviewers
    }
    return assignments, loads


def find_capacity_gap_proof(
    manuscripts: list[Manuscript],
    reviewers: list[Reviewer],
    pairs: dict[tuple[str, str], Fraction],
) -> dict:
    """Smallest Hall-style certificate that no complete assignment exists.

    For a subset S of manuscripts, the usable reviewers N(S) are those that
    may serve at least one member of S. Because a reviewer may serve each
    manuscript at most once, its effective capacity towards S is
    ``min(capacity, number of S-manuscripts it may serve)``. When the demand
    of S exceeds the summed effective capacity, no complete assignment can
    exist (max-flow/min-cut). Returns the violating subset with the fewest
    manuscripts, breaking ties by lexicographic manuscript-id order.
    """
    required = {m.id: m.required for m in manuscripts}
    capacity = {r.id: r.capacity for r in reviewers}
    usable: dict[str, set[str]] = {m.id: set() for m in manuscripts}
    for manuscript_id, reviewer_id in pairs:
        usable[manuscript_id].add(reviewer_id)

    ids = sorted(required)
    for size in range(1, len(ids) + 1):
        for combo in combinations(ids, size):
            subset = set(combo)
            demand = sum(required[m_id] for m_id in subset)
            neighborhood: set[str] = set()
            for m_id in subset:
                neighborhood |= usable[m_id]
            cap_sum = 0
            for reviewer_id in neighborhood:
                serveable = sum(1 for m_id in subset if reviewer_id in usable[m_id])
                cap_sum += min(capacity[reviewer_id], serveable)
            if demand > cap_sum:
                return {
                    "manuscripts": list(combo),
                    "reviewers": sorted(neighborhood),
                    "demand": demand,
                    "capacity": cap_sum,
                    "gap": demand - cap_sum,
                }
    raise RuntimeError("instance is feasible; no capacity-gap proof exists")


def assign(manuscripts: list[Manuscript], reviewers: list[Reviewer]) -> dict:
    """Solve one assignment request and return the response payload."""
    pairs = allowed_pairs(manuscripts, reviewers)
    demand = sum(m.required for m in manuscripts)

    full_caps = {r.id: r.capacity for r in reviewers}
    if _max_flow_value(manuscripts, reviewers, pairs, full_caps) < demand:
        return {
            "status": "infeasible",
            "proof": find_capacity_gap_proof(manuscripts, reviewers, pairs),
        }

    ratio = _minimal_max_load_ratio(manuscripts, reviewers, pairs, demand)
    assignments, loads = _solve_optimal(manuscripts, reviewers, pairs, demand, ratio)

    total_score = sum((pairs[pair] for pair in assignments), Fraction(0))
    max_load_ratio = max(
        (Fraction(loads[r.id], r.capacity) for r in reviewers if r.capacity > 0),
        default=Fraction(0),
    )
    return {
        "status": "feasible",
        "assignments": [
            {
                "manuscript_id": manuscript_id,
                "reviewer_id": reviewer_id,
                "score": _as_number(pairs[(manuscript_id, reviewer_id)]),
            }
            for manuscript_id, reviewer_id in assignments
        ],
        "total_score": _as_number(total_score),
        "max_load_ratio": float(max_load_ratio),
        "loads": loads,
    }


# ---------------------------------------------------------------------------
# Emergency reassignment after a reviewer withdraws
# ---------------------------------------------------------------------------


def _lex_weights(pairs: dict[tuple[str, str], Fraction]) -> dict[tuple[str, str], int]:
    """Per-pair lexicographic weights as used by :func:`_solve_optimal`."""
    ordered_pairs = sorted(pairs)
    n_pairs = len(ordered_pairs)
    return {
        pair: 1 << (n_pairs - 1 - rank)
        for rank, pair in enumerate(ordered_pairs)
    }


def _min_cost_flow_pairs(
    manuscripts: list[Manuscript],
    reviewer_ids: list[str],
    pairs: dict[tuple[str, str], Fraction],
    capacities: dict[str, int],
    weights: dict[tuple[str, str], int],
    demand: int,
) -> tuple[list[tuple[str, str]], dict[str, int]] | None:
    """Min-cost b-matching. Returns ``None`` when no flow of value ``demand``
    exists. ``pairs`` only contains edges open to the flow; manuscript node
    capacities come from ``manuscripts`` (residual requirements)."""
    graph = nx.DiGraph()
    graph.add_node(_SOURCE, demand=-demand)
    graph.add_node(_SINK, demand=demand)
    for manuscript in manuscripts:
        graph.add_edge(
            _SOURCE, _m_node(manuscript.id), capacity=manuscript.required, weight=0
        )
    for pair in sorted(pairs):
        graph.add_edge(
            _m_node(pair[0]), _r_node(pair[1]), capacity=1, weight=weights.get(pair, 0)
        )
    for reviewer_id in reviewer_ids:
        graph.add_edge(
            _r_node(reviewer_id), _SINK, capacity=capacities[reviewer_id], weight=0
        )
    try:
        _, flow = nx.network_simplex(graph)
    except nx.NetworkXUnfeasible:
        return None

    assignments = [
        pair
        for pair in sorted(pairs)
        if flow.get(_m_node(pair[0]), {}).get(_r_node(pair[1]), 0)
    ]
    if len(assignments) < demand:
        return None
    loads = {
        reviewer_id: flow.get(_r_node(reviewer_id), {}).get(_SINK, 0)
        for reviewer_id in reviewer_ids
    }
    return assignments, loads


def reassign(
    manuscripts: list[Manuscript],
    reviewers: list[Reviewer],
    original: set[tuple[str, str]],
    withdrawn_id: str,
    confirmed: set[tuple[str, str]],
) -> dict:
    """Emergency reassignment after reviewer ``withdrawn_id`` withdraws.

    ``original`` and ``confirmed`` are already validated against the
    manuscript/reviewer data. The withdrawn reviewer's relations are all
    revoked; every other confirmed relation is forced to stay. Among feasible
    complete reassignments the solver:

    1. maximises the number of retained non-withdrawn original relations,
    2. minimises the maximum reviewer load ratio (withdrawn excluded),
    3. maximises the total match score,
    4. breaks ties by lexicographically smallest sorted relation list.
    """
    all_pairs = allowed_pairs(manuscripts, reviewers)

    # Confirmed relations of other reviewers must be kept; confirmed relations
    # of the withdrawn reviewer are simply part of the revoked set.
    forced = {
        pair
        for pair in confirmed
        if pair[1] != withdrawn_id
    }
    revoked = {pair for pair in original if pair[1] == withdrawn_id}

    forced_loads: dict[str, int] = {}
    forced_per_manuscript: dict[str, set[str]] = {}
    for manuscript_id, reviewer_id in forced:
        forced_loads[reviewer_id] = forced_loads.get(reviewer_id, 0) + 1
        forced_per_manuscript.setdefault(manuscript_id, set()).add(reviewer_id)

    active_reviewers = [r for r in reviewers if r.id != withdrawn_id]
    active_ids = [r.id for r in active_reviewers]
    active_capacity = {r.id: r.capacity - forced_loads.get(r.id, 0) for r in active_reviewers}

    # Residual instance: remaining requirements, remaining capacities, edges
    # excluding the withdrawn reviewer, already forced pairs and unusable
    # conflicts/zero-score pairs.
    residual_manuscripts: list[Manuscript] = []
    open_pairs: dict[tuple[str, str], Fraction] = {}
    for manuscript in manuscripts:
        taken = forced_per_manuscript.get(manuscript.id, set())
        residual_required = manuscript.required - len(taken)
        if residual_required < 0:  # defence in depth; input validation forbids it
            raise ValueError(
                f"稿件 {manuscript.id} 的已确认关系超过其所需评审人数"
            )
        residual_manuscripts.append(
            Manuscript(
                id=manuscript.id,
                topics=manuscript.topics,
                required=residual_required,
            )
        )
        for reviewer in active_reviewers:
            if reviewer.id in taken:
                continue
            pair = (manuscript.id, reviewer.id)
            if pair in all_pairs:
                open_pairs[pair] = all_pairs[pair]

    residual_reviewers = [
        Reviewer(
            id=r.id,
            scores=r.scores,
            capacity=r.capacity - forced_loads.get(r.id, 0),
            conflicts=r.conflicts,
        )
        for r in active_reviewers
    ]
    demand = sum(m.required for m in residual_manuscripts)

    # Step 1: feasibility (plain max-flow on the residual instance), then the
    # maximum number of retained original relations among the free edges.
    if (
        _max_flow_value(
            residual_manuscripts,
            residual_reviewers,
            open_pairs,
            active_capacity,
        )
        < demand
    ):
        return {
            "status": "infeasible",
            "proof": find_capacity_gap_proof(
                residual_manuscripts, residual_reviewers, open_pairs
            ),
        }

    retain_weights = {pair: -1 for pair in open_pairs if pair in original}
    first = _min_cost_flow_pairs(
        residual_manuscripts,
        active_ids,
        open_pairs,
        active_capacity,
        retain_weights,
        demand,
    )
    k_var = sum(1 for pair in first[0] if pair in original)

    # Step 2: smallest maximum load ratio attainable while retaining k_var
    # original free relations.
    candidates: set[Fraction] = {Fraction(0)}
    for reviewer in active_reviewers:
        if reviewer.capacity <= 0:
            continue
        base = forced_loads.get(reviewer.id, 0)
        for extra in range(0, min(reviewer.capacity - base, len(manuscripts)) + 1):
            candidates.add(Fraction(base + extra, reviewer.capacity))
    ordered_ratios = sorted(candidates)

    def ratio_feasible(ratio: Fraction) -> bool:
        # Forced relations already fix a load floor per reviewer; the global
        # ratio cap may not sit below it.
        if any(
            forced_loads.get(r.id, 0) > _floor(ratio * r.capacity)
            for r in active_reviewers
        ):
            return False
        caps = {
            r.id: _floor(ratio * r.capacity) - forced_loads.get(r.id, 0)
            for r in active_reviewers
        }
        result = _min_cost_flow_pairs(
            residual_manuscripts,
            active_ids,
            open_pairs,
            caps,
            retain_weights,
            demand,
        )
        if result is None:
            return False
        return sum(1 for pair in result[0] if pair in original) >= k_var

    lo, hi = 0, len(ordered_ratios) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if ratio_feasible(ordered_ratios[mid]):
            hi = mid
        else:
            lo = mid + 1
    ratio = ordered_ratios[lo]
    capped_capacity = {
        r.id: _floor(ratio * r.capacity) - forced_loads.get(r.id, 0)
        for r in active_reviewers
    }

    # Step 3: lexicographically smallest complete reassignment with the most
    # retained relations, the minimal ratio cap and the highest total score.
    denom = 1
    for score in open_pairs.values():
        denom = lcm(denom, score.denominator)
    int_score = {
        pair: score.numerator * (denom // score.denominator)
        for pair, score in open_pairs.items()
    }
    lex_weights = _lex_weights(open_pairs)
    n_pairs = max(len(open_pairs), 1)
    score_weight = 1 << n_pairs
    retain_weight = score_weight * (1 + sum(int_score.values()))
    final_weights = {
        pair: -(retain_weight if pair in original else 0)
        - int_score[pair] * score_weight
        - lex_weights[pair]
        for pair in open_pairs
    }
    free_assignments, _free_loads = _min_cost_flow_pairs(
        residual_manuscripts,
        active_ids,
        open_pairs,
        capped_capacity,
        final_weights,
        demand,
    )

    final_pairs = sorted(forced | set(free_assignments))

    loads = {r.id: 0 for r in reviewers}
    for manuscript_id, reviewer_id in final_pairs:
        loads[reviewer_id] += 1

    total_score = sum((all_pairs[pair] for pair in final_pairs), Fraction(0))
    max_load_ratio = max(
        (Fraction(loads[r.id], r.capacity) for r in active_reviewers if r.capacity > 0),
        default=Fraction(0),
    )
    added = set(final_pairs) - original

    def _out(pair: tuple[str, str]) -> dict:
        return {
            "manuscript_id": pair[0],
            "reviewer_id": pair[1],
            "score": _as_number(all_pairs[pair]),
        }

    return {
        "status": "feasible",
        "assignments": [_out(pair) for pair in final_pairs],
        "revoked_assignments": [_out(pair) for pair in sorted(revoked)],
        "added_assignments": [_out(pair) for pair in sorted(added)],
        "withdrawn_reviewer_id": withdrawn_id,
        "total_score": _as_number(total_score),
        "max_load_ratio": float(max_load_ratio),
        "loads": loads,
    }
