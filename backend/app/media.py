"""Conservative file signature checks for the M0 demonstration.

This check is not a substitute for M1's full FFprobe/decode verification.
"""
from pathlib import Path

SIGNATURES = [
    (b'\x89PNG\r\n\x1a\n', 'image', 'image/png', '.png'),
    (b'\xff\xd8\xff', 'image', 'image/jpeg', '.jpg'),
    (b'\x1a\x45\xdf\xa3', 'video', 'video/x-matroska', '.mkv'),
]

def detect_signature(header: bytes) -> tuple[str, str, str] | None:
    for prefix, kind, mime, suffix in SIGNATURES:
        if header.startswith(prefix):
            return kind, mime, suffix
    if len(header) >= 12 and header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return 'image', 'image/webp', '.webp'
    if len(header) >= 12 and header[:4] == b'RIFF' and header[8:12] == b'AVI ':
        return 'video', 'video/x-msvideo', '.avi'
    if len(header) >= 12 and header[4:8] == b'ftyp':
        return 'video', 'video/mp4', '.mp4'
    return None

def safe_basename(name: str | None) -> str:
    return Path((name or 'unnamed').replace('\\', '/')).name[:255]
