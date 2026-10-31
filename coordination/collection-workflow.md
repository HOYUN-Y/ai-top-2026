# 수집 속도·범위·깊이 운영 변경

사용자: 전체10명 연결, 깊이와 넓이를 모두 확보하며 속도 개선 요청. 기존 세션을 병렬 활용한다. 카카오UI는 interview만 사용한다.

- interview: 승인된 질문 발송/답변 원문JSON/새연락처/횟수/첨부URL을 즉시 저장. 원문을 받은 뒤 첨부 전체판독이나 긴보고서 때문에 다음 승인질문을 늦추지 않는다. INDEX와관계자md는2~3질문 묶음단위로 동기화. 받은 답을읽고 중복인지판단하는 과정은 생략하지 않는다. 새로운질문은 감독검토후발송.
- implement(자료판독 역할): 코드수정은 대기. 새 첨부의 안전저장·텍스트추출·원문검토·조건/예외/담당범위 추출. 소유 research/** 및 coordination/document-analysis.md. interviews/**,knowledge/**,코드는수정하지않는다. 기존PDF는 source파일재사용. 외부액션/카카오메시지/UI사용금지.
- knowledge: docs/지식지도.md, coordination/knowledge.md, contact-routes.md, interview-queues.md 소유. 새대상 확보와 각사람 심층질문을병렬로설계. 현재4명에대해 다음1개핵심질문과답에따른후속경로를우선작성. 출처/알수있는범위/미확인구분. 문서판독담당결과도읽는다. 질문전송권한없음.
- review: coordination/collection-review.md 소유. 질문후보 중복/담당범위/비즈니스맥락과 새로운자료의과장해석만간결히검수. 코드재검사안함. 작업중보고서는한정범위짧게작성.
- 감독: 발송문구승인,수집진척/한도검사,병목해결,사용자진행공유. 질문이곧바로보내질수있는준비상태를유지한다.

2026-10-31 12:08부터 interviews/knowledge.json 큐레이션은 supervisor 소유다. interview가 진행 중 편집 없음을 확인했다. 새원문판독결과는 research에 작성하고 독립검수 뒤 supervisor가 통합한다. implement는 research/source-manifest.json 및 docs/자료목록.md도 소유한다. 사용자 대화자료 원본은 이동·삭제·덮어쓰지 않는다.
