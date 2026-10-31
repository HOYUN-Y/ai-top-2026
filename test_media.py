import base64
import unittest
from unittest.mock import patch

import httpx
import media

PREFIX = "https://t1.kakaocdn.net/aitop100/aitop100/dist/"


class MediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_does_not_open_client(self):
        with patch("media.httpx.AsyncClient") as client:
            self.assertEqual(await media.build_content("배송 문의입니다"), "배송 문의입니다")
            client.assert_not_called()

    async def test_image_audio_are_real_parts_without_credentials(self):
        requests = []
        def serve(request):
            requests.append(request)
            return httpx.Response(200, headers={"content-type": "image/jpeg" if request.url.path.endswith("jpg") else "audio/mp4"}, content=b"synthetic-media")
        client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
        with patch("media.httpx.AsyncClient", return_value=client):
            parts = await media.build_content(f"image: {PREFIX}photo.jpg\n\naudio: {PREFIX}voice.m4a\n상태를 확인해주세요")
        self.assertEqual([p['type'] for p in parts], ['text', 'image_url', 'input_audio'])
        self.assertTrue(parts[1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(base64.b64decode(parts[2]['input_audio']['data']), b'synthetic-media')
        self.assertEqual(parts[2]['input_audio']['format'], 'm4a')
        self.assertTrue(all('authorization' not in r.headers and 'cookie' not in r.headers for r in requests))

    async def test_untrusted_urls_rejected_before_network(self):
        for url in ['http://127.0.0.1/a.jpg', PREFIX+'../secret.jpg', PREFIX+'a.jpg?token=x', 'https://t1.kakaocdn.net@evil.example/a.jpg', PREFIX+'a.pdf']:
            with self.subTest(url=url), patch('media.httpx.AsyncClient') as client:
                with self.assertRaises(media.MediaInputError):
                    await media.build_content('image: '+url)
                client.assert_not_called()

    async def test_redirect_html_empty_and_oversize_fail_closed(self):
        for response in [httpx.Response(302, headers={'location':'http://127.0.0.1/'}), httpx.Response(200, headers={'content-type':'text/html'}, content=b'not an image'), httpx.Response(200, headers={'content-type':'image/jpeg'}), httpx.Response(200, headers={'content-type':'image/jpeg'}, content=b'x'*17)]:
            requests=[]
            def serve(request):
                requests.append(request)
                return response
            client=httpx.AsyncClient(transport=httpx.MockTransport(serve))
            with patch('media.httpx.AsyncClient', return_value=client), patch('media.MAX_BYTES',16):
                with self.assertRaises(media.MediaInputError):
                    await media.build_content('image: '+PREFIX+'photo.jpg')
            self.assertEqual(len(requests),1)
