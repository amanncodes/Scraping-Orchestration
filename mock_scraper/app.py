"""
Mock Instagram Scraper Server
Mimics the remote office PC scraper that SOP talks to.

- Receives scrape requests from SOP at POST /posts/scrape
- Waits 2-4 seconds (simulates scraping work)
- POSTs fake Instagram data back to SOP's webhook (callback_url)
"""

import time
import threading
import random
from urllib.parse import urlparse

import httpx
from flask import Flask, request, jsonify

app = Flask(__name__)

FAKE_PROFILES = {
    "ABC123": {
        "username": "cristiano",
        "full_name": "Cristiano Ronaldo",
        "followers": 636000000,
        "following": 567,
        "posts_count": 3812,
        "bio": "SIU! ⚽🏆",
        "is_verified": True,
        "recent_posts": [
            {"id": "post_001", "likes": 4200000, "comments": 38000, "caption": "Always working 💪"},
            {"id": "post_002", "likes": 3800000, "comments": 29000, "caption": "Family first ❤️"},
            {"id": "post_003", "likes": 5100000, "comments": 51000, "caption": "New chapter 🔥"},
        ]
    },
    "DEF456": {
        "username": "natgeo",
        "full_name": "National Geographic",
        "followers": 280000000,
        "following": 2,
        "posts_count": 28000,
        "bio": "🌍 Inspiring people to care about the planet.",
        "is_verified": True,
        "recent_posts": [
            {"id": "post_b1", "likes": 950000, "comments": 3200, "caption": "The wild 🦁"},
            {"id": "post_b2", "likes": 870000, "comments": 2900, "caption": "Ocean depths 🌊"},
        ]
    },
    "GHI789": {
        "username": "testuser",
        "full_name": "Test User",
        "followers": 1234,
        "following": 567,
        "posts_count": 42,
        "bio": "Just a test account 🤖",
        "is_verified": False,
        "recent_posts": [
            {"id": "post_a1", "likes": 88,  "comments": 4, "caption": "Hello world"},
            {"id": "post_a2", "likes": 102, "comments": 7, "caption": "Another post"},
        ]
    }
}


def do_scrape_and_callback(job_id: str, platform: str, post_id: str, callback_url: str):
    """
    Runs in a background thread.
    Simulates scraping delay then POSTs results back to SOP's callback_url.
    """
    print(f"[MOCK] Starting scrape — job={job_id} platform={platform} post_id={post_id}")

    # Simulate scraping taking 2–4 seconds
    delay = random.uniform(2, 4)
    time.sleep(delay)

    # Use fake profile if post_id matches, otherwise generate random data
    data = FAKE_PROFILES.get(post_id, {
        "username": f"user_{post_id[:6].lower()}",
        "full_name": f"Mock User {post_id[:6]}",
        "followers": random.randint(100, 500000),
        "following": random.randint(10, 2000),
        "posts_count": random.randint(1, 1000),
        "bio": f"Auto-generated mock profile for post {post_id}",
        "is_verified": False,
        "post_id": post_id,
        "likes": random.randint(10, 100000),
        "comments": random.randint(0, 5000),
        "caption": "Mock scraped post content 🤖",
        "recent_posts": [
            {
                "id": f"mock_{post_id}_1",
                "likes": random.randint(10, 500),
                "comments": random.randint(0, 50),
                "caption": "Mock post 1"
            },
            {
                "id": f"mock_{post_id}_2",
                "likes": random.randint(10, 500),
                "comments": random.randint(0, 50),
                "caption": "Mock post 2"
            },
        ]
    })

    payload = {
        "job_id":   job_id,
        "platform": platform,
        "status":   "success",
        "data":     data,
    }

    print(f"[MOCK] Scrape done in {delay:.1f}s — sending callback to: {callback_url}")

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(callback_url, json=payload)
            print(f"[MOCK] Callback response: {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        print(f"[MOCK] Callback FAILED: {e}")


def extract_post_id_from_uri(post_uri: str) -> str:
    """
    Extract the post shortcode from an Instagram URL.
    https://www.instagram.com/p/ABC123/  →  ABC123
    https://www.instagram.com/reel/XYZ/  →  XYZ
    Falls back to last path segment or 'unknown'.
    """
    try:
        parts = [p for p in urlparse(post_uri).path.split("/") if p]
        # Instagram URLs: /p/{id}/, /reel/{id}/, /reels/{id}/, /tv/{id}/
        for i, part in enumerate(parts):
            if part in ("p", "reel", "reels", "tv") and i + 1 < len(parts):
                return parts[i + 1]
        # fallback: last path segment
        return parts[-1] if parts else "unknown"
    except Exception:
        return "unknown"


@app.route("/posts/scrape", methods=["POST"])
def scrape():
    """
    SOP calls this endpoint to request a scrape.
    Expected body:
    {
        "post_uri":     "https://www.instagram.com/p/ABC123/",
        "callback_url": "http://api:8000/api/v1/webhook/receive",
        "job_id":       "uuid-string"
    }
    """
    body = request.get_json(force=True)
    print(f"[MOCK] Received scrape request: {body}")

    job_id       = body.get("job_id", "unknown")
    post_uri     = body.get("post_uri", "")
    callback_url = body.get("callback_url", "http://api:8000/api/v1/webhook/receive")
    platform     = "instagram"

    post_id = extract_post_id_from_uri(post_uri)
    print(f"[MOCK] Extracted post_id='{post_id}' from uri='{post_uri}'")

    # Kick off scraping in background — return 202 immediately
    thread = threading.Thread(
        target=do_scrape_and_callback,
        args=(job_id, platform, post_id, callback_url),
        daemon=True
    )
    thread.start()

    return jsonify({
        "status":  "accepted",
        "job_id":  job_id,
        "post_id": post_id,
    }), 202


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "mock-scraper"}), 200


if __name__ == "__main__":
    print("[MOCK] Mock scraper starting on port 9000...")
    app.run(host="0.0.0.0", port=9000, debug=False)