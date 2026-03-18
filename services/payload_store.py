from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId
from django.conf import settings

_client = None
_db = None


def _get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(settings.MONGO_URL)
        _db = _client[settings.MONGO_DB]
    return _db


def store(job_id: str, platform: str, raw: dict) -> str:
    doc = {
        "job_id":          str(job_id),
        "platform":        platform,
        "stored_at":       datetime.now(timezone.utc),
        "webhook_sent_at": None,
        "data":            raw,
    }
    result = _get_db().payloads.insert_one(doc)
    return str(result.inserted_id)


def fetch(storage_ref: str) -> dict | None:
    doc = _get_db().payloads.find_one({"_id": ObjectId(storage_ref)})
    if not doc:
        return None
    doc["_id"] = str(doc["_id"])
    return doc


def mark_sent(storage_ref: str):
    _get_db().payloads.update_one(
        {"_id": ObjectId(storage_ref)},
        {"$set": {"webhook_sent_at": datetime.now(timezone.utc)}}
    )