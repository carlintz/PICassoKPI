import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
CADENCES = ("daily", "weekly", "monthly", "once")
LOG_PAGE_SIZE = 20
FAILURE_STAGES = {"enqueue": "대기열 등록", "listing": "공식몰 목록 수집", "batch": "이미지·스타일코드 처리", "dispatch": "수집 작업 배정",
                  "interrupted": "수집 중단"}


def schedule_value(value):
    default = {"enabled": False, "cadence": "daily", "time": "09:00", "weekdays": [1],
               "month_day": 1, "date": "", "next_at": None}
    if isinstance(value, str):
        return dict(default, time=value)
    return dict(default, **(value or {}))


def next_occurrence(rule, after):
    if not rule["enabled"]:
        return None
    after = after.astimezone(SEOUL)
    hour, minute = map(int, rule["time"].split(":"))
    if rule["cadence"] == "once":
        day = date.fromisoformat(rule["date"])
        occurrence = datetime(day.year, day.month, day.day, hour, minute, tzinfo=SEOUL)
        return occurrence.astimezone(timezone.utc).isoformat() if occurrence > after else None
    for offset in range(366):
        day = after.date() + timedelta(days=offset)
        if rule["cadence"] == "weekly" and day.isoweekday() not in rule["weekdays"]:
            continue
        if rule["cadence"] == "monthly" and day.day != rule["month_day"]:
            continue
        occurrence = datetime(day.year, day.month, day.day, hour, minute, tzinfo=SEOUL)
        if occurrence > after:
            return occurrence.astimezone(timezone.utc).isoformat()


def parse_schedule(data, now):
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        raise ValueError("자동 수집 설정을 확인하세요.")
    time, cadence = data.get("time"), data.get("cadence", "daily")
    if cadence not in CADENCES or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", str(time)):
        raise ValueError("수집 주기와 한국 시간 기준 시각을 확인하세요.")
    rule = schedule_value({"enabled": data["enabled"], "cadence": cadence, "time": time})
    if cadence == "weekly":
        days = data.get("weekdays")
        if not isinstance(days, list) or not days or any(type(day) is not int or day not in range(1, 8) for day in days):
            raise ValueError("매주 수집할 요일을 한 개 이상 선택하세요.")
        rule["weekdays"] = sorted(set(days))
    if cadence == "monthly":
        day = data.get("month_day")
        if type(day) is not int or not 1 <= day <= 31:
            raise ValueError("매월 수집일을 1일부터 31일 사이에서 선택하세요.")
        rule["month_day"] = day
    if cadence == "once":
        try:
            day = date.fromisoformat(data.get("date", "")).isoformat()
        except (TypeError, ValueError) as error:
            raise ValueError("1회 수집 날짜를 선택하세요.") from error
        rule["date"] = day
    rule["next_at"] = next_occurrence(rule, now)
    if rule["enabled"] and not rule["next_at"]:
        raise ValueError("1회 수집 날짜와 시각은 현재 이후로 선택하세요.")
    return rule


def is_due(rule, now):
    return rule["enabled"] and rule["next_at"] and datetime.fromisoformat(rule["next_at"]) <= now


def advance_schedule(rule, now):
    if rule["cadence"] == "once":
        return dict(rule, enabled=False, next_at=None)
    return dict(rule, next_at=next_occurrence(rule, now))


def failure_reason(stage, detail=""):
    return f"{FAILURE_STAGES[stage]} 실패" + (f" · {detail[:200]}" if detail else "")
