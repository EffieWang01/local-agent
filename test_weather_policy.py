# 离线回归测试：复现普通对话绕过天气工具的漏洞，并检查概念解释不会被强制查天气。
import json
import unittest
from unittest.mock import patch
import agent
from policies import weather_request
from test_weather import observation


def act(name, **arguments):
    return json.dumps({"action": name, "arguments": arguments, "summary": "test", "answer": ""})


class WeatherPolicyTests(unittest.TestCase):
    def test_user_case_dialogue_plan_is_upgraded_and_tools_run(self):
        steps = iter([
            act("set_tasks", tasks=[{"id": "d1", "kind": "dialogue", "description": "回应明天武汉天气"}]),
            act("answer_dialogue", task_id="d1", answer="明天武汉温度18到22°C，降水概率5%。"),
            act("set_weather_task", task_id="d1", locations=["武汉"], dates=["明天"], metrics=["temperature_max"]),
            act("get_weather", task_id="d1", locations=["武汉"], dates=["明天"], metrics=["temperature_max"]),
            act("finish")])
        trace = []
        with patch.dict(agent.TOOLS, {"get_weather": observation}):
            result = agent.run_agent("我想知道明天天气如何（武汉）", lambda _: next(steps), max_steps=5, emit=trace.append)
        self.assertFalse(result["incomplete"])
        self.assertEqual([o["tool"] for o in result["observations"]], ["get_weather"])
        self.assertTrue(any("纠正为weather" in t for t in trace))
        self.assertNotIn("18到22", result["answer"])
        self.assertNotIn("5%", result["answer"])

    def test_output_guard_catches_missed_intent_and_allows_recovery(self):
        steps = iter([
            act("set_tasks", tasks=[{"id": "d1", "kind": "dialogue", "description": "回应追问"}]),
            act("answer_dialogue", task_id="d1", answer="明天武汉气温预计22°C。"),
            act("finish")])
        result = agent.run_agent("那边如何呢", lambda _: next(steps), max_steps=3, emit=lambda _: None)
        self.assertTrue(result["incomplete"])
        task = result["task_state"]["subtasks"][0]
        self.assertEqual(task["kind"], "weather")
        self.assertEqual(task["status"], "needs_requirements")
        self.assertNotIn("22°C", result["answer"])

    def test_definitions_history_and_personal_dialogue_not_weather(self):
        for question in ("温度是什么", "天气预报是什么意思", "解释天气工具调用原理", "回顾上次武汉天气", "我叫张晴"):
            self.assertFalse(weather_request(question), question)


if __name__ == "__main__":
    unittest.main()
