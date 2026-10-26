# 固定题集评测：调用真实模型，默认以固定天气数据隔离网络波动；结构通过不等于语义完全正确。
"""Fixed-task evaluation with real models; reproducible fixture tools by default."""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import time
from itertools import product
from unittest.mock import patch
import agent

CASES = [
    {"id": "concept", "question": "温度是什么？请只根据本地知识库回答，没有收录则说明。", "kinds": ["knowledge"]},
    {"id": "mixed", "question": "查上海今天最高气温，并解释ReAct是什么。", "kinds": ["weather", "knowledge"],
     "weather": {"locations": ["上海"], "dates": ["今天"], "metrics": ["temperature_max"]}, "contains": "ReAct"},
    {"id": "batch", "question": "查上海、武汉、北京今天和明天的最高气温和降水概率，比较每天哪里最高气温更高。",
     "kinds": ["weather"], "weather": {"locations": ["上海", "武汉", "北京"], "dates": ["今天", "明天"],
         "metrics": ["temperature_max", "rain_probability"]}, "compare_metric": "temperature_max"},
    {"id": "absent", "question": "请仅根据本地知识库解释量子纠缠，未收录请明确说明。", "kinds": ["knowledge"], "unavailable": True},
    {"id": "failure", "question": "比较上海和不存在的测试城市XYZ今天的最高气温，查不到不要猜测。", "kinds": ["weather"],
     "expect_failure": True},
]


# 提供固定天气数据，减少网络波动对模型决策评测的影响。
def fixture_weather(locations, dates, metrics):
    base = {"上海": 23.3, "武汉": 24.5, "北京": 13.3}
    results = []
    for location in locations:
        entry = {"requested_location": location, "rows": [], "errors": [],
                 "resolved_location": {"name": location}, "source": "fixture://weather"}
        if location not in base:
            entry["errors"].append({"error": "测试地点不存在"})
        else:
            for selector in dates:
                actual = ((date.today() + timedelta(days={"今天": 0, "明天": 1, "后天": 2}[selector])).isoformat()
                          if selector in ("今天", "明天", "后天") else selector)
                entry["rows"].append({"requested_date": selector, "date": actual,
                    "values": {m: {"temperature_max": base[location], "temperature_min": base[location] - 5,
                                    "rain_probability": 20}[m] for m in metrics}})
        results.append(entry)
    return {"results": results}


# 按任务类别、数据覆盖和比较范围检查结果，不把结构检查当作语义正确率。
def check(case, result):
    issues = []
    tasks = result.get("task_state", {}).get("subtasks", [])
    if set(t["kind"] for t in tasks) != set(case["kinds"]):
        issues.append("子任务类型不符合预期")
    if case.get("expect_failure"):
        if not result["incomplete"]:
            issues.append("查询失败仍声称完成")
        return issues
    if result["incomplete"]:
        issues.append("未完成")
    if "weather" in case:
        weather = [t for t in tasks if t["kind"] == "weather"]
        for field, expected in case["weather"].items():
            actual = set(v for t in weather for v in t.get("requirements", {}).get(field, []))
            if actual != set(expected):
                issues.append("天气需求遗漏或增添：" + field)
        expected_cells = set(product(*(case["weather"][k] for k in ("locations", "dates", "metrics"))))
        actual_cells = set()
        for task in weather:
            req = task.get("requirements", {})
            actual_cells.update(product(*(req.get(k, []) for k in ("locations", "dates", "metrics"))))
        if actual_cells != expected_cells:
            issues.append("地点×日期×指标覆盖不符")
        if case.get("compare_metric"):
            compared = set()
            for task in weather:
                req = task.get("requirements", {})
                if (req.get("operation") == "compare_max" and req.get("metric") == case["compare_metric"]
                        and set(req.get("locations", [])) == set(case["weather"]["locations"])):
                    compared.update(req["dates"])
            if compared != set(case["weather"]["dates"]):
                issues.append("未完成所有日期的跨城市最高气温比较")
        if any(t.get("missing") for t in weather):
            issues.append("天气证据不齐")
    if case.get("contains") and case["contains"] not in result["answer"]:
        issues.append("答案缺少预期主题")
    if case.get("unavailable") and not any(token in result["answer"] for token in ("未检索到", "未收录")):
        issues.append("未明确说明知识库无结果")
    return issues


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["deepseek", "ollama"], default="ollama")
    parser.add_argument("--model")
    parser.add_argument("--cases", nargs="+", choices=[c["id"] for c in CASES])
    parser.add_argument("--live-tools", action="store_true", help="实际联网查询天气，默认用固定工具数据")
    args = parser.parse_args()
    model = agent.DeepSeekModel(args.model) if args.provider == "deepseek" else agent.OllamaModel(args.model or "deepseek-r1:7b")
    records = []
    for case in CASES:
        if args.cases and case["id"] not in args.cases:
            continue
        trace, started = [], time.monotonic()
        try:
            if args.live_tools:
                result = agent.run_agent(case["question"], model, emit=trace.append)
            else:
                with patch.dict(agent.TOOLS, {"get_weather": fixture_weather}):
                    result = agent.run_agent(case["question"], model, emit=trace.append)
            issues = check(case, result)
        except Exception as exc:
            result, issues = {"incomplete": True, "events": [], "observations": []}, [str(exc)]
        events = result["events"]
        record = {"case": case, "issues": issues, "passed": not issues, "seconds": round(time.monotonic()-started, 2),
                  "model_calls": len({e["step"] for e in events}), "tool_calls": len(result["observations"]),
                  "validation_errors": sum("error" in e for e in events), "result": result, "trace": trace}
        records.append(record)
        print(case["id"], "PASS" if not issues else "FAIL", issues, "seconds:", record["seconds"], flush=True)
        report = {"provider": args.provider, "model": model.model, "live_tools": args.live_tools,
                  "note": "自动结构/覆盖检查，不是知识答案语义正确率；小样本不代表通用成功率。",
                  "passed": sum(r["passed"] for r in records), "total": len(records), "records": records}
        Path(__file__).with_name("evaluation_" + args.provider + ".json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
