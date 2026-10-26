# 会话与记忆验收

日期：2026-10-25。

## 功能

- 交互模式每轮保留问题、最终回答和任务需求，自动保存到.sessions/。
- --session创建或恢复、--resume仅恢复、--no-save禁用磁盘写入。
- /history、/memory、/remember、/forget、/clear、/new、/load、/sessions。
- 上下文预算内载入近期对话和显式记忆；recall_memory可关键词查找更早记录。
- active_weather_context提供最近天气任务焦点，辅助省略式追问。每轮独立创建TaskLedger与Observation编号，新天气重新查询。
- dialogue任务可处理问候、用户自述、历史回顾与澄清，无需查知识库。模型调用失败也保存为失败轮次。

## 测试

`python -X utf8 -m unittest -v`：29项通过。

真实模型＋固定天气工具数据；每轮之间重新从文件加载会话：

1. 我叫小林，请叫我小林。
2. 我叫什么？
3. 查上海今天最高气温。
4. 那武汉呢？
5. 改查明天。

- 云端deepseek-flash：5/5通过，见dialogue_evaluation_deepseek.json。
- 本地deepseek-r1:7b：完整测试4/5通过，第五轮错误添加了未请求城市。原始结果保留在dialogue_evaluation_ollama.json。
- 加入active_weather_context后，重放此前真实测试的前四轮历史，单独复测第五轮通过，见dialogue_followup_ollama.json。使用对应ISO日期与“明天”等价验收；没有再跑完整五轮，不能合并宣称新版本完整5/5。

最初本地模型还曾将姓名误归为知识检索、达到输出上限。补充对话示例后姓名两轮通过；这些情况说明存储机制正确并不能保证模型每次正确选择任务或理解指代。

## 边界

这是本地会话文件和有限上下文/关键词检索，不是训练模型或向量记忆。完整历史保存于磁盘，但不是全部同时送入模型。历史天气不是最新天气证据，跨轮知识Observation编号不复用。云端模式会把选定的历史发送到配置API服务。会话文件未加密，已加入.gitignore；同一会话不支持多进程并发写入。
