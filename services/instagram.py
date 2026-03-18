import re
from urllib.parse import urlparse

INSTAGRAM_PATTERNS = [
    r"instagram\.com/p/([A-Za-z0-9_-]+)",
    r"instagram\.com/reel/([A-Za-z0-9_-]+)",
    r"instagram\.com/reels/([A-Za-z0-9_-]+)",
    r"instagram\.com/tv/([A-Za-z0-9_-]+)",
]

VALID_HOSTS = {"instagram.com", "instagr.am"}


def extract_post_id(url: str) -> str | None:
    """
    Extract Instagram shortcode from any valid post URL variant.

    Handles:
      https://www.instagram.com/p/ABC123/
      https://instagram.com/p/ABC123/?utm_source=ig
      https://www.instagram.com/reel/ABC123/
      https://www.instagram.com/tv/ABC123/
      https://instagr.am/p/ABC123/

    Returns shortcode string or None if not a valid post URL.
    """
    try:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower().removeprefix("www.")
        if host not in VALID_HOSTS:
            return None
    except Exception:
        return None

    for pattern in INSTAGRAM_PATTERNS:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1)

    return None  # valid IG domain but not a post (profile, stories, etc.)


def validate_instagram_url(url: str) -> tuple[str | None, str | None]:
    """
    Returns (post_id, error_message).
    post_id is None if invalid, error_message is None if valid.
    """
    post_id = extract_post_id(url)
    if post_id is None:
        return None, (
            "Could not extract a valid Instagram post ID from the URL. "
            "Supported formats: /p/{id}/, /reel/{id}/, /tv/{id}/"
        )
    return post_id, None