import random
import requests

SAFEBOORU_API = "https://safebooru.org/index.php"

def fetch_booru_artwork(tags="scenery", timeout=12):
    """Return image bytes and a small source record, or (None, None)."""
    tags = " ".join(str(tags or "scenery").split())
    params = {
        "page": "dapi",
        "s": "post",
        "q": "index",
        "json": 1,
        "limit": 20,
        "tags": tags,
    }
    headers = {
        "User-Agent": "LRCPlus/1.0 (lyrics artwork background)",
        "Accept": "application/json",
    }

    response = requests.get(
        SAFEBOORU_API,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()

    payload = response.json()
    posts = payload if isinstance(payload, list) else payload.get("post", [])
    if not posts:
        return None, None

    usable = [
        post for post in posts
        if isinstance(post, dict)
        and (post.get("sample_url") or post.get("file_url"))
    ]
    if not usable:
        return None, None

    post = random.choice(usable)
    image_url = post.get("sample_url") or post.get("file_url")
    if image_url and image_url.startswith("//"):
        image_url = "https:" + image_url

    if not image_url:
        return None, None

    image_response = requests.get(
        image_url,
        headers=headers,
        timeout=timeout,
    )
    image_response.raise_for_status()

    return image_response.content, {
        "provider": "Safebooru",
        "post_id": post.get("id"),
        "tags": tags,
    }
