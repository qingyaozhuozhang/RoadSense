"""Atomic, local-file record persistence (compatible with legacy records.json)."""
import json
import os
import threading
from pathlib import Path


class RecordStore:
    def __init__(self, data_dir):
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.image_dir = self.directory / 'images'
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'records.json'
        self.lock = threading.RLock()

    def all(self):
        with self.lock:
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                return value if isinstance(value, list) else []
            except (FileNotFoundError, ValueError, OSError):
                return []

    def add(self, record):
        with self.lock:
            records = self.all()
            records.append(record)
            temporary = self.path.with_suffix('.json.tmp')
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(records, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            return len(records)
