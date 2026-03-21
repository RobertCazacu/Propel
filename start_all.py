"""
start_all.py — Porneste Streamlit + Cloudflare Tunnel si trimite URL-ul pe Telegram.
Ruleaza automat la pornirea Windows via Task Scheduler.
"""
import os
import subprocess
import re
import time
import urllib.request
import urllib.parse

try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ── CONFIGURARE ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
STREAMLIT_PORT   = int(os.getenv("STREAMLIT_PORT", "8501"))
# ──────────────────────────────────────────────────────────────────────────────


def send_telegram(message: str):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }).encode()
    try:
        urllib.request.urlopen(url, data, timeout=10)
    except Exception as e:
        print(f"[Telegram] Eroare: {e}")


def start_streamlit():
    print("[Streamlit] Pornire...")
    return subprocess.Popen(
        ["streamlit", "run", "app.py", "--server.headless", "true"],
        cwd=r"C:\Users\manue\Desktop\marketplace_tool",
    )


def start_tunnel():
    print("[Cloudflared] Pornire tunel...")
    return subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{STREAMLIT_PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def main():
    # 1. Porneste Streamlit
    streamlit_proc = start_streamlit()

    # 2. Asteapta sa fie gata (10 secunde)
    print("[Streamlit] Astept 10 secunde sa porneasca...")
    time.sleep(10)

    # 3. Porneste tunelul si citeste URL-ul din output
    tunnel_proc = start_tunnel()
    url_found   = False

    print("[Cloudflared] Astept URL-ul...")
    for line in tunnel_proc.stdout:
        print(line, end="")
        if not url_found:
            match = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", line)
            if match:
                tunnel_url = match.group(0)
                url_found  = True
                print(f"\n✅ URL tunel: {tunnel_url}\n")
                send_telegram(
                    f"Marketplace Tool online!\n\n"
                    f"URL: <b>{tunnel_url}</b>\n\n"
                    f"Acceseaza de pe orice retea."
                )

    # 4. Tine procesele vii
    tunnel_proc.wait()
    streamlit_proc.wait()


if __name__ == "__main__":
    main()
