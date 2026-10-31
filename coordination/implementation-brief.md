# 구현 담당 브리핑

사용자는 Claude Opus high로 기초 구현을 진행하도록 지시했다. 저장소 /Users/lyuhoyun/Documents/GitHub/ai-top-2026. docs/진행현황.md, README.md, bot.py, test_bot.py, knowledge/interview-progress.json을 먼저 읽어라. 아직 Git 추적 안 된 파일도 사용자의 작업이므로 지우지 말 것. 작업한 내용과 검증을 coordination/implementation.md에 기록하라.

당신만 코드·테스트·README·환경변수 예제를 수정한다. interviews/**, knowledge/interview-progress.json, docs/진행현황.md는 읽기만 한다. 검수는 별도 GPT-6.1 Sol이 담당한다. 카카오톡·브라우저 UI를 조작하거나 외부 메시지를 보내지 말 것. 실제 환불·이관·주문 API를 호출하지 말 것.

목표: 이미 구현된 Python 게이트웨이 봇이 실제 확보한 운영 지식을 참고하도록 최소한 연결하고, 초기 코드의 치명적 안정성 문제를 수정한다. 일반 프레임워크·벡터DB·관리자 웹사이트는 추가하지 않는다. API 명세 미확보이므로 액션 인터페이스를 상상해 만들지 않는다. .env 값은 출력하지 않고 원본은 수정하지 않는다. KAKAO_BOT_TOKEN은 마지막 확인 때 없었으며 사용자 추가 여부는 존재 여부만 검사 가능하다.

초기 JSON의 knowledge[].fact 및 source를 로컬 근거로 모델에 제공한다. 출처와 예외를 같이 제공하고 자료에 없는 사실·업무 완료를 주장하지 않도록 한다. 인터뷰 담당의 interviews/knowledge.json이 있으면 확인된 사실을 읽되 partial write·파일 부재로 서비스가 죽지 않게 한다. 불확실한 사실은 확정 정책으로 쓰지 않는다. 근거 충돌·미확인 액션은 확인 필요라고 답하도록 한다. 작은 자료이므로 별도 검색 서비스보다 단순 로딩으로 충분하다. 읽는 파일 규격을 README에 기록한다.

보안/정확성: 대화 메모리와 질문 기록을 다른 사용자에게 섞지 말 것. 의도치 않은 전체 예외 삼킴·세션 복원·재접속·중복 처리 문제를 살펴라. 토큰값·콜백토큰·대화 원문 로그 금지. 비용 우선 모델은 유지. 필요한 변경에 대해서만 의미 있는 테스트 추가 후 uv run --locked python -m unittest -v 실행. 네트워크·권한 장애면 원인을 기록하고 감독에게 알릴 것. 봇 프로세스 실제 구동은 하지 않는다(중복 연결 방지).

완료 시 coordination/implementation.md에 바뀐 동작, 테스트 결과, 위험·미구현 항목, 검수할 파일을 기록하고 대기. 리뷰 피드백은 감독이 전달한다. 커밋·푸시하지 않는다.
