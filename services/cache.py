import json
import redis as redis_lib
from django.conf import settings

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def _key(post_id: str) -> str:
    return f"cache:instagram:{post_id}"


def check(post_id: str) -> dict | None:
    raw = _get_client().get(_key(post_id))
    return json.loads(raw) if raw else None


def write(post_id: str, storage_ref: str, coverage: float | None = None):
    ttl = settings.CACHE_TTL.get("instagram", 86400)
    _get_client().setex(
        _key(post_id),
        ttl,
        json.dumps({"storage_ref": storage_ref, "coverage": coverage})
    )


def bust(post_id: str):
    _get_client().delete(_key(post_id))