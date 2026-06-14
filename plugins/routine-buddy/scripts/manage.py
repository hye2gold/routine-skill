#!/usr/bin/env python3
"""routine-buddy 저장소 관리 CLI.

Claude가 /routine 스킬에서 호출해 루틴을 등록/완료/스누즈/삭제한다.
저장소는 홈 기준 경로(~/.claude/routine-buddy/routines.json)에 두어,
플러그인을 업데이트/재설치해도 사용자의 루틴 기록이 보존된다.
환경변수 ROUTINE_BUDDY_HOME 로 저장 위치를 덮어쓸 수 있다(테스트용).

지원 타입:
  recurring  N분마다            (--interval, --active-hours)
  session    Claude Code 연속 작업 N분째 (--after, --idle-reset)
  daily      매일 HH:MM          (--at)
  weekly     매주 요일 HH:MM      (--at, --weekday 월,수,금)
  monthly    매달 N일 HH:MM       (--at, --day)
  yearly     매년 M/D HH:MM       (--at, --month, --day)
  oneshot    특정 날짜 1회        (--due, --remind-from)
"""
import argparse
import calendar
import contextlib
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

STYLE_PRESETS = {
    "natural": "평소 대화 흐름에 맞춰 자연스럽고 짧게 챙긴다.",
    "warm": "챙겨주는 듯한 따뜻한 말투로, 부담 주지 않고 다정하게 묻는다. 문장 끝에 귀여운 이모지를 가볍게 곁들인다.",
    "urgent": "조금 쪼아대는 긴급한 말투로, 지금 바로 하게 만드는 느낌을 준다.",
    "calm": "차분하고 담백한 말투로, 과장 없이 조용히 상기한다.",
    "playful": "가볍고 장난기 있는 말투로, 너무 딱딱하지 않게 챙긴다.",
}

ONBOARDING_INITIAL = {"completed": False, "step": "tone"}


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
LOCK = STORE + ".lock"
ID_RE = re.compile(r"^[\w가-힣][\w가-힣-]{0,63}$")


def empty_store():
    return {
        "routines": [],
        "settings": {"tone": "natural", "tone_instruction": STYLE_PRESETS["natural"]},
        "onboarding": dict(ONBOARDING_INITIAL),
    }


def load():
    try:
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return empty_store()
    except json.JSONDecodeError as e:
        raise SystemExit(f"저장소 JSON 파싱 실패: {STORE} ({e})")
    if not isinstance(data, dict) or not isinstance(data.get("routines"), list):
        raise SystemExit(f"저장소 형식 오류: {STORE}")
    settings = data.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    tone = settings.get("tone") if settings.get("tone") in STYLE_PRESETS or settings.get("tone") == "custom" else "natural"
    instruction = settings.get("tone_instruction") if isinstance(settings.get("tone_instruction"), str) else STYLE_PRESETS["natural"]
    data["settings"] = {"tone": tone, "tone_instruction": instruction}
    onboarding = data.get("onboarding")
    if not isinstance(onboarding, dict):
        completed = bool(data.get("routines")) or tone != "natural"
        onboarding = {"completed": completed, "step": "done" if completed else "tone"}
    if onboarding.get("completed"):
        onboarding["completed"] = True
        onboarding["step"] = "done"
    elif onboarding.get("step") not in {"tone", "preseed", "preseed_details", "cancel_ack"}:
        onboarding["step"] = "tone"
    data["onboarding"] = onboarding
    return data


def save(data):
    # atomic write: unique tmp에 쓰고 교체 → 중간에 죽어도 routines.json 손상 방지
    fd, tmp = tempfile.mkstemp(prefix="routines.", suffix=".tmp", dir=data_dir(), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(STORE):
            shutil.copy2(STORE, STORE + ".bak")
        os.replace(tmp, STORE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def locked_store():
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(LOCK, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = load()
        try:
            yield data
        except BaseException:
            raise
        else:
            save(data)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def validate_id(rid):
    if not ID_RE.fullmatch(rid):
        raise SystemExit("id는 1~64자의 한글/영문/숫자/_/- 만 사용할 수 있습니다")
    return rid


def parse_iso_local(s, field):
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        raise SystemExit(f"{field}는 ISO 시각이어야 합니다: 2026-06-13T16:00")
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(microsecond=0)


def parse_time(s):
    try:
        parts = s.split(":")
        if len(parts) != 2:
            raise ValueError
        hh, mm = map(int, parts)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
        return hh, mm
    except Exception:
        raise SystemExit("--at 은 HH:MM 24시간제여야 합니다 (예: 09:30)")


def parse_positive_minutes(value, name):
    if value is None:
        return None
    if value <= 0:
        raise SystemExit(f"{name}는 1분 이상이어야 합니다")
    return value


def sanitize_custom_tone(text):
    text = (text or "").strip()
    if not text:
        raise SystemExit("--custom 값은 비어 있을 수 없습니다")
    return text[:200]  # 컨텍스트에 raw 주입되므로 길이 상한


def tone_config(tone=None, custom=None, fallback=None):
    if custom:
        return "custom", sanitize_custom_tone(custom)
    if tone:
        return tone, STYLE_PRESETS[tone]
    fallback = fallback if isinstance(fallback, dict) else {}
    base_tone = fallback.get("tone")
    base_instruction = fallback.get("tone_instruction")
    if base_tone in STYLE_PRESETS and isinstance(base_instruction, str):
        return base_tone, base_instruction
    if base_tone == "custom" and isinstance(base_instruction, str):
        return "custom", base_instruction
    return "natural", STYLE_PRESETS["natural"]


def validate_day(day):
    if day is None or not (1 <= day <= 31):
        raise SystemExit("--day 는 1~31 사이여야 합니다")
    return day


def validate_month(month):
    if month is None or not (1 <= month <= 12):
        raise SystemExit("--month 는 1~12 사이여야 합니다")
    return month


def default_remind_from(due):
    # 특정날 알림은 이벤트 정각보다 먼저 챙겨야 자연스럽다.
    start = due.replace(hour=9, minute=0, second=0, microsecond=0)
    return min(start, due)


def write_cli_path(manage_path):
    path = os.path.join(data_dir(), "cli.json")
    fd, tmp = tempfile.mkstemp(prefix="cli.", suffix=".tmp", dir=data_dir(), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"manage": manage_path}, f, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def init_store():
    with locked_store() as data:
        data.setdefault("routines", [])
        data.setdefault("settings", {"tone": "natural", "tone_instruction": STYLE_PRESETS["natural"]})
        data.setdefault("onboarding", dict(ONBOARDING_INITIAL))


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def slugify(label, data=None):
    base = "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-")
    base = base[:20] or "routine"
    ids = {r["id"] for r in (data or load())["routines"]}
    if base not in ids:
        return base
    i = 2
    while f"{base}-{i}" in ids:
        i += 1
    return f"{base}-{i}"


def parse_hours(s):
    try:
        a, b = s.split("-")
        lo, hi = int(a), int(b)
    except Exception:
        raise SystemExit("--active-hours 는 '9-19' 형식이어야 합니다")
    if not (0 <= lo <= 23 and 0 <= hi <= 24):
        raise SystemExit("--active-hours 는 0-24 범위여야 합니다")
    if lo == hi:
        raise SystemExit("--active-hours 시작/끝은 달라야 합니다")
    return [lo, hi]


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
    hh, mm = parse_time(at_str)
    return now >= now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def find(data, rid):
    for r in data["routines"]:
        if r["id"] == rid:
            return r
    return None


TIME_ANCHORED = ("daily", "weekly", "monthly", "yearly")


def cmd_add(args):
    with locked_store() as data:
        rid = validate_id(args.id) if args.id else slugify(args.label, data)
        if find(data, rid):
            print(f"이미 존재하는 id: {rid}", file=sys.stderr)
            sys.exit(1)
        now = datetime.now()
        r = {"id": rid, "label": args.label, "type": args.type, "active": True}
        tone, instruction = tone_config(args.tone, args.custom_tone, data.get("settings"))
        r["tone"] = tone
        r["tone_instruction"] = instruction

        if args.type == "recurring":
            r["interval_minutes"] = parse_positive_minutes(args.interval, "--interval") or 120
            if args.active_hours:
                r["active_hours"] = parse_hours(args.active_hours)
            r["last_done"] = now_iso()  # 등록 직후엔 한 주기 뒤부터

        elif args.type == "session":
            r["threshold_minutes"] = parse_positive_minutes(args.after, "--after") or 180
            r["idle_reset_minutes"] = parse_positive_minutes(args.idle_reset, "--idle-reset") or 45
            if args.active_hours:
                r["active_hours"] = parse_hours(args.active_hours)
            r["last_done"] = now_iso()

        elif args.type in TIME_ANCHORED:
            if not args.at:
                print(f"{args.type} 는 --at 'HH:MM' 가 필요합니다", file=sys.stderr)
                sys.exit(1)
            hh, mm = parse_time(args.at)
            r["at"] = f"{hh:02d}:{mm:02d}"
            if args.type == "weekly":
                if not args.weekday:
                    print("weekly 는 --weekday (월..일, 콤마로 여러개) 가 필요합니다", file=sys.stderr)
                    sys.exit(1)
                r["weekdays"] = parse_weekdays(args.weekday)
            elif args.type == "monthly":
                r["day"] = validate_day(args.day)
            elif args.type == "yearly":
                r["month"] = validate_month(args.month)
                r["day"] = validate_day(args.day)
            if slot_passed_today(r, r["at"], now):
                r["last_done"] = now_iso()  # 오늘 슬롯 지났으면 다음 발생부터

        else:  # oneshot
            if not args.due:
                print("oneshot 은 --due 가 필요합니다", file=sys.stderr)
                sys.exit(1)
            due = parse_iso_local(args.due, "--due")
            remind_from = parse_iso_local(args.remind_from, "--remind-from") if args.remind_from else default_remind_from(due)
            if remind_from > due:
                remind_from = default_remind_from(due)  # 상기 시작이 이벤트보다 늦으면 비정상 → 기본값으로
            r["due_at"] = due.isoformat()
            r["remind_from"] = remind_from.isoformat()
            r["acked"] = False

        data["routines"].append(r)
    print(f"등록됨: [{rid}] {args.label} (말투: {tone})")


def cmd_done(args):
    validate_id(args.id)
    with locked_store() as data:
        r = find(data, args.id)
        if not r:
            print(f"없는 id: {args.id}", file=sys.stderr)
            sys.exit(1)
        if r["type"] == "oneshot":
            r["acked"] = True
            r["active"] = False
            msg = f"확인 완료(종료): [{r['id']}] {r['label']}"
        else:
            r["last_done"] = now_iso()
            r.pop("snooze_until", None)
            msg = f"완료 처리(다음 차례로 리셋): [{r['id']}] {r['label']}"
    print(msg)


def cmd_snooze(args):
    validate_id(args.id)
    parse_positive_minutes(args.minutes, "minutes")
    with locked_store() as data:
        r = find(data, args.id)
        if not r:
            print(f"없는 id: {args.id}", file=sys.stderr)
            sys.exit(1)
        until = (datetime.now() + timedelta(minutes=args.minutes)).replace(microsecond=0)
        r["snooze_until"] = until.isoformat()
        msg = f"{args.minutes}분 미룸: [{r['id']}] {r['label']} (until {until.strftime('%H:%M')})"
    print(msg)


def cmd_remove(args):
    validate_id(args.id)
    with locked_store() as data:
        before = len(data["routines"])
        data["routines"] = [r for r in data["routines"] if r["id"] != args.id]
        if len(data["routines"]) == before:
            print(f"없는 id: {args.id}", file=sys.stderr)
            sys.exit(1)
    print(f"삭제됨: {args.id}")


def describe(r):
    t = r["type"]
    if t == "recurring":
        ah = r.get("active_hours")
        ah_s = f", {ah[0]}-{ah[1]}시" if ah else ""
        return f"매 {r.get('interval_minutes', 120)}분{ah_s}"
    if t == "session":
        return f"Claude Code 연속 작업 {r.get('threshold_minutes', 180)}분째"
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
        tone = r.get("tone") or data.get("settings", {}).get("tone", "natural")
        print(f"[{r['id']}] {r['label']} — {describe(r)} — 말투:{tone}{status}")


def cmd_style(args):
    with locked_store() as data:
        target = None
        if args.id:
            validate_id(args.id)
            target = find(data, args.id)
            if not target:
                print(f"없는 id: {args.id}", file=sys.stderr)
                sys.exit(1)
        settings = data.setdefault("settings", {})
        dest = target or settings
        if args.custom:
            text = sanitize_custom_tone(args.custom)
            dest["tone"] = "custom"
            dest["tone_instruction"] = text
            msg = f"말투 설정됨: {text}"
        elif args.tone:
            dest["tone"] = args.tone
            dest["tone_instruction"] = STYLE_PRESETS[args.tone]
            msg = f"말투 설정됨: {args.tone} — {STYLE_PRESETS[args.tone]}"
        else:
            source = target or settings
            msg = f"현재 말투: {source.get('tone', 'natural')} — {source.get('tone_instruction', STYLE_PRESETS['natural'])}"
    print(msg)


def set_onboarding(data, step, completed=False):
    data["onboarding"] = {"completed": bool(completed), "step": "done" if completed else step}


def yes_no(value):
    value = (value or "").strip().lower()
    yes = {"yes", "y", "true", "1", "예", "네", "응", "ㅇㅇ", "좋아", "확인", "할래"}
    no = {"no", "n", "false", "0", "아니오", "아니", "ㄴㄴ", "안해", "괜찮아", "나중에"}
    if value in yes:
        return True
    if value in no:
        return False
    raise SystemExit("--answer 는 yes/no 또는 예/아니오 계열이어야 합니다")


def cmd_onboarding(args):
    with locked_store() as data:
        if args.action == "status":
            ob = data.get("onboarding", {})
            msg = f"온보딩: step={ob.get('step', 'tone')}, completed={bool(ob.get('completed'))}"
        elif args.action == "reset":
            set_onboarding(data, "tone", completed=False)
            msg = "온보딩을 처음 단계로 리셋했습니다"
        elif args.action == "tone":
            settings = data.setdefault("settings", {})
            if args.custom:
                text = sanitize_custom_tone(args.custom)
                settings["tone"] = "custom"
                settings["tone_instruction"] = text
                msg = f"온보딩 말투 설정됨: {text}"
            elif args.tone:
                settings["tone"] = args.tone
                settings["tone_instruction"] = STYLE_PRESETS[args.tone]
                msg = f"온보딩 말투 설정됨: {args.tone} — {STYLE_PRESETS[args.tone]}"
            else:
                raise SystemExit("tone 단계에는 --tone 또는 --custom 이 필요합니다")
            set_onboarding(data, "preseed", completed=False)
        elif args.action == "preseed":
            if yes_no(args.answer):
                set_onboarding(data, "preseed_details", completed=False)
                msg = "미리 등록할 루틴을 받을 준비가 됐습니다"
            else:
                set_onboarding(data, "cancel_ack", completed=False)
                msg = "미리 등록은 건너뛰고 취소 안내 확인 단계로 이동했습니다"
        elif args.action == "preseed-done":
            set_onboarding(data, "cancel_ack", completed=False)
            msg = "초기 루틴 등록 단계를 마치고 취소 안내 확인 단계로 이동했습니다"
        elif args.action == "cancel-ack":
            if yes_no(args.answer):
                set_onboarding(data, "done", completed=True)
                msg = "온보딩 완료"
            else:
                set_onboarding(data, "cancel_ack", completed=False)
                msg = "취소 안내 확인 전까지 같은 단계를 유지합니다"
        elif args.action == "skip":
            set_onboarding(data, "done", completed=True)
            msg = "온보딩을 건너뛰고 완료 처리했습니다"
        else:
            raise SystemExit(f"알 수 없는 온보딩 action: {args.action}")
    print(msg)


def main():
    p = argparse.ArgumentParser(description="routine-buddy 관리 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--label", required=True)
    a.add_argument("--type",
                   choices=["recurring", "session", "oneshot", "daily", "weekly", "monthly", "yearly"],
                   required=True)
    a.add_argument("--id")
    a.add_argument("--interval", type=int, help="recurring: 분 단위 주기 (기본 120)")
    a.add_argument("--after", type=int, help="session: 연속 작업 몇 분째에 알릴지 (기본 180)")
    a.add_argument("--idle-reset", type=int, help="session: 이만큼 프롬프트가 없으면 작업 세션 리셋 (기본 45)")
    a.add_argument("--active-hours", help="recurring/session: 알림 허용 시간대 '9-19'")
    a.add_argument("--at", help="daily/weekly/monthly/yearly: 시각 'HH:MM'")
    a.add_argument("--weekday", help="weekly: 요일 (월..일/mon..sun, 콤마로 여러개 '월,수,금')")
    a.add_argument("--day", type=int, help="monthly/yearly: 날짜 1-31")
    a.add_argument("--month", type=int, help="yearly: 월 1-12")
    a.add_argument("--due", help="oneshot: ISO 시각 2026-06-13T16:00")
    a.add_argument("--remind-from", help="oneshot: 이 시각부터 상기 (기본 due와 동일)")
    a.add_argument("--tone", choices=sorted(STYLE_PRESETS), help="이 루틴 알림에만 적용할 말투")
    a.add_argument("--custom-tone", help="이 루틴 알림에만 적용할 직접 말투 지시")
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

    st = sub.add_parser("style")
    st.add_argument("--id", help="특정 루틴 id의 말투를 확인/변경")
    st.add_argument("--tone", choices=sorted(STYLE_PRESETS))
    st.add_argument("--custom", help="routine-buddy 알림에만 적용할 직접 말투 지시")
    st.set_defaults(func=cmd_style)

    ob = sub.add_parser("onboarding")
    ob.add_argument("action", choices=["status", "reset", "tone", "preseed", "preseed-done", "cancel-ack", "skip"])
    ob.add_argument("--tone", choices=sorted(STYLE_PRESETS))
    ob.add_argument("--custom", help="온보딩에서 설정할 직접 말투 지시")
    ob.add_argument("--answer", help="yes/no, 예/아니오")
    ob.set_defaults(func=cmd_onboarding)

    pth = sub.add_parser("path")
    pth.set_defaults(func=lambda a: print(STORE))

    init = sub.add_parser("init")
    init.set_defaults(func=lambda a: (init_store(), write_cli_path(os.path.abspath(__file__)), print(STORE)))

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
