# 批量天气工具与天气证据状态。HTTP取数和数值比较由Python完成，模型只决定查询参数。
"""Batch weather tool and evidence-based task state (no model dependencies)."""
from datetime import date, timedelta
from itertools import product
import math
import urllib.parse

# 内部指标名映射到天气API字段、中文名称和单位，避免展示时丢失数值含义。
METRICS = {"temperature_max": ("temperature_2m_max", "最高气温", "°C"),
           "temperature_min": ("temperature_2m_min", "最低气温", "°C"),
           "rain_probability": ("precipitation_probability_max", "降水概率", "%")}


# 检查地点、日期、指标列表与数量上限；不支持的指标和日期格式应明确拒绝。
def validate_arguments(locations, dates, metrics):
    for label, values, limit in [("locations", locations, 10), ("dates", dates, 7), ("metrics", metrics, 3)]:
        if (not isinstance(values, list) or not 1 <= len(values) <= limit
                or any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in values)):
            raise ValueError(f"{label} 必须是1至{limit}个非空字符串的列表")
        if len(set(values)) != len(values):
            raise ValueError(f"{label} 不能有重复值")
    if any(m not in METRICS for m in metrics):
        raise ValueError("metrics 仅支持 " + ", ".join(METRICS))
    for value in dates:
        if value not in ("今天", "明天", "后天"):
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("日期格式必须为YYYY-MM-DD")


# 按地点逐个查询地理编码与预报；某地点失败独立记录，不能拖累其余地点。
def get_weather(locations, dates, metrics, *, request_json):
    validate_arguments(locations, dates, metrics)
    aliases = {"北京": "Beijing", "上海": "Shanghai", "武汉": "Wuhan", "杭州": "Hangzhou",
               "深圳": "Shenzhen", "广州": "Guangzhou", "成都": "Chengdu"}
    results = []
    for requested_location in locations:
        entry = {"requested_location": requested_location, "rows": [], "errors": []}
        try:
            name = requested_location.strip().removesuffix("市")
            geo_url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
                {"name": aliases.get(name, name), "count": 3, "language": "zh"})
            candidates = request_json(geo_url).get("results", [])
            if not candidates:
                raise ValueError("找不到地点，请补充地区或英文名称")
            # 当前采用首个地点候选，可能有同名歧义；因此一并返回地区和其他候选供核对。
            place = candidates[0]
            entry["resolved_location"] = {k: place.get(k) for k in
                ("name", "country", "admin1", "latitude", "longitude")}
            entry["other_candidates"] = [{k: p.get(k) for k in ("name", "country", "admin1")}
                                         for p in candidates[1:]]
            url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
                "latitude": place["latitude"], "longitude": place["longitude"],
                "daily": ",".join(METRICS[m][0] for m in metrics), "timezone": "auto", "forecast_days": 7})
            data = request_json(url)
            entry.update(source=url, timezone=data["timezone"])
            daily = data["daily"]
            # timezone=auto下，预报首日作为地点当地“今天”，不是直接用电脑日期替代。
            today = date.fromisoformat(daily["time"][0])
            for selector in dates:
                target = ((today + timedelta(days={"今天": 0, "明天": 1, "后天": 2}[selector])).isoformat()
                          if selector in ("今天", "明天", "后天") else selector)
                if target not in daily["time"]:
                    entry["errors"].append({"requested_date": selector, "error": "日期不在未来7天预报范围内"})
                    continue
                index = daily["time"].index(target)
                row = {"requested_date": selector, "date": target, "values": {}}
                for metric in metrics:
                    values = daily.get(METRICS[metric][0], [])
                    value = values[index] if index < len(values) else None
                    # JSON中的null或非有限值不能算有效天气数据；布尔值虽是Python整数子类也应排除。
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                        row["values"][metric] = value
                    else:
                        entry["errors"].append({"requested_date": selector, "metric": metric, "error": "数据缺失"})
                entry["rows"].append(row)
        except Exception as exc:
            entry["errors"].append({"error": str(exc)})
        results.append(entry)
    return {"results": results, "units": {m: METRICS[m][2] for m in metrics},
            "note": "相对日期按各地点当地日期解释。resolved_location为首个地理编码候选，需核对同名地点。"}


# 以“地点、请求日期、指标”为键记录证据；数据齐全后才能输出天气结论。
class WeatherTask:
    # 锁定需求和比较指标，避免模型通过缩减需求把未完成任务说成完成。
    def __init__(self, locations, dates, metrics, operation="report", metric=None):
        validate_arguments(locations, dates, metrics)
        if operation not in ("report", "compare_max", "compare_min"):
            raise ValueError("operation 必须为 report/compare_max/compare_min")
        if operation != "report" and metric not in metrics:
            raise ValueError("比较所用metric必须包含在metrics中")
        self.requirements = {"locations": locations[:], "dates": dates[:], "metrics": metrics[:],
                             "operation": operation, "metric": metric}
        self.evidence = {}
        self.errors = []
        self.status = None

    # 允许整批查询或对子集补查，但不允许偷偷更换已登记的地点、日期和指标。
    def validate_call(self, arguments):
        if set(arguments) != {"locations", "dates", "metrics"}:
            raise ValueError("get_weather仅接受locations、dates、metrics")
        validate_arguments(arguments["locations"], arguments["dates"], arguments["metrics"])
        for field in ("locations", "dates", "metrics"):
            if not set(arguments[field]) <= set(self.requirements[field]):
                raise ValueError(f"{field} 必须是已声明任务的子集；不可替换任务对象")

    # 合并新观察；空值、布尔值和非有限数值不计入有效证据，错误保留以供诊断。
    def update(self, result):
        if "error" in result:
            self.errors.append({"error": result["error"]})
        for entry in result.get("results", []):
            location = entry["requested_location"]
            self.errors.extend({"location": location, **error} for error in entry.get("errors", []))
            for row in entry.get("rows", []):
                for metric, value in row["values"].items():
                    # 证据键包含地点、日期、指标，避免把上海明天的数据误当成武汉今天的数据。
                    key = (location, row["requested_date"], metric)
                    if (location in self.requirements["locations"] and row["requested_date"] in self.requirements["dates"]
                            and metric in self.requirements["metrics"] and isinstance(value, (int, float))
                            and not isinstance(value, bool) and math.isfinite(value)):
                        self.evidence[key] = {"date": row["date"], "value": value,
                            "source": entry.get("source", ""), "resolved_location": entry.get("resolved_location", {})}

    # 计算需求笛卡尔积并找出缺失项；ready仅代表登记的数据齐全。
    def snapshot(self):
        # 笛卡尔积得到所有必须覆盖的数据格；例如3地点×2日期×2指标等于12项。
        required = product(*(self.requirements[k] for k in ("locations", "dates", "metrics")))
        missing = [dict(zip(("location", "date", "metric"), key)) for key in required if key not in self.evidence]
        return {"requirements": self.requirements, "status": self.status or ("ready" if not missing else "collecting"),
                "missing": missing, "completed_cells": len(self.evidence), "errors": self.errors}

    # 只使用程序中的真实证据生成报告、按日比较；检查日期一致，避免模型编造数值。
    def answer(self):
        state = self.snapshot()
        if state["missing"]:
            raise ValueError("任务数据不齐，禁止finish。请按TaskState.missing补查；无法补齐时使用fail如实报告。")
        req = self.requirements
        lines = ["以下为已核验的天气预报数据（并非实时实测气温）："]
        sources = set()
        for selector in req["dates"]:
            dates = {self.evidence[(loc, selector, req["metrics"][0])]["date"] for loc in req["locations"]}
            if req["operation"] != "report" and len(dates) != 1:
                raise ValueError("各地点对应日期不一致，不能直接比较；请用明确ISO日期重新声明任务。")
            for location in req["locations"]:
                if len({self.evidence[(location, selector, m)]["date"] for m in req["metrics"]}) != 1:
                    raise ValueError("同一地点的指标日期不一致，请重新查询全部指标")
                parts = []
                for metric in req["metrics"]:
                    cell = self.evidence[(location, selector, metric)]
                    parts.append(f"{METRICS[metric][1]}{cell['value']:g}{METRICS[metric][2]}")
                    sources.add(cell["source"])
                cell = self.evidence[(location, selector, req["metrics"][0])]
                resolved = cell["resolved_location"]
                label = "/".join(str(resolved[k]) for k in ("country", "admin1", "name") if resolved.get(k))
                lines.append(f"{location}（{label}），{cell['date']}（{selector}）：" + "，".join(parts))
            if req["operation"] != "report":
                metric = req["metric"]
                values = {loc: self.evidence[(loc, selector, metric)]["value"] for loc in req["locations"]}
                # 排序和最大最小值由Python计算，不让模型凭语言印象决定。
                best = (max if req["operation"] == "compare_max" else min)(values.values())
                winners = [loc for loc, value in values.items() if value == best]
                lines.append(f"{selector}按{METRICS[metric][1]}比较：" + "、".join(winners)
                             + ("最高" if req["operation"] == "compare_max" else "最低")
                             + ("（并列）。" if len(winners) > 1 else "。"))
        return "\n".join(lines + ["来源：" + "；".join(sorted(sources))])
