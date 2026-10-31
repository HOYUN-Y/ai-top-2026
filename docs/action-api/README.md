# 대회 액션 API 원문

- 원문: https://aitop100.org/contest/docs
- 2026-10-31 13:19 KST경 사용자 로그인 Chrome에서 `Download OpenAPI Document` 버튼으로 `actions.json`을 다운로드했다. 프로젝트 사본은 [openapi.json](openapi.json)이다.
- OpenAPI 3.1.0, 경로41개. 업무 API-01~40과 참가자 데이터 초기화1개가 있다. 로컬 참조286개는 모두 해석됨을 확인했다.
- 서버: `https://gyodong-mall-action.aitop100.org`
- 인증: `Authorization: Bearer <AI_TOP_ACTION>` 및 모든 호출에 `X-Aitop100-Test-Case-Id`. 키는 환경에서만 읽고 모델·로그에 넣지 않는다.
- 평가 대화에서 받은 TC ID를 같은 TC 처리 동안 유지한다. 평가 전에는 공개 TC ID만 사용할 수 있다.
- 접수 API는 요청만 기록한다. 정책을 자동 판정하지 않으며 주문·결제 상태나 금액을 바꾸지 않는다. 성공 응답은 처리 완료가 아닌 접수 사실로 안내해야 한다.
- 같은 접수 API·대상에 재접수하면 같은 접수번호이고 본문은 마지막 요청으로 바뀐다. 대상 없는 에스컬레이션은 본문 기준이다.
- 연습/평가 각각60초100회, 평가는 누적3000회. 429의 시간 제한은 Retry-After를 따르고 누적 한도 초과는 기다려도 풀리지 않는다.
- 데이터 초기화는 모델 도구에 노출하지 않는다. 이 문서를 받은 작업에서는 실제 조회·접수·초기화를 호출하지 않았다.

## 공개 사례

목록: https://aitop100.org/contest/docs/test-cases

Chrome에서 목록에 노출된15개: L-03, L-05, L-11, L-20, L-23, L-32, L-33, L-34, L-40, L-47, L-60, R-05-a, Y-07-b, Y-16, Y-24.

- [L-03 배송지 변경 요청](public-cases/L-03.md): 원문 다운로드 버튼으로 보관. 대상 주문번호·대상 여부를 확인하고, 그 주문의 배송만 조회한 뒤, 동·호수까지 고객에게 확인해 변경 요청을 접수한다. 접수 후 완료가 아닌 요청 접수로 안내한다.
- 나머지14개도 사용자 로그인 Chrome에서 다운로드해 원문과 해시를 [manifest](public-cases/manifest.json)에 보관했다. 세션이나 쿠키를 추출하지 않았다.

OpenAPI SHA-256: `20082b8f76e41f5d6f8e2a66be927f80e12540883e36198587ff93f64278911e`

## 실제 연습 조회 확인

2026-10-31 14:07 KST: 공개 TC L-47에서 API-20 `CARE5000`을 읽기 전용으로 1회 조회해 200을 받았다. 5,000원 정액 보상 쿠폰이며 `valid_until=null`이다. [조회 결과](../../coordination/action-readonly-check.json). 접수·환불·데이터 초기화는 실행하지 않았다.
