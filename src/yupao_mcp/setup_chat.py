"""Download the pinned official browser SDK; never downloads or handles account data."""
import argparse
import base64
import hashlib
import io
from pathlib import Path
import tarfile

import httpx

SDK_VERSION = '3.6.7'
SDK_URL = 'https://registry.npmjs.org/@tencentcloud/chat/-/chat-3.6.7.tgz'
SDK_INTEGRITY = 'HEmyOFTWRzP4PlHNNVSk3dxEq8wYcyqhGkYT2a0/qgSFD6X7DBwrJWnD5K4HnVXqEafnwF7c8FUIkr2OvxXoMQ=='
SDK_SHA256 = '5f79c963986ccc7d88a1cfef84ea9a38841d30d35621f768f0bc7f8d3b4bfe87'
DEFAULT_SDK_FILE = Path(__file__).resolve().parents[2] / '.cache' / 'chat-3.6.7' / 'index.js'


def download_sdk(destination: Path):
    response = httpx.get(SDK_URL, timeout=30, follow_redirects=False)
    response.raise_for_status()
    archive = response.content
    if base64.b64encode(hashlib.sha512(archive).digest()).decode() != SDK_INTEGRITY:
        raise ValueError('Official SDK archive integrity mismatch')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
        # Extract one known file into memory, never extract arbitrary archive paths.
        entry = bundle.extractfile('package/index.js')
        if entry is None:
            raise ValueError('Official SDK entry is missing')
        script = entry.read(2_000_000)
    if hashlib.sha256(script).hexdigest() != SDK_SHA256:
        raise ValueError('Official SDK entry integrity mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(script)


def main():
    parser = argparse.ArgumentParser(description='Download pinned @tencentcloud/chat browser SDK')
    parser.add_argument('--output', type=Path, default=DEFAULT_SDK_FILE)
    args = parser.parse_args()
    download_sdk(args.output)
    print(f'Official Chat SDK {SDK_VERSION} verified and saved to {args.output.resolve()}')


if __name__ == '__main__':
    main()
