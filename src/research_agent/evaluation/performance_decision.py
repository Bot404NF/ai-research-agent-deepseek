"""按运行前门槛作离线采用判断，不重复调用模型或调整评分。"""

from research_agent.evaluation.target import summarize


def assess(report, reviews, smoke):
    quality = summarize(report, reviews)
    base, candidate = (quality["arms"][a] for a in ("baseline", "candidate"))
    floors = {
        "answer_correct": 74,
        "citation_supported": 80,
        "abstention_correct": 18,
        "multi_correct": 16,
    }
    quality_gate = candidate["all_targets_met"] and all(
        candidate[f]["passed"] >= max(base[f]["passed"], floor) for f, floor in floors.items()
    )
    performance = report["performance"]
    b, c = (performance[a] for a in ("baseline", "candidate"))
    p95b, p95c = (p["total_seconds"]["p95_seconds"] for p in (b, c))
    p50b, p50c = (p["total_seconds"]["median_seconds"] for p in (b, c))
    costb, costc = b["normalized_cost_cny"], c["normalized_cost_cny"]
    time_known = all(v is not None and v > 0 for v in (p95b, p95c, p50b, p50c))
    cost_known = costb is not None and costb > 0 and costc is not None
    time_met = time_known and p95c <= p95b * 0.8 and p50c <= p50b
    cost_met = cost_known and costc <= costb * 0.85
    no_performance_regression = (
        time_known and cost_known and p95c <= p95b and p50c <= p50b and costc <= costb
    )
    stability = c["attempted"] == 100 and c["failed"] == 0 and c["truncated"] == 0
    smoke_gate = (
        smoke.get("status") == "completed"
        and bool(smoke.get("checks"))
        and all(smoke["checks"].values())
    )
    changes = {}
    scores = {(r["id"], r["arm"]): r for r in reviews["reviews"]}
    for field in ("answer_correct", "citation_supported", "abstention_correct"):
        changes[field] = {"improved": [], "regressed": []}
        for row in report["results"]:
            before = scores.get((row["id"], "baseline"), {}).get(field)
            after = scores.get((row["id"], "candidate"), {}).get(field)
            if before is False and after is True:
                changes[field]["improved"].append(row["id"])
            if before is True and after is False:
                changes[field]["regressed"].append(row["id"])
    gates = {
        "quality": bool(quality_gate),
        "stability": stability,
        "smoke": smoke_gate,
        "frozen_code": report.get("code_unchanged") is True,
        "performance": bool(no_performance_regression and (time_met or cost_met)),
    }
    return {
        "adopt": all(gates.values()),
        "gates": gates,
        "quality": quality,
        "performance": performance,
        "time_target_met": bool(time_met),
        "cost_target_met": bool(cost_met),
        "p95_reduction": 1 - p95c / p95b if time_known else None,
        "cost_reduction": 1 - costc / costb if cost_known else None,
        "paired_changes": changes,
    }
