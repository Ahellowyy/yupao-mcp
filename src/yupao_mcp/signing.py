"""Request format observed in Yupao's public web app, release 9.40.0.

The public protocol constant below is not an account token or an IM UserSig.
Account-only operations still require the user's legitimate logged-in session.
"""
import hashlib
import json
import math

_PUBLIC_SUFFIX = "*js1(Uc_m12j%hsn#1o%cn1"


def normalize(value):
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('Request keys must be strings')
        return {key: normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Non-finite numbers are unsupported')
        return int(value) if value.is_integer() else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError('Unsupported request value')


def js_value(value):
    if value is None or isinstance(value, (bool, list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return str(value)


def sign_request(body: dict, timestamp: str, nonce: str) -> str:
    values = normalize(dict(body, timestamp=timestamp, nonce=nonce))
    canonical = ''.join(f'{key}={js_value(value)}&' for key, value in values.items())
    return hashlib.sha256((canonical + _PUBLIC_SUFFIX).encode('utf-8')).hexdigest()
