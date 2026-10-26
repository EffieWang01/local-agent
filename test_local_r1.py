# 本地模型下载与推理检查：确认4bit量化，记录耗时、生成速度及GPU状态，不代表Agent能力评测。
"""Download and smoke-test DeepSeek-R1-Distill-Qwen-7B Q4 through Ollama."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.request

MODEL = "deepseek-r1:7b"
BASE = "http://localhost:11434"


# 向本地Ollama发送JSON请求。
def post(path, payload, timeout=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


# 读取模型下载进度，遇到服务端错误立即报告。
def pull():
    last_print = 0
    with post("/api/pull", {"model": MODEL, "stream": True}, timeout=1800) as response:
        for line in response:
            item = json.loads(line)
            if "error" in item:
                raise RuntimeError(item["error"])
            now = time.monotonic()
            if now - last_print > 15 or item.get("status") == "success":
                total, completed = item.get("total", 0), item.get("completed", 0)
                print(item["status"], f"{completed / total:.1%}" if total else "", flush=True)
                last_print = now


# 核对量化元数据并做两次推理，记录速度与硬件使用情况。
def test():
    with post("/api/show", {"model": MODEL}) as response:
        info = json.load(response)
    details = info["details"]
    print("Model:", MODEL, "Details:", details, flush=True)
    if details.get("quantization_level") != "Q4_K_M":
        raise RuntimeError("Expected Q4_K_M quantization")
    results = []
    for question in ["请计算17乘以23，只在最终答案中给出结果。",
                     "用中文简要解释Agent中模型与工具各自负责什么。"]:
        started = time.monotonic()
        with post("/api/chat", {"model": MODEL, "stream": False,
                 "messages": [{"role": "user", "content": question}],
                 "options": {"num_ctx": 4096, "num_predict": 1536, "temperature": 0},
                 "keep_alive": "10m"}) as response:
            result = json.load(response)
        record = {"question": question, "answer": result["message"].get("content", ""),
                  "done_reason": result.get("done_reason"),
                  "elapsed_seconds": round(time.monotonic() - started, 2),
                  "load_seconds": round(result.get("load_duration", 0) / 1e9, 2),
                  "generated_tokens": result.get("eval_count"),
                  "tokens_per_second": round(result.get("eval_count", 0) /
                       max(result.get("eval_duration", 0) / 1e9, 0.001), 2)}
        results.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    report = {"model": MODEL, "details": details, "context": 4096, "results": results}
    for cmd in [["ollama", "ps"], ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv"]]:
        output = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace")
        print(output, flush=True)
        report[cmd[0]] = output
    Path(__file__).with_name("local_r1_test_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull", action="store_true")
    args = parser.parse_args()
    if args.pull:
        pull()
    test()
