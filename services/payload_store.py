from datetime import datetime, timezone
from pymongo import MongoClient
from django.conf import settings

_client = None
_db = None

def get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(settings.MONGO_URL)
        _db = _client[settings.MONGO_DB]
    return _db


def store_payload(job_id: str, platform: str, raw_data: dict) -> str:
    db = get_db()
    doc = {
        "job_id":          str(job_id),
        "platform":        platform,
        "stored_at":       datetime.now(timezone.utc),
        "webhook_sent_at": None,
        "data":            raw_data,
    }
    result = db.payloads.insert_one(doc)
    return str(result.inserted_id)


def get_payload(storage_ref: str) -> dict | None:
    from bson import ObjectId
    db = get_db()
    doc = db.payloads.find_one({"_id": ObjectId(storage_ref)})
    if not doc:
        return None
    doc["_id"] = str(doc["_id"])
    return doc


def mark_webhook_sent(storage_ref: str):
    from bson import ObjectId
    db = get_db()
    db.payloads.update_one(
        {"_id": ObjectId(storage_ref)},
        {"$set": {"webhook_sent_at": datetime.now(timezone.utc)}}
    )


def get_undelivered_payloads() -> list:
    db = get_db()
    docs = db.payloads.find({"webhook_sent_at": None})
    result = []
    for doc in docs:
        doc["_id"] = str(doc["_id"])
        result.append(doc)
    return result