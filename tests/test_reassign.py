"""End-to-end tests for the emergency /reassign endpoint."""
from __future__ import annotations

import itertools
import random
from fractions import Fraction

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers / reference solver
# ---------------------------------------------------------------------------

def post_reassign(payload):
    return client.post("/reassign", json=payload)


def _allowed(payload):
    allowed = {}
    for manuscript in payload["manuscripts"]:
        for reviewer in payload["reviewers"]:
            if manuscript["id"] in reviewer.get("conflicts", []):
                continue
            score = sum(
                Fraction(str(reviewer.get("scores", {}).get(topic, 0)))
                for topic in manuscript["topics"]
            )
            if score > 0:
                allowed[(manuscript["id"], reviewer["id"])] = score
    return allowed


def brute_force_reassign(payload):
    """Exhaustive reference solver.

    Returns None when infeasible, else (max_ratio, total, retained, pairs).
    Lexicographic minimisation of pairs is applied last, per the spec.
    """
    manuscripts = payload["manuscripts"]
    reviewers = payload["reviewers"]
    allowed = _allowed(payload)

    original = {
        (p["manuscript_id"], p["reviewer_id"])
        for p in payload["original_assignments"]
    }
    withdrawn = payload["withdrawn_reviewer_id"]
    confirmed = {
        (p["manuscript_id"], p["reviewer_id"])
        for p in payload["confirmed_assignments"]
    }
    forced = {pair for pair in confirmed if pair[1] != withdrawn}

    per_manuscript = []
    for manuscript in manuscripts:
        fixed = sorted(p for p in forced if p[0] == manuscript["id"])
        free_candidates = sorted(
            rid
            for (mid, rid) in allowed
            if mid == manuscript["id"]
            and rid != withdrawn
            and (mid, rid) not in forced
        )
        n_free = manuscript["required_reviewers"] - len(fixed)
        if n_free < 0 or len(free_candidates) < n_free:
            return None
        options = [
            fixed + [(manuscript["id"], rid) for rid in combo]
            for combo in itertools.combinations(free_candidates, n_free)
        ]
        per_manuscript.append(options)

    capacities = {r["id"]: r["capacity"] for r in reviewers}
    best = None
    choice_lists = per_manuscript

    for choice in itertools.product(*choice_lists):
        pairs = [pair for group in choice for pair in group]
        loads = {rid: 0 for rid in capacities}
        for _, rid in pairs:
            loads[rid] += 1
        if any(loads[rid] > capacities[rid] for rid in loads):
            continue
        max_ratio = max(
            (
                Fraction(loads[rid], capacities[rid])
                for rid in capacities
                if rid != withdrawn and capacities[rid] > 0
            ),
            default=Fraction(0),
        )
        total = sum(allowed[pair] for pair in pairs)
        kept = frozenset(pairs) & (original - {p for p in original if p[1] == withdrawn})
        ordered = sorted(pairs)
        key = (-len(kept), max_ratio, -total, ordered)
        if best is None or key < best[0]:
            best = (key, max_ratio, total, len(kept), ordered)
    if best is None:
        return None
    _, max_ratio, total, retained, pairs = best
    return max_ratio, total, retained, pairs


def residual_gap_proof(payload):
    """Independent minimal capacity-gap proof on the residual instance.

    Emulates the residual graph seen by the solver after removing the withdrawn
    reviewer and all forced edges: residual requirements, residual capacities,
    forced (manuscript, reviewer) pairs removed by zeroing that pair's score.
    """
    withdrawn = payload["withdrawn_reviewer_id"]
    confirmed = {
        (p["manuscript_id"], p["reviewer_id"])
        for p in payload["confirmed_assignments"]
        if p["reviewer_id"] != withdrawn
    }
    residual = {"manuscripts": [], "reviewers": []}
    forced_by_m: dict[str, set[str]] = {}
    forced_loads: dict[str, int] = {}
    for mid, rid in confirmed:
        forced_by_m.setdefault(mid, set()).add(rid)
        forced_loads[rid] = forced_loads.get(rid, 0) + 1
    for manuscript in payload["manuscripts"]:
        taken = forced_by_m.get(manuscript["id"], set())
        residual["manuscripts"].append(
            {
                "id": manuscript["id"],
                "topics": list(manuscript["topics"]),
                "required_reviewers": manuscript["required_reviewers"] - len(taken),
            }
        )
    for reviewer in payload["reviewers"]:
        if reviewer["id"] == withdrawn:
            continue
        residual["reviewers"].append(
            {
                "id": reviewer["id"],
                "scores": dict(reviewer.get("scores", {})),
                "capacity": reviewer["capacity"]
                - forced_loads.get(reviewer["id"], 0),
                "conflicts": list(reviewer.get("conflicts", [])),
            }
        )

    manuscripts = residual["manuscripts"]
    reviewers = {r["id"]: r for r in residual["reviewers"]}
    usable = {m["id"]: set() for m in manuscripts}
    for manuscript in manuscripts:
        for reviewer in residual["reviewers"]:
            if manuscript["id"] in reviewer.get("conflicts", []):
                continue
            # Forced pairs are removed from the residual graph entirely.
            if (manuscript["id"], reviewer["id"]) in confirmed:
                continue
            if sum(
                reviewer.get("scores", {}).get(topic, 0)
                for topic in manuscript["topics"]
            ) > 0:
                usable[manuscript["id"]].add(reviewer["id"])
    ids = sorted(m["id"] for m in manuscripts)
    for size in range(1, len(ids) + 1):
        for combo in itertools.combinations(ids, size):
            subset = set(combo)
            demand = sum(
                m["required_reviewers"] for m in manuscripts if m["id"] in subset
            )
            neighborhood = set().union(*(usable[m_id] for m_id in subset))
            cap_sum = 0
            for reviewer_id in neighborhood:
                serveable = sum(
                    1 for m_id in subset if reviewer_id in usable[m_id]
                )
                cap_sum += min(reviewers[reviewer_id]["capacity"], serveable)
            if demand > cap_sum:
                return {
                    "manuscripts": list(combo),
                    "reviewers": sorted(neighborhood),
                    "demand": demand,
                    "capacity": cap_sum,
                    "gap": demand - cap_sum,
                }
    return None


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

def test_reassign_documented_in_openapi():
    spec = client.get("/openapi.json").json()
    assert "/reassign" in spec["paths"]


# ---------------------------------------------------------------------------
# Hand-computed scenarios
# ---------------------------------------------------------------------------

def _base_payload():
    return {
        "manuscripts": [
            {"id": "m1", "topics": ["nlp", "ml"], "required_reviewers": 2},
            {"id": "m2", "topics": ["vision"], "required_reviewers": 1},
        ],
        "reviewers": [
            {"id": "r1", "scores": {"nlp": 5, "ml": 4}, "capacity": 2, "conflicts": ["m2"]},
            {"id": "r2", "scores": {"nlp": 3, "vision": 5}, "capacity": 1, "conflicts": []},
            {"id": "r3", "scores": {"ml": 4, "vision": 2}, "capacity": 2, "conflicts": []},
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


def test_basic_reassignment_revokes_and_reports_deltas():
    body = post_reassign(_base_payload()).json()
    assert body["status"] == "feasible"
    assert body["withdrawn_reviewer_id"] == "r2"
    pairs = [(a["manuscript_id"], a["reviewer_id"]) for a in body["assignments"]]
    assert pairs == [("m1", "r1"), ("m1", "r3"), ("m2", "r3")]
    # The other confirmed relation is preserved.
    assert ("m1", "r1") in pairs
    # All withdrawn relations revoked.
    assert all(rid != "r2" for _, rid in pairs)
    assert [(a["manuscript_id"], a["reviewer_id"]) for a in body["revoked_assignments"]] == [
        ("m2", "r2")
    ]
    assert [(a["manuscript_id"], a["reviewer_id"]) for a in body["added_assignments"]] == [
        ("m2", "r3")
    ]
    assert body["loads"] == {"r1": 1, "r2": 0, "r3": 2}
    assert body["total_score"] == 15
    assert body["max_load_ratio"] == 1.0
    # deltas reconcile with the complete assignment
    assert {(a["manuscript_id"], a["reviewer_id"]) for a in body["revoked_assignments"]} | (
        set(pairs)
        - {(a["manuscript_id"], a["reviewer_id"]) for a in body["added_assignments"]}
    ) == {
        ("m1", "r1"),
        ("m1", "r3"),
        ("m2", "r2"),
    }


def test_infeasible_when_no_replacement_returns_proof_no_partial():
    payload = _base_payload()
    payload["reviewers"][2]["capacity"] = 1  # r3 cannot cover both m1 and m2
    body = post_reassign(payload).json()
    assert body["status"] == "infeasible"
    assert "assignments" not in body
    assert body["proof"]["manuscripts"] == ["m1", "m2"]
    assert body["proof"]["reviewers"] == ["r3"]
    assert body["proof"]["gap"] == 1


def test_retain_original_relations_outranks_load_ratio():
    # Without withdrawal: one manuscript each. Withdrawing r1 frees m1.
    # Keeping the original (m2, r2) forces load ratio 1.0; dropping it lets
    # r2 take m1 at ratio 0.5 while r3 covers m2. Retention must win.
    payload = {
        "manuscripts": [
            {"id": "m1", "topics": ["t"], "required_reviewers": 1},
            {"id": "m2", "topics": ["t"], "required_reviewers": 1},
        ],
        "reviewers": [
            {"id": "r1", "scores": {"t": 5}, "capacity": 1, "conflicts": []},
            {"id": "r2", "scores": {"t": 5}, "capacity": 2, "conflicts": []},
            {"id": "r3", "scores": {"t": 5}, "capacity": 1, "conflicts": []},
        ],
        "original_assignments": [
            {"manuscript_id": "m1", "reviewer_id": "r1"},
            {"manuscript_id": "m2", "reviewer_id": "r2"},
        ],
        "withdrawn_reviewer_id": "r1",
        "confirmed_assignments": [],
    }
    body = post_reassign(payload).json()
    pairs = [(a["manuscript_id"], a["reviewer_id"]) for a in body["assignments"]]
    assert pairs == [("m1", "r2"), ("m2", "r2")]
    assert body["max_load_ratio"] == 1.0


def test_load_ratio_outranks_total_score():
    # r1 withdraws from m1. Completing with m1->r2 spreads the load
    # (ratio 0.5, score 15); piling both manuscripts on r3 reaches ratio 1.0
    # with a higher score of 17. Retention counts are equal (the original
    # (m2, r3) survives in both), so the ratio objective must win.
    payload = {
        "manuscripts": [
            {"id": "m1", "topics": ["t"], "required_reviewers": 1},
            {"id": "m2", "topics": ["t"], "required_reviewers": 1},
        ],
        "reviewers": [
            {"id": "r1", "scores": {"t": 10}, "capacity": 1, "conflicts": []},
            {"id": "r2", "scores": {"t": 6}, "capacity": 2, "conflicts": []},
            {"id": "r3", "scores": {"t": 9}, "capacity": 2, "conflicts": []},
        ],
        "original_assignments": [
            {"manuscript_id": "m1", "reviewer_id": "r1"},
            {"manuscript_id": "m2", "reviewer_id": "r3"},
        ],
        "withdrawn_reviewer_id": "r1",
        "confirmed_assignments": [],
    }
    body = post_reassign(payload).json()
    pairs = [(a["manuscript_id"], a["reviewer_id"]) for a in body["assignments"]]
    assert pairs == [("m1", "r2"), ("m2", "r3")]
    assert body["max_load_ratio"] == 0.5
    assert body["total_score"] == 15


def test_score_maximised_within_higher_priorities():
    # r1 withdrawn from m1. r2,r3 both free; r2 scores 9, r3 scores 1 for m1;
    # original (m2, r2) exists. Keeping it forces m1->r3 (score 1, retain 1).
    # Dropping it: m1->r2 (score 9), m2->r3 (score 8), retain 0. Retention
    # dominates, so to test score alone both free reviewers must yield equal
    # retention counts:
    payload = {
        "manuscripts": [
            {"id": "m1", "topics": ["t"], "required_reviewers": 1},
        ],
        "reviewers": [
            {"id": "r1", "scores": {"t": 10}, "capacity": 1, "conflicts": []},
            {"id": "r2", "scores": {"t": 9}, "capacity": 1, "conflicts": []},
            {"id": "r3", "scores": {"t": 1}, "capacity": 1, "conflicts": []},
        ],
        "original_assignments": [
            {"manuscript_id": "m1", "reviewer_id": "r1"},
        ],
        "withdrawn_reviewer_id": "r1",
        "confirmed_assignments": [],
    }
    body = post_reassign(payload).json()
    assert [(a["manuscript_id"], a["reviewer_id"]) for a in body["assignments"]] == [
        ("m1", "r2")
    ]
    assert body["total_score"] == 9


def test_lexicographic_tie_break():
    payload = {
        "manuscripts": [
            {"id": "m1", "topics": ["t"], "required_reviewers": 1},
        ],
        "reviewers": [
            {"id": "r1", "scores": {"t": 5}, "capacity": 1, "conflicts": []},
            {"id": "r2", "scores": {"t": 5}, "capacity": 1, "conflicts": []},
            {"id": "r3", "scores": {"t": 5}, "capacity": 1, "conflicts": []},
        ],
        "original_assignments": [
            {"manuscript_id": "m1", "reviewer_id": "r3"},
        ],
        "withdrawn_reviewer_id": "r3",
        "confirmed_assignments": [],
    }
    body = post_reassign(payload).json()
    assert [(a["manuscript_id"], a["reviewer_id"]) for a in body["assignments"]] == [
        ("m1", "r1")
    ]


def test_confirmed_non_withdrawn_relations_are_forced():
    # Force (m1, r3): m1 still needs r1; m2 freed from r2 must go to ... only
    # r1 is conflicted with m2, r3 full (capacity 1) => infeasible.
    payload = _base_payload()
    payload["reviewers"][2]["capacity"] = 1
    payload["confirmed_assignments"] = [
        {"manuscript_id": "m1", "reviewer_id": "r3"}
    ]
    body = post_reassign(payload).json()
    assert body["status"] == "infeasible"
    assert "assignments" not in body


def test_confirmed_load_floor_counts_into_max_ratio():
    # Regression: forced relations already put r4/r5 at load ratio 1.0, so
    # replacing withdrawn r1 on m3 must not be optimised against a lower cap.
    # Among ratio-1.0 completions the higher-scoring (m3, r5) must win.
    payload = {
        "manuscripts": [
            {"id": "m1", "topics": ["t1", "t2"], "required_reviewers": 2},
            {"id": "m2", "topics": ["t2", "t1"], "required_reviewers": 2},
            {"id": "m3", "topics": ["t2", "t1"], "required_reviewers": 1},
        ],
        "reviewers": [
            {"id": "r1", "scores": {"t1": 1, "t2": 2}, "capacity": 2, "conflicts": ["m1", "m2"]},
            {"id": "r3", "scores": {"t2": 1}, "capacity": 3, "conflicts": []},
            {"id": "r4", "scores": {"t1": 1}, "capacity": 2, "conflicts": []},
            {"id": "r5", "scores": {"t1": 2, "t2": 3}, "capacity": 3, "conflicts": []},
        ],
        "original_assignments": [
            {"manuscript_id": "m1", "reviewer_id": "r4"},
            {"manuscript_id": "m1", "reviewer_id": "r5"},
            {"manuscript_id": "m2", "reviewer_id": "r4"},
            {"manuscript_id": "m2", "reviewer_id": "r5"},
            {"manuscript_id": "m3", "reviewer_id": "r1"},
        ],
        "withdrawn_reviewer_id": "r1",
        "confirmed_assignments": [
            {"manuscript_id": "m1", "reviewer_id": "r4"},
            {"manuscript_id": "m1", "reviewer_id": "r5"},
            {"manuscript_id": "m2", "reviewer_id": "r4"},
            {"manuscript_id": "m2", "reviewer_id": "r5"},
        ],
    }
    body = post_reassign(payload).json()
    assert body["status"] == "feasible"
    assert body["max_load_ratio"] == 1.0
    assert [(a["manuscript_id"], a["reviewer_id"]) for a in body["assignments"]
            if a["manuscript_id"] == "m3"] == [("m3", "r5")]
    assert body["total_score"] == 17


def test_confirmed_relation_of_withdrawn_is_simply_revoked():
    payload = _base_payload()
    payload["confirmed_assignments"] = [
        {"manuscript_id": "m2", "reviewer_id": "r2"},
    ]
    body = post_reassign(payload).json()
    assert body["status"] == "feasible"
    assert [(a["manuscript_id"], a["reviewer_id"]) for a in body["revoked_assignments"]] == [
        ("m2", "r2")
    ]
    assert all(a["reviewer_id"] != "r2" for a in body["assignments"])


def test_no_remaining_candidate_returns_infeasible_not_500():
    # The withdrawn reviewer was the sole usable reviewer; the residual graph
    # has no manuscript->reviewer edges at all.
    payload = {
        "manuscripts": [{"id": "m1", "topics": ["t"], "required_reviewers": 1}],
        "reviewers": [
            {"id": "r1", "scores": {"t": 5}, "capacity": 1, "conflicts": []},
        ],
        "original_assignments": [{"manuscript_id": "m1", "reviewer_id": "r1"}],
        "withdrawn_reviewer_id": "r1",
        "confirmed_assignments": [{"manuscript_id": "m1", "reviewer_id": "r1"}],
    }
    response = post_reassign(payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "infeasible"
    assert body["proof"] == {
        "manuscripts": ["m1"],
        "reviewers": [],
        "demand": 1,
        "capacity": 0,
        "gap": 1,
    }


def test_withdrawn_with_no_assignments_is_valid():
    payload = {
        "manuscripts": [{"id": "m1", "topics": ["t"], "required_reviewers": 1}],
        "reviewers": [
            {"id": "r1", "scores": {"t": 5}, "capacity": 1, "conflicts": []},
            {"id": "r2", "scores": {"t": 1}, "capacity": 1, "conflicts": []},
        ],
        "original_assignments": [{"manuscript_id": "m1", "reviewer_id": "r1"}],
        "withdrawn_reviewer_id": "r2",
        "confirmed_assignments": [],
    }
    body = post_reassign(payload).json()
    assert body["status"] == "feasible"
    assert [(a["manuscript_id"], a["reviewer_id"]) for a in body["assignments"]] == [
        ("m1", "r1")
    ]
    assert body["revoked_assignments"] == []
    assert body["added_assignments"] == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _valid_reassign_payload():
    return _base_payload()


def _assert_422(mutate):
    payload = _valid_reassign_payload()
    mutate(payload)
    response = post_reassign(payload)
    assert response.status_code == 422, response.text


def test_invalid_reassign_payloads_return_422():
    _assert_422(lambda p: p.update(withdrawn_reviewer_id="ghost"))
    _assert_422(
        lambda p: p.update(
            confirmed_assignments=[
                {"manuscript_id": "m2", "reviewer_id": "r3"}
            ]
        )
    )
    _assert_422(
        lambda p: p["original_assignments"].append(
            {"manuscript_id": "m1", "reviewer_id": "r1"}
        )
    )
    _assert_422(
        lambda p: p["confirmed_assignments"].append(
            {"manuscript_id": "m1", "reviewer_id": "r1"}
        )
    )
    _assert_422(
        lambda p: p.update(
            original_assignments=p["original_assignments"][:2]
        )
    )
    _assert_422(
        lambda p: p["original_assignments"].__setitem__(
            2, {"manuscript_id": "m2", "reviewer_id": "r1"}
        )
    )
    _assert_422(lambda p: p["reviewers"][2].__setitem__("capacity", 0))
    _assert_422(
        lambda p: p["original_assignments"].__setitem__(
            0, {"manuscript_id": "ghost", "reviewer_id": "r1"}
        )
    )
    _assert_422(
        lambda p: p["original_assignments"].__setitem__(
            0, {"manuscript_id": "m1", "reviewer_id": "ghost"}
        )
    )
    _assert_422(lambda p: p["manuscripts"].append(
        {"id": "m1", "topics": ["t"], "required_reviewers": 1}
    ))
    _assert_422(lambda p: p.update(original_assignments=[]))
    _assert_422(
        lambda p: p["reviewers"][2]["scores"].__setitem__("ml", 0)
        or p["reviewers"][2]["scores"].__setitem__("vision", 0)
    )


# ---------------------------------------------------------------------------
# Randomised cross-check against the brute-force reference
# ---------------------------------------------------------------------------

def _random_reassign_payload(rng):
    n_manuscripts = rng.randint(1, 3)
    n_reviewers = rng.randint(2, 5)
    topics = ["t1", "t2"]
    manuscripts = [
        {
            "id": f"m{i}",
            "topics": rng.sample(topics, rng.randint(1, len(topics))),
            "required_reviewers": rng.randint(1, 2),
        }
        for i in range(1, n_manuscripts + 1)
    ]
    reviewers = []
    for j in range(1, n_reviewers + 1):
        scores = {
            topic: rng.randint(0, 3)
            for topic in topics
            if rng.random() < 0.75
        }
        conflicts = [m["id"] for m in manuscripts if rng.random() < 0.1]
        reviewers.append(
            {
                "id": f"r{j}",
                "scores": scores,
                "capacity": rng.randint(0, 3),
                "conflicts": conflicts,
            }
        )

    # Build a valid original assignment with a tiny greedy search (random
    # restarts over reviewer order).
    allowed = _allowed({"manuscripts": manuscripts, "reviewers": reviewers})
    original = None
    for _ in range(40):
        order = list(allowed)
        rng.shuffle(order)
        loads = {r["id"]: 0 for r in reviewers}
        candidate = set()
        ok = True
        for manuscript in manuscripts:
            pool = [
                rid
                for mid, rid in order
                if mid == manuscript["id"] and (mid, rid) not in candidate
            ]
            rng.shuffle(pool)
            picked = 0
            for rid in pool:
                if picked >= manuscript["required_reviewers"]:
                    break
                if loads[rid] < next(
                    r["capacity"] for r in reviewers if r["id"] == rid
                ):
                    candidate.add((manuscript["id"], rid))
                    loads[rid] += 1
                    picked += 1
            if picked < manuscript["required_reviewers"]:
                ok = False
                break
        if ok:
            original = candidate
            break
    if original is None:
        return None

    withdrawn = rng.choice(sorted({rid for _, rid in original}
                                  | {r["id"] for r in reviewers}))
    remaining = [pair for pair in original if pair[1] != withdrawn]
    confirmed = [
        {"manuscript_id": mid, "reviewer_id": rid}
        for mid, rid in rng.sample(sorted(remaining), rng.randint(0, len(remaining)))
    ]
    return {
        "manuscripts": manuscripts,
        "reviewers": reviewers,
        "original_assignments": [
            {"manuscript_id": mid, "reviewer_id": rid}
            for mid, rid in sorted(original)
        ],
        "withdrawn_reviewer_id": withdrawn,
        "confirmed_assignments": confirmed,
    }


def test_reassign_matches_brute_force_on_random_instances():
    rng = random.Random(20261005)
    checked = 0
    attempts = 0
    while checked < 150 and attempts < 2000:
        attempts += 1
        payload = _random_reassign_payload(rng)
        if payload is None:
            continue
        checked += 1
        response = post_reassign(payload)
        assert response.status_code == 200, response.text
        body = response.json()
        reference = brute_force_reassign(payload)
        if reference is None:
            assert body["status"] == "infeasible", payload
            assert "assignments" not in body
            assert body["proof"] == residual_gap_proof(payload), payload
            continue
        max_ratio, total, retained, pairs = reference
        assert body["status"] == "feasible", payload
        got_pairs = sorted(
            (a["manuscript_id"], a["reviewer_id"]) for a in body["assignments"]
        )
        assert got_pairs == pairs, payload
        assert Fraction(str(body["total_score"])) == total, payload
        assert Fraction(body["max_load_ratio"]).limit_denominator(10**9) == max_ratio, payload

        original = {
            (p["manuscript_id"], p["reviewer_id"])
            for p in payload["original_assignments"]
        }
        withdrawn = payload["withdrawn_reviewer_id"]
        revoked = {p for p in original if p[1] == withdrawn}
        added = set(got_pairs) - original
        assert {(a["manuscript_id"], a["reviewer_id"]) for a in body["revoked_assignments"]} == revoked
        assert {(a["manuscript_id"], a["reviewer_id"]) for a in body["added_assignments"]} == added
        assert len(set(got_pairs) & original) == retained
