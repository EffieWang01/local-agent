# 文本 ReAct Agent

Python标准库实现，支持DeepSeek云端API和本地DeepSeek-R1 7B。两个后端共用任务状态和工具，不使用原生function calling。

## 运行

```powershell
# 云端API，默认deepseek-flash，读取.env
python -X utf8 agent.py --provider deepseek "今天上海和武汉谁温度更高"

# 本地Ollama，默认deepseek-r1:7b Q4_K_M，自动GPU
python -X utf8 agent.py --provider ollama "查询上海、武汉、北京今天和明天的最高气温和降水概率，比较最高气温"

# 本地CPU或替换模型
python -X utf8 agent.py --provider ollama --device cpu "ReAct是什么？"
python -X utf8 agent.py --provider ollama --model qwen2.5:1.5b "ReAct是什么？"
```

省略provider默认云端，不传问题进入交互模式，输入exit退出。Ollama须运行并提前下载模型：`ollama pull deepseek-r1:7b`。`--max-steps`默认12，范围1–40，每轮纠错或任务登记也计一步。

API支持.env中的OPENAI_API_KEY / OPENAI_BASE_URL，以及优先级更高的DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL；DEEPSEEK_MODEL或--model可替换云端模型。密钥不打印，.env不纳入Git。API会将问题和工具观察发送至配置服务端，并消耗余额。本地模式不调用云端模型，天气工具仍联网。

## 多轮对话与会话记忆

启动时不传问题，即可连续对话：

```powershell
python -X utf8 agent.py --provider ollama --session demo
# 或云端API，同一份会话可切换模型
python -X utf8 agent.py --provider deepseek --session demo
```

例如依次输入：`我叫小林` → `我叫什么` → `查上海今天最高气温` → `那武汉呢` → `改查明天`。模型会收到此前问题、回答和任务需求，用于理解省略的城市/日期；每轮创建新的任务清单，不受上一轮需求锁定影响。新天气请求必须重新查询，历史Observation编号不能作为本轮知识证据。

默认每次启动创建一个新会话，并显示会话名。`--session demo`创建或恢复指定会话；`--resume demo`只恢复已有会话，不存在时报错。单次问题也能接续：

```powershell
python -X utf8 agent.py --provider deepseek --session demo "查上海今天最高气温"
python -X utf8 agent.py --provider deepseek --resume demo "那武汉呢"
```

会话自动存入项目`.sessions/<会话名>.json`，已加入.gitignore。保存用户消息、最终回答、成功/失败状态、任务需求和天气历史，不保存模型内部思考、API密钥或请求头。云端模式会把选取的会话历史发送给配置的API服务。`--no-save`只在当前进程内记忆，不写磁盘；若显式指定已有会话，可读取它但不会写回。

| 命令 | 作用 |
| --- | --- |
| `/help` | 查看命令 |
| `/history` | 最近10轮对话（显示内容会截短） |
| `/memory` | 查看显式记忆条目 |
| `/remember 我喜欢简洁回答` | 增加一条显式记忆 |
| `/forget 1` | 删除第1条显式记忆 |
| `/clear` | 清空当前会话的全部对话和显式记忆，并写回文件 |
| `/new [名称]` | 创建空白会话，已有同名会话会拒绝覆盖 |
| `/load demo` | 恢复已有会话 |
| `/sessions` | 查看最近保存的会话名 |
| `exit` | 退出；已完成的轮次已自动保存 |

上下文自动选取最近最多4轮与部分显式记忆，约4500字符预算，完整历史仍存磁盘。提供只读`recall_memory(task_id,query)`关键词工具检索更早历史或未自动载入的记忆；它只可用于dialogue子任务，不产生当前天气证据。这是会话存储与关键词检索，不是修改模型权重或向量数据库。自然语言告知的个人信息保留在对话历史；重要偏好可用/remember显式保存。显式记忆最多20条，每条300字符；同一会话不支持多个进程同时写入。

上下文还包含active_weather_context，明确标记最近一次天气任务的地点、日期、指标及成功/失败状态，帮助“改查明天”沿用最近地点，而不是合并更早城市。它是指代线索，不替代新一轮工具查询，也不能保证模型每次解析正确。

新增dialogue任务及answer_dialogue动作，处理问候、个人自述、历史回顾和澄清，避免把“我叫什么”送去知识库。模型仍可能误分类或误解指代，需要检查实际执行；未知个人信息应询问，不能猜测。

验证脚本：`python -X utf8 evaluate_dialogue.py --provider deepseek`；本地可用`--provider ollama --steps 2`。默认使用固定天气数据、真实模型，每一轮之间重新从磁盘加载会话。结果见dialogue_evaluation_*.json。

## 新的天气工具参数

```json
{
  "action": "get_weather",
  "arguments": {
    "task_id": "w1",
    "locations": ["上海", "武汉", "北京"],
    "dates": ["今天", "明天"],
    "metrics": ["temperature_max", "rain_probability"]
  },
  "summary": "批量查询天气",
  "answer": ""
}
```

- locations：1–10个地点，支持中文或英文，工具内部逐个查询。返回requested_location和resolved_location，后者含省份、国家、经纬度。同名地点默认首个地理编码候选，同时列出其他候选；核对地区仍很重要。
- dates：1–7个日期，可用今天、明天、后天或YYYY-MM-DD。相对日期按每个地点当地日期解析，支持未来7天；范围外明确报错，不猜测。
- metrics：temperature_max（最高气温，°C）、temperature_min（最低气温，°C）、rain_probability（日最高降水概率，%）。查询范围是天气预报，不是当前实测气温。
- 每个地点独立返回rows和errors，行中包含请求日期、解析后的实际日期及指标值。某地点失败或某指标为null，不阻止其他地点成功，也不算已取得证据。

工具函数本身不需要task_id，ReAct调用时必须携带task_id。直接Python调用：`agent.get_weather(locations=["上海", "武汉"], dates=["今天"], metrics=["temperature_max"])`。

## 任务状态与ReAct循环

先调用set_tasks登记所有子任务，再对weather子任务调用set_weather_task。参数为task_id、完整locations、dates、metrics，以及operation和可选metric：

```json
{
  "action": "set_weather_task",
  "arguments": {
    "task_id": "w1",
    "locations": ["上海", "武汉", "北京"],
    "dates": ["今天", "明天"],
    "metrics": ["temperature_max", "rain_probability"],
    "operation": "compare_max",
    "metric": "temperature_max"
  },
  "summary": "登记完整需求",
  "answer": ""
}
```

operation支持report（列出数据）、compare_max（逐日选最高指标）、compare_min（逐日选最低指标）；后两者必须指定metric。温度比较采用哪个指标，会在最终报告明确列出。

程序按“地点 × 日期 × 指标”记录证据，不再匹配双城市固定问法。上述任务共12项数据，每次观察后打印TaskLedger，其中各天气子任务包含：

```text
requirements：锁定的全部需求，不能重设来删除缺失项
status：天气子任务collecting → ready；整体任务最终completed或failed
missing：缺少的地点、日期和指标
completed_cells：已取得的有效数据数量
errors：查询失败和缺失值记录（保留历史）
```

模型可一次查全部，也可先查一部分再补查。查询参数必须是已声明需求的子集。finish前检查所有数据，缺失时拒绝结束并反馈missing；失败可重试或调用fail如实报告。步数到上限也会报告未完成及状态。

天气数值报告和逐日最高/最低比较由Python根据证据生成，不使用模型自由编造的温度。比较时检查实际日期相同，跨时区“今天”对应不同日期时拒绝直接比较。复杂出游建议、体感温度等尚不支持确定性计算，不应宣称已完成这些判断。

**边界：**自然语言到任务的需求提取仍由模型负责。完整性校验针对已登记任务，不能证明模型一开始没有漏掉用户的城市、日期或子问题；请检查打印的任务清单和requirements。支持多个天气与知识子任务，不允许同一轮删改已锁定清单。没有通用事实验证。

## 知识工具与协议

search_knowledge使用arguments.task_id及arguments.query检索knowledge.json，可多次搜索。每个观察有本轮唯一编号。answer_knowledge用task_id、answer和evidence_ids登记知识回答，引用必须属于同一子任务。空检索强制显示未收录，失败检索不能作为回答证据。知识库很小，不是互联网搜索或向量数据库。

协议统一为action、arguments、summary、answer；不再使用顶层query。finish/fail的arguments为空。Ollama使用按动作区分的JSON Schema约束普通文本，DeepSeek使用JSON输出模式；均不发送tools、tool_choice、functions或function_call。JSON由Python校验后执行白名单函数，工具结果经普通消息反馈。schema约束格式，不保证模型决策正确。

ReAct核心是“模型选择动作→程序执行工具→观察结果回到上下文→模型继续或结束”；增加任务状态是在程序侧定义验收标准，不等于模型突然获得了可靠推理能力。

## 文件和验证

- agent.py：模型适配、动作协议和ReAct循环。
- tasks.py：通用子任务清单、知识证据及组合答案。
- prompts.py：简短完整示范，包括知识概念、天气补查和混合任务；数据是虚构示例。
- evaluate_react.py：固定题集，用真实模型和固定天气工具数据隔离模型决策问题。
- weather.py：批量天气工具、参数验证和WeatherTask证据状态。
- test_agent.py / test_weather.py：模型请求契约、状态完整性、补查、失败及日期测试。

```powershell
python -X utf8 -m unittest -v
```

单元测试用模拟数据，不代表真实模型答案质量。历史单城市和双城市记录见API_EVALUATION.md、LOCAL_R1_TEST.md；这些旧记录使用旧版工具参数，不能视为新协议验收结果。新版本测试记录见WEATHER_STATE_TEST.md。

本地适配器上下文8192、每轮最多生成2048 tokens，默认自动GPU，--device cpu强制CPU。截断会报错，思考文本不作为JSON动作执行。云端与本地是不同模型，不应期待能力相同。


## 子任务清单与混合问题

第一步的协议示例：

```json
{"action":"set_tasks","arguments":{"tasks":[{"id":"w1","kind":"weather","description":"查上海今天最高气温"},{"id":"k1","kind":"knowledge","description":"解释ReAct"}]},"summary":"登记所有子任务","answer":""}
```

没有用“温度”等关键词强制路由天气，模型按语义登记任务。“温度是什么”可登记knowledge。任务清单冻结后不能删除子任务。天气任务ready且每个知识任务均answer_knowledge后，finish才组合所有结果；不会再以天气报告覆盖知识部分。

```json
{"action":"search_knowledge","arguments":{"task_id":"k1","query":"ReAct"},"summary":"检索","answer":""}
{"action":"answer_knowledge","arguments":{"task_id":"k1","answer":"依据实际检索内容回答","evidence_ids":[2]},"summary":"登记知识答案","answer":""}
{"action":"finish","arguments":{},"summary":"完成全部子任务","answer":""}
```

其中2必须是本轮真实Observation编号，不能引用教学示例编号或其他子任务的证据。程序只验证来源关联与覆盖，不能自动证明知识回答语义正确。

## 固定评测

```powershell
python -X utf8 evaluate_react.py --provider deepseek
python -X utf8 evaluate_react.py --provider ollama
# 仅测试两道题
python -X utf8 evaluate_react.py --provider ollama --cases concept mixed
# 如需实际天气网络请求
python -X utf8 evaluate_react.py --provider ollama --live-tools
```

默认使用真实模型＋固定天气数据＋真实本地知识库，模型调用仍有实际成本（云端消耗余额，本地消耗算力）。覆盖知识概念、混合任务、多城市多日期、知识无匹配、工具失败。输出evaluation_deepseek.json或evaluation_ollama.json，每题完成后保存，记录完整轨迹、校验错误次数、模型/工具调用次数和结构检查结果。不是标准基准，不验证所有知识回答的语义正确性，也不能据少量通过项推导通用成功率。合理的不同子任务拆分应按覆盖范围判定，而非强制固定任务数量。

模型上下文使用精简Observation：去掉天气候选列表和长URL；missing最多展示12项并带总数；完整结果仍留在程序和终端。加入完整轨迹示例的方式参考ReAct官方实现思路，未照抄论文示例，也不要求输出详细内部推理。

动作还受到当前状态限制：初始仅可set_tasks；知识任务检索前不可answer_knowledge；子任务均有结果才可finish。Ollama按状态动态缩小JSON Schema中的动作和task_id范围；云端使用相同状态提示与程序校验。它属于文本输出约束，仍未开启原生function calling。登记需求的遗漏、错误分类和知识答案的语义错误仍可能发生。

天气证据保护：针对明确的天气查询，若模型误登记为dialogue，程序会将对应子任务升级为weather；若普通对话尝试生成新的天气预报，也会拒绝该答案并升级证据要求。升级仅增加校验，不能把weather降回dialogue来跳过查询。定义类问题（如“温度是什么”）和历史回顾不因此强制查询天气。这是当前天气能力的保守规则，不能覆盖所有语言表达，不等于通用意图识别或事实验证。

日志新增Decision显示每轮所选动作；只有实际执行工具才出现Action与Observation，结尾会列出“本轮实际工具调用”。模型编写answer_dialogue不等于查询了天气。

官方参考：https://github.com/ysymyth/ReAct/blob/master/hotpotqa.ipynb 、 https://github.com/ysymyth/ReAct/blob/master/prompts/prompts_naive.json
