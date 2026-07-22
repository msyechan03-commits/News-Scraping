"""
Rangkuman Berita Ekonomi Harian -> WhatsApp
=============================================
Alur:
1. Ambil berita terbaru (24 jam terakhir) dari beberapa RSS feed ekonomi.
2. Kirim judul + ringkasan singkat tiap berita ke Claude API untuk dirangkum
   jadi satu digest singkat, enak dibaca di WhatsApp.
3. Kirim hasil rangkuman ke nomor WhatsApp Anda lewat Twilio WhatsApp API.

Environment variables yang dibutuhkan (diisi lewat GitHub Secrets, lihat README.md):
- ANTHROPIC_API_KEY
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_WHATSAPP_FROM   (format: whatsapp:+14155238886, dari Twilio)
- TWILIO_WHATSAPP_TO     (format: whatsapp:+62812xxxxxxx, nomor Anda)
"""

import os
import sys
import datetime
import feedparser
import requests
import anthropic

# ---------------------------------------------------------------------------
# 1. Sumber berita (RSS). Tambah/kurangi sesuai selera.
# CNBC/CNN Indonesia diblokir (HTTP 403) dari IP GitHub Actions, jadi pakai
# Google News RSS (aggregator lintas media, tidak memblokir IP cloud).
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    "https://news.google.com/rss/search?q=ekonomi+indonesia+when:1d&hl=id&gl=ID&ceid=ID:id",
    "https://news.google.com/rss/search?q=bisnis+OR+market+OR+bursa+indonesia+when:1d&hl=id&gl=ID&ceid=ID:id",
]

HOURS_LOOKBACK = 20  # ambil berita dari X jam terakhir (jalan tiap pagi)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def fetch_recent_entries():
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS_LOOKBACK)
    entries = []

    for url in RSS_FEEDS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
        except requests.RequestException as exc:
            print(f"Gagal ambil feed {url}: {exc}", file=sys.stderr)
            continue

        feed = feedparser.parse(resp.content)
        print(
            f"  {url} -> HTTP {resp.status_code}, {len(resp.content)} bytes, "
            f"{len(feed.entries)} entri, bozo={feed.bozo}"
            + (f", bozo_exception={feed.bozo_exception}" if feed.bozo else "")
        )

        for e in feed.entries:
            pub = None
            if getattr(e, "published_parsed", None):
                pub = datetime.datetime(*e.published_parsed[:6], tzinfo=datetime.timezone.utc)

            # kalau tidak ada tanggal, tetap sertakan (beberapa feed tidak isi published)
            if pub is not None and pub < cutoff:
                continue

            entries.append({
                "title": e.get("title", "").strip(),
                "summary": e.get("summary", "").strip(),
                "link": e.get("link", "").strip(),
            })

    return entries


# ---------------------------------------------------------------------------
# 2. Rangkum pakai Claude
# ---------------------------------------------------------------------------
def summarize_with_claude(entries: list) -> str:
    if not entries:
        return "Tidak ada berita ekonomi baru yang terdeteksi pagi ini."

    raw_text = "\n\n".join(
        f"Judul: {it['title']}\nCuplikan: {it['summary']}\nLink: {it['link']}"
        for it in entries[:40]  # batasi biar tidak kepanjangan
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Berikut kumpulan berita ekonomi Indonesia & global dari beberapa media hari ini:

{raw_text}

Tolong buatkan rangkuman berita ekonomi untuk pesan WhatsApp pagi hari, dengan format:
- Buka dengan satu baris tanggal & sapaan singkat.
- 5-8 poin berita terpenting (gabungkan berita duplikat/topik yang sama jadi satu poin).
- Tiap poin: judul singkat (bold pakai *asterisk*, gaya WhatsApp) + 1-2 kalimat inti.
- Tutup dengan 1 baris highlight paling penting hari ini (misal: data inflasi, suku bunga, nilai tukar, dsb kalau ada).
- Total keseluruhan maksimal sekitar 300-400 kata, bahasa Indonesia, ringkas dan langsung ke inti, tanpa basa-basi berlebihan.
- Jangan pakai markdown heading (#), cukup format WhatsApp (*bold*, garis baru antar poin)."""

    resp = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    text_blocks = [block.text for block in resp.content if block.type == "text"]
    return "\n".join(text_blocks).strip()


# ---------------------------------------------------------------------------
# 3. Kirim ke WhatsApp lewat Twilio
# ---------------------------------------------------------------------------
def send_whatsapp(message: str):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_FROM"]
    to_number = os.environ["TWILIO_WHATSAPP_TO"]

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    # WhatsApp lewat Twilio membatasi ~1600 karakter per pesan; potong jadi beberapa
    # bagian kalau perlu supaya tidak gagal terkirim.
    chunks = [message[i:i + 1500] for i in range(0, len(message), 1500)] or [message]

    for chunk in chunks:
        resp = requests.post(
            url,
            data={"From": from_number, "To": to_number, "Body": chunk},
            auth=(account_sid, auth_token),
        )
        if resp.status_code >= 300:
            print(f"Gagal kirim WhatsApp: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
        print(f"Terkirim: {resp.status_code}")


def main():
    print("Mengambil berita...")
    entries = fetch_recent_entries()
    print(f"Ditemukan {len(entries)} berita dalam {HOURS_LOOKBACK} jam terakhir.")

    print("Merangkum dengan Claude...")
    digest = summarize_with_claude(entries)
    print("--- DIGEST ---")
    print(digest)

    print("Mengirim ke WhatsApp...")
    send_whatsapp(digest)
    print("Selesai.")


if __name__ == "__main__":
    main()
