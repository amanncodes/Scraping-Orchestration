import json
import redis
from django.conf import settings

_client = None

def get_redis():
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def cache_key(post_id: str, platform: str = "instagram") -> str:
    return f"cache:{platform}:{post_id}"


def check_cache(post_id: str, platform: str = "instagram") -> dict | None:
    r = get_redis()
    raw = r.get(cache_key(post_id, platform))
    if not raw:
        return None
    return json.loads(raw)


def write_cache(post_id: str, storage_ref: str, coverage_ratio: float | None,
                platform: str = "instagram"):
    r = get_redis()
    ttl = settings.CACHE_TTL.get(platform, 86400)
    data = {"storage_ref": storage_ref, "coverage_ratio": coverage_ratio}
    r.setex(cache_key(post_id, platform), ttl, json.dumps(data))


def invalidate_cache(post_id: str, platform: str = "instagram"):
    r = get_redis()
    r.delete(cache_key(post_id, platform))