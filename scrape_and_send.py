"""
Rangkuman Berita Ekonomi Harian -> PDF -> WhatsApp
=============================================
Alur:
1. Ambil berita terbaru (24 jam terakhir) dari beberapa RSS feed ekonomi.
2. Kirim ke Claude API, minta output terstruktur (JSON): caption pendek untuk
   WhatsApp + isi laporan lebih panjang (dengan link sumber) untuk PDF.
3. Render laporan jadi PDF bermerek (logo BI + Departemen Regional).
4. PDF di-commit ke repo (lihat langkah git di GitHub Actions workflow) supaya
   punya URL publik, lalu dikirim ke WhatsApp lewat Twilio sebagai lampiran
   dokumen dengan caption.

Dua mode, dipanggil terpisah dari workflow:
  python scrape_and_send.py generate   -> scrape + rangkum + build PDF
  python scrape_and_send.py send       -> kirim PDF (setelah di-push ke repo)

Environment variables yang dibutuhkan (diisi lewat GitHub Secrets, lihat README.md):
- ANTHROPIC_API_KEY
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_WHATSAPP_FROM   (format: whatsapp:+14155238886, dari Twilio)
- TWILIO_WHATSAPP_TO     (format: whatsapp:+62812xxxxxxx, nomor Anda)
"""

import base64
import datetime
import html
import json
import os
import sys

import anthropic
import feedparser
import requests
from weasyprint import HTML

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
PDF_PATH = os.path.join(OUTPUT_DIR, "digest.pdf")
CAPTION_PATH = os.path.join(OUTPUT_DIR, "caption.txt")
PDF_REPO_PATH = "output/digest.pdf"  # path relatif di repo, untuk URL raw GitHub

BI_LOGO_PATH = os.path.join(BASE_DIR, "assets", "bi_logo.png")
DR_LOGO_PATH = os.path.join(BASE_DIR, "assets", "dr_logo.png")


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
# 2. Rangkum pakai Claude -> output terstruktur (JSON)
# ---------------------------------------------------------------------------
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {
            "type": "string",
            "description": "Caption singkat gaya WhatsApp (5-8 poin, ±300-400 kata, *bold* untuk judul, tanpa markdown heading).",
        },
        "report_title": {
            "type": "string",
            "description": "Judul laporan, misal 'Rangkuman Berita Ekonomi Harian'.",
        },
        "highlight": {
            "type": "string",
            "description": "1-2 kalimat highlight paling penting hari ini.",
        },
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string", "description": "Nama kategori, misal 'Makroekonomi & Kebijakan', 'Pasar & Bursa', 'Sektor & Korporasi'."},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "body": {"type": "string", "description": "2-4 kalimat penjelasan, lebih detail dari caption WhatsApp."},
                                "source_url": {"type": "string"},
                            },
                            "required": ["title", "body", "source_url"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["heading", "items"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["caption", "report_title", "highlight", "sections"],
    "additionalProperties": False,
}


def summarize_with_claude(entries: list) -> dict:
    if not entries:
        return {
            "caption": "Tidak ada berita ekonomi baru yang terdeteksi pagi ini.",
            "report_title": "Rangkuman Berita Ekonomi Harian",
            "highlight": "Tidak ada berita ekonomi baru yang terdeteksi pagi ini.",
            "sections": [],
        }

    raw_text = "\n\n".join(
        f"Judul: {it['title']}\nCuplikan: {it['summary']}\nLink: {it['link']}"
        for it in entries[:40]  # batasi biar tidak kepanjangan
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Berikut kumpulan berita ekonomi Indonesia & global dari beberapa media hari ini:

{raw_text}

Buatkan DUA versi rangkuman dari berita-berita di atas:

1. "caption" - versi singkat gaya pesan WhatsApp pagi hari:
   - Buka dengan satu baris tanggal & sapaan singkat.
   - 5-8 poin berita terpenting (gabungkan berita duplikat/topik sama jadi satu poin).
   - Tiap poin: judul singkat (bold pakai *asterisk*, gaya WhatsApp) + 1-2 kalimat inti.
   - Tutup dengan 1 baris highlight paling penting hari ini.
   - Total maksimal ±300-400 kata, bahasa Indonesia, ringkas, tanpa markdown heading (#).

2. "sections" - versi lebih panjang & detail untuk laporan PDF:
   - Kelompokkan berita jadi beberapa kategori (misal: Makroekonomi & Kebijakan, Pasar & Bursa, Sektor & Korporasi, Global).
   - Tiap item: judul, 2-4 kalimat penjelasan (lebih detail dari caption), dan link sumber asli beritanya (ambil dari field Link di atas, jangan dikarang).
   - Cakup lebih banyak berita daripada versi caption (boleh 10-20 item total).

Isi juga "report_title" (judul laporan) dan "highlight" (1-2 kalimat insight paling penting hari ini, terpisah dari caption)."""

    resp = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=4000,
        thinking={"type": "disabled"},
        output_config={"format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )

    text_blocks = [block.text for block in resp.content if block.type == "text"]
    return json.loads("\n".join(text_blocks).strip())


# ---------------------------------------------------------------------------
# 3. Render PDF (logo BI + Departemen Regional, palet biru & putih)
# ---------------------------------------------------------------------------
def _b64_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def build_html(data: dict, date_str: str) -> str:
    bi_logo_b64 = _b64_image(BI_LOGO_PATH)
    dr_logo_b64 = _b64_image(DR_LOGO_PATH)

    sections_html = []
    for section in data.get("sections", []):
        items_html = []
        for item in section.get("items", []):
            source_url = html.escape(item.get("source_url", ""))
            items_html.append(f"""
            <div class="item">
                <div class="item-title">{html.escape(item.get('title', ''))}</div>
                <div class="item-body">{html.escape(item.get('body', ''))}</div>
                {f'<a class="item-source" href="{source_url}">{source_url}</a>' if source_url else ''}
            </div>
            """)
        sections_html.append(f"""
        <div class="section">
            <h2>{html.escape(section.get('heading', ''))}</h2>
            {''.join(items_html)}
        </div>
        """)

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<style>
    @page {{
        size: A4;
        margin: 2.2cm 1.8cm 2cm 1.8cm;
        @bottom-center {{
            content: "Halaman " counter(page) " dari " counter(pages);
            font-size: 8pt;
            color: #6b7a90;
        }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
        font-family: 'Helvetica Neue', Arial, sans-serif;
        color: #1a2332;
        font-size: 10.5pt;
        line-height: 1.5;
    }}
    .header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 3px solid #0b3d91;
        padding-bottom: 14px;
        margin-bottom: 6px;
    }}
    .header img {{ height: 34px; }}
    .header .logos {{ display: flex; gap: 22px; align-items: center; }}
    .masthead {{
        background: #0b3d91;
        color: #ffffff;
        padding: 22px 26px;
        border-radius: 6px;
        margin-bottom: 22px;
    }}
    .masthead .org {{
        font-size: 9pt;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #cfe0ff;
        margin-bottom: 6px;
    }}
    .masthead h1 {{
        font-size: 20pt;
        margin: 0 0 8px 0;
        font-weight: 700;
    }}
    .masthead .date {{
        font-size: 10pt;
        color: #dbe8ff;
    }}
    .highlight-box {{
        background: #eef3fc;
        border-left: 4px solid #0b3d91;
        padding: 12px 16px;
        margin-bottom: 24px;
        font-size: 10.5pt;
        color: #12233f;
    }}
    .highlight-box .label {{
        font-weight: 700;
        color: #0b3d91;
        display: block;
        margin-bottom: 4px;
        font-size: 9pt;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }}
    .section {{ margin-bottom: 22px; }}
    .section h2 {{
        font-size: 12.5pt;
        color: #0b3d91;
        border-bottom: 1.5px solid #c6d6f0;
        padding-bottom: 6px;
        margin-bottom: 12px;
    }}
    .item {{
        margin-bottom: 14px;
        padding-left: 2px;
    }}
    .item-title {{
        font-weight: 700;
        font-size: 10.5pt;
        color: #12233f;
        margin-bottom: 3px;
    }}
    .item-body {{
        color: #33415c;
        margin-bottom: 3px;
    }}
    .item-source {{
        font-size: 8.5pt;
        color: #5c7bb0;
        word-break: break-all;
        text-decoration: none;
    }}
    .footer-note {{
        margin-top: 28px;
        padding-top: 12px;
        border-top: 1px solid #dde5f0;
        font-size: 8pt;
        color: #8494ab;
    }}
</style>
</head>
<body>
    <div class="header">
        <div class="logos">
            <img src="data:image/png;base64,{bi_logo_b64}" alt="Bank Indonesia">
        </div>
        <img src="data:image/png;base64,{dr_logo_b64}" alt="Departemen Regional" style="height: 40px;">
    </div>

    <div class="masthead">
        <div class="org">Departemen Regional &mdash; Bank Indonesia</div>
        <h1>{html.escape(data.get('report_title', 'Rangkuman Berita Ekonomi Harian'))}</h1>
        <div class="date">{html.escape(date_str)}</div>
    </div>

    <div class="highlight-box">
        <span class="label">Highlight Hari Ini</span>
        {html.escape(data.get('highlight', ''))}
    </div>

    {''.join(sections_html) if sections_html else '<p>Tidak ada berita ekonomi baru yang terdeteksi pagi ini.</p>'}

    <div class="footer-note">
        Dihasilkan otomatis dari agregasi berita publik (Google News). Bukan rilis resmi Bank Indonesia / Departemen
        Regional. Untuk keperluan internal.
    </div>
</body>
</html>"""


def build_pdf(data: dict, date_str: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    html_str = build_html(data, date_str)
    HTML(string=html_str, base_url=BASE_DIR).write_pdf(PDF_PATH)
    print(f"PDF dibuat: {PDF_PATH}")


# ---------------------------------------------------------------------------
# 4. Kirim ke WhatsApp lewat Twilio (dokumen PDF + caption)
# ---------------------------------------------------------------------------
def get_pdf_public_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY tidak ada di environment - jalankan dari GitHub Actions.")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{PDF_REPO_PATH}"


def send_whatsapp_pdf(caption: str):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_FROM"]
    to_number = os.environ["TWILIO_WHATSAPP_TO"]

    media_url = get_pdf_public_url()
    print(f"URL PDF publik: {media_url}")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    # Body/caption Twilio WhatsApp dibatasi ~1600 karakter; potong kalau perlu.
    caption = caption[:1550]

    resp = requests.post(
        url,
        data={
            "From": from_number,
            "To": to_number,
            "Body": caption,
            "MediaUrl": media_url,
        },
        auth=(account_sid, auth_token),
    )
    if resp.status_code >= 300:
        print(f"Gagal kirim WhatsApp: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    print(f"Terkirim: {resp.status_code}")


# ---------------------------------------------------------------------------
# 5. Entry points
# ---------------------------------------------------------------------------
def cmd_generate():
    print("Mengambil berita...")
    entries = fetch_recent_entries()
    print(f"Ditemukan {len(entries)} berita dalam {HOURS_LOOKBACK} jam terakhir.")

    print("Merangkum dengan Claude...")
    data = summarize_with_claude(entries)
    print("--- CAPTION ---")
    print(data["caption"])

    date_str = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=7))
    ).strftime("%A, %d %B %Y")

    print("Membangun PDF...")
    build_pdf(data, date_str)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(CAPTION_PATH, "w", encoding="utf-8") as f:
        f.write(data["caption"])
    print(f"Caption disimpan: {CAPTION_PATH}")


def cmd_send():
    with open(CAPTION_PATH, "r", encoding="utf-8") as f:
        caption = f.read()
    print("Mengirim PDF ke WhatsApp...")
    send_whatsapp_pdf(caption)
    print("Selesai.")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "generate"
    if mode == "generate":
        cmd_generate()
    elif mode == "send":
        cmd_send()
    else:
        print(f"Mode tidak dikenal: {mode} (pakai 'generate' atau 'send')", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
