# 程序入口与ReAct主循环。阅读顺序建议：main → run_agent → allowed_schema → 两个模型适配器。
"""Text-based ReAct agent with DeepSeek API or local Ollama. Standard library only."""
import argparse
import json
import os
import re
from datetime import date
from weather import get_weather as batch_weather
from tasks import TaskLedger
from prompts import SYSTEM, EXAMPLES
from memory import Conversation
from policies import weather_request, weather_claim, historical_request
from pathlib import Path
import urllib.parse
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent


# 读取.env配置但不执行其中内容；setdefault使进程环境变量优先于文件配置。
def load_env(path=ROOT / ".env"):
    """Read simple KEY=VALUE settings without executing shell code."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key.strip()):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


# 云端模型适配器：把统一消息列表转为DeepSeek请求，返回普通JSON文本，不启用原生工具调用。
class DeepSeekModel:
    """Text-only ReAct: no tools/functions/tool_choice sent to the API."""
    # 确定模型名、密钥和API地址；密钥只放在请求头，不打印。
    def __init__(self, model=None):
        load_env()
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
        self.api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.base_url = (os.environ.get("DEEPSEEK_BASE_URL") or
                         os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        if not self.api_key:
            raise ValueError("请在 .env 设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY")
        if urllib.parse.urlparse(self.base_url).scheme != "https":
            raise ValueError("API 地址必须使用 HTTPS")

    # 向模型发送上下文并检查响应；模型给出的动作文本随后交给run_agent解析。
    def __call__(self, messages):
        # 只请求普通JSON文本；thinking控制模型思考模式，与是否执行工具是两个不同概念。
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "temperature": 0, "max_tokens": 1800,
                   "thinking": {"type": "disabled"},
                   "response_format": {"type": "json_object"}}
        req = urllib.request.Request(self.base_url + "/chat/completions",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"DeepSeek API HTTP {exc.code}；请检查模型名称、余额和配置。") from None
        choice = result["choices"][0]
        message = choice["message"]
        if message.get("tool_calls") or message.get("function_call"):
            raise RuntimeError("API 意外返回原生工具调用，已拒绝执行。")
        if choice.get("finish_reason") == "length":
            raise RuntimeError("API 输出被截断，请减少问题长度或提高 max_tokens。")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("API 未返回文本内容。")
        return content


# 通用HTTP JSON请求：无payload时为GET，有payload时为POST；异常由调用方处理。
def request_json(url, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", "User-Agent": "LocalReActTutorial/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


# 按关键词匹配本地知识条目并返回前三项；这是简单检索，不是互联网搜索或向量检索。
def search_knowledge(query):
    """Tiny retrieval tool; content is editable, not invented by the model."""
    entries = json.loads((ROOT / "knowledge.json").read_text(encoding="utf-8"))
    words = query.lower().split()
    scored = []
    for entry in entries:
        score = sum(key.lower() in query.lower() for key in entry["keywords"])
        score += sum(word in entry["title"].lower() for word in words)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return {"source": "local:knowledge.json", "matches": [e for _, e in scored[:3]],
            "note": "无匹配时请说明本地知识库未收录；此工具不是互联网搜索。"}


# 为天气模块注入HTTP函数；工具与网络访问解耦，便于用固定数据做测试。
def get_weather(locations, dates, metrics):
    return batch_weather(locations, dates, metrics, request_json=request_json)


# 工具注册表只包含程序允许执行的函数；模型给出函数名，并不意味着可以执行任意Python。
TOOLS = {"get_weather": get_weather, "search_knowledge": search_knowledge}
# 以下Schema约束的是普通JSON文本格式，不是向服务端注册原生function calling。
TASK_ITEM = {"type": "object", "properties": {
    "id": {"type": "string"}, "kind": {"type": "string", "enum": ["weather", "knowledge", "dialogue"]},
    "description": {"type": "string"}}, "required": ["id", "kind", "description"], "additionalProperties": False}
ARGUMENTS = {
    "tasks": {"type": "array", "items": TASK_ITEM, "minItems": 1, "maxItems": 8},
    "task_id": {"type": "string"},
    "locations": {"type": "array", "items": {"type": "string"}},
    "dates": {"type": "array", "items": {"type": "string"}},
    "metrics": {"type": "array", "items": {"type": "string", "enum": ["temperature_max", "temperature_min", "rain_probability"]}},
    "operation": {"type": "string", "enum": ["report", "compare_max", "compare_min"]},
    "metric": {"type": "string"}, "query": {"type": "string", "minLength": 1},
    "answer": {"type": "string", "minLength": 1},
    "evidence_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1}}


# 构造一个动作的JSON格式约束，分别限制参数，避免不同工具字段混用。
def action_schema(name, fields, required=None):
    return {"type": "object", "properties": {
        "action": {"type": "string", "enum": [name]},
        "arguments": {"type": "object", "properties": {k: ARGUMENTS[k] for k in fields},
                      "required": fields if required is None else required, "additionalProperties": False},
        "summary": {"type": "string"}, "answer": {"type": "string"}},
        "required": ["action", "arguments", "summary", "answer"], "additionalProperties": False}


SCHEMA = {"anyOf": [
    action_schema("set_tasks", ["tasks"]),
    action_schema("set_weather_task", ["task_id", "locations", "dates", "metrics", "operation", "metric"],
                  ["task_id", "locations", "dates", "metrics", "operation"]),
    action_schema("get_weather", ["task_id", "locations", "dates", "metrics"]),
    action_schema("search_knowledge", ["task_id", "query"]),
    action_schema("answer_knowledge", ["task_id", "answer", "evidence_ids"]),
    action_schema("answer_dialogue", ["task_id", "answer"]),
    action_schema("recall_memory", ["task_id", "query"]),
    action_schema("finish", []), action_schema("fail", [])]}


# 本地模型适配器；schema可随当前任务状态变化，限制模型本轮可输出的动作。
class OllamaModel:
    # 默认使用本地R1 7B；auto由Ollama选择GPU，cpu才显式禁用GPU。
    def __init__(self, model="deepseek-r1:7b", device="auto"):
        load_env()
        self.model = model
        self.device = device
        self.schema = SCHEMA

    # 调用本机Ollama服务；截断输出不能当作完整动作，思考标签也不能直接执行。
    def __call__(self, messages):
        options = {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}
        # auto不手动设置num_gpu，交给Ollama调度；仅cpu模式明确设置为0。
        if self.device == "cpu":
            options["num_gpu"] = 0
        result = request_json(os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
                              + "/api/chat", {
            "model": self.model, "messages": messages, "stream": False, "format": self.schema,
            "options": options}, timeout=300)
        if result.get("done_reason") == "length":
            raise RuntimeError("本地模型输出达到2048 token上限，未完成动作；请缩短问题。")
        message = result["message"]
        if message.get("tool_calls"):
            raise RuntimeError("本地模型意外返回原生工具调用，已拒绝执行。")
        content = message.get("content", "")
        # Older Ollama templates may embed R1 thinking in content instead of a separate field.
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        if content.startswith("```json") and content.endswith("```"):
            content = content[7:-3].strip()
        if not content:
            raise RuntimeError("本地模型未返回JSON动作。")
        return content


# 仅缩短送给模型的工具观察；完整数据仍保留在程序中，不因摘要丢失验收证据。
def compact_observation(observation):
    result = observation["result"]
    if observation["tool"] == "get_weather":
        result = {"results": [{k: entry[k] for k in
            ("requested_location", "resolved_location", "rows", "errors") if k in entry}
            for entry in result.get("results", [])], **({"error": result["error"]} if "error" in result else {})}
    return {"id": observation["id"], "task_id": observation["task_id"], "result": result}


# 按账本状态生成本轮动作白名单及可用task_id；格式约束不等于保证模型理解正确。
def allowed_schema(ledger, has_memory=False):
    """Constrain text actions by execution state rather than question keywords."""
    eligible = {}
    if ledger is None:
        eligible["set_tasks"] = None
    else:
        eligible["fail"] = None
        for ident, task in ledger.tasks.items():
            if task["kind"] == "knowledge":
                state = ledger.knowledge[ident]
                if state["answer"] is None:
                    eligible.setdefault("search_knowledge", []).append(ident)
                    if any("error" not in r for r in state["observations"].values()):
                        eligible.setdefault("answer_knowledge", []).append(ident)
            elif task["kind"] == "dialogue":
                if ledger.dialogue[ident] is None:
                    eligible.setdefault("answer_dialogue", []).append(ident)
                    if has_memory:
                        eligible.setdefault("recall_memory", []).append(ident)
            elif ident not in ledger.weather:
                eligible.setdefault("set_weather_task", []).append(ident)
            elif ledger.weather[ident].snapshot()["missing"]:
                eligible.setdefault("get_weather", []).append(ident)
        if len(eligible) == 1:
            eligible["finish"] = None
    branches = []
    for original in SCHEMA["anyOf"]:
        name = original["properties"]["action"]["enum"][0]
        if name not in eligible:
            continue
        # 复制Schema后再缩小动作范围，避免修改全局模板并污染下一轮或下一个会话。
        branch = json.loads(json.dumps(original))
        if eligible[name] is not None:
            branch["properties"]["arguments"]["properties"]["task_id"]["enum"] = eligible[name]
        branches.append(branch)
    return {"anyOf": branches}, list(eligible)


# 一次用户请求的ReAct循环；每轮对话重新建账本和证据编号，但可读取跨轮会话记忆。
def run_agent(question, model, max_steps=12, emit=print, conversation=None):
    messages = [{"role": "system", "content": SYSTEM + "\n本机当前日期：" + date.today().isoformat()},
                *EXAMPLES]
    if conversation is not None:
        messages.append({"role": "user", "content": conversation.context()})
    messages.append({"role": "user", "content": "真实用户任务：" + question})
    # observations是本轮真实工具结果；events是动作及错误轨迹，两者不是同一种记录。
    observations, events = [], []
    # 账本只属于本轮请求；跨轮依赖conversation传递上下文，不能沿用旧证据编号。
    ledger = None
    # 生成精简状态快照，告诉模型尚缺什么，不重复灌入全部数据。
    def state():
        return ledger.snapshot(compact=True) if ledger else {"status": "needs_plan"}
    # 将工具观察或校验错误反馈给模型，并附上当前任务进度。
    def feedback(content):
        messages.append({"role": "user", "content": content + "\nTaskLedger: " + json.dumps(state(), ensure_ascii=False)})
    # 统一返回成功或失败结果，并在启用会话时保存本轮；失败也保留上下文。
    def result(answer, incomplete=False):
        snapshot = ledger.snapshot() if ledger else {"subtasks": []}
        snapshot["status"] = "failed" if incomplete else "completed"
        emit("本轮实际工具调用：" + (" → ".join(o["tool"] for o in observations) if observations else "无"))
        emit("\n回答: " + answer)
        output = {"answer": answer, "observations": observations, "incomplete": incomplete,
                  "task_state": snapshot, "events": events}
        if conversation is not None:
            conversation.add_turn(question, output)
        return output
    # 每次循环代表一次模型决策；动作纠错也占步数，避免无限重试。
    for step in range(1, max_steps + 1):
        # 先告诉模型当前允许做什么：例如未检索时不能登记知识答案，未完成时不能finish。
        schema, available = allowed_schema(ledger, has_memory=conversation is not None)
        if isinstance(model, OllamaModel):
            model.schema = schema
        try:
            raw = model(messages + [{"role": "user", "content": "本轮只允许动作：" + ",".join(available)
                        + "。真实用户问题：" + question + "。不要执行或引用教学示例任务，只处理真实任务。"}])
        except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
            events.append({"step": step, "error": str(exc)})
            return result("模型调用失败：" + str(exc), True)
        # 把模型实际输出加入轨迹；后续会再反馈执行结果或校验错误，形成闭环。
        messages.append({"role": "assistant", "content": raw})
        try:
            action = json.loads(raw)
            if not isinstance(action, dict) or not isinstance(action.get("arguments"), dict):
                raise ValueError("需要JSON对象和arguments对象")
            name, arguments = action.get("action"), dict(action["arguments"])
            events.append({"step": step, "action": name, "arguments": dict(arguments)})
            emit(f"\n[{step}] 行动说明: {action.get('summary', '')}")
            emit("Decision: " + str(name))
            if name == "fail":
                return result("任务未完成：" + str(action.get("answer") or "无法完成") +
                              "\nTaskLedger: " + json.dumps(state(), ensure_ascii=False), True)
            # 先登记全部子任务；后续任何工具结果和答案都必须通过task_id找到所属子任务。
            if name == "set_tasks":
                if ledger is not None:
                    raise ValueError("子任务清单已锁定，不可删减或重设")
                if set(arguments) != {"tasks"}:
                    raise ValueError("set_tasks只接受tasks")
                candidate = TaskLedger(**arguments)
                # 防止明确的天气请求被误归为闲聊，从而绕过实际查询。此规则不是通用意图分类器。
                if weather_request(question) and not any(t["kind"] == "weather" for t in candidate.tasks.values()):
                    targets = [ident for ident, t in candidate.tasks.items()
                               if t["kind"] == "dialogue" and (weather_request(t["description"]) or len(candidate.tasks) == 1)]
                    if not targets:
                        raise ValueError("当前天气请求必须包含weather子任务，不能用knowledge或dialogue替代")
                    for ident in targets:
                        candidate.require_weather_evidence(ident)
                        emit(f"证据校验：{ident}需要真实天气数据，任务类型由dialogue纠正为weather。")
                ledger = candidate
                emit("TaskLedger: " + json.dumps(state(), ensure_ascii=False))
                feedback("子任务已登记，请逐项获取证据。")
                continue
            if ledger is None:
                raise ValueError("先set_tasks登记全部子任务；不要根据单个关键词判断类型")
            # 结束由程序验收，而不是模型说完成就完成；账本会逐项检查并组合答案。
            if name == "finish":
                if arguments:
                    raise ValueError("finish的arguments应为空")
                return result(ledger.answer())
            # task_id是控制层字段，不属于HTTP天气工具或知识检索函数本身的参数。
            task_id = arguments.pop("task_id", None)
            if name == "set_weather_task":
                ledger.register_weather(task_id, **arguments)
                emit("TaskLedger: " + json.dumps(state(), ensure_ascii=False))
                feedback("天气需求已登记，请查询缺失项。")
                continue
            if name == "answer_knowledge":
                ledger.answer_knowledge(task_id, **arguments)
                feedback("知识子任务答案已登记，继续处理其他子任务或finish。")
                continue
            # 普通对话可回应个人信息，但不能凭空给出新的天气预报。
            if name == "answer_dialogue":
                if (not historical_request(question) and weather_claim(str(arguments.get("answer", "")))):
                    ledger.require_weather_evidence(task_id)
                    raise ValueError("普通对话不能生成新的天气预报；该任务已升级为weather，请set_weather_task再get_weather")
                ledger.answer_dialogue(task_id, **arguments)
                feedback("对话回答已登记，继续其他子任务或finish。")
                continue
            if name == "get_weather":
                ledger.require(task_id, "weather")
                if task_id not in ledger.weather:
                    raise ValueError("先为该子任务set_weather_task")
                ledger.weather[task_id].validate_call(arguments)
            elif name == "search_knowledge":
                ledger.require(task_id, "knowledge")
                if set(arguments) != {"query"} or not isinstance(arguments.get("query"), str) or not 1 <= len(arguments["query"].strip()) <= 500:
                    raise ValueError("search_knowledge需要非空query，最长500")
            elif name == "recall_memory":
                ledger.require(task_id, "dialogue")
                if conversation is None or set(arguments) != {"query"}:
                    raise ValueError("记忆检索需要启用会话并提供query")
            else:
                raise ValueError("未知动作")
            # 只有到这里才真正进入工具执行；前面的Decision可能只是登记任务或答案。
            emit("Action: " + name + "(" + json.dumps({"task_id": task_id, **arguments}, ensure_ascii=False) + ")")
            try:
                data = conversation.search(**arguments) if name == "recall_memory" else TOOLS[name](**arguments)
            except Exception as exc:
                data = {"error": str(exc)}
            # Observation编号只在本轮内递增，知识答案只能引用本轮对应子任务的编号。
            observation = {"id": len(observations) + 1, "task_id": task_id, "tool": name,
                           "arguments": arguments, "result": data}
            observations.append(observation)
            if name == "get_weather":
                ledger.weather[task_id].update(data)
            elif name == "search_knowledge":
                ledger.record_knowledge(task_id, observation["id"], data)
            emit("Observation " + str(observation["id"]) + ": " + json.dumps(data, ensure_ascii=False))
            emit("TaskLedger: " + json.dumps(state(), ensure_ascii=False))
            # 把工具结果反馈给模型，它才能依据观察决定补查、回答、结束或报告失败。
            feedback("Observation: " + json.dumps(compact_observation(observation), ensure_ascii=False))
        # 格式或动作校验失败后反馈给模型纠正，不直接执行不合法的动作。
        except (ValueError, TypeError, KeyError) as exc:
            emit(f"动作校验未通过: {exc}")
            events.append({"step": step, "error": str(exc)})
            feedback(f"动作校验失败: {exc}。选择正确动作继续。")
    return result("已达到步数上限。TaskLedger: " + json.dumps(state(), ensure_ascii=False), True)


# 处理本地会话命令；这些命令不调用模型，也不占用一次对话轮次。
def session_command(text, conversation, emit=print):
    """Local commands never call the model or become chat turns."""
    if not text.startswith("/"):
        return False, conversation
    parts = text.split(maxsplit=1)
    command, argument = parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""
    if command == "/help":
        emit("/history 历史；/memory 显式记忆；/remember 内容；/forget 序号；/clear 清空本会话历史和记忆；"
             "/new [名称] 新会话；/load 名称 恢复；/sessions 列表；exit 退出")
    elif command == "/history":
        emit(f"会话 {conversation.session_id}，共{len(conversation.turns)}轮（显示最近10轮）")
        for turn in conversation.turns[-10:]:
            emit(turn["timestamp"] + "\n你: " + turn["question"] + "\nAgent: " + turn["answer"][:1000])
    elif command == "/memory":
        emit("显式记忆：" + ("\n" + "\n".join(f"{i+1}. {v}" for i, v in enumerate(conversation.notes))
                           if conversation.notes else "暂无；最近对话请用/history查看"))
    elif command == "/remember":
        conversation.remember(argument)
        emit("已记录显式记忆。")
    elif command == "/forget":
        conversation.forget(int(argument))
        emit("已删除该条显式记忆。")
    elif command == "/clear":
        conversation.clear()
        emit("已清空本会话的对话历史和显式记忆。")
    elif command == "/new":
        new = Conversation(conversation.directory, argument or None, persist=conversation.persist)
        if new.path.exists():
            raise ValueError("会话名已存在，请用/load恢复或选择其他名称")
        conversation = new
        conversation.save()
        emit("新会话：" + conversation.session_id)
    elif command == "/load":
        if not argument:
            raise ValueError("请提供会话名称")
        conversation = Conversation(conversation.directory, argument, persist=conversation.persist, require_existing=True)
        emit(f"已恢复 {conversation.session_id}，{len(conversation.turns)}轮对话")
    elif command == "/sessions":
        files = sorted(conversation.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        emit("最近会话：\n" + "\n".join(p.stem for p in files))
    else:
        emit("未知命令，请输入/help")
    return True, conversation


# 解析命令行参数，选择模型并创建或恢复会话，然后逐轮读取用户输入。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--provider", choices=["deepseek", "ollama"], default="deepseek")
    parser.add_argument("--model", default=None)
    parser.add_argument("--device", choices=["auto", "cpu"], default="auto",
                        help="仅用于ollama：auto自动使用GPU，cpu强制CPU")
    parser.add_argument("--max-steps", type=int, default=12)
    sessions = parser.add_mutually_exclusive_group()
    sessions.add_argument("--session", help="指定会话名：不存在则创建，存在则恢复")
    sessions.add_argument("--resume", help="恢复已存在的会话，不存在时报错")
    parser.add_argument("--no-save", action="store_true", help="只保留本次进程中的对话，不写入磁盘")
    args = parser.parse_args()
    if not 1 <= args.max_steps <= 40:
        parser.error("--max-steps 必须在1至40之间")
    model = DeepSeekModel(args.model) if args.provider == "deepseek" else OllamaModel(args.model or "deepseek-r1:7b", args.device)
    try:
        conversation = Conversation(ROOT / ".sessions", args.resume or args.session,
                                    persist=not args.no_save, require_existing=bool(args.resume))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"ReAct Agent | {args.provider} | {model.model} | 原生 function call 关闭 | 输入 exit 退出")
    print(f"会话: {conversation.session_id} | 已有{len(conversation.turns)}轮 | "
          + ("自动保存至 .sessions/" if conversation.persist else "不保存到磁盘") + " | /help 查看命令")
    while True:
        try:
            question = args.question or input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in ("exit", "quit", "退出"):
            break
        if not question:
            continue
        try:
            handled, conversation = session_command(question, conversation)
            if not handled:
                run_agent(question, model, args.max_steps, conversation=conversation)
        except Exception as exc:
            print(f"运行失败: {exc}")
            if args.question:
                raise SystemExit(1)
        if args.question:
            break


if __name__ == "__main__":
    main()
