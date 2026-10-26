# 离线回归测试：检查批量天气参数、缺失数据、部分失败、补查和日期一致性。
import json
import unittest
from unittest.mock import patch
from datetime import date
import agent
from weather import WeatherTask, get_weather

REQ = {"locations": ["上海", "武汉", "北京"], "dates": ["今天", "明天"],
       "metrics": ["temperature_max", "rain_probability"]}


def observation(locations, dates, metrics):
    return {"results": [{"requested_location": loc, "resolved_location": {"name": loc},
        "source": "test", "errors": [], "rows": [{"requested_date": day,
        "date": "2026-10-25" if day == "今天" else "2026-10-26",
        "values": {m: (20 + i if m == "temperature_max" else 10) for m in metrics}}
        for day in dates]} for i, loc in enumerate(locations)]}


def decision(action, arguments=None, answer=""):
    return json.dumps({"action": action, "arguments": arguments or {}, "summary": "test", "answer": answer})


class WeatherTests(unittest.TestCase):
    def test_cartesian_coverage_and_partial_retry(self):
        task = WeatherTask(**REQ)
        self.assertEqual(len(task.snapshot()["missing"]), 12)
        task.update(observation(["上海"], ["今天"], ["temperature_max"]))
        self.assertEqual(len(task.snapshot()["missing"]), 11)
        with self.assertRaisesRegex(ValueError, "数据不齐"):
            task.answer()
        task.update(observation(**REQ))
        self.assertEqual(task.snapshot()["status"], "ready")
        self.assertIn("武汉", task.answer())
        task.update(observation(**REQ))
        self.assertEqual(task.snapshot()["completed_cells"], 12)

    def test_failure_null_nan_do_not_count(self):
        task = WeatherTask(**REQ)
        result = observation(**REQ)
        result["results"][1]["rows"][0]["values"]["temperature_max"] = None
        result["results"][2]["rows"][0]["values"]["temperature_max"] = float("nan")
        task.update(result)
        self.assertEqual(len(task.snapshot()["missing"]), 2)

    def test_cannot_replace_task_subjects(self):
        task = WeatherTask(**REQ)
        with self.assertRaisesRegex(ValueError, "子集"):
            task.validate_call({**REQ, "locations": ["南京"]})

    def test_incomplete_finish_rejected_and_task_cannot_shrink(self):
        plan = {**REQ, "operation": "compare_max", "metric": "temperature_max"}
        responses = iter([decision("set_tasks", {"tasks": [{"id": "w1", "kind": "weather", "description": "比较天气"}]}),
            decision("set_weather_task", {"task_id": "w1", **plan}),
            decision("get_weather", {"task_id": "w1", **REQ, "locations": ["上海"]}),
            decision("finish", answer="武汉99度"),
            decision("set_weather_task", {"task_id": "w1", **plan, "locations": ["上海"]}),
            decision("get_weather", {"task_id": "w1", **REQ}), decision("finish", answer="武汉99度")])
        trace = []
        with patch.dict(agent.TOOLS, {"get_weather": observation}):
            result = agent.run_agent("比较三个城市两天的天气", lambda _: next(responses), emit=trace.append)
        self.assertFalse(result["incomplete"])
        self.assertEqual(result["task_state"]["status"], "completed")
        self.assertIn("北京最高", result["answer"])
        self.assertNotIn("99", result["answer"])
        self.assertTrue(any("不可重设" in line for line in trace))

    def test_weather_cannot_finish_without_task(self):
        responses = iter([decision("search_knowledge", {"query": "天气"}),
                          decision("finish", answer="武汉99度"), decision("fail", answer="无法查询")])
        result = agent.run_agent("上海天气", lambda _: next(responses), emit=lambda _: None)
        self.assertTrue(result["incomplete"])

    def test_batch_tool_partial_failure_absolute_date_and_null(self):
        calls = []
        def request(url):
            calls.append(url)
            if "geocoding" in url:
                if "Missing" in url:
                    return {"results": []}
                return {"results": [{"name": "上海", "latitude": 31, "longitude": 121}]}
            return {"timezone": "Asia/Shanghai", "daily": {"time": ["2026-10-25", "2026-10-26"],
                "temperature_2m_max": [23, None], "precipitation_probability_max": [20, 30]}}
        result = get_weather(["上海", "Missing"], ["今天", "2026-10-26", "2099-01-01"],
                             ["temperature_max", "rain_probability"], request_json=request)
        first, failed = result["results"]
        self.assertEqual(first["rows"][0]["date"], "2026-10-25")
        self.assertNotIn("temperature_max", first["rows"][1]["values"])
        self.assertEqual(len(first["errors"]), 2)
        self.assertTrue(failed["errors"])
        self.assertEqual(len(calls), 3)

    def test_date_mismatch_rejected_in_comparison(self):
        task = WeatherTask(**REQ, operation="compare_max", metric="temperature_max")
        result = observation(**REQ)
        result["results"][1]["rows"][0]["date"] = "2026-10-26"
        task.update(result)
        with self.assertRaisesRegex(ValueError, "日期不一致"):
            task.answer()

    def test_invalid_tool_arguments(self):
        for args in [([], ["今天"], ["temperature_max"]),
                     (["上海"], ["明年"], ["temperature_max"]),
                     (["上海"], ["今天"], ["unknown"]),
                     (["上海", "上海"], ["今天"], ["temperature_max"])]:
            with self.assertRaises(ValueError):
                get_weather(*args, request_json=lambda _: self.fail("must not call network"))


if __name__ == "__main__":
    unittest.main()
