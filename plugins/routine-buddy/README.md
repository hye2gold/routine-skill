# routine-buddy 🫧

직장인을 위한 **루틴 리마인더 플러그인** for Claude Code.

"물 2시간마다 알려줘", "내일 4시 미팅 잊지 마" 처럼 등록해 두면, Claude가 **무슨 얘기를 하고 있든 매 대화마다** 답변 끝에 자연스럽게 챙겨줍니다. 알림 앱처럼 따로 보는 게 아니라, 이미 켜둔 Claude Code 대화 속에 녹아드는 게 포인트.

## 어떻게 동작하나

| 부품 | 역할 |
|---|---|
| `/routine` 스킬 | 루틴 등록·관리 (자연어 → 저장소). 특정 날짜는 "그날만? 매일?" 자동 질문 |
| `UserPromptSubmit` 훅 | **매 턴 자동 실행.** 지금 due인 루틴을 계산해 Claude 답변에 자연스럽게 주입 |
| `~/.claude/routine-buddy/routines.json` | 전역 저장소 (모든 세션 공유, 업데이트해도 보존) |

- **주기 루틴**: "물 2시간마다" 등 N분마다 반복. 기본 근무시간(9~19시)에만 알림.
- **매일 루틴**: "업무일지 매일 18:00" 등 매일 같은 시각.
- **매주 루틴**: "미팅룸 매주 월요일 09:00" 등 요일+시각.
- **특정날 루틴**: "내일 4시 미팅" 등 지정한 날 1회.
- 사용자가 "했어" 하면 완료 처리되어 조용해지고, "이따" 하면 스누즈. 응답 없으면 매 답변마다 가볍게 다시 상기.
- 등록된 게 없으면 완전 무동작(무비용).

## 설치

```
/plugin marketplace add <github-user>/routine-buddy
/plugin install routine-buddy@routine-buddy
```

설치 후 Claude Code를 **재시작**하면 훅이 활성화됩니다.

### 로컬에서 테스트 설치

```
/plugin marketplace add /path/to/routine-buddy
/plugin install routine-buddy@routine-buddy
```

## 사용

```
/routine 물 2시간마다 알려줘
/routine 스트레칭 매일 10:30 알려줘
/routine 업무일지 매일 18:00 체크해줘
/routine 매주 월요일 9시 미팅룸 잡으라고 알려줘
/routine 내일 오후 4시 팀 미팅 잊지 마        # → "그날만? 매일?" 물어봄
```

이후엔 그냥 평소처럼 Claude와 대화하면 됩니다. 때 되면 답변 끝에 알아서 끼워줍니다.

## 요구사항

- Python 3 (`python3` PATH에 존재)
- macOS / Linux / WSL

## 저장 위치 / 프라이버시

모든 루틴은 로컬 `~/.claude/routine-buddy/` 에만 저장됩니다. 외부 전송 없음.
환경변수 `ROUTINE_BUDDY_HOME` 으로 저장 위치를 바꿀 수 있습니다.

## 라이선스

MIT
