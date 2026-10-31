**최신 운영 근거: runtime 배치·READY·자동복구 확인, UI 왕복은 진행 중.** root가 `/Users/lyuhoyun/.local/share/ai-top-2026`에 전용 snapshot을 배치했다. 아래 초기 읽기 검토와 실제 운영 확인을 구분한다.

- **독립 읽기 확인:** runtime `bot.py` SHA `e0dce620ce45ed9cb31149bd10c1294f59b5c4257e656ef4c4047e974e43f22a`로 승인 후보와 일치. `media.py`도 승인 사본과 일치. KB는 255+11, runtime 지식 SHA `459ec834…aab4d` / `734af770…43d`. `.env`는 읽지 않았다.
- **runtime 로그 직접 확인:** `14:38:25,602 action api enabled operations=40`, `14:38:25,706 gateway READY`, `14:39:26,877 action api enabled operations=40`, `14:39:26,984 gateway READY`. 로그는 비밀 없는 READY/회차/응답 메타데이터 줄만 읽었다.
- **root 실행 증거:** 기존 repo 경로 launchd는 `posix_spawn EPERM`. 시스템 보안을 변경하지 않고 사용자 전용 폴더로 배치해 해결. root가 SIGKILL **1회**로 재시작을 검증해 `state running / runs 2 / PID 62940`을 확인했다. 검수자가 프로세스를 종료·시작하거나 이 파괴 검증을 반복하지 않았다.
- **중복/범위:** 과거 임시 action OFF와 현재 launchd action ON을 구분한다. root가 종료·전환을 관리했고, 검수자는 중복 프로세스를 새로 띄우지 않았다. 프로세스 운영·기본 카카오 왕복 검증 외 **실제 업무 POST/reset은 없음**. 실제 액션 성공 검증으로 표현하지 않는다.
- **문서 동기화:** 검토 당시 `docs/로컬-운영.md`는 준비본/미설치·repo 로그 경로 표기가 남아 있어 root에게 실제 runtime 경로·실행 상태로 갱신 필요를 알렸다. 설정 plist는 전용 사용자 폴더로 갱신돼 있다.
- **UI:** [왕복 기록](kakao-roundtrip.md)의 최종 질문은 대상 창 선택 실패로 아직 입력/발송 0회, root 전면 인계/직접 발송 지시 대기. 기존 14:13 성공을 현재 서비스의 최종 왕복으로 대체하지 않는다.

---

## 초기 읽기 검토 보존

**읽기 검토 판정: 설정 자체에서 P0/P1 차단 사유는 확인하지 못함. 실제 설치·실행·재시작 성공 판정은 아님.** 문서의 재시작 간격 표현 1곳만 좁히면 된다. 코드 핵심 판정은 [액션 검수](action-implementation-review.md)의 S7을 우선한다.

- **대상:** 2026-10-31 14:06:15판 `ops/local.ai-top-2026.bot.plist` / `docs/로컬-운영.md`. plist를 표준 라이브러리로 파싱했고 caffeinate·uv·작업 디렉터리 경로 존재를 확인했다. `.env`는 읽지 않았다. 설치·프로세스 실행·launchctl·UI·네트워크 조작 없음.
- **P2 / 문구 정확성:** `docs/로컬-운영.md:10`의 “비정상 종료 후 30초 이상 간격”은 **종료 후 항상 30초 대기한다는 뜻으로 읽히지 않게** “비정상 종료 시 재시작하며, 시작 빈도를 ThrottleInterval 30초로 제한” 정도로 좁히면 정확하다. 로컬 Apple man `launchd.plist(5)`의 ThrottleInterval은 실행 시작 빈도 제한이다.
- **중복 실행:** 동일 GUI 도메인의 같은 Label을 쓰는 LaunchAgent와 별개로, 터미널에서 `bot.py`를 직접 실행하면 중복을 막는 로컬 잠금은 없다. 문서 17행의 수동 중복 금지는 필요하고 현재 범위에서 충분한 운영 조건이다. **실제 프로세스 목록·게이트웨이 중복 접속은 검사하지 않아 현재 중복 0개라고 판정하지 않는다.** 시작 담당자가 기존 수동 실행 여부를 확인하면 되며 별도 감독 시스템 추가는 요구하지 않는다.
- **재시작:** `KeepAlive.SuccessfulExit=false`와 `RunAtLoad=true`는 비정상 종료 재시작 의도와 맞다. 코드의 인증/중복 관련 FatalGatewayError는 잡아서 정상 종료하므로 자동 복구 대신 원인 수정이 필요하다는 문서 설명도 맞다. **caffeinate→uv→Python의 실제 종료코드 전달·종료/재시작 및 Documents 접근 권한은 미검증**이다. 설정 파일만으로 재시작 성공을 주장할 수 없다.
- **로그:** 토큰은 plist에 없고 `uv --env-file`의 경로만 있다. stdout/stderr는 같은 `bot-service.log`, `.gitignore`는 `*.log`를 제외한다. 읽은 봇 코드는 본문·액션 인자 대신 상태/오류 종류를 기록한다. 실제 생성 파일의 권한·크기·로그 출력은 미검증이다. Git 제외는 접근권한을 대신하지 않는다. 하루 남짓 로컬 운영 범위에서 별도 로그 인프라 추가를 요구할 근거는 없다.
- **기존 한계 보존:** 메모리 상태·중복 기록·호출 누적의 재시작 초기화, 지속 저장 미구현, 사용자 로그인 필요, 덮개/전원/인터넷 한계, 자동 종료 없음이 명시돼 있다. `caffeinate -s`가 AC 전원에서만 유효하다는 설명은 로컬 Apple man `caffeinate(8)`과 일치한다. 대화 지속 저장이나 외부 감시를 새로 요구하지 않는다.

근거는 두 설정 파일·현재 봇 종료/로그 경로·`.gitignore` 및 로컬 Apple man `/usr/share/man/man5/launchd.plist.5`, `/usr/share/man/man8/caffeinate.8`이다. 원본 설정/코드는 수정하지 않았다.
