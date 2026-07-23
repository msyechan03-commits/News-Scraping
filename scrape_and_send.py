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
import re
import sys
import time
import urllib.parse

import anthropic
import feedparser
import requests
from weasyprint import HTML

# ---------------------------------------------------------------------------
# 1. Sumber berita (RSS). Tambah/kurangi sesuai selera.
# CNBC/CNN Indonesia diblokir (HTTP 403) dari IP GitHub Actions, jadi pakai
# Google News RSS (aggregator lintas media, tidak memblokir IP cloud).
# CATATAN: SENGAJA TIDAK ada feed khusus "BI Rate". Feed umum sudah pasti
# mengangkat keputusan BI Rate sbg berita utama di hari-H; sebaliknya, feed
# khusus BI Rate malah membanjiri kandidat dgn berita REAKSI/ulasan lanjutan
# selama berhari-hari setelah keputusan, bikin BI Rate seolah muncul tiap hari.
#
# Feed per-wilayah (Sumatera/Jawa/Kalimantan/Balinusra/Sulampua) ditambahkan
# supaya berita perkembangan ekonomi regional (investasi, proyek, dst) benar-
# benar tertangkap - feed umum di atas jarang cukup spesifik ke level wilayah.
# ---------------------------------------------------------------------------
REGIONS = ["Sumatera", "Jawa", "Kalimantan", "Balinusra", "Sulampua"]

# Query per wilayah: "Balinusra"/"Sulampua" bukan istilah umum di media, jadi
# dipecah ke nama pulau/provinsi penyusunnya biar hasil pencarian tidak kosong.
_REGION_QUERY_TERMS = {
    "Sumatera": "Sumatera",
    "Jawa": "Jawa",
    "Kalimantan": "Kalimantan",
    "Balinusra": "Bali+OR+%22Nusa+Tenggara%22",
    "Sulampua": "Sulawesi+OR+Maluku+OR+Papua",
}

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=ekonomi+indonesia+when:1d&hl=id&gl=ID&ceid=ID:id",
    "https://news.google.com/rss/search?q=bisnis+OR+market+OR+bursa+indonesia+when:1d&hl=id&gl=ID&ceid=ID:id",
] + [
    f"https://news.google.com/rss/search?q=%28investasi+OR+ekonomi+OR+inflasi%29+{terms}+when:1d&hl=id&gl=ID&ceid=ID:id"
    for terms in _REGION_QUERY_TERMS.values()
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
CAPTION_PATH = os.path.join(OUTPUT_DIR, "caption.txt")
PDF_FILENAME_STATE_PATH = os.path.join(OUTPUT_DIR, "pdf_filename.txt")

BI_LOGO_PATH = os.path.join(BASE_DIR, "assets", "bi_logo.png")
DR_LOGO_PATH = os.path.join(BASE_DIR, "assets", "dr_logo.png")

DAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MONTHS_ID = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]


def format_date_id(dt: datetime.datetime) -> str:
    """Format tanggal Indonesia manual (tidak bergantung locale sistem, yang
    seringkali tidak tersedia di runner GitHub Actions / berbeda OS)."""
    return f"{DAYS_ID[dt.weekday()]}, {dt.day} {MONTHS_ID[dt.month - 1]} {dt.year}"


def today_wib() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))


def fetch_recent_entries():
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS_LOOKBACK)
    entries = []
    seen_titles = set()

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

            title = e.get("title", "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)

            # Google News RSS punya tag <source url="...">Nama Media</source> -
            # ini nama media aslinya (CNBC Indonesia, Bloomberg, dst), beda dari
            # e['link'] yang cuma link redirect Google News.
            source = e.get("source", {}) or {}
            source_name = (source.get("title") or "").strip()
            source_href = (source.get("href") or "").strip()

            entries.append({
                "title": title,
                "summary": e.get("summary", "").strip(),
                "link": e.get("link", "").strip(),
                "source_name": source_name,
                "source_href": source_href,
            })

    return entries


# ---------------------------------------------------------------------------
# 2. Rangkum pakai Claude -> output terstruktur (JSON)
#
# Struktur laporan (3 section tetap, sesuai framework):
#   1. Perkembangan Ekonomi Global dan Nasional  -> global_national[]
#   2 & 3. Per wilayah -> regions[] (tiap wilayah punya demand[]/sectors[]
#          utk section "Ekonomi Wilayah", dan inflation[] utk section
#          "Inflasi Wilayah" - dipisah saat render PDF, bukan saat generate,
#          krn satu wilayah yg sama dipakai di kedua section).
# Semua list boleh kosong - tidak dipaksakan ada isinya tiap hari.
# ---------------------------------------------------------------------------
_ITEM_PROPS = {
    "title": {"type": "string"},
    "body": {"type": "string", "description": "1-3 kalimat. Kuantitatif (angka/persentase) kalau tersedia di berita, kualitatif/naratif kalau tidak - yang penting ada indikasi perkembangan/update."},
    "source_url": {"type": "string", "description": "Link asli, disalin persis dari field Link pada data sumber - jangan dikarang."},
    "source_name": {"type": "string", "description": "Nama media, disalin persis dari field Sumber pada data (mis. 'CNBC Indonesia') - jangan dikarang, kosongkan jika tidak ada."},
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {
            "type": "string",
            "description": "Caption singkat gaya WhatsApp (5-8 poin lintas semua section, ±300-400 kata, *bold* untuk judul, tanpa markdown heading).",
        },
        "report_title": {
            "type": "string",
            "description": "Judul laporan, misal 'Rangkuman Ekonomi Harian'.",
        },
        "highlight": {
            "type": "string",
            "description": "1-2 kalimat insight paling penting hari ini (lintas global/nasional/wilayah).",
        },
        "global_national": {
            "type": "array",
            "description": "Section 1: Perkembangan Ekonomi Global dan Nasional. Maks 6 item.",
            "items": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["Global", "Nasional"]},
                    **_ITEM_PROPS,
                },
                "required": ["scope", "title", "body", "source_url", "source_name"],
                "additionalProperties": False,
            },
        },
        "regions": {
            "type": "array",
            "description": (
                "Section 2 & 3, dipecah per wilayah kerja. HANYA sertakan wilayah yang benar-benar "
                "punya berita relevan hari ini - jangan buat entri wilayah kosong (demand, sectors, "
                "dan inflation ketiganya kosong)."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "region_name": {"type": "string", "enum": REGIONS},
                    "demand": {
                        "type": "array",
                        "description": "Sisi Permintaan. Kosongkan kalau tidak ada berita relevan utk wilayah ini.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "category": {"type": "string", "enum": ["Fiskal", "Konsumsi RT", "Investasi", "Ekspor"]},
                                **_ITEM_PROPS,
                            },
                            "required": ["category", "title", "body", "source_url", "source_name"],
                            "additionalProperties": False,
                        },
                    },
                    "sectors": {
                        "type": "array",
                        "description": "Sisi Penawaran / Lapangan Usaha. Kosongkan kalau tidak ada berita relevan utk wilayah ini.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "category": {
                                    "type": "string",
                                    "enum": ["Pertanian", "Perdagangan", "Pertambangan", "Konstruksi", "Industri Pengolahan", "Akmamin"],
                                },
                                **_ITEM_PROPS,
                            },
                            "required": ["category", "title", "body", "source_url", "source_name"],
                            "additionalProperties": False,
                        },
                    },
                    "inflation": {
                        "type": "array",
                        "description": "Perkembangan inflasi wilayah. Kosongkan kalau tidak ada berita relevan utk wilayah ini.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "component": {"type": "string", "enum": ["Inflasi Inti", "Inflasi VF", "Inflasi AP"]},
                                **_ITEM_PROPS,
                            },
                            "required": ["component", "title", "body", "source_url", "source_name"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["region_name", "demand", "sectors", "inflation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["caption", "report_title", "highlight", "global_national", "regions"],
    "additionalProperties": False,
}


DEFAULT_TITLE = "Rangkuman Berita Ekonomi Harian"


def _extract_scalar(raw: str, key: str) -> str:
    """Ambil satu field string dari JSON (mentah/terpotong) via regex, lalu unescape."""
    m = re.search(rf'"{key}"\s*:\s*("(?:[^"\\]|\\.)*")', raw)
    if not m:
        return ""
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return ""


def _normalize_report(data: dict) -> dict:
    """Pastikan semua key wajib ada & bertipe benar, isi default kalau kosong."""
    return {
        "caption": data.get("caption") or "Rangkuman berita ekonomi hari ini.",
        "report_title": data.get("report_title") or DEFAULT_TITLE,
        "highlight": data.get("highlight") or "",
        "global_national": data.get("global_national") if isinstance(data.get("global_national"), list) else [],
        "regions": data.get("regions") if isinstance(data.get("regions"), list) else [],
    }


def summarize_with_claude(entries: list) -> dict:
    if not entries:
        return {
            "caption": "Tidak ada berita ekonomi baru yang terdeteksi pagi ini.",
            "report_title": DEFAULT_TITLE,
            "highlight": "Tidak ada berita ekonomi baru yang terdeteksi pagi ini.",
            "global_national": [],
            "regions": [],
        }

    raw_text = "\n\n".join(
        f"Judul: {it['title']}\nSumber: {it['source_name'] or '(tidak diketahui)'}"
        f"\nCuplikan: {it['summary']}\nLink: {it['link']}"
        for it in entries[:60]  # batasi biar tidak kepanjangan (lebih banyak drpd sebelumnya krn skrg cakupan lebih luas: global/nasional + 5 wilayah)
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Berikut kumpulan berita ekonomi Indonesia & global dari beberapa media hari ini:

{raw_text}

Susun laporan ekonomi dengan struktur TETAP berikut (kerangka standar laporan regional Bank
Indonesia):

SECTION 1 - "global_national": Perkembangan Ekonomi Global dan Nasional
  - "Global": pertumbuhan ekonomi global/negara maju, kebijakan moneter bank sentral utama
    (The Fed dll), harga komoditas global, risiko geopolitik berdampak ekonomi.
  - "Nasional": pertumbuhan PDB nasional, inflasi nasional, nilai tukar rupiah, kebijakan
    moneter BI (lihat catatan BI Rate di bawah), neraca perdagangan, kebijakan fiskal pusat.

SECTION 2 & 3 - "regions": Perkembangan Ekonomi & Inflasi per Wilayah Kerja
  Wilayah yang dipakai HANYA 5 ini (gunakan persis nama ini): {", ".join(REGIONS)}.
  Untuk tiap wilayah yang ADA beritanya, klasifikasikan ke:
  - "demand" (Sisi Permintaan): Fiskal (realisasi APBD/belanja daerah), Konsumsi RT (daya beli/
    aktivitas belanja masyarakat), Investasi (PMA/PMDN, pembangunan proyek/objek investasi baru,
    groundbreaking), Ekspor (kinerja ekspor komoditas/produk unggulan wilayah).
  - "sectors" (Sisi Penawaran/Lapangan Usaha): Pertanian, Perdagangan, Pertambangan, Konstruksi,
    Industri Pengolahan, Akmamin (Akomodasi & Makan Minum).
  - "inflation" (Inflasi Wilayah): Inflasi Inti, Inflasi VF (Volatile Food), Inflasi AP
    (Administered Prices).

ATURAN PENTING - JANGAN DIPAKSAKAN:
- TIDAK WAJIB semua 5 wilayah terisi. Hanya masukkan wilayah yang benar-benar punya berita
  relevan hari ini (investasi baru, perkembangan sektor, data inflasi, dll). Kalau cuma 1-2
  wilayah yang ada beritanya, itu wajar - jangan mengarang isi utk wilayah lain.
- Dalam satu wilayah, sub-bagian (demand/sectors/inflation) yang tidak ada beritanya HARUS
  dikosongkan (array kosong) - jangan dipaksa diisi/dikarang.
- Konten boleh KUANTITATIF (ada angka/persentase dari berita) atau KUALITATIF/naratif (kalau
  beritanya tidak menyebut angka pasti) - yang penting ada indikasi perkembangan/update nyata,
  bukan basa-basi.
- Berita yang cuma menyebut kota/kabupaten spesifik tetap dipetakan ke wilayah induknya
  (mis. Semarang/Surabaya -> Jawa, Makassar -> Sulampua, Denpasar -> Balinusra, dst).

PRIORITAS: Pikirkan baik-baik mana berita ekonomi yang PALING PENTING dan paling berdampak luas
HARI INI untuk pembaca laporan ini (kalangan Bank Indonesia / pengambil kebijakan ekonomi
regional). Urutkan dari yang paling signifikan, berdasarkan APA YANG BENAR-BENAR TERJADI atau
DIUMUMKAN hari itu - bukan asal ikut mana yang paling banyak diberitakan media. Keputusan/data
BARU (kebijakan moneter, rilis data makro, kebijakan fiskal) biasanya lebih penting daripada
berita korporasi tunggal, promosi produk, atau seremonial - tapi gunakan penilaianmu sendiri.

PENTING soal BI Rate / suku bunga - bedakan BERITA BARU vs BERITA REAKSI:
- Hanya keputusan/pengumuman BI yang BENAR-BENAR BARU hari itu (mis. "BI naikkan/tahan suku
  bunga jadi X%") yang boleh masuk prioritas atas di section Nasional.
- Setelah keputusan, media terus menerbitkan berita REAKSI/ANALISIS LANJUTAN yang mengutip BI
  Rate selama beberapa hari (mis. "analis menilai kenaikan BI Rate...", "dampak BI Rate ke
  rupiah..."). INI BUKAN BERITA BARU - jangan angkat ke prioritas hanya karena masih sering
  disebut. RDG BI hanya sekali sebulan - di mayoritas hari (tanpa keputusan baru), TIDAK apa-apa
  kalau tidak ada satupun poin tentang BI Rate.

Buatkan output berikut:

1. "global_national" - lihat Section 1 di atas. Maks 6 item total, 1-3 kalimat per item.

2. "regions" - lihat Section 2 & 3 di atas. Utk tiap item: judul, 1-3 kalimat penjelasan,
   "source_url" (link asli dari field Link) dan "source_name" (nama media dari field Sumber) -
   salin persis, jangan dikarang.

3. "caption" - versi singkat gaya pesan WhatsApp pagi hari, MERANGKUM LINTAS semua section
   di atas (global/nasional + wilayah yang ada beritanya):
   - JANGAN tulis baris tanggal/sapaan di awal (sistem akan menambahkannya otomatis) - langsung
     mulai dari poin berita pertama.
   - 5-8 poin berita terpenting (gabungkan berita duplikat/topik sama jadi satu poin).
   - Tiap poin: judul singkat (bold pakai *asterisk*, gaya WhatsApp) + 1-2 kalimat inti.
   - Tutup dengan 1 baris highlight paling penting hari ini.
   - Total maksimal ±300-400 kata, bahasa Indonesia, ringkas, tanpa markdown heading (#).

Isi juga "report_title" (judul laporan, mis. 'Rangkuman Ekonomi Harian') dan "highlight"
(1-2 kalimat insight paling penting hari ini, terpisah dari caption)."""

    # Sonnet 5 dgn thinking ringan (effort low): tugas ini adalah PENILAIAN
    # (memilih & mengurutkan berita terpenting, klasifikasi ke wilayah/kategori),
    # bukan sekadar meringkas - Sonnet jauh lebih baik menimbang ini drpd Haiku.
    # max_tokens dinaikkan krn struktur skrg jauh lebih besar (global/nasional +
    # 5 wilayah x 3 sub-bagian) + thinking ikut terhitung dlm token output.
    resp = client.with_options(max_retries=6).messages.create(
        model="claude-sonnet-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": REPORT_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )

    print(f"  stop_reason={resp.stop_reason}, output_tokens={resp.usage.output_tokens}")

    text_blocks = [block.text for block in resp.content if block.type == "text"]
    raw_json = "\n".join(text_blocks).strip()

    try:
        return _normalize_report(json.loads(raw_json))
    except json.JSONDecodeError:
        # JSON kepotong (mis. kena max_tokens). Jangan crash & buang seluruh run -
        # selamatkan minimal caption/highlight (field pertama, biasanya utuh) via
        # regex, kirim tanpa detail section. Caption WhatsApp tetap terkirim.
        print(
            f"  JSON tidak lengkap (panjang={len(raw_json)}, stop_reason={resp.stop_reason}) - "
            f"pakai fallback caption-only.",
            file=sys.stderr,
        )
        salvaged = {
            "caption": _extract_scalar(raw_json, "caption"),
            "report_title": _extract_scalar(raw_json, "report_title"),
            "highlight": _extract_scalar(raw_json, "highlight"),
            "global_national": [],
            "regions": [],
        }
        return _normalize_report(salvaged)


# ---------------------------------------------------------------------------
# 3. Render PDF (logo BI + Departemen Regional, palet biru & putih)
# ---------------------------------------------------------------------------
def _b64_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _source_label(item: dict) -> str:
    """Nama media yang ditampilkan sbg teks link (mis. 'CNBC Indonesia'),
    fallback ke nama domain (mis. 'cnbcindonesia.com') kalau source_name kosong."""
    name = (item.get("source_name") or "").strip()
    if name:
        return name
    url = (item.get("source_url") or "").strip()
    if not url:
        return ""
    domain = urllib.parse.urlparse(url).netloc
    return domain[4:] if domain.startswith("www.") else domain


def _render_item(item: dict, category_label: str = "") -> str:
    """Render satu item berita (dipakai di semua section: global/nasional,
    demand, sectors, inflation) - opsional dgn label kategori kecil di atas judul."""
    source_url = html.escape(item.get("source_url", ""))
    source_label = html.escape(_source_label(item))
    source_line = (
        f'<a class="item-source" href="{source_url}">&#8599;&nbsp;{source_label}</a>'
        if source_url and source_label else ""
    )
    category_html = (
        f'<div class="item-category">{html.escape(category_label)}</div>' if category_label else ""
    )
    return f"""
    <div class="item">
        {category_html}
        <div class="item-title">{html.escape(item.get('title', ''))}</div>
        <div class="item-body">{html.escape(item.get('body', ''))}</div>
        {source_line}
    </div>
    """


def _render_subgroup(label: str, items: list, category_key: str) -> str:
    """Render satu sub-kelompok (mis. 'Sisi Permintaan') dgn label kategori per item
    (mis. 'Investasi', 'Fiskal') diambil dari category_key ('category'/'component'/'scope')."""
    if not items:
        return ""
    items_html = "".join(_render_item(it, it.get(category_key, "")) for it in items)
    return f"""
    <div class="subgroup">
        <div class="subgroup-label">{html.escape(label)}</div>
        {items_html}
    </div>
    """


def _render_empty_state(text: str) -> str:
    return f'<p class="empty-state">{html.escape(text)}</p>'


def _build_section1_html(global_national: list) -> str:
    global_items = [i for i in global_national if i.get("scope") == "Global"]
    national_items = [i for i in global_national if i.get("scope") == "Nasional"]
    # category_key="" -> tidak ada badge kategori per-item (judul sub-grup "Global"/
    # "Nasional" sudah cukup, badge lagi cuma redundan)
    body = _render_subgroup("Global", global_items, "") + _render_subgroup("Nasional", national_items, "")
    if not body:
        body = _render_empty_state("Tidak ada perkembangan ekonomi global/nasional signifikan yang tercatat hari ini.")
    return f"""
    <div class="section">
        <div class="section-head">
            <span class="section-num">01</span>
            <span class="section-title">Perkembangan Ekonomi Global dan Nasional</span>
        </div>
        {body}
    </div>
    """


def _build_section2_html(regions: list) -> str:
    region_blocks = []
    for region in regions:
        demand = region.get("demand", [])
        sectors = region.get("sectors", [])
        if not demand and not sectors:
            continue
        block = _render_subgroup("Sisi Permintaan", demand, "category")
        block += _render_subgroup("Sisi Penawaran (Lapangan Usaha)", sectors, "category")
        region_blocks.append(f"""
        <div class="region-block">
            <div class="region-name">{html.escape(region.get('region_name', ''))}</div>
            {block}
        </div>
        """)
    body = "".join(region_blocks) if region_blocks else _render_empty_state(
        "Tidak ada perkembangan ekonomi wilayah yang tercatat hari ini."
    )
    return f"""
    <div class="section">
        <div class="section-head">
            <span class="section-num">02</span>
            <span class="section-title">Perkembangan Terkini Ekonomi Wilayah</span>
        </div>
        {body}
    </div>
    """


def _build_section3_html(regions: list) -> str:
    region_blocks = []
    for region in regions:
        inflation = region.get("inflation", [])
        if not inflation:
            continue
        # tanpa label sub-grup "Inflasi" - judul section sudah "Inflasi Wilayah",
        # badge per item (mis. "Inflasi VF") sudah cukup, jadi tidak perlu diulang.
        block = "".join(_render_item(it, it.get("component", "")) for it in inflation)
        region_blocks.append(f"""
        <div class="region-block">
            <div class="region-name">{html.escape(region.get('region_name', ''))}</div>
            {block}
        </div>
        """)
    body = "".join(region_blocks) if region_blocks else _render_empty_state(
        "Tidak ada data/berita inflasi wilayah yang tercatat hari ini."
    )
    return f"""
    <div class="section">
        <div class="section-head">
            <span class="section-num">03</span>
            <span class="section-title">Perkembangan Terkini Inflasi Wilayah</span>
        </div>
        {body}
    </div>
    """


def build_html(data: dict, date_str: str) -> str:
    bi_logo_b64 = _b64_image(BI_LOGO_PATH)
    dr_logo_b64 = _b64_image(DR_LOGO_PATH)

    regions = data.get("regions", [])
    sections_html = (
        _build_section1_html(data.get("global_national", []))
        + _build_section2_html(regions)
        + _build_section3_html(regions)
    )

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<style>
    @page {{
        size: A4;
        margin: 2.3cm 1.9cm 1.8cm 1.9cm;
        @top-left {{
            content: "DEPARTEMEN REGIONAL \\2014 BANK INDONESIA";
            font-family: Arial, sans-serif;
            font-size: 7.5pt;
            letter-spacing: 0.08em;
            color: #8a9bb5;
        }}
        @top-right {{
            content: "{html.escape(date_str).upper()}";
            font-family: Arial, sans-serif;
            font-size: 7.5pt;
            letter-spacing: 0.05em;
            color: #8a9bb5;
        }}
        @bottom-right {{
            content: "Halaman " counter(page) " / " counter(pages);
            font-family: Arial, sans-serif;
            font-size: 7.5pt;
            color: #8a9bb5;
        }}
        @bottom-left {{
            content: "Laporan Internal";
            font-family: Arial, sans-serif;
            font-size: 7.5pt;
            color: #b9863f;
        }}
    }}
    /* Cover: full-bleed, tanpa margin & tanpa header/footer */
    @page :first {{
        margin: 0;
        @top-left {{ content: ""; }}
        @top-right {{ content: ""; }}
        @bottom-right {{ content: ""; }}
        @bottom-left {{ content: ""; }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
        font-family: 'Helvetica Neue', Arial, sans-serif;
        color: #1b2536;
        font-size: 10.5pt;
        line-height: 1.55;
        margin: 0;
    }}

    /* ============ COVER (halaman 1) ============ */
    .cover {{
        position: relative;
        width: 21cm;
        height: 29.7cm;
        background: #0a2342;
        color: #ffffff;
        page-break-after: always;
        overflow: hidden;
    }}
    /* pita aksen emas vertikal di tepi kiri */
    .cover-accent {{
        position: absolute;
        top: 0; left: 0; bottom: 0;
        width: 0.5cm;
        background: #c9a24b;
    }}
    /* panel navy lebih terang di bawah utk kedalaman */
    .cover-band {{
        position: absolute;
        left: 0; right: 0; bottom: 0;
        height: 9.5cm;
        background: #0d2b52;
    }}
    .cover-inner {{
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        padding: 2cm 2cm 1.8cm 2.3cm;
    }}
    .logo-card {{
        background: #ffffff;
        border-radius: 10px;
        padding: 20px 26px;
        width: 100%;
    }}
    .logo-card td {{ vertical-align: middle; }}
    .cover-eyebrow {{
        position: absolute;
        left: 2.3cm; right: 2cm;
        top: 13.2cm;
        font-size: 12pt;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: #c9a24b;
        font-weight: 700;
    }}
    .cover-title {{
        position: absolute;
        left: 2.3cm; right: 2cm;
        top: 14.6cm;
        font-size: 40pt;
        line-height: 1.15;
        font-weight: 800;
        color: #ffffff;
    }}
    .cover-rule {{
        position: absolute;
        left: 2.3cm;
        top: 22cm;
        width: 2.4cm;
        height: 4px;
        background: #c9a24b;
    }}
    .cover-tagline {{
        position: absolute;
        left: 2.3cm; right: 5cm;
        top: 22.5cm;
        font-size: 12.5pt;
        color: #b7c8e4;
        line-height: 1.6;
    }}
    .cover-foot {{
        position: absolute;
        left: 2.3cm; right: 2cm;
        bottom: 1.8cm;
    }}
    .cover-foot .date {{
        font-size: 15pt;
        font-weight: 700;
        color: #ffffff;
    }}
    .cover-foot .sub {{
        font-size: 9.5pt;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: #8fa6cc;
        margin-top: 4px;
    }}

    /* ============ KONTEN (halaman 2 dst) ============ */
    .content {{ padding-top: 2px; }}

    .highlight-box {{
        background: #0a2342;
        color: #ffffff;
        border-radius: 8px;
        padding: 16px 20px 18px 20px;
        margin-bottom: 26px;
    }}
    .highlight-box .label {{
        display: block;
        font-weight: 700;
        color: #e0be6a;
        margin-bottom: 6px;
        font-size: 8.5pt;
        text-transform: uppercase;
        letter-spacing: 0.12em;
    }}
    .highlight-box .text {{ font-size: 11pt; line-height: 1.55; color: #eef3fb; text-align: justify; }}

    .section {{ margin-bottom: 24px; }}
    .section-head {{
        margin-bottom: 14px;
        padding-bottom: 8px;
        border-bottom: 2px solid #0a2342;
    }}
    .section-num {{
        display: inline-block;
        background: #c9a24b;
        color: #0a2342;
        font-weight: 800;
        font-size: 10pt;
        padding: 2px 8px;
        border-radius: 4px;
        margin-right: 10px;
        letter-spacing: 0.03em;
    }}
    .section-title {{
        font-size: 13pt;
        font-weight: 700;
        color: #0a2342;
        letter-spacing: 0.01em;
    }}
    /* Blok per wilayah (dipakai di section 2 & 3) */
    .region-block {{
        margin-bottom: 18px;
        padding: 14px 16px 4px 16px;
        background: #f7f9fc;
        border-radius: 6px;
        border: 1px solid #e6ebf3;
    }}
    .region-name {{
        font-size: 11.5pt;
        font-weight: 800;
        color: #0a2342;
        margin-bottom: 10px;
        padding-bottom: 6px;
        border-bottom: 1px solid #0a2342;
    }}
    /* Sub-kelompok dalam section/wilayah (mis. "Sisi Permintaan", "Global") */
    .subgroup {{ margin-bottom: 14px; }}
    .subgroup-label {{
        font-size: 8.5pt;
        font-weight: 800;
        color: #b9863f;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 8px;
    }}
    .empty-state {{
        font-size: 9.5pt;
        color: #94a1b8;
        font-style: italic;
        padding: 8px 0;
    }}
    .item {{
        margin-bottom: 15px;
        padding-left: 14px;
        border-left: 3px solid #d9e2f0;
    }}
    .item-category {{
        display: inline-block;
        font-size: 7.5pt;
        font-weight: 800;
        color: #0a2342;
        background: #e6ebf3;
        padding: 1px 7px;
        border-radius: 3px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 5px;
    }}
    .item-title {{
        font-weight: 700;
        font-size: 11pt;
        color: #142743;
        margin-bottom: 3px;
    }}
    .item-body {{
        color: #3c4a63;
        margin-bottom: 4px;
        font-size: 10pt;
        text-align: justify;
        text-justify: inter-word;
    }}
    .item-source {{
        font-size: 8pt;
        color: #b9863f;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-decoration: none;
        text-transform: uppercase;
    }}
    .footer-note {{
        margin-top: 30px;
        padding-top: 12px;
        border-top: 1px solid #e2e8f2;
        font-size: 7.5pt;
        color: #94a1b8;
        line-height: 1.5;
    }}
</style>
</head>
<body>
    <div class="cover">
        <div class="cover-accent"></div>
        <div class="cover-band"></div>
        <div class="cover-inner">
            <table class="logo-card" cellspacing="0" cellpadding="0" style="width:100%;">
                <tr>
                    <td style="text-align:left;">
                        <img src="data:image/png;base64,{bi_logo_b64}" alt="Bank Indonesia" style="width:230px;">
                    </td>
                    <td style="text-align:right;">
                        <img src="data:image/png;base64,{dr_logo_b64}" alt="Departemen Regional" style="width:95px;">
                    </td>
                </tr>
            </table>
        </div>
        <div class="cover-eyebrow">Departemen Regional</div>
        <div class="cover-title">Rangkuman Berita<br>Ekonomi Harian</div>
        <div class="cover-rule"></div>
        <div class="cover-tagline">Rangkuman perkembangan ekonomi terkini, disusun otomatis dari agregasi berita publik.</div>
        <div class="cover-foot">
            <div class="date">{html.escape(date_str)}</div>
            <div class="sub">Bank Indonesia &nbsp;&middot;&nbsp; Laporan Internal</div>
        </div>
    </div>

    <div class="content">
        <div class="highlight-box">
            <span class="label">Highlight Hari Ini</span>
            <span class="text">{html.escape(data.get('highlight', ''))}</span>
        </div>

        {sections_html}

        <div class="footer-note">
            Dihasilkan otomatis dari agregasi berita publik (Google News). Bukan rilis resmi Bank Indonesia / Departemen
            Regional. Untuk keperluan internal.
        </div>
    </div>
</body>
</html>"""


def build_pdf(data: dict, date_str: str, pdf_path: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    html_str = build_html(data, date_str)
    HTML(string=html_str, base_url=BASE_DIR).write_pdf(pdf_path)
    print(f"PDF dibuat: {pdf_path}")


# ---------------------------------------------------------------------------
# 4. Kirim ke WhatsApp lewat Twilio (dokumen PDF + caption)
# ---------------------------------------------------------------------------
def get_pdf_public_url(pdf_filename: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY tidak ada di environment - jalankan dari GitHub Actions.")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    # encode nama file (ada spasi, kurung siku, koma) supaya jadi URL valid
    encoded_filename = urllib.parse.quote(pdf_filename)
    return f"https://raw.githubusercontent.com/{repo}/{branch}/output/{encoded_filename}"


def send_whatsapp_pdf(caption: str, pdf_filename: str):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_WHATSAPP_FROM"]
    to_number = os.environ["TWILIO_WHATSAPP_TO"]

    media_url = get_pdf_public_url(pdf_filename)
    print(f"URL PDF publik: {media_url}")

    # raw.githubusercontent.com kadang butuh beberapa detik ter-update setelah push
    # (CDN lag). Pastikan PDF sudah bisa diakses (HTTP 200) sebelum Twilio fetch,
    # supaya kiriman tidak gagal karena media 404.
    for attempt in range(6):
        try:
            check = requests.head(media_url, timeout=15, allow_redirects=True)
            if check.status_code == 200:
                print(f"PDF siap diakses (percobaan {attempt + 1}).")
                break
            print(f"PDF belum siap (HTTP {check.status_code}), tunggu 10 detik...")
        except requests.RequestException as exc:
            print(f"Gagal cek URL PDF ({exc}), tunggu 10 detik...")
        time.sleep(10)
    else:
        print("Peringatan: PDF belum terkonfirmasi bisa diakses; tetap coba kirim.", file=sys.stderr)

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

    date_str = format_date_id(today_wib())  # mis. "Rabu, 22 Juli 2026" - dihitung
    # sendiri di kode (bukan ditebak Claude), supaya selalu benar & konsisten.

    caption_body = (data.get("caption") or "").strip()
    caption = f"📅 *{date_str}* — Selamat pagi!\n\n{caption_body}"
    print("--- CAPTION ---")
    print(caption)

    pdf_filename = f"Ringkasan Ekonomi [{date_str}].pdf"
    pdf_path = os.path.join(OUTPUT_DIR, pdf_filename)

    print("Membangun PDF...")
    build_pdf(data, date_str, pdf_path)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(CAPTION_PATH, "w", encoding="utf-8") as f:
        f.write(caption)
    with open(PDF_FILENAME_STATE_PATH, "w", encoding="utf-8") as f:
        f.write(pdf_filename)
    print(f"Caption disimpan: {CAPTION_PATH}")
    print(f"Nama file PDF disimpan: {PDF_FILENAME_STATE_PATH} -> {pdf_filename}")


def cmd_send():
    with open(CAPTION_PATH, "r", encoding="utf-8") as f:
        caption = f.read()
    with open(PDF_FILENAME_STATE_PATH, "r", encoding="utf-8") as f:
        pdf_filename = f.read().strip()
    print("Mengirim PDF ke WhatsApp...")
    send_whatsapp_pdf(caption, pdf_filename)
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
