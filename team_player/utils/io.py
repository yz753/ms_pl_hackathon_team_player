"""File helpers: JSON lines and content fingerprints."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Iterable, TextIO


def write_jsonl(path: str | Path, records: Iterable[dict]) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n")


def read_jsonl(handle: TextIO):
    for number, line in enumerate(handle, 1):
        if line.strip():
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise ValueError(f"Invalid JSON on line {number}") from exc


def fingerprint(path):
    """SHA-256 of a file's content (identifies a recording even if renamed)."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def open_text(path, mode='rt'):
    """Open .jsonl or .jsonl.gz transparently (text mode, UTF-8)."""
    import gzip
    path = Path(path)
    if path.suffix == '.gz':
        return gzip.open(path, mode, encoding='utf-8')
    return path.open(mode[0], encoding='utf-8')


def match_files(folder):
    """All match recordings in a folder tree (plain or gzipped JSONL), sorted."""
    folder = Path(folder)
    return sorted(p for p in folder.rglob('*') if p.name.endswith(('.jsonl', '.jsonl.gz')))
