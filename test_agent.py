# 离线回归测试：模拟模型响应或HTTP返回，检查接口协议、格式处理和循环停止，不访问真实模型。
"""Deterministic control-loop tests; no network/model download required."""
import json
import unittest
from unittest.mock import patch
import agent


def decision(action, query="", answer=""):
    return json.dumps(dict(action=action, arguments={"query": query} if query else {}, summary="test", answer=answer))


class AgentTests(unittest.TestCase):
    def test_local_auto_gpu_and_r1_content(self):
        raw = "<think>internal text</think>" + decision("finish", answer="完成")
        with patch("agent.request_json", return_value={"message": {"content": raw}}) as send:
            content = agent.OllamaModel()([{"role": "user", "content": "hello"}])
        payload = send.call_args.args[1]
        self.assertEqual(payload["model"], "deepseek-r1:7b")
        self.assertNotIn("num_gpu", payload["options"])
        self.assertNotIn("tools", payload)
        self.assertEqual(json.loads(content)["answer"], "完成")

    def test_local_cpu_and_truncation(self):
        with patch("agent.request_json", return_value={"done_reason": "length"}) as send:
            with self.assertRaisesRegex(RuntimeError, "上限"):
                agent.OllamaModel(device="cpu")([])
        self.assertEqual(send.call_args.args[1]["options"]["num_gpu"], 0)

    def test_deepseek_uses_text_only_api(self):
        from io import BytesIO
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": decision("finish", answer="完成")}}]}
        with patch.dict(agent.os.environ, {"DEEPSEEK_API_KEY": "test-only",
                        "DEEPSEEK_BASE_URL": "https://api.deepseek.com"}):
            model = agent.DeepSeekModel()
        with patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(response).encode())) as send:
            model([{"role": "user", "content": "hello"}])
        payload = json.loads(send.call_args.args[0].data)
        for key in ("tools", "tool_choice", "functions", "function_call"):
            self.assertNotIn(key, payload)
        self.assertEqual(payload["thinking"]["type"], "disabled")

    def test_loop_is_bounded(self):
        result = agent.run_agent("?", lambda _: "invalid", max_steps=2, emit=lambda _: None)
        self.assertTrue(result["incomplete"])


if __name__ == "__main__":
    unittest.main()
