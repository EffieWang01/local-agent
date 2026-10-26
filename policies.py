# 天气证据保护规则：防止模型借普通对话直接编造预报；这些规则不是通用语义理解。
"""Conservative evidence guards for this project's weather capability, not general NLU."""
import re


# 保守识别历史回顾；出现重新查询或未来日期时，不直接当作只读回忆。
def historical_request(text):
    return bool(re.search(r"回顾|上次|刚才|之前|历史|查过", text)
                and not re.search(r"重新|最新|再查|现在|明天|后天", text))


# 组合天气主题与查询线索，排除定义、原理等问题；规则有表达覆盖边界。
def weather_request(text):
    """Require a weather subject plus a query/time cue; definitions are not forecasts."""
    if historical_request(text) or re.search(r"是什么|什么意思|定义|原理|区别|如何实现|代码|工具调用|翻译|这句话", text):
        return False
    subject = re.search(r"天气|气温|降水概率|下雨|降雨|温度|带伞|暖和|更热|更冷", text)
    query = re.search(r"今天|明天|后天|今日|明日|现在|未来|预报|查询|查|如何|怎么样|多少|更高|更低|相比|比较|知道", text)
    return bool(subject and query)


# 检查普通对话是否在生成新的天气预测；命中后应要求工具证据。
def weather_claim(answer):
    predictive = re.search(r"今天|明天|后天|今日|明日|当前|现在|未来|预计|预报|预测", answer)
    weather = re.search(r"天气|气温|温度|降水|降雨|下雨|晴天|多云|阴天|雨天|下雪|风速|[°℃]|摄氏|\d\s*度", answer)
    return bool(predictive and weather)
