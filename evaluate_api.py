# 云端API冒烟测试：使用真实模型和工具，记录工具轨迹与答案，运行会产生API调用。
"""Explicit live evaluation: calls the configured API and weather service (billable)."""
import json
from pathlib import Path
from agent import DeepSeekModel, run_agent


if __name__ == "__main__":
    model = DeepSeekModel()
    questions = ["北京和上海哪个城市明天更暖和·",
                 "上海明天天气如何，需要带伞吗？",
                 "ReAct是什么？模型和Python程序分别负责什么？",
                 "请只根据本地知识库解释量子纠缠，没有收录请明确说明。"]
    results = []
    for question in questions:
        print("\n问题:", question, flush=True)
        trace = []
        result = run_agent(question, model, emit=trace.append)
        results.append({"question": question, "model": model.model,
                        "trace": trace, **result})
        print("工具:", [(o["tool"], o["arguments"]) for o in result["observations"]], flush=True)
        print("回答:", result["answer"], flush=True)
    Path(__file__).with_name("api_evaluation.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
