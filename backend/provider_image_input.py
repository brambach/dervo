"""Bounded provider JSON input. Only PNG payload bytes may exceed the text limit."""
import base64
import binascii
import json
from dervo_connections import Rejected

TEXT_BYTES = 131072
IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES = 4
PREFIX = 'data:image/png;base64,'


def encode_message(message):
    raw = (json.dumps(message) + '\n').encode()
    if len(raw) <= TEXT_BYTES: return raw
    if len(raw) > TEXT_BYTES + MAX_IMAGES * ((IMAGE_BYTES + 2) // 3 * 4):
        raise Rejected('Provider images exceed the attachment limit.')
    if message.get('method') == 'turn/start':
        blocks = message.get('params', {}).get('input')
        envelope = dict(message)
        envelope['params'] = dict(message['params'])
        destination = envelope['params']
        field = 'input'
        provider = 'codex'
    elif message.get('type') == 'user':
        blocks = message.get('message', {}).get('content')
        envelope = dict(message)
        envelope['message'] = dict(message['message'])
        destination = envelope['message']
        field = 'content'
        provider = 'claude'
    else: raise Rejected('Provider input exceeds 128 KiB.')
    if not isinstance(blocks, list): raise Rejected('Provider input exceeds 128 KiB.')
    stripped, count = [], 0
    for block in blocks:
        if not isinstance(block, dict) or block.get('type') != 'image':
            stripped.append(block)
            continue
        count += 1
        if count > MAX_IMAGES: raise Rejected('Attach up to four images.')
        if provider == 'codex':
            url = block.get('url')
            if set(block) != {'type', 'url'} or not isinstance(url, str) or not url.startswith(PREFIX):
                raise Rejected('Provider image input must contain a local PNG.')
            encoded = url[len(PREFIX):]
            stripped.append({'type': 'image', 'url': PREFIX})
        else:
            source = block.get('source')
            if (set(block) != {'type', 'source'} or not isinstance(source, dict)
                    or set(source) != {'type', 'media_type', 'data'} or source['type'] != 'base64'
                    or source['media_type'] != 'image/png' or not isinstance(source['data'], str)):
                raise Rejected('Provider image input must contain a local PNG.')
            encoded = source['data']
            stripped.append({'type': 'image', 'source': dict(source, data='')})
        if len(encoded) > (IMAGE_BYTES + 2) // 3 * 4: raise Rejected('This provider image exceeds 2 MB.')
        try: image = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error): raise Rejected('Provider image bytes couldn’t be read.') from None
        if len(image) > IMAGE_BYTES or not image.startswith(b'\x89PNG\r\n\x1a\n'):
            raise Rejected('Provider image input must contain a local PNG.')
    destination[field] = stripped
    if not count or len((json.dumps(envelope) + '\n').encode()) > TEXT_BYTES:
        raise Rejected('Provider text and tool input exceeds 128 KiB.')
    return raw
