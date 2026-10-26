# 离线回归测试：检查子任务覆盖、证据归属、混合答案及动作状态约束。
import json
import unittest
from unittest.mock import patch
import agent
from tasks import TaskLedger
from test_weather import observation


def act(name, **args):
    return json.dumps({"action": name, "arguments": args, "summary": "test", "answer": ""})


class LedgerTests(unittest.TestCase):
    def test_allowed_actions_follow_evidence_state(self):
        _, names = agent.allowed_schema(None)
        self.assertEqual(set(names), {"set_tasks"})
        ledger = TaskLedger([{"id": "k1", "kind": "knowledge", "description": "ReAct"}])
        _, names = agent.allowed_schema(ledger)
        self.assertEqual(set(names), {"search_knowledge", "fail"})
        ledger.record_knowledge("k1", 1, {"matches": [{"content": "ReAct"}]})
        schema, names = agent.allowed_schema(ledger)
        self.assertIn("answer_knowledge", names)
        for branch in schema["anyOf"]:
            fields = branch["properties"]["arguments"]["properties"]
            if "task_id" in fields:
                self.assertEqual(fields["task_id"]["enum"], ["k1"])
        ledger.answer_knowledge("k1", "ReAct", [1])
        _, names = agent.allowed_schema(ledger)
        self.assertEqual(set(names), {"finish", "fail"})

    def run_steps(self, question, steps):
        responses = iter(steps)
        with patch.dict(agent.TOOLS, {"get_weather": observation,
                 "search_knowledge": lambda query: {"source": "fixture", "matches": [{"content": query + "定义"}]}}):
            return agent.run_agent(question, lambda _: next(responses), max_steps=len(steps), emit=lambda _: None)

    def test_temperature_definition_is_knowledge_not_weather(self):
        result = self.run_steps("温度是什么？", [
            act("set_tasks", tasks=[{"id": "k1", "kind": "knowledge", "description": "温度是什么"}]),
            act("search_knowledge", task_id="k1", query="温度"),
            act("answer_knowledge", task_id="k1", answer="温度是物理量", evidence_ids=[1]), act("finish")])
        self.assertFalse(result["incomplete"])
        self.assertIn("温度是物理量", result["answer"])
        self.assertEqual([o["tool"] for o in result["observations"]], ["search_knowledge"])

    def test_mixed_answer_cannot_skip_knowledge(self):
        result = self.run_steps("查上海天气，并解释ReAct", [
            act("set_tasks", tasks=[{"id": "w1", "kind": "weather", "description": "上海天气"},
                                    {"id": "k1", "kind": "knowledge", "description": "解释ReAct"}]),
            act("set_weather_task", task_id="w1", locations=["上海"], dates=["今天"], metrics=["temperature_max"]),
            act("get_weather", task_id="w1", locations=["上海"], dates=["今天"], metrics=["temperature_max"]),
            act("finish"),
            act("search_knowledge", task_id="k1", query="ReAct"),
            act("answer_knowledge", task_id="k1", answer="ReAct是推理与行动交替", evidence_ids=[2]), act("finish")])
        self.assertFalse(result["incomplete"])
        self.assertIn("上海", result["answer"])
        self.assertIn("ReAct是推理与行动交替", result["answer"])
        self.assertTrue(any("尚未answer_knowledge" in e.get("error", "") for e in result["events"]))

    def test_evidence_must_belong_to_same_subtask(self):
        ledger = TaskLedger([{"id": "a", "kind": "knowledge", "description": "A"},
                             {"id": "b", "kind": "knowledge", "description": "B"}])
        ledger.record_knowledge("a", 1, {"matches": [{"content": "A"}]})
        with self.assertRaisesRegex(ValueError, "实际得到"):
            ledger.answer_knowledge("b", "A", [1])
        with self.assertRaises(ValueError):
            ledger.answer_knowledge("a", "A", [True])

    def test_empty_results_force_explicit_unavailable(self):
        ledger = TaskLedger([{"id": "a", "kind": "knowledge", "description": "量子纠缠"}])
        ledger.record_knowledge("a", 1, {"matches": []})
        ledger.answer_knowledge("a", "编造内容", [1])
        self.assertIn("未检索到", ledger.answer())
        self.assertNotIn("编造", ledger.answer())

    def test_failed_search_cannot_be_answer_evidence(self):
        ledger = TaskLedger([{"id": "a", "kind": "knowledge", "description": "A"}])
        ledger.record_knowledge("a", 1, {"error": "offline"})
        with self.assertRaisesRegex(ValueError, "失败"):
            ledger.answer_knowledge("a", "A", [1])

    def test_plan_locked_and_tools_require_plan(self):
        result = self.run_steps("解释ReAct", [act("search_knowledge", task_id="k1", query="ReAct"),
            act("set_tasks", tasks=[{"id": "k1", "kind": "knowledge", "description": "ReAct"}]),
            act("set_tasks", tasks=[{"id": "k2", "kind": "knowledge", "description": "省略原任务"}]), act("finish")])
        self.assertTrue(result["incomplete"])
        self.assertEqual(len(result["observations"]), 0)


if __name__ == "__main__":
    unittest.main()
