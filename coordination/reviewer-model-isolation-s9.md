**판정: 평면 B를 후보로 선택. 현재 S10 코드는 모의 52개 및 L-03 실제 모델+모의 접수 E2E 범위에서 승인 가능.** A는 malformed, B는 정확한 API-24를 반환했고, B E2E는 POST 1회로 접수까지 완결됐다. 각 1표본이며 일반적인 오류 소멸을 보장하지 않는다. 실제 액션·콜백은 실행하지 않았다.

## 고정한 후보

- `bot.py` **14:25:33판**, SHA-256 `e0dce620ce45ed9cb31149bd10c1294f59b5c4257e656ef4c4047e974e43f22a`.
- `test_bot.py` 같은 시각판, SHA-256 `0491897ee31c02f897a495cdebba07e0ae5b9da492080d26708365f43728816a`.
- 모델 `google/gemini-2.5-flash`, 도구 40종, max_tokens 512. 평면 인자를 `nest`로 원래 body에 복원하고 원본 body schema 검증을 유지한다. 기존 모의검사 **52개 통과**. 확인되지 않은 데이터로 실제 주문을 실행한 검증은 아니다.

## A/B 격리 — 각각 실제 요청 1회, 재시도·도구 실행 없음

현재 지식은 S9 이후 바뀌었으므로 비교에는 **보존된 S9 지식** SHA `28c3bfb6…80d5d` / `734af770…43d`를 사용했다. system·history·최종 주소가 든 messages 6개는 A/B 동일이며 SHA-256 **`a0da992fbab11895903b254a70d7b77438a6e42c9018fddff9c3947b1debbaf2`**로 구현 담당의 고정 요청과 일치한다. tools만 바꿨다.

| 변형 | HTTP / finish / native | 결과 | 지연 | usage |
|---|---|---|---|---|
| A 전체40 중첩 | 200 / error / MALFORMED_FUNCTION_CALL | tool_calls=[]·text=null | 2,340ms | 미제공 |
| B 전체40 평면 | 200 / tool_calls / STOP | **API-24**, 정확 주문 `100100003`·전체 새 주소 | 1,614ms | prompt 58,000 / completion 58 / reasoning 0, $0.00272416 |

B의 인자 `{"order_no":"100100003","new_address":"경기 성남시 분당구 정자일로 95, 101동 1203호"}`를 `nest`로 복원해 원래 schema 검증 통과도 확인했다(여기서는 액션 실행 없음).

- **조건부 비교 원칙 준수:** 차이가 명확해 **C/D·이름 치환 E는 실행하지 않았다**. 같은 변형 재시도 없음.
- **원인 해석 한계:** B는 body 평면화와 전송용 `additionalProperties` 제거를 함께 바꾸므로 어느 한 요소만 원인이라고 확정하지 않는다. 각 1표본으로 현재 후보 선택을 지지하는 관측이다.
- 원스크립트는 5개 연속 실행 및 동기 함수를 AsyncClient request hook으로 등록하는 문제가 있어 그대로 실행하지 않았다. 검수 사본에서 **기존 messages/variants 생성은 유지**, 비동기 목적지 guard와 단계별 호출·중복 방지만 보완했다. 제품 코드는 검수자가 수정하지 않았다.

## 선택 B의 L-03 3턴 E2E — 1회

E2E에는 같은 합성 주문/회원/새 주소 fixture와 **14:29:18 지식** SHA `f536347b…97dc5` / `734af770…43d`를 고정했다. A/B의 통제 지식과 이 최신 후보 지식은 구분한다.

| 턴 | 답변/행동 | 지연(카카오 제외) | 실제 모델 요청 |
|---|---|---|---|
| 1 | 주문번호 질문 | 2,088ms | 1회 |
| 2 | 주문·귀속·집하 전 확인 후 시 단위 변경 의사 질문 | 4,680ms | 3회 |
| 3 | 고객이 상세주소 제공 → **정확한 모의 POST 1회**, 요청 접수 안내 | 3,124ms | 2회 |

- 2턴 실제 모의 액션 순서: GET `/api/orders/100100003` → GET `/api/members/MB-SYN-1` → GET `/api/shipments?order_no=100100003`. 다른 주문 조회 없음.
- 3턴 모델은 평면 `API-24` 인자를 반환했다. 실제 모의 전송은 **POST `/api/orders/100100003/address-change-requests`**, body **`{"new_address":"경기 성남시 분당구 정자일로 95, 101동 1203호"}`**, TC **L-03**. POST 총 **1회**를 assert로 확인했다.
- 최종 답: `주문번호 100100003번의 배송지 변경 요청이 접수되었습니다.`
- 모델 요청 6개 모두 HTTP 200, native **STOP**이며 finish는 정상 stop/tool_calls. **MALFORMED·복구 재시도 0회**, 저장된 합성 주소 선노출/추측주소·허위 완료 표현 없음.
- 제공된 usage 합계: prompt **364,917**, completion **198**, reasoning **0**, 비용 **$0.060552**. 요청별 usage/finish/native/인자는 결과 JSON에 보존했다. 지연은 카카오 제외이며 p95가 아니다.

### 비차단 품질 잔여

2턴은 동·호수 상세주소를 명시적으로 요청하는 대신 `경기 성남시`로 변경할지 물었다. 이번에는 3턴 고객이 전체 주소를 제공해 **시 단위만으로 POST하지는 않았다**. 최종 접수 문구에도 접수번호·담당 확인 후 처리 확정 안내가 없다. 핵심 정확 POST/접수 구분은 통과했지만 공개 L-03의 질문 문구·후속 안내까지 모두 완벽하다고 판정하지 않는다. S9의 Y24 상호·페이지 경로·비공개 범위 설명 잔여도 별도 유지한다.

## 재현 자료

- [A/B 원본 결과](reviewer-model-isolation-s9.json), [선택판 E2E 결과](reviewer-model-e2e-s10.json), [합성 fixture](reviewer-model-fixture-s9.json).
- 사본 루트 `/var/folders/x_/gq1xtwcs4svfp86x4f3d0vjc0000gn/T/ai-top-action-review-22umkqeb/s10/`.
- A/B: `isolation/isolation-original.py`(검토한 원본), `isolation/isolation-staged.py`(실행한 단계 러너), `isolation/manifest.json`(S9 지식 고정), 관련 코드/지식 사본.
- E2E: `reviewer-e2e-run.py`, `manifest.json`, 코드/지식 사본. 결과 파일이 있으면 같은 검증의 재실행을 거부한다.

## 승인 범위·마감

기존 P1/P2 해결을 유지하는 **현재 후보 코드 승인**이며 실제 업무 API/카카오 액션 성공 승인과 다르다. 실키는 OpenRouter 인증에만 사용하고 출력·저장하지 않았다. 네트워크 목적지는 OpenRouter/Gemini뿐, 실제 업무 GET/POST/reset·콜백·CDN·UI는 0회다. 실제 카카오 왕복은 [기존 일반질문 1회 기록](kakao-roundtrip.md)과 별개다. 서비스 전환 후 새 UI 검증은 root 인계 확인 후 수행한다.

검색 모듈은 이 후보에 아직 통합되지 않았으며 성능/회귀를 검증하지 않았다. 이후 변경은 별도 후보로 보고한다. 14:42 기능 동결·14:45 검수/자료 확정·14:48 root 보고·**14:50 최종 마감** 기준을 유지한다.
