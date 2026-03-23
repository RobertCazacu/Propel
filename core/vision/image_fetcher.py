"""
Image fetcher with local cache.

Downloads images from public URLs and caches them by URL-hash in
data/image_cache/. Cache is persistent (no TTL) — same URL always
returns the same file. Call clear_image_cache() to reset.

Returns a PIL.Image (RGB) or None on any failure.
"""
import io
import time
import hashlib
import requests
from pathlib import Path
from PIL import Image
from core.app_logger import get_logger

log = get_logger("marketplace.vision.fetcher")

CACHE_DIR  = Path(__file__).parent.parent.parent / "data" / "image_cache"
TIMEOUT_S  = (5, 20)          # (connect_timeout, read_timeout) seconds
MAX_SIZE   = (600, 600)        # resize after download for analysis speed
MAX_BYTES  = 15 * 1024 * 1024  # 15 MB — abort download if exceeded
_HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; MarketplaceTool/1.0)"}

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# Create cache directory once at import time — not on every fetch call
CACHE_DIR.mkdir(parents=True, exist_ok=True)


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
        sku: Unused — kept for backward compatibility.
    """
    if not url or not url.strip().startswith("http"):
        log.warning("[Fetch] URL invalid sau lipsa: %r", url)
        return None, "Invalid or missing URL"

    cache_key  = _url_hash(url)
    cache_path = CACHE_DIR / f"{cache_key}.jpg"

    log.debug("[Fetch] Start fetch key=%s url=%s", cache_key, url[:100])

    # ── Cache hit ──────────────────────────────────────────────────────────────
    if cache_path.exists():
        try:
            t0  = time.perf_counter()
            img = Image.open(cache_path).convert("RGB")
            ms  = round((time.perf_counter() - t0) * 1000)
            size_kb = round(cache_path.stat().st_size / 1024, 1)
            log.debug(
                "[Fetch] Cache HIT key=%s size=%skb dims=%dx%d load=%dms",
                cache_key, size_kb, img.width, img.height, ms,
            )
            return img, ""
        except Exception as e:
            log.warning(
                "[Fetch] Cache corupt key=%s — se re-descarca. Eroare: %s",
                cache_key, e,
            )
            cache_path.unlink(missing_ok=True)

    # ── Download ───────────────────────────────────────────────────────────────
    log.info("[Fetch] Descarcare imagine url=%s key=%s", url[:100], cache_key)
    t_start = time.perf_counter()

    try:
        resp = requests.get(
            url.strip(),
            timeout=TIMEOUT_S,       # (connect, read) — separate limits
            headers=_HEADERS,
            allow_redirects=True,
            stream=True,             # stream=True: inspect headers before reading body
        )
    except requests.exceptions.Timeout:
        ms = round((time.perf_counter() - t_start) * 1000)
        log.warning(
            "[Fetch] TIMEOUT url=%s (connect=%ds read=%ds elapsed=%dms)",
            url[:100], TIMEOUT_S[0], TIMEOUT_S[1], ms,
        )
        return None, f"Timeout (connect={TIMEOUT_S[0]}s read={TIMEOUT_S[1]}s)"
    except requests.exceptions.ConnectionError as e:
        ms = round((time.perf_counter() - t_start) * 1000)
        log.warning("[Fetch] CONNECTION ERROR url=%s err=%s elapsed=%dms", url[:100], str(e)[:120], ms)
        return None, f"Connection error: {str(e)[:100]}"
    except Exception as e:
        ms = round((time.perf_counter() - t_start) * 1000)
        log.warning("[Fetch] REQUEST ERROR url=%s err=%s elapsed=%dms", url[:100], str(e)[:120], ms)
        return None, f"Request error: {str(e)[:100]}"

    # Log redirect dacă URL-ul final diferă de cel original
    final_url = resp.url or url
    if final_url.rstrip("/") != url.strip().rstrip("/"):
        log.debug("[Fetch] Redirect: %s -> %s", url[:80], final_url[:80])

    if not resp.ok:
        ms = round((time.perf_counter() - t_start) * 1000)
        log.warning(
            "[Fetch] HTTP ERROR status=%d url=%s elapsed=%dms",
            resp.status_code, url[:100], ms,
        )
        return None, f"HTTP {resp.status_code}"

    # Check Content-Type pe URL-ul FINAL (după redirecturi)
    content_type = resp.headers.get("Content-Type", "")
    if not _is_image_url(final_url, content_type):
        resp.close()
        log.warning(
            "[Fetch] Content-Type invalid url=%s content_type=%r final_url=%s",
            url[:80], content_type[:80], final_url[:80],
        )
        return None, f"Not an image (Content-Type: {content_type[:60]})"

    # Respinge fișiere prea mari din header Content-Length
    content_length = resp.headers.get("Content-Length")
    if content_length:
        cl_int = int(content_length)
        log.debug(
            "[Fetch] Content-Length=%d bytes (%.1f MB) key=%s",
            cl_int, cl_int / 1_048_576, cache_key,
        )
        if cl_int > MAX_BYTES:
            resp.close()
            log.warning(
                "[Fetch] Prea mare din header: %.1f MB > %d MB limita url=%s",
                cl_int / 1_048_576, MAX_BYTES // 1_048_576, url[:100],
            )
            return None, f"Image too large ({cl_int / 1_048_576:.1f} MB > {MAX_BYTES // 1_048_576} MB limit)"

    # Citire body în chunks cu cap de MAX_BYTES
    chunks = []
    downloaded = 0
    try:
        for chunk in resp.iter_content(chunk_size=65_536):
            downloaded += len(chunk)
            if downloaded > MAX_BYTES:
                resp.close()
                log.warning(
                    "[Fetch] Download aborted: depasit %d MB streaming url=%s downloaded=%d bytes",
                    MAX_BYTES // 1_048_576, url[:100], downloaded,
                )
                return None, f"Image too large (> {MAX_BYTES // 1_048_576} MB), download aborted"
            chunks.append(chunk)
    except Exception as e:
        ms = round((time.perf_counter() - t_start) * 1000)
        log.warning("[Fetch] DOWNLOAD ERROR url=%s err=%s elapsed=%dms", url[:100], str(e)[:120], ms)
        return None, f"Download error: {str(e)[:100]}"

    raw      = b"".join(chunks)
    t_dl_ms  = round((time.perf_counter() - t_start) * 1000)

    log.debug(
        "[Fetch] Body primit: %d bytes (%.1f kB) in %dms url=%s",
        len(raw), len(raw) / 1024, t_dl_ms, url[:80],
    )

    # ── Decode + cache ─────────────────────────────────────────────────────────
    try:
        t_dec = time.perf_counter()
        img   = Image.open(io.BytesIO(raw)).convert("RGB")
        orig_w, orig_h = img.width, img.height
        img.thumbnail(MAX_SIZE)
        img.save(cache_path, "JPEG", quality=85)
        dec_ms     = round((time.perf_counter() - t_dec) * 1000)
        total_ms   = round((time.perf_counter() - t_start) * 1000)
        cached_kb  = round(cache_path.stat().st_size / 1024, 1)

        log.info(
            "[Fetch] OK key=%s orig=%dx%d -> %dx%d raw=%.1fkB cached=%.1fkB dl=%dms decode=%dms total=%dms",
            cache_key,
            orig_w, orig_h, img.width, img.height,
            len(raw) / 1024, cached_kb,
            t_dl_ms, dec_ms, total_ms,
        )
        return img, ""
    except Exception as e:
        ms = round((time.perf_counter() - t_start) * 1000)
        log.error(
            "[Fetch] DECODE ERROR url=%s raw_size=%d err=%s elapsed=%dms",
            url[:100], len(raw), str(e)[:120], ms,
            exc_info=True,
        )
        return None, f"Image decode error: {str(e)[:100]}"


def clear_image_cache():
    """Delete all cached images."""
    stats = cache_stats()
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.jpg"):
            f.unlink(missing_ok=True)
    log.info(
        "[Fetch] Cache sters: %d fisiere (%.2f MB) eliminate",
        stats["count"], stats["size_mb"],
    )


def cache_stats() -> dict:
    """Return cache size info."""
    if not CACHE_DIR.exists():
        return {"count": 0, "size_mb": 0.0}
    files = list(CACHE_DIR.glob("*.jpg"))
    size  = sum(f.stat().st_size for f in files)
    return {"count": len(files), "size_mb": round(size / 1_048_576, 2)}
