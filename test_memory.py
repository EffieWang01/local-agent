# 离线回归测试：用临时目录验证保存恢复、隔离清空、记忆检索及追问重新查询。
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import agent
from memory import Conversation
from test_weather import observation


def act(name, **arguments):
    return json.dumps({"action": name, "arguments": arguments, "summary": "test", "answer": ""})


class MemoryTests(unittest.TestCase):
    def test_persistence_notes_and_session_isolation(self):
        with TemporaryDirectory() as directory:
            first = Conversation(directory, "first")
            first.remember("我喜欢简洁回答")
            first.add_turn("我叫小林", {"answer": "你好，小林", "task_state": {"subtasks": []}})
            resumed = Conversation(directory, "first", require_existing=True)
            self.assertEqual(resumed.notes, ["我喜欢简洁回答"])
            self.assertIn("小林", resumed.context())
            self.assertEqual(Conversation(directory, "second").turns, [])
            resumed.forget(1)
            self.assertEqual(Conversation(directory, "first").notes, [])
            resumed.clear()
            self.assertEqual(Conversation(directory, "first").turns, [])

    def test_no_save_and_invalid_names(self):
        with TemporaryDirectory() as directory:
            memory = Conversation(directory, "ephemeral", persist=False)
            memory.remember("不写磁盘")
            memory.add_turn("你好", {"answer": "你好"})
            self.assertFalse(memory.path.exists())
            for name in ("../escape", "a/b", "a\\b", "x" * 81):
                with self.assertRaises(ValueError):
                    Conversation(directory, name)
            with self.assertRaises(ValueError):
                Conversation(directory, "missing", require_existing=True)
            Path(directory, "bad.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "损坏"):
                Conversation(directory, "bad")

    def test_relative_and_iso_dates_are_equivalent_in_evaluation(self):
        from datetime import date, timedelta
        from evaluate_dialogue import equivalent_dates
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.assertTrue(equivalent_dates([tomorrow], "明天"))
        self.assertFalse(equivalent_dates([date.today().isoformat()], "明天"))

    def test_context_bounded_and_old_history_searchable(self):
        with TemporaryDirectory() as directory:
            memory = Conversation(directory, persist=False)
            memory.add_turn("我喜欢南京", {"answer": "记住了"})
            for i in range(10):
                memory.add_turn(str(i), {"answer": "长答案" * 800})
            self.assertNotIn("我喜欢南京", memory.context())
            self.assertIn("南京", json.dumps(memory.search("南京"), ensure_ascii=False))
            self.assertLess(len(memory.context()), 5000)
            self.assertEqual(memory.search("不存在关键词")["matches"], [])

    def test_dialogue_rounds_use_history_without_knowledge_tools(self):
        with TemporaryDirectory() as directory:
            memory = Conversation(directory, "chat")
            steps = iter([act("set_tasks", tasks=[{"id": "d1", "kind": "dialogue", "description": "打招呼"}]),
                          act("answer_dialogue", task_id="d1", answer="你好，小林"), act("finish")])
            agent.run_agent("我叫小林", lambda _: next(steps), conversation=memory, emit=lambda _: None)
            resumed = Conversation(directory, "chat", require_existing=True)
            steps = iter([act("set_tasks", tasks=[{"id": "d1", "kind": "dialogue", "description": "回忆姓名"}]),
                          act("answer_dialogue", task_id="d1", answer="你叫小林"), act("finish")])
            def model(messages):
                context = next(m["content"] for m in messages if m["content"].startswith("会话历史"))
                self.assertIn("我叫小林", context)
                return next(steps)
            result = agent.run_agent("我叫什么", model, conversation=resumed, emit=lambda _: None)
            self.assertEqual(result["observations"], [])
            self.assertIn("小林", result["answer"])
            self.assertEqual(len(Conversation(directory, "chat").turns), 2)

    def test_weather_followup_requeries_and_does_not_reuse_evidence(self):
        with TemporaryDirectory() as directory:
            memory = Conversation(directory, persist=False)
            for city, day, question in [("上海", "今天", "查上海今天最高气温"),
                                        ("武汉", "今天", "那武汉呢"), ("武汉", "明天", "改查明天")]:
                steps = iter([act("set_tasks", tasks=[{"id": "w1", "kind": "weather", "description": question}]),
                    act("set_weather_task", task_id="w1", locations=[city], dates=[day], metrics=["temperature_max"]),
                    act("get_weather", task_id="w1", locations=[city], dates=[day], metrics=["temperature_max"]), act("finish")])
                with patch.dict(agent.TOOLS, {"get_weather": observation}):
                    result = agent.run_agent(question, lambda _: next(steps), conversation=memory, emit=lambda _: None)
                self.assertFalse(result["incomplete"])
                self.assertEqual(len(result["observations"]), 1)
                self.assertEqual(result["observations"][0]["id"], 1)
                self.assertEqual(result["observations"][0]["arguments"]["locations"], [city])
                self.assertEqual(result["observations"][0]["arguments"]["dates"], [day])
            context = json.loads(memory.context().split("\n", 1)[1])
            self.assertEqual(context["active_weather_context"]["requirements"][0]["locations"], ["武汉"])
            self.assertEqual(context["active_weather_context"]["requirements"][0]["dates"], ["明天"])

    def test_commands_do_not_create_chat_turns(self):
        with TemporaryDirectory() as directory:
            memory = Conversation(directory, "one")
            for command in ("/remember 偏好简短", "/memory", "/history", "/help", "/sessions"):
                handled, memory = agent.session_command(command, memory, emit=lambda _: None)
                self.assertTrue(handled)
            self.assertFalse(memory.turns)
            _, new = agent.session_command("/new two", memory, emit=lambda _: None)
            self.assertFalse(new.notes)
            _, restored = agent.session_command("/load one", new, emit=lambda _: None)
            self.assertEqual(restored.notes, ["偏好简短"])
            _, restored = agent.session_command("/clear", restored, emit=lambda _: None)
            self.assertFalse(Conversation(directory, "one").notes)

    def test_failed_model_turn_is_preserved(self):
        with TemporaryDirectory() as directory:
            memory = Conversation(directory, "failed")
            def model(_):
                raise RuntimeError("test failure")
            result = agent.run_agent("那武汉呢", model, conversation=memory, emit=lambda _: None)
            self.assertTrue(result["incomplete"])
            self.assertTrue(Conversation(directory, "failed").turns[0]["incomplete"])

    def test_recall_memory_tool_reads_old_turns_without_weather_evidence(self):
        with TemporaryDirectory() as directory:
            memory = Conversation(directory, persist=False)
            memory.add_turn("我喜欢南京", {"answer": "好的"})
            steps = iter([act("set_tasks", tasks=[{"id": "d1", "kind": "dialogue", "description": "回忆喜好"}]),
                          act("recall_memory", task_id="d1", query="南京"),
                          act("answer_dialogue", task_id="d1", answer="你说过喜欢南京"), act("finish")])
            result = agent.run_agent("我喜欢哪里", lambda _: next(steps), conversation=memory, emit=lambda _: None)
            self.assertEqual([o["tool"] for o in result["observations"]], ["recall_memory"])
            self.assertIn("南京", result["observations"][0]["result"]["matches"][0]["question"])


if __name__ == "__main__":
    unittest.main()
