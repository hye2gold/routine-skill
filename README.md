# routine-buddy

Claude Code 안에서 작업 흐름과 어우러지게 루틴을 챙겨주는 리마인더 플러그인입니다.

물 마시기, 스트레칭, 업무일지, 미팅 같은 루틴을 등록해두면 별도 알림 앱을 띄우지 않고도 Claude Code 답변 끝에서 자연스럽게 챙겨줍니다.

## 설치

```bash
/plugin marketplace add hye2gold/routine-skill
/plugin install routine-buddy@routine-buddy
```

설치 후 Claude Code를 재시작하면 훅이 활성화됩니다.

## 핵심 기능

- 주기 루틴: `물 2시간마다 알려줘`
- 매일/매주/매달/매년 루틴
- 특정날 1회 리마인더
- Claude Code 연속 작업 시간 기준 세션 루틴
- 설치 직후 온보딩
- 루틴 알림 전용 말투 설정
- 완료, 스누즈, 삭제

## 자세한 문서

플러그인 상세 README는 아래 파일에 있습니다.

[plugins/routine-buddy/README.md](plugins/routine-buddy/README.md)

## 저장 위치

루틴 데이터는 로컬 `~/.claude/routine-buddy/`에 저장됩니다.

## License

MIT
