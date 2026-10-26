# 评测器自身的测试：允许等价的任务拆分，同时检查真正缺失的比较范围。
import unittest
from evaluate_react import CASES, check


class EvaluationTests(unittest.TestCase):
    def test_equivalent_task_decompositions_are_allowed(self):
        case = next(c for c in CASES if c["id"] == "batch")
        req = case["weather"]
        tasks = [{"kind": "weather", "requirements": {**req, "operation": "report"}, "missing": []}]
        for day in req["dates"]:
            tasks.append({"kind": "weather", "requirements": {**req, "dates": [day],
                "metrics": ["temperature_max"], "operation": "compare_max", "metric": "temperature_max"}, "missing": []})
        result = {"task_state": {"subtasks": tasks}, "incomplete": False, "answer": "报告"}
        self.assertEqual(check(case, result), [])
        tasks.pop()
        self.assertIn("未完成所有日期的跨城市最高气温比较", check(case, result))


if __name__ == "__main__":
    unittest.main()
