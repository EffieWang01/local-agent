# DeepSeek-R1 7B 4bit 本机测试

测试日期：2026-10-25。

- Ollama 模型：`deepseek-r1:7b`，对应 DeepSeek-R1-Distill-Qwen-7B。
- 本地元数据：7.6B 参数、GGUF、Q4_K_M（4bit）量化。
- 模型 ID：755ced02ce7b；下载约4.7GB。
- 显卡：RTX 5060 Laptop GPU，8151 MiB 显存。
- 推理上下文：4096 tokens；单请求；num_predict上限1536。
- Ollama 运行状态：100% GPU，模型运行占用显示4.8GB。
- nvidia-smi显存总占用：4652 MiB（包含其他程序，不等同于模型单独占用）。

| 请求 | 总耗时 | 加载耗时 | 生成速度 | 结果 |
| --- | --- | --- | --- | --- |
| 17×23 | 9.02秒 | 3.42秒 | 60.58 token/s | 正确给出391，但未遵守只输出结果的要求 |
| 中文解释Agent中模型和工具的分工 | 15.09秒 | 0.01秒 | 63.78 token/s | 中文生成成功，但概念解释不够准确，不能算知识质量测试通过 |

结论：该模型在本机可通过Ollama本地HTTP API正常加载、完成生成，测试配置下使用GPU推理。未测试长上下文、并发和ReAct工具任务；不能从这两项推断Agent可靠性。

## 使用

```powershell
ollama run deepseek-r1:7b
```

已有Ollama服务地址：`http://localhost:11434`；聊天接口为`POST /api/chat`。

复测（不重复下载）：

```powershell
python -X utf8 test_local_r1.py
```

完整输出、时间和硬件状态见`local_r1_test_results.json`。下载测试脚本通过Ollama HTTP API调用模型，不使用云端DeepSeek API，也未使用原生function calling。

注意：现有`agent.py`的Ollama适配器仍显式设置`num_gpu=0`，因此用它运行会强制CPU；上述GPU成绩来自`test_local_r1.py`的本地服务测试。此任务未修改既有Agent的默认DeepSeek云端API配置。
