# 실제 게이트웨이 인증 점검

2026-10-31 11:35:56 KST. 사용자 토큰 등록 완료 안내 후 진행.

- .env의 KAKAO_BOT_TOKEN 설정 여부 확인(값 미출력).
- bot.py 실행 프로세스 없음 확인.
- 공식 wss://bot-gateway.kakao.com/gateway 접속, HELLO op10 수신.
- 환경변수의 토큰으로 IDENTIFY op2 전송, READY op3 및 session_id 존재 확인.
- 점검 연결 정상 종료. 토큰·session_id·대화 본문 미출력.
- 콜백·LLM·액션 API 호출은 하지 않음. 인증 성공만 검증했고 실제 메시지 왕복/지속 운영은 검증 전.
