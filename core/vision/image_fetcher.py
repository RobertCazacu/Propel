"""
Image fetcher with local cache.

Downloads images from public URLs and caches them by URL-hash in
data/image_cache/. Cache is persistent (no TTL) — same URL always
returns the same file. Call clear_image_cache() to reset.

Returns a PIL.Image (RGB) or None on any failure.
"""
import io
import hashlib
import requests
from pathlib import Path
from PIL import Image
from core.app_logger import get_logger

log = get_logger("marketplace.vision.fetcher")

CACHE_DIR  = Path(__file__).parent.parent.parent / "data" / "image_cache"
TIMEOUT_S  = 10
MAX_SIZE   = (600, 600)   # resize after download for speed
_HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; MarketplaceTool/1.0)"}

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _url_hash(url: str) -> str:
    return hashlib.md5(url.strip().encode("utf-8")).hexdigest()


def _is_image_url(url: str, content_type: str) -> bool:
    if "image" in content_type.lower():
        return True
    path = url.lower().split("?")[0]
    return any(path.endswith(ext) for ext in _IMAGE_EXTENSIONS)


def fetch_image(url: str, sku: str = "") -> tuple:
    """
    Download (or load from cache) and return (PIL.Image | None, error_str).
    error_str is "" on success.

    Args:
        url: Public image URL.
        sku: Optional product SKU — used as cache filename if provided.
    """
    if not url or not url.strip().startswith("http"):
        return None, "Invalid or missing URL"

    cache_key  = sku.strip() if sku and sku.strip() else _url_hash(url)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{cache_key}.jpg"

    # ── Cache hit ──────────────────────────────────────────────────────────────
    if cache_path.exists():
        try:
            img = Image.open(cache_path).convert("RGB")
            img.thumbnail(MAX_SIZE)
            log.debug("Image cache hit: %s", cache_key)
            return img, ""
        except Exception as e:
            log.warning("Corrupt cache entry %s, re-downloading: %s", cache_key, e)
            cache_path.unlink(missing_ok=True)

    # ── Download ───────────────────────────────────────────────────────────────
    try:
        resp = requests.get(
            url.strip(),
            timeout=TIMEOUT_S,
            headers=_HEADERS,
            allow_redirects=True,
            stream=False,
        )
    except requests.exceptions.Timeout:
        return None, f"Timeout after {TIMEOUT_S}s"
    except requests.exceptions.ConnectionError as e:
        return None, f"Connection error: {str(e)[:100]}"
    except Exception as e:
        return None, f"Request error: {str(e)[:100]}"

    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"

    content_type = resp.headers.get("Content-Type", "")
    if not _is_image_url(url, content_type):
        return None, f"Not an image (Content-Type: {content_type[:60]})"

    try:
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img.thumbnail(MAX_SIZE)
        img.save(cache_path, "JPEG", quality=85)
        log.debug("Image downloaded & cached: %s -> %s", url[:80], cache_key)
        return img, ""
    except Exception as e:
        return None, f"Image decode error: {str(e)[:100]}"


def clear_image_cache():
    """Delete all cached images."""
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.jpg"):
            f.unlink(missing_ok=True)
    log.info("Image cache cleared")


def cache_stats() -> dict:
    """Return cache size info."""
    if not CACHE_DIR.exists():
        return {"count": 0, "size_mb": 0.0}
    files = list(CACHE_DIR.glob("*.jpg"))
    size  = sum(f.stat().st_size for f in files)
    return {"count": len(files), "size_mb": round(size / 1_048_576, 2)}
