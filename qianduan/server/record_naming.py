"""Stable, account-aware archive names for phone and edge detections."""
from __future__ import annotations
import hashlib
import re
from datetime import datetime


def archive_name(source: str, username, timestamp: datetime, unique_id: str) -> str:
    """minor_<account>_<date>_<time>_<id> or primary_<account>_..."""
    kind = 'minor' if source == 'phone_photo' else 'primary'
    # Account names are supplied by the trusted account store, never from a posted form.
    account = re.sub(r'[^A-Za-z0-9_-]', '_', str(username or 'unassigned')).strip('_')[:48] or 'unassigned'
    ident = re.sub(r'[^A-Za-z0-9]', '', str(unique_id))[:16]
    if not ident:
        ident = hashlib.sha256(str(unique_id).encode()).hexdigest()[:12]
    return f'{kind}_{account}_{timestamp:%Y%m%d_%H%M%S}_{ident}'


def record_source(record: dict) -> str:
    if record.get('source') == 'phone_photo' or record.get('session_id') == '手机辅助拍照识别':
        return 'phone_photo'
    return 'edge_camera'


def legacy_archive_name(record: dict) -> str:
    """Generate a stable display name for previously archived rows, without moving photos."""
    when = str(record.get('time') or '')
    parsed = None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y%m%d_%H%M%S'):
        try:
            parsed = datetime.strptime(when[:19], fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        # No fabricated date for malformed legacy records.
        return ('minor' if record_source(record) == 'phone_photo' else 'primary') + '_unclassified_unknown_' + hashlib.sha256(str(record.get('id')).encode()).hexdigest()[:12]
    unique = str(record.get('id') or record.get('image') or '')
    return archive_name(record_source(record), record.get('uploaded_by_username'), parsed,
                        hashlib.sha256(unique.encode()).hexdigest()[:12])
