# 多轮会话评测：每轮从文件恢复记忆，检查姓名回忆、地点指代和日期追问。
"""Real-model multi-turn check; saves/reloads memory between turns. Fixture weather by default."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import agent
from evaluate_react import fixture_weather
from memory import Conversation
from datetime import date, timedelta


# 把今天、明天等相对日期与对应ISO日期按同一天验收。
def equivalent_dates(selectors, expected):
    def canonical(value):
        return ((date.today() + timedelta(days={"今天": 0, "明天": 1, "后天": 2}[value])).isoformat()
                if value in ("今天", "明天", "后天") else value)
    return {canonical(v) for v in selectors} == {canonical(expected)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["deepseek", "ollama"], default="deepseek")
    parser.add_argument("--steps", type=int, choices=[2, 5], default=5)
    parser.add_argument("--live-tools", action="store_true")
    args = parser.parse_args()
    model = agent.DeepSeekModel() if args.provider == "deepseek" else agent.OllamaModel()
    records = []
    with TemporaryDirectory() as directory:
        for index, question in enumerate(["我叫小林，请叫我小林。", "我叫什么？",
                                         "查上海今天最高气温", "那武汉呢？", "改查明天"][:args.steps]):
            memory = Conversation(directory, "dialogue-test", require_existing=index > 0)
            trace = []
            if args.live_tools:
                result = agent.run_agent(question, model, conversation=memory, emit=trace.append)
            else:
                with patch.dict(agent.TOOLS, {"get_weather": fixture_weather}):
                    result = agent.run_agent(question, model, conversation=memory, emit=trace.append)
            issues = []
            if result["incomplete"]:
                issues.append("任务未完成")
            if index < 2 and ("小林" not in result["answer"] or result["observations"]):
                issues.append("姓名对话失败或不必要地调用检索")
            if index >= 2:
                expected_city = "上海" if index == 2 else "武汉"
                expected_day = "明天" if index == 4 else "今天"
                weather = [o for o in result["observations"] if o["tool"] == "get_weather"]
                if not any(expected_city in o["arguments"]["locations"] and equivalent_dates(o["arguments"]["dates"], expected_day) for o in weather):
                    issues.append("未按上下文重新查询预期地点和日期")
                state = result["task_state"].get("subtasks", [])
                reqs = [t.get("requirements", {}) for t in state if t["kind"] == "weather"]
                if not any(r.get("locations") == [expected_city] and equivalent_dates(r.get("dates", []), expected_day) for r in reqs):
                    issues.append("追问没有正确修改任务需求")
            records.append({"question": question, "passed": not issues, "issues": issues, "result": result, "trace": trace})
            print(index + 1, question, "PASS" if not issues else "FAIL", issues, flush=True)
            Path(__file__).with_name("dialogue_evaluation_" + args.provider + ".json").write_text(json.dumps(
                {"provider": args.provider, "live_tools": args.live_tools, "records": records,
                 "note": "每轮从文件重新加载会话；默认固定天气数据，检查指代解析与重新查询，不代表通用对话正确率。"},
                ensure_ascii=False, indent=2), encoding="utf-8")
