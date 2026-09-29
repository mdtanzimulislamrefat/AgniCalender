"""In-process cache of finished JSON responses for data that changes only on imports or fetches.

Validating and serialising e.g. 13,000 map points costs seconds on a small free host; a cached
body is returned as-is. Entries expire after their TTL; the scheduler clears 'recent' entries
after new NASA data arrives.
"""
import threading
import time

from fastapi.responses import Response

_entries = {}
_lock = threading.Lock()


def cached_json(key, ttl, produce, model):
    """Return a JSON Response for `key`, building it with produce() validated through `model`."""
    now = time.monotonic()
    with _lock:
        hit = _entries.get(key)
    if hit and hit[0] > now:
        return Response(hit[1], media_type='application/json')
    body = model.model_validate(produce()).model_dump_json().encode()
    with _lock:
        _entries[key] = (now + ttl, body)
    return Response(body, media_type='application/json')


def clear(prefix=None):
    with _lock:
        for key in [k for k in _entries if prefix is None or k[0] == prefix]:
            del _entries[key]
