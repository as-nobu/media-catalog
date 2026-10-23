"""On-demand DINOv2 download, also usable as a standalone command.

The GUI runs downloads in a subprocess so cancellation can interrupt network
waits. Model loading/inference itself remains strictly local-only.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

MODEL_ID = 'facebook/dinov2-small'
MODEL_REVISION = 'ed25f3a31f01632728cabb09d1542f84ab7b0056'
MODEL_FILES = ('config.json', 'preprocessor_config.json', 'model.safetensors')
INCOMPLETE = '.media-catalog-download-incomplete'
_DOWNLOAD_LOCK = threading.Lock()


def configure_download_tls():
    """Use Windows' native trusted CAs in this downloader process only.

    Called by main(), before importing the HTTP client. Never run on import:
    the catalog GUI imports this module too and must keep its own SSL state.
    Without truststore, retain the HTTP client's normal certificate checks.
    """
    if sys.platform != 'win32':
        return False
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    # Both metadata and weights must use the Python TLS context. The optional
    # Rust downloaders have separate certificate handling. These settings are
    # process-local; they do not change Windows/Conda settings or environment.
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HUB_ENABLE_HF_TRANSFER'] = '0'
    return True


def certificate_error_message(error, native_trust):
    """Recognize wrapped certificate failures without printing a huge trace."""
    message = str(error)
    if not any(text in message.lower() for text in
               ('certificate_verify_failed', 'certificate verify failed',
                'unable to get local issuer certificate')):
        return None
    if sys.platform == 'win32' and not native_trust:
        return ('TLS certificate verification failed. Install truststore in the '
                'app environment, then restart the app:\n'
                'conda install --override-channels -c conda-forge truststore\n'
                'For pip environments: python -m pip install truststore')
    return ('TLS certificate verification failed even with the configured trust '
            'store. Ask your IT administrator to check the HTTPS proxy and its '
            'CA certificates. An IT-provided PEM CA bundle can be configured '
            'with REQUESTS_CA_BUNDLE. Certificate verification remains enabled.')


def check_ai_packages():
    if any(importlib.util.find_spec(name) is None for name in ('torch', 'transformers', 'safetensors')):
        raise RuntimeError('AI\u691c\u7d22\u306e\u8ffd\u52a0\u30d1\u30c3\u30b1\u30fc\u30b8\u304c\u3042\u308a\u307e\u305b\u3093\u3002IMAGE_SEARCH.ja.md\u3092\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002')


def model_ready(directory):
    if not directory:
        return False
    folder = Path(directory).expanduser()
    return (not (folder/INCOMPLETE).exists() and
            all((folder/name).is_file() and (folder/name).stat().st_size > 0 for name in MODEL_FILES))


def download_model(directory):
    """Download only fixed model files. Preserve incomplete downloads for retry."""
    folder = Path(directory).expanduser().resolve()
    if model_ready(folder):
        return folder
    from filelock import FileLock
    from huggingface_hub import snapshot_download
    folder.mkdir(parents=True, exist_ok=True)
    # Also coordinates with a manually launched instance of this script.
    with FileLock(str(folder/'.media-catalog-download.lock')):
        if model_ready(folder):
            return folder
        marker = folder/INCOMPLETE
        marker.write_text(MODEL_REVISION, encoding='ascii')
        snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_dir=str(folder), token=False,
            allow_patterns=[*MODEL_FILES, 'README.md'])
        config = json.loads((folder/'config.json').read_text(encoding='utf-8'))
        processor = json.loads((folder/'preprocessor_config.json').read_text(encoding='utf-8'))
        if config.get('model_type') != 'dinov2' or not isinstance(processor, dict):
            raise RuntimeError('\u53d6\u5f97\u3057\u305fDINOv2\u30e2\u30c7\u30eb\u306e\u8a2d\u5b9a\u304c\u4e0d\u6b63\u3067\u3059\u3002')
        # Validate the safetensors header without loading all weights into RAM.
        import struct
        weights = folder/'model.safetensors'
        with weights.open('rb') as stream:
            size = struct.unpack('<Q', stream.read(8))[0]
            if not 2 <= size <= min(16_000_000, weights.stat().st_size-8):
                raise RuntimeError('\u53d6\u5f97\u3057\u305fDINOv2\u30e2\u30c7\u30eb\u304c\u4e0d\u5b8c\u5168\u3067\u3059\u3002')
            header = json.loads(stream.read(size))
        if not any(k != '__metadata__' for k in header):
            raise RuntimeError('\u53d6\u5f97\u3057\u305fDINOv2\u30e2\u30c7\u30eb\u304c\u4e0d\u5b8c\u5168\u3067\u3059\u3002')
        for key, tensor in header.items():
            if key != '__metadata__':
                start, end = tensor['data_offsets']
                if not 0 <= start <= end <= weights.stat().st_size-8-size:
                    raise RuntimeError('\u53d6\u5f97\u3057\u305fDINOv2\u30e2\u30c7\u30eb\u304c\u4e0d\u5b8c\u5168\u3067\u3059\u3002')
        marker.unlink()
    return folder


def ensure_model(directory, stop=lambda: False, progress=lambda *args: None, timeout=900):
    """Called only after startup consent or an authorized generation/search job."""
    from visual_search import check_cancel
    check_cancel(stop)
    if model_ready(directory):
        return Path(directory).expanduser()
    if not directory:
        raise RuntimeError('DINOv2\u30e2\u30c7\u30eb\u306e\u4fdd\u5b58\u5148\u30d5\u30a9\u30eb\u30c0\u3092\u6307\u5b9a\u3057\u3066\u304f\u3060\u3055\u3044\u3002')
    if importlib.util.find_spec('huggingface_hub') is None:
        raise RuntimeError('\u30e2\u30c7\u30eb\u53d6\u5f97\u306b\u306fhuggingface_hub\u304c\u5fc5\u8981\u3067\u3059\u3002IMAGE_SEARCH.ja.md\u3092\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002')
    progress('download', 0, 0)
    # An automatic build and a user search must not start duplicate downloads.
    while not _DOWNLOAD_LOCK.acquire(timeout=.1):
        check_cancel(stop)
    try:
        check_cancel(stop)
        if model_ready(directory):
            return Path(directory).expanduser()
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                '--output', str(Path(directory).expanduser().resolve())],
                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic()+timeout
                while process.poll() is None:
                    check_cancel(stop)
                    if time.monotonic() > deadline:
                        raise RuntimeError('DINOv2\u30e2\u30c7\u30eb\u306e\u53d6\u5f97\u304c\u30bf\u30a4\u30e0\u30a2\u30a6\u30c8\u3057\u307e\u3057\u305f\u3002\u901a\u4fe1\u74b0\u5883\u3092\u78ba\u8a8d\u3057\u3066\u518d\u8a66\u884c\u3057\u3066\u304f\u3060\u3055\u3044\u3002')
                    time.sleep(.1)
                check_cancel(stop)
                if process.returncode or not model_ready(directory):
                    log.seek(0, os.SEEK_END)
                    log.seek(max(0, log.tell()-2400))
                    detail = log.read().decode('utf-8', errors='replace').strip()
                    raise RuntimeError('DINOv2\u30e2\u30c7\u30eb\u3092\u53d6\u5f97\u3067\u304d\u307e\u305b\u3093\u3067\u3057\u305f\u3002\u901a\u4fe1\u74b0\u5883\u30fb\u4fdd\u5b58\u5148\u3092\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002\n'+detail)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        return Path(directory).expanduser()
    finally:
        _DOWNLOAD_LOCK.release()


def main():
    parser = argparse.ArgumentParser(description='Download DINOv2 for local Media Catalog image search.')
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('models')/'dinov2-small')
    args = parser.parse_args()
    native_trust = False
    try:
        # Complete local models never need a TLS setup or network connection.
        if not model_ready(args.output):
            native_trust = configure_download_tls()
        folder = download_model(args.output)
    except Exception as exc:
        message = certificate_error_message(exc, native_trust)
        if message is None:
            raise
        print(message, file=sys.stderr)
        return 1
    print('Model saved to:',folder)
    print('Select this folder in Image search > AI (DINOv2). Searches then work offline.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
