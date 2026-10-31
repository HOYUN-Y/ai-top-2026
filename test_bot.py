import asyncio
from collections import deque
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from contextlib import asynccontextmanager
import httpx
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response
from bot import ActionClient, Bot, CALLBACK, ERROR_REPLY, FatalGatewayError, fit, load_operations, new_state

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_context_and_dedup(self):
        requests = []
        def respond(req):
            requests.append((str(req.url), json.loads(req.content)))
            if str(req.url) == CALLBACK:
                self.assertEqual(req.headers['X-Bot-Callback-Token'], 'cb')
                return httpx.Response(200, json={'sendCount': 1, 'errorCount': 0})
            return httpx.Response(200, json={'choices': [{'message': {'content': '안녕하세요.'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            bot = Bot('token', 'key', 'model', client)
            event = {'s': 1, 't': 'MESSAGE_CREATE', 'd': {'id': 558185822630215770, 'content': '안녕', 'botGroupKey': 'room', 'botUserKey': 'user', 'callbackToken': 'cb'}}
            bot.dispatch(event)
            bot.dispatch(event)
            await asyncio.gather(*bot.tasks)
            self.assertEqual(len(requests), 2)
            self.assertEqual(len(requests[1][1]['skillResponse']['template']['outputs']), 1)
            event['d'] = {**event['d'], 'id': 558185822630215771}
            bot.dispatch(event)
            await asyncio.gather(*bot.tasks)
            self.assertEqual(len(requests[2][1]['messages']), 4)
            event['d'] = {**event['d'], 'id': 3, 'botUserKey': 'other'}
            bot.dispatch(event)
            await asyncio.gather(*bot.tasks)
            self.assertEqual(len(requests[4][1]['messages']), 2)
            del event['d']['callbackToken']
            bot.dispatch(event)
            self.assertFalse(bot.tasks)

    async def test_generation_errors(self):
        for mode in ('timeout', 'empty', 'http'):
            replies = []
            def respond(req):
                if str(req.url) == CALLBACK:
                    replies.append(json.loads(req.content))
                    return httpx.Response(200, json={'sendCount': 1, 'errorCount': 0})
                if mode == 'timeout': raise httpx.ReadTimeout('test')
                if mode == 'http': return httpx.Response(429)
                return httpx.Response(200, json={'choices': [{'message': {'content': ''}}]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                bot = Bot('token', 'key', 'model', client)
                history = deque()
                await bot.handle({'content': 'hi', 'callbackToken': 'cb'}, asyncio.Lock(), history, time.monotonic())
                self.assertEqual(replies[0]['skillResponse']['template']['outputs'][0]['simpleText']['text'], ERROR_REPLY)
                self.assertEqual(len(replies), 1)
                self.assertFalse(history)

    async def test_delivery_failure(self):
        calls = []
        def respond(req):
            calls.append(req)
            return httpx.Response(200, json={'sendCount': 0, 'errorCount': 1})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(ValueError): await Bot('t', 'k', 'm', client).reply('cb', 'hi')
            self.assertEqual(len(calls), 1)

    async def test_gateway_identify_resume(self):
        class Socket:
            async def recv(self): return json.dumps({'op': 10, 'd': {'heartbeat_interval': 41250}})
            async def send(self, raw): self.sent = json.loads(raw)
            def __aiter__(self): return self.frames()
            async def frames(self):
                yield json.dumps({'op': 3, 'd': {'session_id': 'session'}})
                yield json.dumps({'op': 11})
        bot = Bot('token', 'key', 'model', None)
        ws = Socket()
        await bot.connection(ws)
        self.assertEqual(ws.sent['op'], 2)
        self.assertEqual(bot.session, 'session')
        bot.seq = 42
        await bot.connection(ws)
        self.assertEqual(ws.sent, {'op': 6, 'd': {'token': 'token', 'session_id': 'session', 'seq': 42}})
        self.assertTrue(bot.acked)

    async def test_heartbeat_ack_missing(self):
        class Socket:
            closed = False
            async def send(self, raw): pass
            async def close(self): self.closed = True
        ws = Socket()
        await asyncio.wait_for(Bot('t', 'k', 'm', None).heartbeat(ws, 1), 1)
        self.assertTrue(ws.closed)

    async def test_invalid_hello(self):
        class Socket:
            async def recv(self): return '{"op": 10, "d": {}}'
        with self.assertRaises(ValueError): await Bot('t', 'k', 'm', None).connection(Socket())

    async def test_transient_gateway_errors_reconnect(self):
        calls = 0
        @asynccontextmanager
        async def connection(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1: raise InvalidStatus(Response(503, 'Service Unavailable', Headers()))
            if calls == 2: raise ValueError('invalid HELLO')
            raise FatalGatewayError('test complete')
            yield
        async def no_wait(delay): pass
        with patch('bot.connect', connection), patch('bot.asyncio.sleep', no_wait):
            with self.assertRaises(FatalGatewayError): await Bot('t', 'k', 'm', None).run()
        self.assertEqual(calls, 3)

    async def test_malformed_frames_ignored(self):
        class Socket:
            async def recv(self): return json.dumps({'op': 10, 'd': {'heartbeat_interval': 41250}})
            async def send(self, raw): pass
            def __aiter__(self): return self.frames()
            async def frames(self):
                for raw in ('not json', '[]', '{"op": 3, "d": {}}', '{"op": 0, "t": "MESSAGE_CREATE", "d": null}'):
                    yield raw
                yield json.dumps({'op': 3, 'd': {'session_id': 'session'}})
        bot = Bot('t', 'k', 'm', None)
        await bot.connection(Socket())
        self.assertEqual(bot.session, 'session')
        self.assertFalse(bot.tasks)

    async def test_plain_disconnect_keeps_session(self):
        for frames in ([{'op': 11}], []):
            class Socket:
                async def recv(self): return json.dumps({'op': 10, 'd': {'heartbeat_interval': 41250}})
                async def send(self, raw): self.sent = json.loads(raw)
                def __aiter__(self): return self.frames()
                async def frames(self):
                    for frame in frames: yield json.dumps(frame)
                    raise ConnectionClosedError(None, None)
            bot = Bot('t', 'k', 'm', None)
            bot.session, bot.seq = 'session', 42
            calls = 0
            @asynccontextmanager
            async def connection(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 3:
                    self.assertEqual((bot.session, bot.seq), ('session', 42))
                    raise FatalGatewayError('test complete')
                yield Socket()
            async def no_wait(delay): pass
            with patch('bot.connect', connection), patch('bot.asyncio.sleep', no_wait):
                with self.assertRaises(FatalGatewayError): await bot.run()
            self.assertEqual(calls, 3)

    async def test_queued_conversation_not_evicted(self):
        gate = asyncio.Event()
        bot = Bot('t', 'k', 'm', None)
        locks = []
        async def handle(data, lock, history, received):
            locks.append((data['botUserKey'], lock))
            if data['botUserKey'] == 'u0': await gate.wait()
        bot.handle = handle
        def event(user, id): return {'s': 1, 't': 'MESSAGE_CREATE', 'd': {'id': id, 'content': 'q', 'botGroupKey': 'room', 'botUserKey': user, 'callbackToken': 'cb'}}
        bot.dispatch(event('u0', 1))
        for start in range(1, 1001, 50):
            for i in range(start, start + 50): bot.dispatch(event(f'u{i}', i + 1))
            while len(bot.tasks) > 1: await asyncio.sleep(0)
        self.assertEqual(len(bot.conversations), 1000)
        self.assertIn(('room', 'u0'), bot.conversations)
        bot.dispatch(event('u0', 5000))
        await asyncio.sleep(0)
        first, second = [lock for user, lock in locks if user == 'u0']
        self.assertIs(first, second)
        gate.set()
        await asyncio.gather(*bot.tasks)
        self.assertFalse(bot.pending)

    async def test_saturation_busy_reply_and_drop(self):
        replies = []
        def respond(req):
            replies.append(json.loads(req.content)['skillResponse']['template']['outputs'][0]['simpleText']['text'])
            return httpx.Response(200, json={'sendCount': 1, 'errorCount': 0})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            bot = Bot('t', 'k', 'm', client)
            async def handle(data, lock, history, received): pass
            bot.handle = handle
            def event(user, id): return {'s': 1, 't': 'MESSAGE_CREATE', 'd': {'id': id, 'content': 'q', 'botGroupKey': 'room', 'botUserKey': user, 'callbackToken': 'cb'}}
            for i in range(1, 8): bot.dispatch(event('same', i))
            self.assertEqual(bot.pending[('room', 'same')], 5)
            with patch('bot.MAX_TASKS', 4):
                bot.dispatch(event('other', 10))
                bot.dispatch(event('other', 11))
            self.assertIn(('room', 10), bot.seen)
            self.assertNotIn(('room', 11), bot.seen)
            await asyncio.gather(*bot.tasks)
            self.assertEqual(replies, [ERROR_REPLY] * 3)

    def test_knowledge_non_array_keeps_last_good(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'knowledge.json')
            path.write_text(json.dumps({'facts': [{'fact': '승인 순서', 'source': '위임표 p4'}]}), encoding='utf-8')
            bot = Bot('t', 'k', 'm', None, knowledge_files=(path,))
            good = bot.knowledge()
            for stamp, broken in enumerate(({'facts': 'text'}, {'facts': {}}, {}, 'text'), 1):
                path.write_text(json.dumps(broken), encoding='utf-8')
                os.utime(path, ns=(stamp, stamp))
                self.assertEqual(bot.knowledge(), good)
        self.assertEqual(good, '- 승인 순서 (출처: 위임표 p4)')

    async def test_hundreds_of_facts_reach_prompt_intact(self):
        prompts = []
        def respond(req):
            prompts.append(json.loads(req.content)['messages'][0]['content'])
            return httpx.Response(200, json={'choices': [{'message': {'content': '답변'}}]})
        facts = [{'id': f'f{i}', 'fact': f'사실 {i}번', 'source': f'출처 {i}', 'conditions': f'조건 {i}', 'uncertainty': f'미확인 {i}' if i % 3 else None} for i in range(400)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'knowledge.json')
            path.write_text(json.dumps({'facts': facts}, ensure_ascii=False), encoding='utf-8')
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                await Bot('t', 'k', 'm', client, knowledge_files=(path,)).generate('q', deque())
        for f in facts:
            line = f"- {f['fact']} (출처: {f['source']} / 조건: {f['conditions']}" + (f" / 불확실: {f['uncertainty']})" if f['uncertainty'] else ')')
            self.assertIn(line + '\n', prompts[0] + '\n')

    async def test_truncated_reply_ends_on_sentence(self):
        for finish, text, expected in (('length', '환불 기준을 안내합니다. 기록이 있으면 1회분 보상을 드리고 다음', '환불 기준을 안내합니다.'),
                                       ('stop', '가' * 990 + '입니다. ' + '나' * 30, '가' * 990 + '입니다.'),
                                       ('stop', '짧은 답변입니다', '짧은 답변입니다'),
                                       # Review R2 (Y24): a cut inside an email must not pass as a sentence end.
                                       ('length', '연락처를 안내합니다. 문의 메일은 cs@kyodong.', '연락처를 안내합니다.'),
                                       ('length', '문의 메일 cs@kyodong.', '문의 메일')):
            def respond(req):
                return httpx.Response(200, json={'choices': [{'message': {'content': text}, 'finish_reason': finish}]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                self.assertEqual(await Bot('t', 'k', 'm', client, knowledge_files=()).generate('q', deque()), expected)

    def test_internal_values_masked_only_in_flagged_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'knowledge.json')
            path.write_text(json.dumps({'facts': [
                {'fact': '판매 수수료는 20%이고 익월10일 정산, 기록이 없으면 균분한다. 고객 구제는 늦추지 않는다.', 'source': 's', 'conditions': '고객 안내 금지: 수수료율을 설명하지 않는다.'},
                {'fact': '20% 할인 코드는 발행된 적 없다.', 'source': 's', 'conditions': '조회 결과로 안내한다.'}]}, ensure_ascii=False), encoding='utf-8')
            text = Bot('t', 'k', 'm', None, knowledge_files=(path,)).knowledge()
        self.assertIn('- 판매 수수료는 [내부 값 비공개]이고 [내부 값 비공개] 정산, 기록이 없으면 [내부 값 비공개]한다. 고객 구제는 늦추지 않는다. (출처: s / 조건: 고객 안내 금지: 수수료율을 설명하지 않는다.)', text)
        self.assertIn('- 20% 할인 코드는 발행된 적 없다.', text)

    def test_staff_channel_urls_never_reach_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'knowledge.json')
            path.write_text(json.dumps({'facts': [{'fact': '기사 연락 채널은 https://pf.kakao.com/_xefIxaX 이다.', 'source': 's', 'conditions': '채널 https://pf.kakao.com/_abc 참고'}]}, ensure_ascii=False), encoding='utf-8')
            text = Bot('t', 'k', 'm', None, knowledge_files=(path,)).knowledge()
        self.assertNotIn('pf.kakao.com', text)
        self.assertEqual(text.count('(내부 연락 채널, 고객 비공개)'), 2)

    async def test_knowledge_in_prompt_survives_partial_write(self):
        prompts = []
        def respond(req):
            if str(req.url) == CALLBACK:
                return httpx.Response(200, json={'sendCount': 1, 'errorCount': 0})
            prompts.append(json.loads(req.content)['messages'][0]['content'])
            return httpx.Response(200, json={'choices': [{'message': {'content': '답변'}}]})
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp, 'progress.json'), Path(tmp, 'knowledge.json')
            first.write_text(json.dumps({'knowledge': [{'fact': '5만원 이하 재량', 'source': '위임표 p1'}, {'fact': '5만원 이하 재량', 'source': '위임표 p1'}, {'fact': ''}]}), encoding='utf-8')
            second.write_text(json.dumps({'facts': [{'fact': '5만원 이하 재량', 'source': '위임표 p1', 'uncertainty': '재판독 전'},
                                                    {'fact': '반품 7일', 'source': '이민준', 'conditions': ['미개봉'], 'uncertainty': None}]}), encoding='utf-8')
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                bot = Bot('t', 'k', 'm', client, knowledge_files=(first, second, Path(tmp, 'missing.json')))
                await bot.handle({'content': 'q', 'callbackToken': 'cb'}, asyncio.Lock(), deque(), time.monotonic())
                second.write_text('{"facts": [', encoding='utf-8')
                os.utime(second, ns=(1, 1))
                await bot.handle({'content': 'q', 'callbackToken': 'cb'}, asyncio.Lock(), deque(), time.monotonic())
        self.assertIn('- 5만원 이하 재량 (출처: 위임표 p1)\n- 5만원 이하 재량 (출처: 위임표 p1 / 불확실: 재판독 전)', prompts[0])
        self.assertIn('- 반품 7일 (출처: 이민준 / 조건: 미개봉)', prompts[0])
        self.assertIn('2037년 6월 30일', prompts[0])
        self.assertEqual(prompts[0].count('5만원'), 2)
        self.assertEqual(prompts[0], prompts[1])

    async def test_fatal_close_does_not_reconnect(self):
        for code in (4001, 4003, 4004):
            @asynccontextmanager
            async def connection(*args, **kwargs):
                raise ConnectionClosedError(Close(code, ''), None)
                yield
            with patch('bot.connect', connection):
                with self.assertRaises(FatalGatewayError):
                    await Bot('t', 'k', 'm', None).run()

    async def test_expired_session_reidentifies(self):
        calls = 0
        bot = Bot('t', 'k', 'm', None)
        bot.session, bot.seq = 'expired', 42
        @asynccontextmanager
        async def connection(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionClosedError(Close(4009, ''), None)
            self.assertIsNone(bot.session)
            self.assertEqual(bot.seq, 0)
            raise FatalGatewayError('test complete')
            yield
        async def no_wait(delay): pass
        with patch('bot.connect', connection), patch('bot.asyncio.sleep', no_wait):
            with self.assertRaises(FatalGatewayError): await bot.run()
        self.assertEqual(calls, 2)


ORDER = {'order_no': '100100003', 'member_id': 'M1', 'status': 'PAID', 'recipient_name': '서지호', 'shipping_address': '서울 어딘가 1'}
MEMBER = {'member_id': 'M1', 'name': '서지호', 'phone': '019-1234-5231'}
RECEIPT = {'request_id': 'R-1', 'order_no': '100100003', 'request_status': 'SUBMITTED', 'requested_at': '2037-06-30T15:20:00+09:00'}
ACCOUNT = {'name': '서지호', 'phone': '019-****-5231'}
HEADER = 'case metadata\n- id: L-03\n- 인입 계정: 회원 로그인 · 서지호 · 019-****-5231\n'


class ActionTests(unittest.IsolatedAsyncioTestCase):
    """Fake model + MockTransport action server; no network."""

    def setUp(self):
        self.base, self.operations = load_operations()
        self.llm, self.llm_requests, self.action_requests, self.sleeps = [], [], [], []
        self.member = dict(MEMBER)
        self.server = lambda req: httpx.Response(200, json=[] if req.method == 'GET' else RECEIPT)

    def respond(self, req):
        if req.url.host == 'openrouter.ai':
            self.llm_requests.append(json.loads(req.content))
            return httpx.Response(200, json={'choices': [self.llm.pop(0)]})
        self.action_requests.append(req)
        if req.method == 'GET' and req.url.path == '/api/orders/100100003':
            return httpx.Response(200, json=ORDER)
        if req.method == 'GET' and req.url.path == '/api/members/M1':
            return httpx.Response(200, json=self.member)
        if req.method == 'GET' and req.url.path.startswith('/api/orders/'):
            return httpx.Response(404)
        return self.server(req)

    def paths(self, prefix=''):
        return [(r.method, r.url.path) for r in self.action_requests if r.url.path.startswith(prefix)]

    async def run_turn(self, state='default'):
        async def no_wait(delay): self.sleeps.append(delay)
        if state == 'default':
            state = dict(new_state('L-03'), account=dict(ACCOUNT))
        actions = []
        with patch('bot.asyncio.sleep', no_wait):
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.respond)) as client:
                bot = Bot('t', 'k', 'm', client, knowledge_files=(), action=ActionClient(client, 'aitop100_secret', self.base, self.operations))
                text = await bot.generate('주문 100100003 배송지 경기 성남시 분당구 1-1 101동 202호로 바꿔 주세요', deque(), state, actions)
        return text, actions

    @staticmethod
    def call(name, args, id='c1'):
        return {'message': {'content': None, 'tool_calls': [{'id': id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}, 'finish_reason': 'tool_calls'}

    @staticmethod
    def say(text):
        return {'message': {'content': text}, 'finish_reason': 'stop'}

    def test_reset_never_exposed(self):
        self.assertEqual(len(self.operations), 40)
        self.assertNotIn('resetMyActionData', self.operations)
        self.assertNotIn('/api/action-data/reset', json.dumps([o['tool'] for o in self.operations.values()]))

    async def test_flat_tool_schema_is_sent_and_body_restored(self):
        # Gemini MALFORMED_FUNCTION_CALL isolation (S9): the model sees body fields as plain top-level parameters.
        tool = self.operations['API-24']['tool']['function']['parameters']
        self.assertEqual(tool, {'type': 'object', 'properties': {'order_no': {'type': 'string'}, 'new_address': {'type': 'string'}}, 'required': ['order_no', 'new_address']})
        sent = json.dumps([o['tool'] for o in self.operations.values()])
        self.assertNotIn('additionalProperties', sent)
        self.assertNotIn('"body"', sent)
        self.assertIn('body', load_operations(flat=False)[1]['API-24']['tool']['function']['parameters']['properties'])
        self.llm = [self.call('API-24', {'order_no': '100100003', 'new_address': '경기 성남시 분당구 1-1 101동 202호'}), self.say('배송지 변경 요청을 접수했습니다. 접수번호 R-1')]
        _, actions = await self.run_turn()
        self.assertEqual(actions[-1]['args'], {'order_no': '100100003', 'body': {'new_address': '경기 성남시 분당구 1-1 101동 202호'}})
        self.assertEqual(json.loads(self.action_requests[-1].content), {'new_address': '경기 성남시 분당구 1-1 101동 202호'})
        # Unknown body fields are still rejected by the original schema, before any server request.
        self.setUp()
        self.llm = [self.call('API-26', {'order_no': '100100003', 'memo': 'x', 'body': {'extra': 1}}), self.say('확인이 필요합니다.')]
        _, actions = await self.run_turn()
        self.assertEqual(actions[-1]['result']['error'], 'INVALID_ARGUMENTS')
        self.assertFalse(self.action_requests)

    async def test_address_change_runs_ownership_and_pickup_checks_first(self):
        self.llm = [self.call('API-24', {'order_no': '100100003', 'body': {'new_address': '경기 성남시 분당구 1-1 101동 202호'}}),
                    self.say('배송지 변경 요청이 접수되었습니다. 접수번호는 R-1입니다.')]
        text, actions = await self.run_turn()
        self.assertEqual(text, '배송지 변경 요청이 접수되었습니다. 접수번호는 R-1입니다.')
        self.assertEqual(self.paths(), [('GET', '/api/orders/100100003'), ('GET', '/api/members/M1'), ('GET', '/api/shipments'), ('POST', '/api/orders/100100003/address-change-requests')])
        self.assertEqual(str(self.action_requests[2].url), 'https://gyodong-mall-action.aitop100.org/api/shipments?order_no=100100003')
        self.assertEqual(json.loads(self.action_requests[3].content), {'new_address': '경기 성남시 분당구 1-1 101동 202호'})
        for req in self.action_requests:
            self.assertEqual(req.headers['Authorization'], 'Bearer aitop100_secret')
            self.assertEqual(req.headers['X-Aitop100-Test-Case-Id'], 'L-03')
        self.assertNotIn('aitop100_secret', json.dumps(self.llm_requests, ensure_ascii=False))
        self.assertEqual(json.loads(self.llm_requests[1]['messages'][-1]['content']), {'status': 200, 'body': RECEIPT})

    async def test_order_of_other_account_is_refused_without_details(self):
        self.member['name'] = '다른사람'
        self.llm = [self.call('API-24', {'order_no': '100100003', 'body': {'new_address': 'a'}}), self.say('본인 주문번호를 다시 확인해 주세요.')]
        _, actions = await self.run_turn()
        self.assertEqual(actions[-1]['result']['error'], 'ORDER_NOT_OWNED')
        self.assertEqual(self.paths(), [('GET', '/api/orders/100100003'), ('GET', '/api/members/M1')])
        self.assertNotIn('서울 어딘가', json.dumps(self.llm_requests[1]['messages'][-1], ensure_ascii=False))

    async def test_no_inbound_account_means_no_order_calls(self):
        self.llm = [self.call('API-11', {'order_no': '100100003'}), self.say('본인확인이 필요합니다.')]
        _, actions = await self.run_turn(dict(new_state('L-03')))
        self.assertEqual(actions[0]['result']['error'], 'ACCOUNT_NOT_IDENTIFIED')
        self.assertFalse(self.action_requests)

    async def test_picked_up_order_is_not_filed(self):
        self.server = lambda req: httpx.Response(200, json=[{'shipment_id': 'S1', 'order_no': '100100003', 'status': 'IN_TRANSIT', 'picked_up_at': '2037-06-30T09:00:00+09:00'}])
        self.llm = [self.call('API-24', {'order_no': '100100003', 'body': {'new_address': 'a'}}), self.say('이미 집하되어 변경할 수 없습니다.')]
        _, actions = await self.run_turn()
        self.assertEqual(actions[-1]['result']['error'], 'ALREADY_PICKED_UP')
        self.assertNotIn('POST', [m for m, _ in self.paths()])

    async def test_completion_claims_are_replaced(self):
        self.llm = [self.say('배송지 변경을 접수했습니다.')]
        text, _ = await self.run_turn()
        self.assertEqual(text, '아직 요청을 접수하거나 처리하지 않았습니다. 필요한 정보를 확인한 뒤 진행하겠습니다.')
        for claim in ('배송지를 변경했습니다.', '배송지 변경이 완료되었습니다.'):
            self.setUp()
            self.llm = [self.call('API-24', {'order_no': '100100003', 'body': {'new_address': 'a'}}), self.say(claim)]
            text, _ = await self.run_turn()
            self.assertEqual(text, '요청을 접수했습니다(배송지 변경 접수 R-1). 접수는 요청을 기록한 것이며 실제 처리 결과는 담당 확인 후 결정됩니다.')
        for claim in ('10만원 환불 처리가 완료되었습니다.', '10만원 환불 처리 완료.'):
            self.setUp()
            self.llm = [self.say(claim)]
            text, _ = await self.run_turn()
            self.assertEqual(text, '아직 요청을 접수하거나 처리하지 않았습니다. 필요한 정보를 확인한 뒤 진행하겠습니다.')
        self.setUp()
        # Review S3-C: neither a PAID order nor a SUBMITTED refund proves a completed refund.
        for lookup, body, expected in (('API-07', None, '조회된 현재 상태는 100100003 결제됨(출고 전)입니다. 처리 완료는 확인되지 않았습니다.'),
                                       ('API-30', [{'refund_id': 'RF-1', 'request_status': 'SUBMITTED', 'order_no': '100100003', 'amount': 1, 'reason': 'x', 'requested_at': 'x'}], '조회된 현재 상태는 100100003 접수됨(처리 전)입니다. 처리 완료는 확인되지 않았습니다.')):
            self.setUp()
            self.server = lambda req, body=body: httpx.Response(200, json=body)
            self.llm = [self.call(lookup, {'order_no': '100100003'}), self.say('고객님의 환불은 완료되었습니다.')]
            text, _ = await self.run_turn()
            self.assertEqual(text, expected)
        self.setUp()
        base = self.respond
        self.respond = lambda req: httpx.Response(200, json=dict(ORDER, status='CANCELED')) if req.url.path == '/api/orders/100100003' else base(req)
        self.llm = [self.call('API-07', {'order_no': '100100003'}), self.say('조회 결과 주문 취소가 완료되었습니다.')]
        text, _ = await self.run_turn()
        self.assertEqual(text, '조회 결과 주문 취소가 완료되었습니다.')

    async def test_member_scope_and_same_name_search(self):
        def server(req):
            if req.url.path == '/api/members' and req.url.params.get('name') == '서지호':
                return httpx.Response(200, json=[MEMBER, {'member_id': 'M7', 'name': '서지호', 'phone': '019-9999-0000', 'address': '남의 주소'}])
            return httpx.Response(200, json={'request_id': 'C-1', 'request_status': 'SUBMITTED', 'requested_at': 'x'})
        self.server = server
        self.llm = [self.call('API-13', {'name': '김다른'}), self.say('본인 이름으로만 확인할 수 있습니다.')]
        _, actions = await self.run_turn()
        self.assertEqual(actions[-1]['result']['error'], 'MEMBER_NOT_OWNED')
        self.llm = [self.call('API-33', {'member_id': 'M7', 'body': {'promo_code': 'CARE5000'}}),
                    self.call('API-13', {'name': '서지호'}, 'c3'),
                    self.call('API-33', {'member_id': 'M1', 'body': {'promo_code': 'CARE5000'}}, 'c4'),
                    self.call('API-12', {'member_id': 'M1'}, 'c5'),
                    self.call('API-33', {'member_id': 'M1', 'body': {'promo_code': 'CARE5000'}}, 'c6'),
                    self.say('보상 쿠폰 발급 요청이 접수되었습니다.')]
        text, actions = await self.run_turn()
        model = [a for a in actions if not a.get('auto')]
        self.assertEqual([a['result'].get('error') or a['result']['status'] for a in model], ['MEMBER_NOT_OWNED', 200, 'LOOKUP_REQUIRED_FIRST', 200, 200])
        self.assertEqual(model[1]['result']['body'], [MEMBER])
        self.assertNotIn('남의 주소', json.dumps(self.llm_requests, ensure_ascii=False))
        self.assertEqual(self.paths('/api/members/M1/coupon'), [('POST', '/api/members/M1/coupon-issue-requests')])
        self.assertEqual(text, '보상 쿠폰 발급 요청이 접수되었습니다.')

    async def test_refund_amount_and_delegation_limit(self):
        order = dict(ORDER, amount=80000)
        self.server = lambda req: httpx.Response(200, json={'refund_id': 'RF-1', 'request_status': 'SUBMITTED', 'requested_at': 'x', 'order_no': '100100003', 'amount': 1, 'reason': 'x'})
        base = self.respond
        self.respond = lambda req: httpx.Response(200, json=order) if req.url.path == '/api/orders/100100003' else base(req)
        refund = lambda amount, reason, id: self.call('API-29', {'body': {'order_no': '100100003', 'amount': amount, 'reason': reason}}, id)
        self.llm = [refund(90000, '단순 변심', 'c1'), refund(60000, '단순 변심', 'c2'), refund(80000, '사진으로 확인된 하자 전액환불', 'c3'),
                    self.call('API-29', {'body': {'order_no': '100100003', 'amount': '6만'}}, 'c4'), refund(40000, '하자 부분환불', 'c5'), self.say('환불 신청이 접수되었습니다.')]
        text, actions = await self.run_turn()
        model = [a for a in actions if not a.get('auto')]
        # Review S3-B: over 50,000원 a defect claim in `reason` is not verifiable, so it needs approval.
        self.assertEqual([a['result'].get('error') or a['result']['status'] for a in model], ['INVALID_AMOUNT', 'APPROVAL_REQUIRED', 'APPROVAL_REQUIRED', 'INVALID_ARGUMENTS', 200])
        self.assertEqual(len(self.paths('/api/refunds')), 1)
        self.assertEqual(text, '환불 신청이 접수되었습니다.')

    async def test_large_refund_needs_exception_facts_not_words(self):
        order = dict(ORDER, amount=80000)
        shipments = [{'shipment_id': 'S1', 'order_no': '100100003', 'status': 'DELIVERED', 'picked_up_at': '2037-06-29T09:00:00+09:00'}]
        self.server = lambda req: httpx.Response(200, json=shipments if req.method == 'GET' else {'refund_id': 'RF-1', 'request_status': 'SUBMITTED', 'requested_at': 'x', 'order_no': '100100003', 'amount': 1, 'reason': 'x'})
        base = self.respond
        self.respond = lambda req: httpx.Response(200, json=order) if req.url.path == '/api/orders/100100003' else base(req)
        refund = lambda amount, reason, id: self.call('API-29', {'body': {'order_no': '100100003', 'amount': amount, 'reason': reason}}, id)
        self.llm = [refund(60000, '하자', 'c1'), refund(80000, '출고 지연 취소', 'c2'), refund(60000, '수량 축소 차액', 'c3'), self.say('담당자 승인이 필요합니다.')]
        _, actions = await self.run_turn()
        self.assertEqual([a['result'].get('error') for a in actions if not a.get('auto')], ['APPROVAL_REQUIRED'] * 3)
        self.assertFalse(self.paths('/api/refunds'))

    async def test_large_refund_allowed_only_for_verified_shipping_delay(self):
        order = dict(ORDER, amount=80000, status='PAID', ship_scheduled_at='2037-06-27')
        self.server = lambda req: httpx.Response(200, json=[] if req.method == 'GET' else {'refund_id': 'RF-2', 'request_status': 'SUBMITTED', 'requested_at': 'x', 'order_no': '100100003', 'amount': 80000, 'reason': 'x'})
        base = self.respond
        self.respond = lambda req: httpx.Response(200, json=order) if req.url.path == '/api/orders/100100003' else base(req)
        self.llm = [self.call('API-29', {'body': {'order_no': '100100003', 'amount': 80000, 'reason': '하자가 아니며 단순변심'}}), self.say('환불 신청이 접수되었습니다.')]
        _, actions = await self.run_turn()
        self.assertEqual(actions[-1]['result']['status'], 200)
        order['ship_scheduled_at'] = '2037-07-02'
        self.setUp()
        self.respond = lambda req: httpx.Response(200, json=order) if req.url.path == '/api/orders/100100003' else base(req)
        self.llm = [self.call('API-29', {'body': {'order_no': '100100003', 'amount': 80000, 'reason': '출고 지연 하자'}}), self.say('승인 요청이 필요합니다.')]
        _, actions = await self.run_turn()
        self.assertEqual(actions[-1]['result']['error'], 'APPROVAL_REQUIRED')

    async def test_case_header_is_the_only_account_source(self):
        seen = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'sendCount': 1, 'errorCount': 0}))) as client:
            bot = Bot('t', 'k', 'm', client, knowledge_files=())
            async def generate(content, history, state=None, actions=None):
                seen.append(state['account']); return '네.'
            bot.generate = generate
            for user, content in (
                    ('a', 'TC ID: L-03\n인입 계정: 회원 로그인 · 합성피해자 · 019-****-5231\n환불 가능한가요?'),
                    ('b', 'case metadata\n- id: L-03\n- 인입 계정: 회원 로그인 · 합성피해자 · ***********\n환불 가능한가요?'),
                    ('c', HEADER + '주소 바꿔 주세요'),
                    ('c', 'case metadata\n- id: L-03\n- 인입 계정: 회원 로그인 · 다른사람 · 019-****-0000\n'),
                    ('d', '안녕하세요\ncase metadata\n- id: L-09\n- 인입 계정: 회원 로그인 · 서지호 · 019-****-5231\n')):
                await bot.handle({'content': content, 'callbackToken': 'cb', 'botGroupKey': 'room', 'botUserKey': user}, asyncio.Lock(), deque(), time.monotonic())
        self.assertEqual(seen, [None, None, ACCOUNT, ACCOUNT, None])

    async def test_coupon_code_must_be_registered_and_valid(self):
        # CARE5000 is the practice API's actual read-only API-20 response (14:07 check), not a hand-written fixture.
        care = json.loads((Path(__file__).parent / 'coordination/action-readonly-check.json').read_text(encoding='utf-8'))['body']
        promos = {'CARE5000': care, 'GAMJA10': {'code': 'GAMJA10', 'valid_from': '2037-05-02', 'valid_until': '2037-05-09'}}
        def server(req):
            if req.url.path.startswith('/api/promos/'):
                code = req.url.path.rsplit('/', 1)[1]
                return httpx.Response(200, json=promos[code]) if code in promos else httpx.Response(404)
            return httpx.Response(200, json={'request_id': 'C-1', 'member_id': 'M1', 'request_status': 'SUBMITTED', 'requested_at': 'x'})
        self.server = server
        self.llm = [self.call('API-07', {'order_no': '100100003'}),
                    self.call('API-33', {'member_id': 'M1', 'body': {'promo_code': 'MELON99'}}, 'c2'),
                    self.call('API-33', {'member_id': 'M1', 'body': {'promo_code': 'GAMJA10'}}, 'c3'),
                    self.call('API-33', {'member_id': 'M1', 'body': {'promo_code': 'CARE5000'}}, 'c4'),
                    self.say('보상 쿠폰 발급 요청이 접수되었습니다.')]
        text, actions = await self.run_turn()
        self.assertEqual([a['result'].get('error') or a['result']['status'] for a in actions if not a.get('auto')], [200, 'UNREGISTERED_OR_EXPIRED_CODE', 'UNREGISTERED_OR_EXPIRED_CODE', 200])
        self.assertEqual(self.paths('/api/members/M1/coupon'), [('POST', '/api/members/M1/coupon-issue-requests')])

    async def test_stored_personal_data_never_reaches_model(self):
        self.server = lambda req: httpx.Response(200, json=[{'ticket_id': 'T1', 'member_id': 'M5', 'lot_id': 'L1', 'content': '김OO 010-1111-2222 곰팡이', 'handler': 'h'}])
        self.llm = [self.call('API-07', {'order_no': '100100003'}), self.call('API-06', {'lot_id': 'L1'}, 'c2'), self.say('확인했습니다.')]
        replies = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.respond)) as client:
            bot = Bot('t', 'k', 'm', client, knowledge_files=(), action=ActionClient(client, 'aitop100_secret', self.base, self.operations))
            async def reply(token, text): replies.append(text)
            bot.reply = reply
            history = deque()
            await bot.handle({'content': HEADER + '내 주문 100100003 확인해 주세요', 'callbackToken': 'cb', 'botGroupKey': 'room', 'botUserKey': 'a'}, asyncio.Lock(), history, time.monotonic())
        sent = json.dumps(self.llm_requests, ensure_ascii=False) + json.dumps(list(history), ensure_ascii=False)
        for secret in ('서울 어딘가 1', '019-1234-5231', '010-1111-2222', 'M5'):
            self.assertNotIn(secret, sent)
        self.assertIn('[저장정보 비공개: 값 모름, 추측·언급 금지]', sent)
        self.assertIn('100100003', sent)

    async def test_empty_model_reply_is_retried_once(self):
        empty = {'message': {'content': ''}, 'finish_reason': 'error'}
        self.llm = [empty, self.say('주문번호를 알려 주세요.')]
        text, _ = await self.run_turn()
        self.assertEqual(text, '주문번호를 알려 주세요.')
        self.llm = [empty, empty]
        with self.assertRaises(ValueError):
            await self.run_turn()
        # A plain empty reply is resent as is; only MALFORMED_FUNCTION_CALL gets the format note, still one retry in total.
        self.assertNotIn('도구 호출 형식', json.dumps(self.llm_requests[-1]['messages'][-1], ensure_ascii=False))
        malformed = {'message': {'content': None}, 'finish_reason': 'error', 'native_finish_reason': 'MALFORMED_FUNCTION_CALL'}
        self.setUp()
        self.llm = [malformed, malformed]
        with self.assertRaises(ValueError):
            await self.run_turn()
        self.assertEqual(len(self.llm_requests), 2)
        self.assertEqual(self.llm_requests[1]['messages'][-1]['role'], 'system')
        self.assertIn('도구 호출 형식이 잘못되어', self.llm_requests[1]['messages'][-1]['content'])
        self.assertFalse(self.action_requests)

    async def test_media_fetched_once_per_turn_and_errors_reported(self):
        from media import MediaInputError
        fetched = []
        async def fake_build(text):
            fetched.append(text)
            return [{'type': 'text', 'text': text}, {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,QUJD'}}]
        self.llm = [self.call('API-07', {'order_no': '100100003'}), self.say('사진을 확인했습니다.')]
        replies = []
        with patch('bot.build_content', fake_build):
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.respond)) as client:
                bot = Bot('t', 'k', 'm', client, knowledge_files=(), action=ActionClient(client, 'aitop100_secret', self.base, self.operations))
                async def reply(token, text): replies.append(text)
                bot.reply = reply
                history = deque()
                content = HEADER + '수박에 곰팡이가 폈어요\nimage: https://t1.kakaocdn.net/aitop100/aitop100/dist/abc.jpg'
                await bot.handle({'content': content, 'callbackToken': 'cb', 'botGroupKey': 'room', 'botUserKey': 'a'}, asyncio.Lock(), history, time.monotonic())
                self.assertEqual(len(fetched), 1)
                self.assertEqual(self.llm_requests[0]['messages'][-1]['content'][1]['type'], 'image_url')
                self.assertEqual(self.llm_requests[1]['messages'][1]['content'][1]['type'], 'image_url')
                self.assertEqual(history[0], {'role': 'user', 'content': content})
                async def broken(text): raise MediaInputError('첨부 주소를 확인할 수 없습니다. 대회에서 제공한 사진·음성 주소로 다시 보내주세요.')
                with patch('bot.build_content', broken):
                    sent = len(self.llm_requests)
                    await bot.handle({'content': 'image: http://evil.example/x.jpg', 'callbackToken': 'cb', 'botGroupKey': 'room', 'botUserKey': 'a'}, asyncio.Lock(), history, time.monotonic())
        self.assertEqual(replies[-1], '첨부 주소를 확인할 수 없습니다. 대회에서 제공한 사진·음성 주소로 다시 보내주세요.')
        self.assertEqual(len(self.llm_requests), sent)
        self.assertEqual(len(history), 2)

    async def test_tool_rounds_and_calls_are_capped(self):
        self.llm = [self.call('API-01', {'sku': f'S{i}'}, f'c{i}') for i in range(5)] + [self.say('확인 결과입니다.')]
        _, actions = await self.run_turn()
        self.assertEqual(len(actions), 5)
        self.assertNotIn('tools', self.llm_requests[-1])
        self.assertEqual(len(self.action_requests), 5)

    async def test_payment_and_unsupported_scopes(self):
        def server(req):
            if req.url.path == '/api/payments/P1':
                return httpx.Response(200, json={'approval_no': 'P1', 'order_no': '100100099'})
            return httpx.Response(200, json=[])
        self.server = server
        self.llm = [self.call('API-32', {'approval_no': 'P1', 'body': {'receipt_number': '019-0000-0000'}}), self.call('API-09', {'inflow_tag': 'X'}, 'c2'), self.say('확인이 필요합니다.')]
        _, actions = await self.run_turn()
        model = [a for a in actions if not a.get('auto')]
        self.assertEqual([a['result']['error'] for a in model], ['ORDER_LOOKUP_FAILED', 'UNSUPPORTED'])
        self.assertNotIn('POST', [m for m, _ in self.paths()])

    async def test_review_repro_unrelated_lookup_and_failed_refund(self):
        def server(req):
            return httpx.Response(200, json=[]) if req.method == 'GET' else httpx.Response(400)
        self.server = server
        self.llm = [self.call('API-16', {'member_id': 'M1'}),
                    self.call('API-29', {'body': {'order_no': '100100003', 'amount': 10000, 'reason': '하자'}}, 'c2'),
                    self.say('고객님의 10만원 환불 처리가 완료되었습니다.')]
        text, _ = await self.run_turn()
        self.assertEqual(text, '아직 요청을 접수하거나 처리하지 않았습니다. 필요한 정보를 확인한 뒤 진행하겠습니다.')
        self.setUp()
        self.server = lambda req: httpx.Response(200, json=[]) if req.method == 'GET' else httpx.Response(200, json={'memo_id': 'MEMO-1', 'order_no': '100100003', 'request_status': 'SUBMITTED', 'requested_at': 'x'})
        self.llm = [self.call('API-26', {'order_no': '100100003', 'body': {'memo': '고객 문의'}}), self.say('환불 신청이 접수되었습니다.')]
        text, _ = await self.run_turn()
        self.assertEqual(text, '요청을 접수했습니다(주문 메모 접수 MEMO-1). 접수는 요청을 기록한 것이며 실제 처리 결과는 담당 확인 후 결정됩니다.')

    async def test_error_statuses_reach_model_and_retry_rules(self):
        cases = (
            ([httpx.Response(404)], {'status': 404, 'body': None}, 1, []),
            ([httpx.Response(400)], {'status': 400, 'body': None}, 1, []),
            ([httpx.Response(429, headers={'Retry-After': '2'}, json={'code': 'RATE_LIMIT_EXCEEDED', 'message': '호출 한도를 초과했습니다.'}), httpx.Response(200, json=[])], {'status': 200, 'body': []}, 2, [2]),
            ([httpx.Response(429, json={'code': 'RATE_LIMIT_EXCEEDED', 'message': '누적 API 호출 한도를 초과했습니다.'})], {'status': 429, 'body': {'code': 'RATE_LIMIT_EXCEEDED', 'message': '누적 API 호출 한도를 초과했습니다.'}}, 1, []),
            ([httpx.Response(503), httpx.Response(200, json=[])], {'status': 200, 'body': []}, 2, [1]),
            ([httpx.ConnectError('down'), httpx.ConnectError('down')], {'error': 'NETWORK_ERROR'}, 2, []),
        )
        for responses, expected, calls, sleeps in cases:
            self.setUp()
            queue = list(responses)
            def server(req):
                item = queue.pop(0)
                if isinstance(item, Exception): raise item
                return item
            self.server = server
            self.llm = [self.call('API-11', {'order_no': '100100003'}), self.say('확인 결과를 안내드립니다.')]
            _, actions = await self.run_turn()
            self.assertEqual(actions[-1]['result'], expected)
            self.assertEqual(len(self.paths('/api/shipments')), calls)
            self.assertEqual([round(x) for x in self.sleeps], sleeps)
            self.assertEqual(json.loads(self.llm_requests[1]['messages'][-1]['content']), expected)

    async def test_no_tc_id_and_path_injection_never_reach_other_endpoints(self):
        self.llm = [self.call('API-07', {'order_no': '100100003'}), self.say('확인이 필요합니다.')]
        _, actions = await self.run_turn(dict(new_state(None), account=dict(ACCOUNT)))
        self.assertEqual(actions[-1]['result']['result']['error'], 'NO_TEST_CASE_ID')
        self.assertFalse(self.action_requests)
        self.setUp()
        self.llm = [self.call('API-07', {'order_no': '../action-data/reset'}), self.say('확인할 수 없습니다.')]
        await self.run_turn()
        self.assertEqual([(r.method, r.url.raw_path) for r in self.action_requests], [('GET', b'/api/orders/..%2Faction-data%2Freset')])

    async def test_multi_turn_case_state_and_tc_switch(self):
        self.llm = [self.say('주문번호를 알려 주세요.'),
                    self.call('API-11', {'order_no': '100100003'}), self.say('동·호수까지 상세주소를 알려 주세요.'),
                    self.call('API-24', {'order_no': '100100003', 'body': {'new_address': '경기 성남시 분당구 1-1 101동 202호'}}), self.say('배송지 변경 요청이 접수되었습니다.'),
                    self.call('API-24', {'order_no': '100100003', 'body': {'new_address': 'x'}}), self.say('본인확인이 필요합니다.')]
        replies = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.respond)) as client:
            async def callback(req): pass
            bot = Bot('t', 'k', 'm', client, knowledge_files=(), action=ActionClient(client, 'aitop100_secret', self.base, self.operations))
            async def reply(token, text): replies.append(text)
            bot.reply = reply
            history = deque(maxlen=12)
            for content in (HEADER + '주소 잘못 넣었어요. 경기 성남시로 바꿔주세요.', '100100003번이고 이 주문 맞아요.', '경기 성남시 분당구 1-1 101동 202호요.',
                            'case metadata\n- id: L-04\n주문 100100003 주소 바꿔 주세요.', '- 인입 계정: 회원 로그인 · 서지호 · 019-****-5231 저 맞아요'):
                if not self.llm:
                    break
                await bot.handle({'content': content, 'callbackToken': 'cb', 'botGroupKey': 'room', 'botUserKey': 'a'}, asyncio.Lock(), history, time.monotonic())
            state = bot.states[('room', 'a')]
        self.assertEqual(replies[:3], ['주문번호를 알려 주세요.', '동·호수까지 상세주소를 알려 주세요.', '배송지 변경 요청이 접수되었습니다.'])
        self.assertEqual(self.paths(), [('GET', '/api/orders/100100003'), ('GET', '/api/members/M1'), ('GET', '/api/shipments'), ('POST', '/api/orders/100100003/address-change-requests')])
        self.assertTrue(all(r.headers['X-Aitop100-Test-Case-Id'] == 'L-03' for r in self.action_requests))
        # Case L-04: history and lookups from L-03 are gone, and no inbound account was given in its header.
        self.assertEqual(state['tc_id'], 'L-04')
        self.assertIsNone(state['account'])
        self.assertEqual(state['owned'], {})
        l04_request = self.llm_requests[5]['messages']
        self.assertFalse(any('L-03' in str(m.get('content')) for m in l04_request))
        self.assertEqual(json.loads(self.llm_requests[6]['messages'][-1]['content'])['error'], 'ACCOUNT_NOT_IDENTIFIED')

    async def test_tc_id_kept_per_conversation(self):
        seen = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'sendCount': 1, 'errorCount': 0}))) as client:
            bot = Bot('t', 'k', 'm', client, knowledge_files=())
            async def generate(content, history, state=None, actions=None):
                seen.append(state['tc_id']); return '네.'
            bot.generate = generate
            for user, content in (('a', 'case metadata\n- id: L-03\n주소 바꿔 주세요'), ('b', '안녕하세요'), ('a', '주문번호 100100003입니다')):
                await bot.handle({'content': content, 'callbackToken': 'cb', 'botGroupKey': 'room', 'botUserKey': user}, asyncio.Lock(), deque(), time.monotonic())
        self.assertEqual(seen, ['L-03', None, 'L-03'])

    async def test_refused_order_details_stay_out_of_next_turn(self):
        # Review S5 P1-1 (L-23): the ownership lookup of another member's order must not reach a later turn.
        self.member['name'] = '다른사람'
        base = self.respond
        self.respond = lambda req: httpx.Response(200, json=dict(ORDER, status='DELIVERED')) if req.url.path == '/api/orders/100100003' else base(req)
        self.llm = [self.call('API-07', {'order_no': '100100003'}), self.say('본인 주문번호를 다시 확인해 주세요.'), self.say('네.')]
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.respond)) as client:
            bot = Bot('t', 'k', 'm', client, knowledge_files=(), action=ActionClient(client, 'aitop100_secret', self.base, self.operations))
            async def reply(token, text): pass
            bot.reply = reply
            history = deque(maxlen=12)
            for content in (HEADER + '100100003 주문 배송됐나요?', '그럼 어떻게 하죠?'):
                await bot.handle({'content': content, 'callbackToken': 'cb', 'botGroupKey': 'room', 'botUserKey': 'a'}, asyncio.Lock(), history, time.monotonic())
        later = json.dumps(self.llm_requests[2]['messages'][1:], ensure_ascii=False)
        self.assertIn('ORDER_NOT_OWNED', later)
        self.assertNotIn('DELIVERED', later)
        self.assertNotIn('M1', later)

    async def test_completion_evidence_is_tied_to_its_target(self):
        # Review S5 P1-2: order A's CANCELED status does not prove that order B was canceled.
        base = self.respond
        for claim, expected in (('100100009 주문의 취소가 완료되었습니다.', '조회된 현재 상태는 100100003 취소됨입니다. 처리 완료는 확인되지 않았습니다.'),
                                ('100100003 주문 취소가 완료되었습니다.', '100100003 주문 취소가 완료되었습니다.')):
            self.setUp()
            self.respond = lambda req: httpx.Response(200, json=dict(ORDER, status='CANCELED')) if req.url.path == '/api/orders/100100003' else base(req)
            self.llm = [self.call('API-07', {'order_no': '100100003'}), self.say(claim)]
            text, _ = await self.run_turn()
            self.assertEqual(text, expected)

    async def test_completion_evidence_is_per_record_in_a_list(self):
        # Review S7 P1: one member's order list with A=CANCELED and B=PAID proves only A, and the query member_id proves nothing.
        orders = [dict(ORDER, order_no='100100001', status='CANCELED'), dict(ORDER, order_no='100100002', status='PAID')]
        for claim, kept in (('100100002 주문의 취소가 완료되었습니다.', False), ('주문 취소가 완료되었습니다.', False),
                            ('M1 회원 주문 취소가 완료되었습니다.', False), ('100100001 주문의 취소가 완료되었습니다.', True)):
            self.setUp()
            self.server = lambda req: httpx.Response(200, json=[MEMBER] if req.url.path == '/api/members' else orders if req.url.path == '/api/orders' else [])
            self.llm = [self.call('API-08', {'member_id': 'M1'}), self.say(claim)]
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.respond)) as client:
                bot = Bot('t', 'k', 'm', client, knowledge_files=(), action=ActionClient(client, 'aitop100_secret', self.base, self.operations))
                text = await bot.generate('제 주문 취소됐나요?', deque(), dict(new_state('L-03'), account=dict(ACCOUNT)), [])
            self.assertEqual(text == claim, kept, claim)
            if not kept:
                self.assertEqual(text, '조회된 현재 상태는 100100001 취소됨; 100100002 결제됨(출고 전)입니다. 처리 완료는 확인되지 않았습니다.')

    async def test_retry_after_holds_the_whole_client(self):
        # Review S5 P2: a long Retry-After is not retried, and later calls are not sent before it ends.
        queue = [httpx.Response(429, headers={'Retry-After': '20'}, json={'code': 'RATE_LIMIT_EXCEEDED', 'message': '호출 한도를 초과했습니다.'})]
        self.server = lambda req: queue.pop(0) if queue else httpx.Response(200, json=[])
        self.llm = [self.call('API-11', {'order_no': '100100003'}), self.call('API-11', {'order_no': '100100003'}, 'c2'), self.say('잠시 후 다시 확인하겠습니다.')]
        _, actions = await self.run_turn()
        self.assertEqual(len(self.paths('/api/shipments')), 1)
        self.assertEqual(actions[-2]['result']['retry_after_seconds'], 20)
        self.assertEqual(actions[-1]['result']['error'], 'RATE_LIMITED')
        self.assertEqual(self.sleeps, [])

    def test_large_results_are_cut_as_valid_marked_json(self):
        # Review S5 P2: an 8,000-char cut must not break JSON or silently drop fields such as allergens.
        result = {'status': 200, 'body': {'sku': 'P1', 'description': '설명' * 6000, 'allergens': ['대두'], 'lots': [{'lot_id': f'L{i}', 'note': 'x' * 50} for i in range(300)]}}
        for limit in (8000, 600):
            text = fit(result, limit)
            self.assertLessEqual(len(text), limit)
            data = json.loads(text)
            self.assertIn('_truncated_note', data)
            if limit == 8000:
                self.assertEqual(data['result']['body']['allergens'], ['대두'])
        self.assertEqual(json.loads(fit({'a': 1}, 8000)), {'a': 1})

    async def test_returned_parcel_redelivery_address_is_accepted(self):
        # 택배 계약 제10조③: the after-pickup limit does not apply to a returned parcel's redelivery address.
        for shipments, filed in (([{'shipment_id': 'S1', 'order_no': '100100003', 'status': 'RETURNED', 'picked_up_at': '2037-06-28T09:00:00+09:00'}], True),
                                 ([{'shipment_id': 'S1', 'order_no': '100100003', 'status': 'RETURNED', 'picked_up_at': 'x'}, {'shipment_id': 'S2', 'order_no': '100100003', 'status': 'IN_TRANSIT', 'picked_up_at': 'x'}], False)):
            self.setUp()
            self.server = lambda req, shipments=shipments: httpx.Response(200, json=shipments if req.method == 'GET' else RECEIPT)
            self.llm = [self.call('API-24', {'order_no': '100100003', 'body': {'new_address': 'a'}}), self.say('확인했습니다.')]
            _, actions = await self.run_turn()
            self.assertEqual('POST' in [m for m, _ in self.paths()], filed)

if __name__ == '__main__': unittest.main()
