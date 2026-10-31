"""Translate the contest's image:/audio: lines to OpenRouter content parts."""
import asyncio
import base64
import re
from urllib.parse import urlsplit

import httpx

MEDIA_LINE = re.compile(r"(?im)^\s*(image|audio):\s*(\S+)\s*$")
MAX_BYTES = 8 * 1024 * 1024
FORMATS = {"image": {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"},
           "audio": {"m4a": "audio/mp4", "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "flac": "audio/flac", "aac": "audio/aac"}}


class MediaInputError(ValueError):
    pass


async def build_content(text):
    matches = MEDIA_LINE.findall(text)
    if not matches:
        return text
    if len(matches) > 3:
        raise MediaInputError("첨부는 한 번에 3개까지 보내주세요.")
    entries = []
    for kind, url in matches:
        kind = kind.lower()
        p = urlsplit(url)
        # shortcut: only the observed contest CDN; add native Kakao attachment hosts after verifying their payload.
        if (p.scheme != "https" or p.netloc != "t1.kakaocdn.net"
                or not re.fullmatch(r"/aitop100/aitop100/dist/[A-Za-z0-9_-]+\.[A-Za-z0-9]+", p.path)
                or p.query or p.fragment):
            raise MediaInputError("첨부 주소를 확인할 수 없습니다. 대회에서 제공한 사진·음성 주소로 다시 보내주세요.")
        fmt = p.path.rsplit(".", 1)[1].lower()
        if fmt not in FORMATS[kind]:
            raise MediaInputError("지원하지 않는 첨부 형식입니다. 사진 또는 음성 내용을 글로 알려주세요.")
        entries.append((kind, url, fmt))
    parts = [{"type": "text", "text": text}]
    try:
        # A separate unauthenticated client prevents model/action credentials reaching the CDN.
        async with asyncio.timeout(12), httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            for kind, url, fmt in entries:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if not (mime.startswith(kind + "/") or mime == "application/octet-stream"):
                        raise MediaInputError("첨부가 사진·음성 파일이 아닙니다. 원본을 다시 보내주세요.")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise MediaInputError("첨부가 너무 큽니다. 8MB 이하 파일로 다시 보내주세요.")
                    if not data:
                        raise MediaInputError("첨부가 비어 있습니다. 원본을 다시 보내주세요.")
                encoded = base64.b64encode(data).decode("ascii")
                if kind == "image":
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{FORMATS[kind][fmt]};base64,{encoded}"}})
                else:
                    parts.append({"type": "input_audio", "input_audio": {"data": encoded, "format": fmt}})
    except (httpx.HTTPError, TimeoutError) as exc:
        raise MediaInputError("첨부를 읽지 못했습니다. 다시 보내주시거나 문의 내용을 글로 알려주세요.") from exc
    return parts
