"""离线重算已保存的服务端用量，不重复调用生成模型。"""

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from research_agent.evaluation.acceptance import digest
from research_agent.evaluation.baseline import token_cost
from research_agent.storage import write_json_atomic


def analyze(report: dict, pricing: dict) -> dict:
    if pricing.get("model") != report["settings"]["model"]:
        raise ValueError("价格模型与原始运行不匹配。")
    for key in ("source_url", "access_date", "currency", "time_band", "applicability"):
        if not pricing.get(key):
            raise ValueError(f"缺少价格出处或适用范围：{key}")
    for key in ("input_hit_per_million", "input_miss_per_million", "output_per_million"):
        value = pricing.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("价格必须为非负有限数值。")
    calls = [row for row in report["results"] if row.get("api_attempted")]
    rows = [{"id": row["id"], **token_cost(row.get("usage"), pricing)} for row in calls]
    known = [row for row in rows if row["estimated_cost"] is not None]
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "pricing": pricing,
        "scope": "官方公开单价乘已保存用量的估算，非账户账单；不含完整代理、搜索、基础设施",
        "api_attempted": len(calls),
        "priced_calls": len(known),
        "estimated_total_cost": sum(row["estimated_cost"] for row in known)
        if rows and len(known) == len(rows)
        else None,
        "observed_partial_cost": sum(row["estimated_cost"] for row in known) if known else None,
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="读取真实运行记录，离线估算 Token 成本")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--pricing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出已存在，请使用新文件，避免覆盖历史证据。")
    result = analyze(
        json.loads(args.report.read_text(encoding="utf-8")),
        json.loads(args.pricing.read_text(encoding="utf-8")),
    )
    result.update(raw_report_sha256=digest(args.report), pricing_sha256=digest(args.pricing))
    write_json_atomic(args.output, result)
    print(f"已估算 {result['priced_calls']}/{result['api_attempted']} 次请求；金额见 {args.output}")


if __name__ == "__main__":
    main()
