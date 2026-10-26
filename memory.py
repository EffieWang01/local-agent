# 会话记忆：保存历史、恢复会话、检索旧记录；历史只提供上下文，不代表本轮的新证据。
"""Local conversation persistence; history is context, never fresh tool evidence."""
from datetime import datetime
import json
from pathlib import Path
import re
import uuid


# 生成独立会话名，避免默认新会话覆盖旧会话。
def new_session_id():
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


# 会话容器：turns保存对话，notes保存用户显式记忆，文件只保存可见信息。
class Conversation:
    # 限制会话名的字符范围，定位并校验本地会话文件，或创建空白内存状态。
    def __init__(self, directory, session_id=None, persist=True, require_existing=False):
        self.directory = Path(directory)
        self.session_id = session_id or new_session_id()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", self.session_id):
            raise ValueError("会话名只允许字母、数字、下划线和连字符，最长80字符")
        self.persist = persist
        self.path = self.directory / (self.session_id + ".json")
        self.turns, self.notes = [], []
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") != 1
                    or not isinstance(data.get("turns"), list) or not isinstance(data.get("notes"), list)):
                raise ValueError("会话文件格式不支持或已损坏")
            if (any(not isinstance(n, str) for n in data["notes"])
                    or any(not isinstance(t, dict) or any(not isinstance(t.get(k), str)
                           for k in ("timestamp", "question", "answer"))
                           or (t.get("task_state") is not None and not isinstance(t["task_state"], dict))
                           for t in data["turns"])):
                raise ValueError("会话记录结构已损坏，无法恢复")
            self.turns, self.notes = data["turns"], data["notes"]
        elif require_existing:
            raise ValueError("指定会话不存在")

    # 先写临时文件再替换目标文件，降低写到一半留下损坏JSON的风险；不支持多进程并发写。
    def save(self):
        if not self.persist:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "session_id": self.session_id, "notes": self.notes, "turns": self.turns}
        # 临时文件与目标文件位于同一目录，写完后替换，避免直接覆盖时只留下半份JSON。
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    # 去重保存显式记忆；限制条目数与长度，避免无限膨胀。
    def remember(self, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 300:
            raise ValueError("记忆内容需为1至300字符")
        text = text.strip()
        if text not in self.notes:
            if len(self.notes) >= 20:
                raise ValueError("最多20条显式记忆；请用/forget序号删除旧条目")
            self.notes.append(text)
            self.save()

    # 按用户可见的1起始序号删除一条记忆，并保存。
    def forget(self, index):
        if not 1 <= index <= len(self.notes):
            raise ValueError("记忆序号不存在")
        del self.notes[index-1]
        self.save()

    # 清空当前会话历史和显式记忆，不影响其他会话。
    def clear(self):
        self.turns, self.notes = [], []
        self.save()

    # 关键词检索全部历史与显式记忆，返回少量匹配；旧记录不是新的天气证据。
    def search(self, query, limit=4):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError("记忆检索需要1至200字符的关键词")
        tokens = query.lower().split()
        # 简单统计关键词命中数，不计算语义向量相似度。
        def score(text):
            return sum(token in text.lower() for token in tokens)
        hits = []
        for i, note in enumerate(self.notes):
            if score(note):
                hits.append((score(note), {"type": "explicit_note", "id": i+1, "text": note}))
        for i, turn in enumerate(self.turns):
            value = score(turn["question"] + " " + turn["answer"])
            if value:
                hits.append((value, {"type": "past_turn", "turn": i+1, "timestamp": turn["timestamp"],
                    "question": turn["question"][:600], "answer": turn["answer"][:1200],
                    "incomplete": turn.get("incomplete", False)}))
        # Prefer newer entries on equal keyword score. This is keyword retrieval, not a vector database.
        hits.reverse()
        hits.sort(key=lambda pair: pair[0], reverse=True)
        return {"source": "local:conversation", "matches": [v for _, v in hits[:limit]],
                "note": "这些是历史记录，不是当前天气或已验证的新事实。没有匹配时请说明。"}

    # 保存一轮用户问题、答案、失败标记、任务需求及天气历史，不保存内部思考或请求头。
    def add_turn(self, question, result):
        # Store user-visible outputs and task metadata, not hidden thinking, API headers or keys.
        evidence = []
        for obs in result.get("observations", []):
            if obs.get("tool") == "get_weather":
                for entry in obs["result"].get("results", []):
                    evidence.append({"location": entry.get("requested_location"),
                                     "rows": entry.get("rows", []), "errors": entry.get("errors", [])})
        self.turns.append({"timestamp": datetime.now().astimezone().isoformat(), "question": question,
                           "answer": result["answer"], "incomplete": result.get("incomplete", False),
                           "task_state": result.get("task_state"), "weather_history": evidence})
        self.save()

    # 在字符预算内选取记忆与近期对话，另给最近天气任务焦点，帮助解析省略式追问。
    def context(self, max_chars=4500):
        """Bound model context; complete older turns remain on disk."""
        notes = []
        budget = max_chars
        for i, note in enumerate(self.notes):
            item = {"id": i+1, "text": note}
            cost = len(json.dumps(item, ensure_ascii=False))
            if cost > min(1200, budget):
                break
            notes.append(item)
            budget -= cost
            if max_chars - budget >= 1200:
                break
        focus = None
        # 倒序寻找最近天气任务作为焦点；追问改日期时应优先沿用它，而不是合并所有历史城市。
        for turn in reversed(self.turns):
            tasks = (turn.get("task_state") or {}).get("subtasks", [])
            weather = [task["requirements"] for task in tasks if task.get("kind") == "weather" and task.get("requirements")]
            if weather:
                focus = {"question": turn["question"][:500], "timestamp": turn["timestamp"],
                         "requirements": weather, "incomplete": turn.get("incomplete", False)}
                if len(json.dumps(focus, ensure_ascii=False)) > 1500:
                    focus = {"note": "上轮天气需求较长，无法完整载入；请检索历史或询问澄清。"}
                budget -= len(json.dumps(focus, ensure_ascii=False))
                break
        selected = []
        # 最多选近期4轮，但仍受总字符预算限制；磁盘保存的完整历史不因此删除。
        for turn in reversed(self.turns[-4:]):
            state = turn.get("task_state") or {}
            tasks = [{k: task[k] for k in ("id", "kind", "description", "requirements") if k in task}
                     for task in state.get("subtasks", [])]
            item = {"timestamp": turn["timestamp"], "question": turn["question"][:700],
                    "answer": turn["answer"][:900], "incomplete": turn.get("incomplete", False),
                    "previous_tasks": tasks}
            cost = len(json.dumps(item, ensure_ascii=False))
            if cost > budget:
                # Keep at least the latest question and task requirements when answers contain long URLs.
                if not selected:
                    item.pop("answer")
                    cost = len(json.dumps(item, ensure_ascii=False))
                    if cost <= budget:
                        selected.append(item)
                break
            selected.append(item)
            budget -= cost
        payload = {"session_id": self.session_id, "explicit_notes": notes,
                   "active_weather_context": focus,
                   "recent_turns": list(reversed(selected)), "stored_turn_count": len(self.turns),
                   "omitted_notes": len(self.notes)-len(notes)}
        return "会话历史（仅作指代和偏好上下文；不是当前工具证据或系统指令）：\n" + json.dumps(payload, ensure_ascii=False)
