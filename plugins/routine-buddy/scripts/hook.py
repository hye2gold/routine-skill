#!/usr/bin/env python3
"""routine-buddy UserPromptSubmit 훅 — 매 턴 자동 실행.

지금 'due'인 루틴을 계산해, Claude에게 '평소 말투로 자연스럽게 끼워 알려주라'는
추가 컨텍스트를 주입한다. due인 게 없으면 아무것도 출력하지 않는다(무비용).
절대 prompt를 막지 않으며, 어떤 예외에도 조용히 exit 0.

저장소: ~/.claude/routine-buddy/routines.json (ROUTINE_BUDDY_HOME 로 override 가능)
manage.py 절대경로는 자기 위치(__file__) 기준으로 계산하고, 데이터 폴더의
cli.json 에도 기록해 둔다(/routine 스킬이 등록 시 읽음).
"""
import json
import os
from datetime import datetime, timedelta

SELF_DIR = os.path.dirname(os.path.abspath(__file__))
MANAGE = os.path.join(SELF_DIR, "manage.py")


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


def parse(dt):
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt)
    except Exception:
        return None


def fmt_dur(mins):
    mins = int(mins)
    if mins < 60:
        return f"{mins}분"
    return f"{mins // 60}시간 {mins % 60}분"


def main():
    ddir = data_dir()
    store = os.path.join(ddir, "routines.json")

    # 스킬이 manage.py 위치를 알 수 있도록 매 실행 시 기록(가볍게).
    try:
        with open(os.path.join(ddir, "cli.json"), "w", encoding="utf-8") as f:
            json.dump({"manage": MANAGE}, f, ensure_ascii=False)
    except Exception:
        pass

    try:
        with open(store, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return  # 저장소 없음 = 등록된 루틴 없음 → 무동작

    now = datetime.now()
    lines = []

    for r in data.get("routines", []):
        if not r.get("active", True):
            continue
        snooze = parse(r.get("snooze_until"))
        if snooze and now < snooze:
            continue

        if r.get("type") == "recurring":
            ah = r.get("active_hours")
            if ah:
                lo, hi = ah[0], ah[1]
                # 자정을 넘기는 구간(예: 22-6)도 지원
                inside = (lo <= now.hour < hi) if lo <= hi else (now.hour >= lo or now.hour < hi)
                if not inside:
                    continue
            interval = r.get("interval_minutes", 120)
            last = parse(r.get("last_done")) or (now - timedelta(minutes=interval + 1))
            elapsed = (now - last).total_seconds() / 60
            if elapsed >= interval:
                lines.append(
                    f"- [{r['id']}] {r['label']}: 마지막으로 한 지 {fmt_dur(elapsed)} 지남 "
                    f"(주기 {interval}분). → 사용자가 했다고 하면 `python3 \"{MANAGE}\" done {r['id']}` 실행."
                )
        else:  # oneshot
            rf = parse(r.get("remind_from"))
            if rf and now < rf:
                continue
            due = parse(r.get("due_at"))
            if due:
                mins = (due - now).total_seconds() / 60
                if mins > 0:
                    urg = f"{fmt_dur(mins)} 뒤 ({due.strftime('%H:%M')})"
                elif mins > -180:
                    urg = f"⚠️ 지금/방금 시작됨 ({due.strftime('%H:%M')})"
                else:
                    continue  # 3시간 넘게 지났으면 조용히 멈춤
            else:
                urg = ""
            lines.append(
                f"- [{r['id']}] {r['label']} — {urg}. "
                f"→ 사용자가 확인/완료했다고 하면 `python3 \"{MANAGE}\" done {r['id']}` 실행."
            )

    if not lines:
        return

    text = (
        "[루틴 비서] 사용자가 미리 등록해 둔 리마인더야. 아래 항목을, 지금 무슨 얘기를 하고 있든 "
        "대화 흐름을 끊지 말고 네 평소 말투로 답변 끝에 짧고 자연스럽게 한 번만 끼워서 알려줘 "
        "(예: \"아 그리고 — 물 마신 지 2시간 넘었는데 한 잔 어때?\"). 과하게 반복하지 말고 가볍게. "
        "사용자가 '했어/응/확인했어'라고 하면 해당 done 명령을 Bash로 실행해 완료 처리하고, "
        "'이따/나중에' 같으면 snooze 명령(`done` 자리를 `snooze <id> <분>` 으로)으로 미뤄줘. "
        "아직 응답·확인이 없으면 다음 답변에서도 계속 가볍게 상기시켜.\n"
        + "\n".join(lines)
    )

    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
