#!/usr/bin/env python3
"""routine-buddy 저장소 관리 CLI.

Claude가 /routine 스킬에서 호출해 루틴을 등록/완료/스누즈/삭제한다.
저장소는 홈 기준 경로(~/.claude/routine-buddy/routines.json)에 두어,
플러그인을 업데이트/재설치해도 사용자의 루틴 기록이 보존된다.
환경변수 ROUTINE_BUDDY_HOME 로 저장 위치를 덮어쓸 수 있다(테스트용).
"""
import argparse
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


def parse_weekday(s):
    key = s.strip().lower()
    if key in WEEKDAYS:
        return WEEKDAYS[key]
    raise SystemExit(f"요일 파싱 실패 (mon..sun / 월..일): {s}")


def already_passed_today(at_str, weekday=None):
    """오늘 그 시각 슬롯이 이미 지났으면 True → 등록 시 오늘은 건너뛰고 다음부터."""
    hh, mm = map(int, at_str.split(":"))
    now = datetime.now()
    if weekday is not None and now.weekday() != weekday:
        return True  # 오늘이 그 요일이 아니면 오늘 슬롯 없음
    return now >= now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def find(data, rid):
    for r in data["routines"]:
        if r["id"] == rid:
            return r
    return None


def cmd_add(args):
    data = load()
    rid = args.id or slugify(args.label)
    if find(data, rid):
        print(f"이미 존재하는 id: {rid}", file=sys.stderr)
        sys.exit(1)
    r = {"id": rid, "label": args.label, "type": args.type, "active": True}
    if args.type == "recurring":
        r["interval_minutes"] = args.interval or 120
        if args.active_hours:
            r["active_hours"] = parse_hours(args.active_hours)
        r["last_done"] = now_iso()  # 등록 직후엔 한 주기 뒤부터 알림
    elif args.type == "daily":
        if not args.at:
            print("daily 는 --at 'HH:MM' 가 필요합니다", file=sys.stderr)
            sys.exit(1)
        r["at"] = args.at
        if already_passed_today(args.at):
            r["last_done"] = now_iso()  # 오늘 시각 지났으면 내일부터
    elif args.type == "weekly":
        if not args.at or args.weekday is None:
            print("weekly 는 --at 'HH:MM' 와 --weekday 가 필요합니다", file=sys.stderr)
            sys.exit(1)
        r["at"] = args.at
        r["weekday"] = parse_weekday(args.weekday)
        if already_passed_today(args.at, r["weekday"]):
            r["last_done"] = now_iso()
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
    if r["type"] in ("recurring", "daily", "weekly"):
        r["last_done"] = now_iso()
        r.pop("snooze_until", None)
        print(f"완료 처리(다음 주기로 리셋): [{r['id']}] {r['label']}")
    else:
        r["acked"] = True
        r["active"] = False
        print(f"확인 완료(종료): [{r['id']}] {r['label']}")
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


def cmd_list(args):
    data = load()
    if not data["routines"]:
        print("등록된 루틴 없음")
        return
    for r in data["routines"]:
        status = "" if r.get("active", True) else " (비활성)"
        t = r["type"]
        if t == "recurring":
            ah = r.get("active_hours")
            ah_s = f", {ah[0]}-{ah[1]}시" if ah else ""
            print(f"[{r['id']}] {r['label']} — 매 {r.get('interval_minutes', 120)}분{ah_s}{status}")
        elif t == "daily":
            print(f"[{r['id']}] {r['label']} — 매일 {r.get('at', '?')}{status}")
        elif t == "weekly":
            wd = "월화수목금토일"[r.get("weekday", 0)]
            print(f"[{r['id']}] {r['label']} — 매주 {wd} {r.get('at', '?')}{status}")
        else:
            print(f"[{r['id']}] {r['label']} — {r.get('due_at', '?')} 까지{status}")


def main():
    p = argparse.ArgumentParser(description="routine-buddy 관리 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--label", required=True)
    a.add_argument("--type", choices=["recurring", "oneshot", "daily", "weekly"], required=True)
    a.add_argument("--id")
    a.add_argument("--interval", type=int, help="recurring: 분 단위 주기 (기본 120)")
    a.add_argument("--active-hours", help="recurring: 알림 허용 시간대 '9-19'")
    a.add_argument("--at", help="daily/weekly: 시각 'HH:MM' (예 10:30)")
    a.add_argument("--weekday", help="weekly: 요일 mon..sun 또는 월..일")
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

    r = sub.add_parser("remove")
    r.add_argument("id")
    r.set_defaults(func=cmd_remove)

    l = sub.add_parser("list")
    l.set_defaults(func=cmd_list)

    pth = sub.add_parser("path")  # 디버그: 저장소 위치 출력
    pth.set_defaults(func=lambda a: print(STORE))

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
