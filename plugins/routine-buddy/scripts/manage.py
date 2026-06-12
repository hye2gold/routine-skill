#!/usr/bin/env python3
"""routine-buddy 저장소 관리 CLI.

Claude가 /routine 스킬에서 호출해 루틴을 등록/완료/스누즈/삭제한다.
저장소는 홈 기준 경로(~/.claude/routine-buddy/routines.json)에 두어,
플러그인을 업데이트/재설치해도 사용자의 루틴 기록이 보존된다.
환경변수 ROUTINE_BUDDY_HOME 로 저장 위치를 덮어쓸 수 있다(테스트용).

지원 타입:
  recurring  N분마다            (--interval, --active-hours)
  daily      매일 HH:MM          (--at)
  weekly     매주 요일 HH:MM      (--at, --weekday 월,수,금)
  monthly    매달 N일 HH:MM       (--at, --day)
  yearly     매년 M/D HH:MM       (--at, --month, --day)
  oneshot    특정 날짜 1회        (--due, --remind-from)
"""
import argparse
import calendar
import json
import os
import sys
from datetime import datetime, timedelta


def data_dir():
    override = os.environ.get("ROUTINE_BUDDY_HOME")
    if override:
        d = os.path.abspath(os.path.expanduser(override))
    else:
        d = os.path.join(os.path.expanduser("~"), ".claude", "routine-buddy")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


STORE = os.path.join(data_dir(), "routines.json")


def load():
    try:
        with open(STORE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"routines": []}


def save(data):
    # atomic write: 임시 파일에 쓰고 교체 → 중간에 죽어도 routines.json 손상 방지
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STORE)


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def slugify(label):
    base = "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-")
    base = base[:20] or "routine"
    ids = {r["id"] for r in load()["routines"]}
    if base not in ids:
        return base
    i = 2
    while f"{base}-{i}" in ids:
        i += 1
    return f"{base}-{i}"


def parse_hours(s):
    a, b = s.split("-")
    return [int(a), int(b)]


WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
WD_KR = "월화수목금토일"


def parse_weekdays(s):
    out = []
    for part in s.replace(",", " ").split():
        key = part.strip().lower()
        if key not in WEEKDAYS:
            raise SystemExit(f"요일 파싱 실패 (mon..sun / 월..일): {part}")
        if WEEKDAYS[key] not in out:
            out.append(WEEKDAYS[key])
    return sorted(out)


def clamp_day(year, month, day):
    """그 달에 없는 날(예: 2월 31일)은 그 달 마지막 날로 보정."""
    return min(day, calendar.monthrange(year, month)[1])


def occurs_today(r, now):
    """오늘이 이 루틴의 '발생일'인가?"""
    t = r.get("type")
    if t == "daily":
        return True
    if t == "weekly":
        wds = r.get("weekdays")
        if wds is None and "weekday" in r:  # 구버전 호환
            wds = [r["weekday"]]
        return now.weekday() in (wds or [])
    if t == "monthly":
        return now.day == clamp_day(now.year, now.month, r.get("day", 1))
    if t == "yearly":
        return (now.month == r.get("month", 1)
                and now.day == clamp_day(now.year, now.month, r.get("day", 1)))
    return False


def slot_passed_today(r, at_str, now):
    """오늘이 발생일이고 그 시각이 이미 지났으면 True → 등록 시 오늘은 건너뜀."""
    if not occurs_today(r, now):
        return True
    hh, mm = map(int, at_str.split(":"))
    return now >= now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def find(data, rid):
    for r in data["routines"]:
        if r["id"] == rid:
            return r
    return None


TIME_ANCHORED = ("daily", "weekly", "monthly", "yearly")


def cmd_add(args):
    data = load()
    rid = args.id or slugify(args.label)
    if find(data, rid):
        print(f"이미 존재하는 id: {rid}", file=sys.stderr)
        sys.exit(1)
    now = datetime.now()
    r = {"id": rid, "label": args.label, "type": args.type, "active": True}

    if args.type == "recurring":
        r["interval_minutes"] = args.interval or 120
        if args.active_hours:
            r["active_hours"] = parse_hours(args.active_hours)
        r["last_done"] = now_iso()  # 등록 직후엔 한 주기 뒤부터

    elif args.type in TIME_ANCHORED:
        if not args.at:
            print(f"{args.type} 는 --at 'HH:MM' 가 필요합니다", file=sys.stderr)
            sys.exit(1)
        r["at"] = args.at
        if args.type == "weekly":
            if not args.weekday:
                print("weekly 는 --weekday (월..일, 콤마로 여러개) 가 필요합니다", file=sys.stderr)
                sys.exit(1)
            r["weekdays"] = parse_weekdays(args.weekday)
        elif args.type == "monthly":
            if args.day is None:
                print("monthly 는 --day (1-31) 가 필요합니다", file=sys.stderr)
                sys.exit(1)
            r["day"] = args.day
        elif args.type == "yearly":
            if args.day is None or args.month is None:
                print("yearly 는 --month (1-12) 와 --day (1-31) 가 필요합니다", file=sys.stderr)
                sys.exit(1)
            r["month"] = args.month
            r["day"] = args.day
        if slot_passed_today(r, args.at, now):
            r["last_done"] = now_iso()  # 오늘 슬롯 지났으면 다음 발생부터

    else:  # oneshot
        if not args.due:
            print("oneshot 은 --due 가 필요합니다", file=sys.stderr)
            sys.exit(1)
        r["due_at"] = args.due
        r["remind_from"] = args.remind_from or args.due
        r["acked"] = False

    data["routines"].append(r)
    save(data)
    print(f"등록됨: [{rid}] {args.label}")


def cmd_done(args):
    data = load()
    r = find(data, args.id)
    if not r:
        print(f"없는 id: {args.id}", file=sys.stderr)
        sys.exit(1)
    if r["type"] == "oneshot":
        r["acked"] = True
        r["active"] = False
        print(f"확인 완료(종료): [{r['id']}] {r['label']}")
    else:
        r["last_done"] = now_iso()
        r.pop("snooze_until", None)
        print(f"완료 처리(다음 차례로 리셋): [{r['id']}] {r['label']}")
    save(data)


def cmd_snooze(args):
    data = load()
    r = find(data, args.id)
    if not r:
        print(f"없는 id: {args.id}", file=sys.stderr)
        sys.exit(1)
    until = (datetime.now() + timedelta(minutes=args.minutes)).replace(microsecond=0)
    r["snooze_until"] = until.isoformat()
    save(data)
    print(f"{args.minutes}분 미룸: [{r['id']}] {r['label']} (until {until.strftime('%H:%M')})")


def cmd_remove(args):
    data = load()
    before = len(data["routines"])
    data["routines"] = [r for r in data["routines"] if r["id"] != args.id]
    if len(data["routines"]) == before:
        print(f"없는 id: {args.id}", file=sys.stderr)
        sys.exit(1)
    save(data)
    print(f"삭제됨: {args.id}")


def describe(r):
    t = r["type"]
    if t == "recurring":
        ah = r.get("active_hours")
        ah_s = f", {ah[0]}-{ah[1]}시" if ah else ""
        return f"매 {r.get('interval_minutes', 120)}분{ah_s}"
    if t == "daily":
        return f"매일 {r.get('at', '?')}"
    if t == "weekly":
        wds = r.get("weekdays") or ([r["weekday"]] if "weekday" in r else [])
        return f"매주 {''.join(WD_KR[w] for w in wds)} {r.get('at', '?')}"
    if t == "monthly":
        return f"매달 {r.get('day', '?')}일 {r.get('at', '?')}"
    if t == "yearly":
        return f"매년 {r.get('month', '?')}/{r.get('day', '?')} {r.get('at', '?')}"
    return f"{r.get('due_at', '?')} 까지"


def cmd_list(args):
    data = load()
    if not data["routines"]:
        print("등록된 루틴 없음")
        return
    for r in data["routines"]:
        status = "" if r.get("active", True) else " (비활성)"
        print(f"[{r['id']}] {r['label']} — {describe(r)}{status}")


def main():
    p = argparse.ArgumentParser(description="routine-buddy 관리 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--label", required=True)
    a.add_argument("--type",
                   choices=["recurring", "oneshot", "daily", "weekly", "monthly", "yearly"],
                   required=True)
    a.add_argument("--id")
    a.add_argument("--interval", type=int, help="recurring: 분 단위 주기 (기본 120)")
    a.add_argument("--active-hours", help="recurring: 알림 허용 시간대 '9-19'")
    a.add_argument("--at", help="daily/weekly/monthly/yearly: 시각 'HH:MM'")
    a.add_argument("--weekday", help="weekly: 요일 (월..일/mon..sun, 콤마로 여러개 '월,수,금')")
    a.add_argument("--day", type=int, help="monthly/yearly: 날짜 1-31")
    a.add_argument("--month", type=int, help="yearly: 월 1-12")
    a.add_argument("--due", help="oneshot: ISO 시각 2026-06-13T16:00")
    a.add_argument("--remind-from", help="oneshot: 이 시각부터 상기 (기본 due와 동일)")
    a.set_defaults(func=cmd_add)

    d = sub.add_parser("done")
    d.add_argument("id")
    d.set_defaults(func=cmd_done)

    s = sub.add_parser("snooze")
    s.add_argument("id")
    s.add_argument("minutes", type=int)
    s.set_defaults(func=cmd_snooze)

    rm = sub.add_parser("remove")
    rm.add_argument("id")
    rm.set_defaults(func=cmd_remove)

    ls = sub.add_parser("list")
    ls.set_defaults(func=cmd_list)

    pth = sub.add_parser("path")
    pth.set_defaults(func=lambda a: print(STORE))

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
