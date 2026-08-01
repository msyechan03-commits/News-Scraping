"""
Pipeline Terintegrasi: RSS + Koran OCR → PDF → WhatsApp Business API
=====================================================================
Upgrade dari scrape_and_send.py — format output identik, ditambah
berita koran cetak (OCR) yang terintegrasi ke section existing.

Alur:
  1. Ambil berita RSS (identik dgn scrape_and_send.py)
  2. Download & OCR koran dari Click n Read (4 koran)
  3. Gabungkan semua ke Claude → JSON terstruktur
  4. Render PDF bermerek BI (format identik, koran terintegrasi)
  5. Kirim via WhatsApp Business Cloud API

Cara pakai:
  python pipeline.py generate          -> scrape + rangkum + build PDF
  python pipeline.py build-pdf         -> rebuild PDF dari data JSON
  python pipeline.py send              -> kirim PDF via WA Business API
  python pipeline.py generate-and-send -> jalankan keduanya sekaligus

Environment variables (GitHub Secrets / .env lokal):
  ANTHROPIC_API_KEY     - API key Anthropic
  CNR_USERNAME          - Username Click n Read
  CNR_PASSWORD          - Password Click n Read
  WA_PHONE_NUMBER_ID    - Phone Number ID WhatsApp Business
  WA_ACCESS_TOKEN       - Access Token WhatsApp Business
  WA_RECIPIENT          - Nomor penerima (format: 6281234567890)
"""

import base64
import datetime
import html
import json
import os
import re
import shutil
import sys
import time
import urllib.parse

import anthropic
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Import komponen koran (terpisah)
from koran_scraper import (
    NEWSPAPERS, create_session as cnr_login, download_pdf, extract_images,
    KORAN_DIR, IMAGES_DIR,
)
from koran_ocr import ocr_newspaper, _image_to_base64

# ---------------------------------------------------------------------------
# 1. RSS Feeds — COPY PERSIS dari existing scrape_and_send.py
# ---------------------------------------------------------------------------
REGIONS = ["Sumatera", "Jawa", "Kalimantan", "Balinusra", "Sulampua"]

_REGION_QUERY_TERMS = {
    "Sumatera": "Sumatera",
    "Jawa": "Jawa",
    "Kalimantan": "Kalimantan",
    "Balinusra": "Bali+OR+%22Nusa+Tenggara%22",
    "Sulampua": "Sulawesi+OR+Maluku+OR+Papua",
}

RSS_FEEDS = [
    ("https://news.google.com/rss/search?q=ekonomi+indonesia+when:1d&hl=id&gl=ID&ceid=ID:id", None),
    ("https://news.google.com/rss/search?q=bisnis+OR+market+OR+bursa+indonesia+when:1d&hl=id&gl=ID&ceid=ID:id", None),
] + [
    (f"https://news.google.com/rss/search?q=%28investasi+OR+ekonomi+OR+inflasi%29+{terms}+when:1d&hl=id&gl=ID&ceid=ID:id", region)
    for region, terms in _REGION_QUERY_TERMS.items()
]

HOURS_LOOKBACK = 24
PER_FEED_LIMIT = 25

NATIONAL_SOURCES = [
    "CNBC Indonesia", "Bisnis Indonesia", "Bisnis.com", "Kontan", "Bloomberg Technoz",
    "Bloomberg", "Katadata", "Databoks", "Investor Daily", "investor.id",
    "detikFinance", "CNN Indonesia", "Kompas.com", "Tempo.co", "Antara News",
    "Media Indonesia", "Republika", "Liputan6", "Warta Ekonomi", "Infobanknews",
    "IDX Channel", "Validnews", "Kumparan", "SWA.co.id", "Marketeers",
    "The Jakarta Post", "Jakarta Globe", "Reuters", "Wall Street Journal",
    "Financial Times", "The Economist",
]

REGIONAL_SOURCES = {
    "Sumatera": [
        "Serambi Indonesia", "Waspada", "Harian Analisa", "Riau Pos", "Sumatera Ekspres",
        "Padang Ekspres", "Lampung Post", "Tribun Pekanbaru", "Tribun Jambi", "Tribun Batam",
    ],
    "Jawa": [
        "Jawa Pos", "Pikiran Rakyat", "Suara Merdeka", "Solopos", "Radar Banten",
        "Surya", "Tribun Jabar", "Tribun Jateng", "Radar Solo", "Radar Surabaya",
    ],
    "Balinusra": [
        "Bali Post", "NusaBali", "Tribun Bali", "Lombok Post", "Suara NTB",
        "Pos Kupang", "Victory News", "Bali Bisnis",
    ],
    "Kalimantan": [
        "Kaltim Post", "Banjarmasin Post", "Pontianak Post", "Kalimantan Post",
        "Tribun Kaltim", "Prokal", "Radar Sampit", "Tribun Pontianak",
    ],
    "Sulampua": [
        "Fajar", "Tribun Timur", "Manado Post", "Kendari Pos", "Malut Post",
        "Cenderawasih Pos", "Jubi", "Tribun Manado", "Tribun Palu", "Tribun Papua",
        "Ambon Ekspres", "Fajar Mansinam",
    ],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _normalize_source(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


_NATIONAL_SOURCES_NORM = {_normalize_source(n) for n in NATIONAL_SOURCES}
_REGIONAL_SOURCES_NORM = {
    region: {_normalize_source(n) for n in names} for region, names in REGIONAL_SOURCES.items()
}


def _source_matches_any(norm_source: str, allowed_norms: set) -> bool:
    return any(norm_source == a or a in norm_source or norm_source in a for a in allowed_norms)


def _is_allowed_source(source_name: str, feed_region: str) -> bool:
    norm = _normalize_source(source_name)
    if not norm:
        return False
    if _source_matches_any(norm, _NATIONAL_SOURCES_NORM):
        return True
    if feed_region and _source_matches_any(norm, _REGIONAL_SOURCES_NORM.get(feed_region, set())):
        return True
    return False


DAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MONTHS_ID = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]


def format_date_id(dt: datetime.datetime) -> str:
    return f"{DAYS_ID[dt.weekday()]}, {dt.day} {MONTHS_ID[dt.month - 1]} {dt.year}"


def today_wib() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))


# ---------------------------------------------------------------------------
# Feedparser — import saat dibutuhkan (mungkin belum terinstall di sandbox)
# ---------------------------------------------------------------------------
def fetch_recent_entries():
    """COPY PERSIS dari existing — ambil berita RSS, filter whitelist."""
    import feedparser

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS_LOOKBACK)
    entries = []
    seen_titles = set()
    total_seen = 0
    total_filtered_out = 0

    for url, feed_region in RSS_FEEDS:
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

        for e in feed.entries[:PER_FEED_LIMIT]:
            pub = None
            if getattr(e, "published_parsed", None):
                pub = datetime.datetime(*e.published_parsed[:6], tzinfo=datetime.timezone.utc)

            if pub is not None and pub < cutoff:
                continue

            title = e.get("title", "").strip()
            if not title or title in seen_titles:
                continue

            source = e.get("source", {}) or {}
            source_name = (source.get("title") or "").strip()
            source_href = (source.get("href") or "").strip()

            total_seen += 1
            if not _is_allowed_source(source_name, feed_region):
                total_filtered_out += 1
                continue
            seen_titles.add(title)

            date_label = ""
            if pub is not None:
                pub_wib = pub.astimezone(datetime.timezone(datetime.timedelta(hours=7)))
                date_label = f"{pub_wib.day} {MONTHS_ID[pub_wib.month - 1][:3]}"

            entries.append({
                "title": title,
                "summary": e.get("summary", "").strip(),
                "link": e.get("link", "").strip(),
                "source_name": source_name,
                "source_href": source_href,
                "date_label": date_label,
            })

    print(f"  Filter sumber: {total_seen} entri diperiksa, {total_filtered_out} dibuang.")
    return entries


# ---------------------------------------------------------------------------
# 2. Koran: Download → Extract → OCR
# ---------------------------------------------------------------------------
def fetch_koran_articles(date_str: str) -> str:
    """Download koran, OCR, return teks gabungan semua koran.
    Hapus gambar temporary setelah selesai (hemat disk)."""

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("SKIP koran OCR: ANTHROPIC_API_KEY belum diisi")
        return ""

    cnr_user = os.environ.get("CNR_USERNAME", "")
    cnr_pass = os.environ.get("CNR_PASSWORD", "")
    if not cnr_user or not cnr_pass:
        print("SKIP koran: CNR_USERNAME/CNR_PASSWORD belum diisi")
        return ""

    # Login & download
    print("\n" + "=" * 50)
    print("TAHAP: Download koran dari Click n Read")
    print("=" * 50)
    session = cnr_login()

    all_ocr_texts = []
    client = anthropic.Anthropic(api_key=api_key)

    for key, info in NEWSPAPERS.items():
        pdf_path = download_pdf(session, key, date_str)
        if not pdf_path:
            continue

        # Extract images
        image_paths = extract_images(pdf_path)
        if not image_paths:
            continue

        # OCR
        print(f"\nOCR {info['name']} ({len(image_paths)} halaman)...")
        result = ocr_newspaper(client, key, image_paths, info["name"])

        if result["ocr_pages"]:
            koran_text = f"\n{'='*40}\nKORAN: {info['name']} — {date_str}\n{'='*40}\n"
            for page_key in sorted(result["ocr_pages"].keys()):
                koran_text += f"\n--- Halaman {page_key.replace('page_', '')} ---\n"
                koran_text += result["ocr_pages"][page_key] + "\n"
            all_ocr_texts.append(koran_text)

        # Cleanup: hapus gambar temporary (user request)
        for img_path in image_paths:
            try:
                os.remove(img_path)
            except OSError:
                pass

    # Cleanup: hapus folder gambar kosong & PDF
    if os.path.exists(IMAGES_DIR):
        shutil.rmtree(IMAGES_DIR, ignore_errors=True)
    # Hapus PDF koran (temporary, tidak perlu disimpan)
    for f in os.listdir(KORAN_DIR) if os.path.exists(KORAN_DIR) else []:
        if f.endswith(".pdf"):
            try:
                os.remove(os.path.join(KORAN_DIR, f))
            except OSError:
                pass

    return "\n".join(all_ocr_texts)


# ---------------------------------------------------------------------------
# 3. Claude summarization — sama persis + tambahan koran
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS = {
    "Fiskal": "APBD, APBN, realisasi belanja, belanja modal, belanja pegawai, belanja barang dan jasa, "
              "penyerapan anggaran, pagu anggaran, TKD, transfer ke daerah, DAU, DAK, DBH, dana desa, "
              "SILPA, PAD, pajak daerah, retribusi daerah, opsen pajak, DIPA, KPPN, APBD Perubahan, "
              "efisiensi anggaran, refocusing, defisit APBD, insentif fiskal, bansos, PKH, BLT, subsidi daerah",
    "Konsumsi RT": "daya beli, penjualan eceran, indeks penjualan riil, omzet pedagang, konsumsi masyarakat, "
                   "UMP, UMK, kenaikan upah, THR, gaji ke-13, penjualan kendaraan bermotor, penjualan motor, "
                   "kredit konsumsi, KPR, tabungan masyarakat, e-commerce, harbolnas, belanja Lebaran, "
                   "belanja Natal, mudik, libur sekolah, kelas menengah, PHK, Indeks Keyakinan Konsumen (IKK), "
                   "konsumsi listrik industri, konsumsi BBM industri, konsumsi semen",
    "Investasi": "PMA, PMDN, realisasi investasi, penanaman modal, BKPM, Kementerian Investasi, groundbreaking, "
                 "peletakan batu pertama, ekspansi pabrik, penambahan kapasitas, capex, belanja modal perusahaan, "
                 "KEK, kawasan industri, OSS, perizinan berusaha, PSN, proyek strategis nasional, IKN, "
                 "Danantara, hilirisasi, komitmen investasi, MoU investasi",
    "Ekspor": "ekspor, nilai ekspor, volume ekspor, neraca perdagangan, pengapalan, bea keluar, DMO, "
              "larangan ekspor, negara tujuan ekspor, kontainer, TEUs, bea cukai, FOB, harga komoditas global, "
              "permintaan Tiongkok, permintaan global, tarif impor AS, safeguard",
    "Pertanian": "panen raya, luas tanam, luas panen, produktivitas, GKP, GKG, gabah, padi, jagung, pupuk "
                 "subsidi, alsintan, El Nino, La Nina, kekeringan, banjir, sawah, OPT, hama, TBS, harga TBS, "
                 "replanting, PSR, kakao, cengkeh, perikanan tangkap, budidaya udang, tambak, musim tanam, "
                 "Bulog, serapan gabah, HPP gabah, food estate, cuaca ekstrem",
    "Perdagangan": "perdagangan besar dan eceran, distribusi barang, pasar tradisional, grosir, distributor, "
                   "arus barang, bongkar muat, stok barang, ritel modern, pusat perbelanjaan, wholesale",
    "Pertambangan": "RKAB, IUP, IUPK, produksi batu bara, HBA, harga batubara acuan, lifting minyak, lifting "
                    "gas, SKK Migas, bijih nikel, ore nikel, bauksit, timah, emas, tembaga, konsentrat",
    "Konstruksi": "proyek infrastruktur, jalan tol, bendungan, jembatan, pembangunan bandara, pembangunan "
                  "pelabuhan, progres fisik, kontraktor BUMN karya, tender proyek, konsumsi semen, properti",
    "Industri Pengolahan": "pabrik, utilisasi kapasitas, PMI manufaktur, smelter, feronikel, NPI, katoda "
                            "tembaga, MHP, alumina, refinery, oleochemical, kilang, petrokimia",
    "Akmamin": "tingkat penghunian kamar, TPK, okupansi hotel, PHRI, wisatawan mancanegara, wisman, wisnus, "
               "kunjungan wisatawan, MICE, restoran, kafe, kuliner, homestay",
    "Inflasi Inti": "inflasi inti, core inflation, emas perhiasan, sewa rumah, kontrak rumah, upah ART, "
                    "biaya pendidikan, SPP, tarif kesehatan, ekspektasi inflasi, imported inflation",
    "Inflasi VF": "harga beras, cabai merah, cabai rawit, bawang merah, bawang putih, daging ayam ras, telur "
                  "ayam ras, minyak goreng, MinyaKita, ikan segar, tomat, daging sapi, pasokan pangan",
    "Inflasi AP": "BBM, Pertalite, Pertamax, Solar, LPG 3 kg, tarif listrik, TDL, tarif angkutan udara, tiket "
                  "pesawat, tarif kereta, angkutan dalam kota, rokok, cukai hasil tembakau, air PDAM",
}

_ITEM_PROPS = {
    "date": {"type": "string", "description": "Tanggal terbit berita, disalin PERSIS dari field Tanggal pada data sumber (mis. '23 Jul') - jangan dihitung/ditebak sendiri, kosongkan kalau field Tanggal kosong."},
    "province": {"type": "string", "description": "Provinsi spesifik yang disebut di berita (mis. 'Sumatera Barat', 'Jawa Barat'). Kosongkan kalau berita tidak menyebut provinsi spesifik."},
    "title": {"type": "string"},
    "body": {"type": "string", "description": "1-3 kalimat. Kuantitatif (angka/persentase) kalau tersedia di berita, kualitatif/naratif kalau tidak."},
    "source_id": {"type": "integer", "description": "Untuk berita RSS: salin PERSIS angka ID dari field 'ID' pada data. Untuk berita KORAN CETAK (OCR): WAJIB isi -1."},
    "source_name": {"type": "string", "description": "Untuk RSS: nama media dari field Sumber. Untuk KORAN CETAK: format 'NAMA KORAN, HAL X (CETAK)' mis. 'BISNIS INDONESIA, HAL 9 (CETAK)'."},
}
_ITEM_REQUIRED = ["date", "province", "title", "body", "source_id", "source_name"]

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {
            "type": "string",
            "description": "Caption singkat gaya WhatsApp (5-8 poin lintas semua section, ±300-400 kata, *bold* untuk judul, tanpa markdown heading).",
        },
        "report_title": {"type": "string"},
        "global_summary": {"type": "string"},
        "national_summary": {"type": "string"},
        "global_national": {
            "type": "array",
            "description": "Section 1: Perkembangan Ekonomi Global dan Nasional. Maks 10 item, minimal 3 dari koran cetak.",
            "items": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["Global", "Nasional"]},
                    **_ITEM_PROPS,
                },
                "required": ["scope"] + _ITEM_REQUIRED,
                "additionalProperties": False,
            },
        },
        "regions": {
            "type": "array",
            "description": "Section 2 & 3, dipecah per wilayah kerja.",
            "items": {
                "type": "object",
                "properties": {
                    "region_name": {"type": "string", "enum": REGIONS},
                    "region_summary": {"type": "string"},
                    "demand": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "category": {"type": "string", "enum": ["Fiskal", "Konsumsi RT", "Investasi", "Ekspor"]},
                                **_ITEM_PROPS,
                            },
                            "required": ["category"] + _ITEM_REQUIRED,
                            "additionalProperties": False,
                        },
                    },
                    "sectors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "category": {
                                    "type": "string",
                                    "enum": ["Pertanian", "Perdagangan", "Pertambangan", "Konstruksi", "Industri Pengolahan", "Akmamin"],
                                },
                                **_ITEM_PROPS,
                            },
                            "required": ["category"] + _ITEM_REQUIRED,
                            "additionalProperties": False,
                        },
                    },
                    "inflation": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "component": {"type": "string", "enum": ["Inflasi Inti", "Inflasi VF", "Inflasi AP"]},
                                **_ITEM_PROPS,
                            },
                            "required": ["component"] + _ITEM_REQUIRED,
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["region_name", "region_summary", "demand", "sectors", "inflation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["caption", "report_title", "global_summary", "national_summary",
                  "global_national", "regions"],
    "additionalProperties": False,
}

DEFAULT_TITLE = "Rangkuman Berita Ekonomi Harian"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
CAPTION_PATH = os.path.join(OUTPUT_DIR, "caption.txt")
PDF_FILENAME_STATE_PATH = os.path.join(OUTPUT_DIR, "pdf_filename.txt")

# Logo BI — di folder assets/ (sejajar dengan pipeline.py di root repo)
BI_LOGO_PATH = os.path.join(SCRIPT_DIR, "assets", "bi_logo.png")
DR_LOGO_PATH = os.path.join(SCRIPT_DIR, "assets", "dr_logo.png")


def _format_category_keywords() -> str:
    return "\n".join(f"- {cat}: {kw}" for cat, kw in CATEGORY_KEYWORDS.items())


def _extract_scalar(raw: str, key: str) -> str:
    m = re.search(rf'"{key}"\s*:\s*("(?:[^"\\]|\\.)*")', raw)
    if not m:
        return ""
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return ""


def _normalize_report(data: dict) -> dict:
    return {
        "caption": data.get("caption") or "Rangkuman berita ekonomi hari ini.",
        "report_title": data.get("report_title") or DEFAULT_TITLE,
        "global_summary": data.get("global_summary") or "",
        "national_summary": data.get("national_summary") or "",
        "global_national": data.get("global_national") if isinstance(data.get("global_national"), list) else [],
        "regions": data.get("regions") if isinstance(data.get("regions"), list) else [],
    }


def _resolve_source_ids(data: dict, entries: list) -> None:
    def resolve_item(item: dict) -> None:
        source_id = item.pop("source_id", None)
        url = ""
        if isinstance(source_id, int) and 0 <= source_id < len(entries):
            url = entries[source_id].get("link", "")
        item["source_url"] = url

    for item in data.get("global_national", []):
        resolve_item(item)
    for region in data.get("regions", []):
        for key in ("demand", "sectors", "inflation"):
            for item in region.get(key, []):
                resolve_item(item)


def summarize_with_claude(entries: list, koran_text: str) -> dict:
    if not entries and not koran_text:
        return {
            "caption": "Tidak ada berita ekonomi baru yang terdeteksi pagi ini.",
            "report_title": DEFAULT_TITLE,
            "global_summary": "",
            "national_summary": "",
            "global_national": [],
            "regions": [],
        }

    entries = entries[:150]
    raw_text = "\n\n".join(
        f"ID: {idx}\nJudul: {it['title']}\nTanggal: {it['date_label'] or '(tidak diketahui)'}"
        f"\nSumber: {it['source_name'] or '(tidak diketahui)'}"
        for idx, it in enumerate(entries)
    )

    # Tambahkan teks koran kalau ada
    koran_section = ""
    if koran_text:
        # Truncate koran text kalau terlalu panjang (hemat token)
        max_koran_chars = 50000
        if len(koran_text) > max_koran_chars:
            koran_text = koran_text[:max_koran_chars] + "\n\n[... teks koran dipotong karena terlalu panjang ...]"
        koran_section = f"""

=== BERITA KORAN CETAK (sumber tambahan — hasil OCR dari koran cetak hari ini) ===
{koran_text}
=== AKHIR BERITA KORAN CETAK ===
"""

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Berikut kumpulan berita ekonomi Indonesia & global dari beberapa media hari ini:

{raw_text}
{koran_section}

Susun laporan ekonomi dengan struktur TETAP berikut (kerangka standar laporan regional Bank
Indonesia):

SECTION 1 - "global_national": Perkembangan Ekonomi Global dan Nasional
  - "Global": pertumbuhan ekonomi global/negara maju, kebijakan moneter bank sentral utama
    (The Fed dll), harga komoditas global, risiko geopolitik berdampak ekonomi.
  - "Nasional": pertumbuhan PDB nasional, inflasi nasional, nilai tukar rupiah, kebijakan
    moneter BI, neraca perdagangan, kebijakan fiskal pusat.

  KHUSUS "Global": WAJIB muat angka/data KUANTITATIF spesifik kalau tersedia di berita.

SECTION 2 & 3 - "regions": Perkembangan Ekonomi & Inflasi per Wilayah Kerja
  Wilayah: {", ".join(REGIONS)}.
  - "demand" (Sisi Permintaan): Fiskal, Konsumsi RT, Investasi, Ekspor.
  - "sectors" (Sisi Penawaran): Pertanian, Perdagangan, Pertambangan, Konstruksi, Industri Pengolahan, Akmamin.
  - "inflation" (Inflasi Wilayah): Inflasi Inti, Inflasi VF, Inflasi AP.
  Maks 2 item PER KATEGORI per wilayah.

KAMUS KEYWORD PER KATEGORI:
{_format_category_keywords()}

SUMBER DATA:
  - Berita RSS (punya ID numerik) → gunakan di section global_national & regions
  - Berita Koran Cetak (hasil OCR, tanpa ID) → WAJIB dimasukkan ke section global_national
    & regions yang sesuai (set source_id = -1, source_name = "NAMA KORAN, HAL X (CETAK)"
    — mis. "BISNIS INDONESIA, HAL 9 (CETAK)" atau "KONTAN, HAL 1 (CETAK)").
    TIDAK ADA section koran terpisah — semua berita koran HARUS masuk ke section yang ada.

⚠️ WAJIB: MINIMAL 3-5 item dari KORAN CETAK harus masuk ke output JSON (global_national
dan/atau regions). Koran cetak = data bernilai tinggi dari OCR. Jika kamu tidak memasukkan
item koran cetak dengan source_id=-1 dan source_name berisi "(CETAK)", output dianggap GAGAL.
Baca bagian "BERITA KORAN CETAK" di bawah dengan seksama dan ekstrak berita ekonomi darinya.

ATURAN PENTING:
- TIDAK WAJIB semua 5 wilayah terisi. Hanya yang benar-benar punya berita.
- Sub-bagian kosong → array kosong, jangan dikarang.
- "region_summary": NETRAL, tanpa kata penilaian/tone.
- "global_summary" & "national_summary": NETRAL, rekap fakta.

Buatkan output berikut:
1. "global_national" - Maks 10 item total (MINIMAL 3 item harus dari koran cetak dgn source_id=-1).
2. "global_summary" dan "national_summary"
3. "regions" - per wilayah, termasuk "region_summary"
4. "caption" - versi singkat gaya WhatsApp dengan NUMBERED LIST:
   JANGAN tulis baris tanggal/sapaan di awal (sistem tambahkan otomatis).
   Format: numbered list 5-8 poin berita terpenting lintas semua section.
   Tiap poin: nomor + judul singkat (*bold* pakai asterisk) + 1-2 kalimat inti.
   PENTING: Setiap poin HARUS dipisah baris baru (\n). JANGAN gabung semua poin dalam 1 baris.
   Contoh format (perhatikan setiap poin di baris terpisah):
   1. *Gubernur BI Mundur* — Perry Warjiyo mengundurkan diri, Destry ditunjuk sebagai pejabat sementara.\n2. *Harga Minyak Melandai* — Harga minyak turun, mendorong bursa Asia menguat.
   Kalau ada berita dari koran cetak, sertakan dengan label 📰.
   Total ±300-400 kata, bahasa Indonesia, ringkas, tanpa markdown heading (#).
5. "report_title" (mis. 'Rangkuman Ekonomi Harian')."""

    with client.with_options(max_retries=6).messages.stream(
        model="claude-sonnet-5",
        max_tokens=24000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": REPORT_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        resp = stream.get_final_message()

    print(
        f"  stop_reason={resp.stop_reason}, input_tokens={resp.usage.input_tokens}, "
        f"output_tokens={resp.usage.output_tokens}"
    )

    text_blocks = [block.text for block in resp.content if block.type == "text"]
    raw_json = "\n".join(text_blocks).strip()

    try:
        data = _normalize_report(json.loads(raw_json))
        _resolve_source_ids(data, entries)
        return data
    except json.JSONDecodeError:
        print(f"  JSON tidak lengkap — pakai fallback caption-only.", file=sys.stderr)
        return _normalize_report({
            "caption": _extract_scalar(raw_json, "caption"),
            "report_title": _extract_scalar(raw_json, "report_title"),
        })


# ---------------------------------------------------------------------------
# 4. Render PDF — IDENTIK dengan existing (koran terintegrasi)
# ---------------------------------------------------------------------------
def _b64_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _source_label(item: dict) -> str:
    name = (item.get("source_name") or "").strip()
    if name:
        return name
    url = (item.get("source_url") or "").strip()
    if not url:
        return ""
    domain = urllib.parse.urlparse(url).netloc
    return domain[4:] if domain.startswith("www.") else domain


def _render_item(item: dict, category_label: str = "") -> str:
    source_url = html.escape(item.get("source_url", ""))
    source_label = html.escape(_source_label(item))
    if source_url and source_label:
        source_line = f'<a class="item-source" href="{source_url}">&#8599;&nbsp;{source_label}</a>'
    elif source_label:
        # Koran cetak: tampilkan nama sumber tanpa link (tidak ada URL)
        source_line = f'<span class="item-source">{source_label}</span>'
    else:
        source_line = ""
    category_html = (
        f'<div class="item-category">{html.escape(category_label)}</div>' if category_label else ""
    )
    date = html.escape((item.get("date") or "").strip())
    province = html.escape((item.get("province") or "").strip())
    meta_parts = [p for p in (date, province) if p]
    meta_html = f'<div class="item-meta">{" &middot; ".join(meta_parts)}</div>' if meta_parts else ""
    return f"""
    <div class="item">
        {category_html}
        {meta_html}
        <div class="item-title">{html.escape(item.get('title', ''))}</div>
        <div class="item-body">{html.escape(item.get('body', ''))}</div>
        {source_line}
    </div>
    """


def _render_subgroup(label: str, items: list, category_key: str) -> str:
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


def _build_highlight_page_html(data: dict) -> str:
    regions = data.get("regions", [])
    by_name = {r.get("region_name"): r for r in regions}

    blocks = [
        ("Global", (data.get("global_summary") or "").strip()),
        ("Nasional", (data.get("national_summary") or "").strip()),
    ]
    for region_name in REGIONS:
        region = by_name.get(region_name)
        summary = (region.get("region_summary") or "").strip() if region else ""
        blocks.append((region_name, summary))

    blocks_html = "".join(
        f"""
        <div class="exec-block">
            <div class="exec-subtitle">{html.escape(subtitle)}</div>
            <p class="exec-text">{html.escape(text) if text else "Tidak ada perkembangan signifikan yang tercatat hari ini."}</p>
        </div>
        """
        for subtitle, text in blocks
    )

    return f"""
    <div class="content-page">
        <div class="section-head">
            <span class="section-title">Executive Summary</span>
        </div>
        {blocks_html}
    </div>
    """


def _build_global_national_page_html(global_national: list) -> str:
    global_items = [i for i in global_national if i.get("scope") == "Global"]
    national_items = [i for i in global_national if i.get("scope") == "Nasional"]
    body = _render_subgroup("Global", global_items, "") + _render_subgroup("Nasional", national_items, "")
    if not body:
        body = _render_empty_state("Tidak ada perkembangan ekonomi global/nasional signifikan yang tercatat hari ini.")
    return f"""
    <div class="content-page">
        <div class="section-head">
            <span class="section-title">Perkembangan Ekonomi Global dan Nasional</span>
        </div>
        {body}
    </div>
    """


def _build_region_page_html(region_name: str, region: dict) -> str:
    region = region or {}
    demand = region.get("demand", [])
    sectors = region.get("sectors", [])
    inflation = region.get("inflation", [])
    region_summary = (region.get("region_summary") or "").strip()

    demand_html = "".join(_render_item(it, it.get("category", "")) for it in demand) or _render_empty_state(
        "Tidak ada perkembangan sisi permintaan yang tercatat untuk wilayah ini hari ini."
    )
    sectors_html = "".join(_render_item(it, it.get("category", "")) for it in sectors) or _render_empty_state(
        "Tidak ada perkembangan sisi penawaran/lapangan usaha yang tercatat untuk wilayah ini hari ini."
    )
    inflation_html = "".join(_render_item(it, it.get("component", "")) for it in inflation) or _render_empty_state(
        "Belum ditemukan berita/data yang memuat angka atau perkembangan inflasi spesifik untuk "
        "wilayah ini dalam periode laporan."
    )

    summary_html = (
        f"""
        <div class="direction-summary">
            <span class="direction-label">Ringkasan Wilayah</span>
            {html.escape(region_summary)}
        </div>
        """
        if region_summary else ""
    )

    return f"""
    <div class="content-page region-page">
        <div class="section-head">
            <span class="section-title">{html.escape(region_name)}</span>
        </div>
        {summary_html}
        <div class="region-cols">
            <div class="region-col">
                <div class="subgroup-label">Sisi Permintaan</div>
                {demand_html}
            </div>
            <div class="region-col">
                <div class="subgroup-label">Sisi Penawaran (Lapangan Usaha)</div>
                {sectors_html}
            </div>
        </div>
        <div class="subgroup-label">Inflasi Wilayah</div>
        {inflation_html}
    </div>
    """


def build_html(data: dict, date_str: str) -> str:
    bi_logo_b64 = _b64_image(BI_LOGO_PATH)
    dr_logo_b64 = _b64_image(DR_LOGO_PATH)

    regions = data.get("regions", [])
    by_region_name = {r.get("region_name"): r for r in regions}

    pages_html = (
        _build_highlight_page_html(data)
        + _build_global_national_page_html(data.get("global_national", []))
        + "".join(
            _build_region_page_html(region_name, by_region_name.get(region_name))
            for region_name in REGIONS
        )
    )

    # CSS identik dengan existing (copy persis)
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
        @bottom-center {{
            content: "\\2014 " counter(page) " \\2014";
            font-family: Georgia, 'Times New Roman', 'Liberation Serif', serif;
            font-size: 8.5pt;
            letter-spacing: 0.06em;
            color: #8a9bb5;
        }}
        @bottom-left {{
            content: "Laporan Internal";
            font-family: Arial, sans-serif;
            font-size: 7pt;
            letter-spacing: 0.04em;
            color: #b9863f;
        }}
        @bottom-right {{
            content: "Departemen Regional \\2014 BI";
            font-family: Arial, sans-serif;
            font-size: 7pt;
            letter-spacing: 0.04em;
            color: #b9863f;
        }}
    }}
    @page :first {{
        margin: 0;
        @top-left {{ content: ""; }}
        @top-right {{ content: ""; }}
        @bottom-right {{ content: ""; }}
        @bottom-left {{ content: ""; }}
        @bottom-center {{ content: ""; }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
        font-family: Georgia, 'Times New Roman', 'Liberation Serif', serif;
        color: #1b2536;
        font-size: 10.5pt;
        line-height: 1.55;
        margin: 0;
    }}

    /* ============ COVER ============ */
    .cover {{
        position: relative;
        width: 21cm;
        height: 29.7cm;
        background: #0a2342;
        color: #ffffff;
        page-break-after: always;
        overflow: hidden;
    }}
    .cover-accent {{
        position: absolute;
        top: 0; left: 0; bottom: 0;
        width: 0.5cm;
        background: #c9a24b;
    }}
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

    /* ============ KONTEN ============ */
    .content {{ padding-top: 2px; }}
    .exec-block {{
        margin-bottom: 11px;
        page-break-inside: avoid;
    }}
    .exec-subtitle {{
        font-family: Georgia, 'Times New Roman', 'Liberation Serif', serif;
        font-style: italic;
        font-weight: 700;
        font-size: 11.5pt;
        color: #0a2342;
        margin-bottom: 4px;
        padding-bottom: 2px;
        border-bottom: 1px solid #d9e2f0;
    }}
    .exec-text {{
        font-size: 9.4pt;
        color: #3c4a63;
        line-height: 1.42;
        text-align: justify;
        text-justify: inter-word;
        margin: 0;
    }}
    .section-head {{
        margin-bottom: 18px;
        padding-bottom: 10px;
        text-align: center;
        border-bottom: 1.5px solid #0a2342;
        position: relative;
    }}
    .section-head::before {{
        content: "";
        display: block;
        width: 100%;
        border-top: 1px solid #0a2342;
        margin-bottom: 3px;
    }}
    .section-title {{
        display: block;
        font-family: Georgia, 'Times New Roman', 'Liberation Serif', serif;
        font-style: italic;
        font-variant: small-caps;
        font-size: 15pt;
        font-weight: 700;
        color: #0a2342;
        letter-spacing: 0.03em;
    }}
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
        margin-bottom: 12px;
        padding-left: 14px;
        border-left: 3px solid #d9e2f0;
        page-break-inside: avoid;
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
    .item-meta {{
        font-size: 8pt;
        color: #94a1b8;
        margin-bottom: 3px;
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
    .direction-summary {{
        margin-top: 6px;
        padding: 8px 12px;
        background: #eef3fc;
        border-left: 3px solid #c9a24b;
        border-radius: 4px;
        font-size: 9.5pt;
        color: #33415c;
        text-align: justify;
        page-break-inside: avoid;
    }}
    .direction-label {{
        display: block;
        font-weight: 800;
        font-size: 7.5pt;
        color: #0a2342;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 4px;
    }}
    .content-page {{
        page-break-before: always;
        padding-top: 2px;
    }}
    .region-cols {{ display: flex; gap: 28px; margin-bottom: 2px; }}
    .region-cols .region-col {{ flex: 1; min-width: 0; }}
    .region-cols .region-col:first-child {{ padding-right: 6px; border-right: 1px solid #e6ebf3; }}
    .region-cols .region-col:last-child {{ padding-left: 6px; }}
    .region-page .item {{ margin-bottom: 9px; }}
    .region-page .item-title {{ font-size: 10.2pt; margin-bottom: 2px; }}
    .region-page .item-body {{ font-size: 9.2pt; line-height: 1.42; margin-bottom: 3px; }}
    .region-page .item-meta {{ font-size: 7.5pt; margin-bottom: 2px; }}
    .region-page .subgroup-label {{ margin-bottom: 5px; }}
    .region-page .direction-summary {{ font-size: 9pt; padding: 7px 11px; margin-top: 0; margin-bottom: 14px; }}
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
        <div class="cover-tagline">Rangkuman perkembangan ekonomi terkini, disusun otomatis oleh AI berbasis large language model (LLM) dari agregasi media nasional &amp; regional.</div>
        <div class="cover-foot">
            <div class="date">{html.escape(date_str)}</div>
            <div class="sub">Bank Indonesia &nbsp;&middot;&nbsp; Laporan Internal</div>
        </div>
    </div>

    {pages_html}
</body>
</html>"""


def build_pdf(data: dict, date_str: str, pdf_path: str):
    from weasyprint import HTML as WeasyprintHTML

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    html_str = build_html(data, date_str)
    WeasyprintHTML(string=html_str, base_url=SCRIPT_DIR).write_pdf(pdf_path)
    print(f"PDF dibuat: {pdf_path}")


# ---------------------------------------------------------------------------
# 5. Kirim via WhatsApp Business Cloud API (bukan Twilio)
# ---------------------------------------------------------------------------
def _build_short_caption(caption: str) -> str:
    """Potong caption agar muat di body template (max 800 karakter).
    Ambil baris-baris bernomor, potong jika melebihi batas."""
    # Hapus header "📅 *Selasa, ...* — Selamat pagi!" kalau ada
    lines = caption.split("\n")
    content_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip header line dan baris kosong di awal
        if stripped.startswith("📅") or (not stripped and not content_lines):
            continue
        content_lines.append(stripped)

    short = "\n".join(content_lines).strip()
    if len(short) <= 800:
        return short
    # Potong per baris agar tidak terpotong di tengah kalimat
    result = ""
    for line in content_lines:
        if len(result) + len(line) + 1 > 780:
            break
        result += line + "\n"
    return result.strip() + "\n..."


def send_whatsapp(caption: str, pdf_path: str):
    """Kirim PDF + ringkasan via 1 template message (document header + body variables).
    Tidak perlu service window / user kirim 'hi' dulu."""
    phone_number_id = os.environ.get("WA_PHONE_NUMBER_ID", "")
    access_token = os.environ.get("WA_ACCESS_TOKEN", "")
    recipient = os.environ.get("WA_RECIPIENT", "")

    if not all([phone_number_id, access_token, recipient]):
        print("ERROR: WA_PHONE_NUMBER_ID, WA_ACCESS_TOKEN, atau WA_RECIPIENT belum diisi di .env")
        sys.exit(1)

    base_url = f"https://graph.facebook.com/v25.0/{phone_number_id}"
    headers_auth = {"Authorization": f"Bearer {access_token}"}
    headers_json = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    # Step 1: Upload PDF ke Media API
    print("Mengupload PDF...")
    with open(pdf_path, "rb") as f:
        resp = requests.post(
            f"{base_url}/media",
            headers=headers_auth,
            files={"file": (os.path.basename(pdf_path), f, "application/pdf")},
            data={"messaging_product": "whatsapp"},
        )
    print(f"  Response: {resp.status_code} {resp.text}")
    if resp.status_code >= 400:
        print(f"GAGAL upload PDF: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    media_id = resp.json()["id"]
    print(f"PDF uploaded, media_id: {media_id}")

    # Step 2: Kirim template 'news_report' dengan document header + body variables
    wib = datetime.timezone(datetime.timedelta(hours=7))
    tanggal_str = datetime.datetime.now(wib).strftime("%d %B %Y")
    short_caption = _build_short_caption(caption)

    print(f"Mengirim template 'news_report' (tanggal: {tanggal_str})...")
    print(f"  Ringkasan ({len(short_caption)} chars):\n{short_caption[:200]}...")

    template_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "template",
        "template": {
            "name": "news_report",
            "language": {"code": "id"},
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "document",
                            "document": {
                                "id": media_id,
                                "filename": os.path.basename(pdf_path),
                            },
                        }
                    ],
                },
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": tanggal_str},
                        {"type": "text", "text": short_caption},
                    ],
                },
            ],
        },
    }
    resp = requests.post(f"{base_url}/messages", headers=headers_json, json=template_payload)
    print(f"  Response: {resp.status_code} {resp.text}")
    if resp.status_code >= 400:
        print(f"GAGAL kirim template: {resp.status_code} {resp.text}", file=sys.stderr)
        # Fallback: coba template lama news_update (tanpa PDF)
        print("Fallback: mencoba template 'news_update'...")
        fallback_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "template",
            "template": {
                "name": "news_update",
                "language": {"code": "id"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": tanggal_str}
                        ],
                    }
                ],
            },
        }
        resp2 = requests.post(f"{base_url}/messages", headers=headers_json, json=fallback_payload)
        print(f"  Fallback response: {resp2.status_code} {resp2.text}")
        if resp2.status_code >= 400:
            print("GAGAL: Semua template gagal.", file=sys.stderr)
            sys.exit(1)
    else:
        print("Template 'news_report' berhasil dikirim (PDF + ringkasan)!")


# ---------------------------------------------------------------------------
# 6. Entry points
# ---------------------------------------------------------------------------
def cmd_generate():
    date_now = today_wib()
    date_str = format_date_id(date_now)
    date_iso = date_now.strftime("%Y-%m-%d")

    # Step 1: RSS
    print("\n" + "=" * 50)
    print("TAHAP: Mengambil berita RSS")
    print("=" * 50)
    entries = fetch_recent_entries()
    print(f"Ditemukan {len(entries)} berita RSS dalam {HOURS_LOOKBACK} jam terakhir.")

    # Step 2: Koran OCR
    koran_text = fetch_koran_articles(date_iso)
    if koran_text:
        print(f"\nTeks koran: {len(koran_text)} karakter dari OCR")
    else:
        print("\nTidak ada teks koran (skip OCR)")

    # Step 3: Rangkum dengan Claude
    print("\n" + "=" * 50)
    print("TAHAP: Merangkum dengan Claude")
    print("=" * 50)
    data = summarize_with_claude(entries, koran_text)

    # Step 4: SIMPAN DATA DULU sebelum build PDF (supaya tidak hilang kalau PDF gagal)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    caption_body = (data.get("caption") or "").strip()
    caption = f"📅 *{date_str}* — Selamat pagi!\n\n{caption_body}"
    print("--- CAPTION ---")
    print(caption)

    pdf_filename = f"Ringkasan Ekonomi [{date_str}].pdf"

    with open(CAPTION_PATH, "w", encoding="utf-8") as f:
        f.write(caption)
    with open(PDF_FILENAME_STATE_PATH, "w", encoding="utf-8") as f:
        f.write(pdf_filename)

    # Simpan data JSON (supaya bisa rebuild PDF tanpa generate ulang)
    data_path = os.path.join(OUTPUT_DIR, "report_data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Data JSON disimpan: {data_path}")
    print(f"Caption disimpan: {CAPTION_PATH}")

    # Step 5: Build PDF
    pdf_path = os.path.join(OUTPUT_DIR, pdf_filename)
    print("\n" + "=" * 50)
    print("TAHAP: Membangun PDF")
    print("=" * 50)
    build_pdf(data, date_str, pdf_path)


def cmd_send():
    with open(CAPTION_PATH, "r", encoding="utf-8") as f:
        caption = f.read()
    with open(PDF_FILENAME_STATE_PATH, "r", encoding="utf-8") as f:
        pdf_filename = f.read().strip()
    pdf_path = os.path.join(OUTPUT_DIR, pdf_filename)

    if not os.path.exists(pdf_path):
        print(f"ERROR: PDF tidak ditemukan: {pdf_path}")
        sys.exit(1)

    print("Mengirim ke WhatsApp Business API...")
    send_whatsapp(caption, pdf_path)
    print("Selesai!")


def cmd_build_pdf():
    """Rebuild PDF dari data JSON yang sudah tersimpan (tanpa generate ulang)."""
    data_path = os.path.join(OUTPUT_DIR, "report_data.json")
    if not os.path.exists(data_path):
        print(f"ERROR: Data tidak ditemukan: {data_path}")
        print("Jalankan 'generate' dulu.")
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    with open(PDF_FILENAME_STATE_PATH, "r", encoding="utf-8") as f:
        pdf_filename = f.read().strip()

    date_str = format_date_id(today_wib())
    pdf_path = os.path.join(OUTPUT_DIR, pdf_filename)
    build_pdf(data, date_str, pdf_path)
    print("PDF berhasil dibuat! Jalankan 'send' untuk kirim ke WhatsApp.")


def cmd_generate_and_send():
    cmd_generate()
    cmd_send()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "generate"

    commands = {
        "generate": cmd_generate,
        "build-pdf": cmd_build_pdf,
        "send": cmd_send,
        "generate-and-send": cmd_generate_and_send,
    }

    if mode not in commands:
        print("Pipeline Terintegrasi: RSS + Koran → PDF → WhatsApp Business")
        print("=" * 55)
        for cmd in commands:
            print(f"  python pipeline.py {cmd}")
        sys.exit(0)

    commands[mode]()


if __name__ == "__main__":
    main()
