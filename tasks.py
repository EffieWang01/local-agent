# 子任务账本：把混合问题拆成天气、知识、普通对话，并分别校验后组合最终答案。
"""Task ledger: independent weather/knowledge subtasks, with evidence and completion."""
import re
from weather import WeatherTask


# 总账本：一个用户问题可以同时含多个天气、知识和对话子任务。
class TaskLedger:
    # 校验唯一任务ID和类型；每类任务使用各自的证据与回答状态。
    def __init__(self, tasks):
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 8:
            raise ValueError("tasks必须为1至8个子任务")
        self.tasks = {}
        self.weather = {}
        self.knowledge = {}
        self.dialogue = {}
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {"id", "kind", "description"}:
                raise ValueError("每个子任务必须包含id/kind/description")
            ident = task["id"]
            if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,23}", ident):
                raise ValueError("任务id必须是字母开头的简短英文标识")
            if ident in self.tasks or task["kind"] not in ("weather", "knowledge", "dialogue"):
                raise ValueError("任务id不可重复，kind仅为weather/knowledge/dialogue")
            if not isinstance(task["description"], str) or not 1 <= len(task["description"].strip()) <= 500:
                raise ValueError("description必须是非空简短需求")
            self.tasks[ident] = dict(task)
            if task["kind"] == "knowledge":
                self.knowledge[ident] = {"observations": {}, "answer": None, "evidence_ids": []}
            elif task["kind"] == "dialogue":
                self.dialogue[ident] = None

    # 登记普通对话回答；新天气预测须先经过agent.py的天气证据保护。
    def answer_dialogue(self, task_id, answer):
        self.require(task_id, "dialogue")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("对话回答不能为空")
        self.dialogue[task_id] = answer

    # 只把普通对话升级为需要天气证据的任务，不允许反向降级来绕过工具。
    def require_weather_evidence(self, task_id):
        """Only escalate evidence requirements; never delete or weaken a task."""
        self.require(task_id, "dialogue")
        del self.dialogue[task_id]
        self.tasks[task_id]["kind"] = "weather"

    # 校验task_id存在且属于正确类型，防止工具结果关联到错误任务。
    def require(self, task_id, kind):
        if task_id not in self.tasks or self.tasks[task_id]["kind"] != kind:
            raise ValueError(f"task_id必须指向已登记的{kind}子任务")

    # 为天气子任务登记完整需求；登记后不能重设以删除缺失项。
    def register_weather(self, task_id, **arguments):
        self.require(task_id, "weather")
        if task_id in self.weather:
            raise ValueError("天气需求已锁定，不可重设缩减")
        self.weather[task_id] = WeatherTask(**arguments)

    # 按实际Observation编号保存此知识子任务的检索结果。
    def record_knowledge(self, task_id, observation_id, result):
        self.require(task_id, "knowledge")
        self.knowledge[task_id]["observations"][observation_id] = result

    # 检查引用是否属于当前子任务；空检索只能报告未收录，不能作为编造答案的许可。
    def answer_knowledge(self, task_id, answer, evidence_ids):
        self.require(task_id, "knowledge")
        state = self.knowledge[task_id]
        if (not isinstance(evidence_ids, list) or not evidence_ids
                or any(type(i) is not int or i not in state["observations"] for i in evidence_ids)):
            raise ValueError("evidence_ids必须引用此子任务实际得到的Observation编号")
        # 先验证引用编号归属，再读取证据；不能拿其他子任务或教学示例的编号冒充。
        results = [state["observations"][i] for i in evidence_ids]
        if any("error" in r for r in results):
            raise ValueError("失败的检索不能用作知识回答证据，请重试或fail")
        matched = [r for r in state["observations"].values() if r.get("matches")]
        # 无检索匹配时由程序生成“未收录”，不接受模型借空结果编造解释。
        if not matched:
            # An empty search is an explicit unavailable answer, not permission to invent facts.
            answer = "本地知识库未检索到相关内容，无法依据现有证据回答此子问题。"
        elif not any(r.get("matches") for r in results):
            raise ValueError("已有匹配结果，不能只引用空结果来跳过回答")
        elif not isinstance(answer, str) or not answer.strip():
            raise ValueError("知识子任务必须提供非空answer")
        state.update(answer=answer, evidence_ids=list(dict.fromkeys(evidence_ids)))

    # 汇总所有子任务的状态；compact只压缩展示，不删除真实证据。
    def snapshot(self, compact=False):
        result = []
        for ident, task in self.tasks.items():
            item = dict(task)
            # 各类任务的完成条件不同：天气验收数据格，知识验收引用及答案，对话验收回应。
            if task["kind"] == "weather":
                if ident not in self.weather:
                    item["status"] = "needs_requirements"
                else:
                    state = self.weather[ident].snapshot()
                    if compact:
                        state["missing_count"] = len(state["missing"])
                        state["missing"] = state["missing"][:12]
                        state["errors"] = state["errors"][-3:]
                    item.update(state)
            elif task["kind"] == "knowledge":
                state = self.knowledge[ident]
                item.update(status="answered" if state["answer"] is not None else "needs_answer",
                            observation_ids=list(state["observations"]), evidence_ids=state["evidence_ids"])
            else:
                item["status"] = "answered" if self.dialogue[ident] is not None else "needs_answer"
            result.append(item)
        return {"subtasks": result}

    # 逐个验收并组合所有答案；任一子任务未完成时禁止全局finish。
    def answer(self):
        sections = []
        for ident, task in self.tasks.items():
            if task["kind"] == "weather":
                if ident not in self.weather:
                    raise ValueError(f"子任务{ident}尚未登记天气需求")
                text = self.weather[ident].answer()
            elif task["kind"] == "knowledge":
                state = self.knowledge[ident]
                if state["answer"] is None:
                    raise ValueError(f"知识子任务{ident}尚未answer_knowledge，不能跳过")
                text = state["answer"] + "\n依据：" + "、".join(f"Observation {i}" for i in state["evidence_ids"])
            else:
                if self.dialogue[ident] is None:
                    raise ValueError(f"对话子任务{ident}尚未回答")
                text = self.dialogue[ident]
            sections.append(task["description"] + "\n" + text)
        return "\n\n".join(sections)
