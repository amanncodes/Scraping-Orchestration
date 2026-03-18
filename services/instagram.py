import re
from urllib.parse import urlparse

PATTERNS = [
    r"instagram\.com/p/([A-Za-z0-9_-]+)",
    r"instagram\.com/reel/([A-Za-z0-9_-]+)",
    r"instagram\.com/reels/([A-Za-z0-9_-]+)",
    r"instagram\.com/tv/([A-Za-z0-9_-]+)",
]

VALID_HOSTS = {"instagram.com", "instagr.am"}


def extract_post_id(url: str) -> str | None:
    """
    Extract Instagram shortcode from any URL variant.
    Returns shortcode string or None.

    These all return 'ABC123':
      https://www.instagram.com/p/ABC123/
      https://instagram.com/p/ABC123/?utm_source=ig
      https://www.instagram.com/reel/ABC123/
      https://instagr.am/p/ABC123/
    """
    try:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower().removeprefix("www.")
        if host not in VALID_HOSTS:
            return None
    except Exception:
        return None

    for pattern in PATTERNS:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1)

    return None