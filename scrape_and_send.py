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

# Setiap feed di-tag dengan wilayahnya (None = feed nasional/umum) - dipakai utk
# menentukan whitelist mana yang berlaku (nasional vs regional per wilayah) saat
# memfilter sumber di fetch_recent_entries().
RSS_FEEDS = [
    ("https://news.google.com/rss/search?q=ekonomi+indonesia+when:1d&hl=id&gl=ID&ceid=ID:id", None),
    ("https://news.google.com/rss/search?q=bisnis+OR+market+OR+bursa+indonesia+when:1d&hl=id&gl=ID&ceid=ID:id", None),
] + [
    (f"https://news.google.com/rss/search?q=%28investasi+OR+ekonomi+OR+inflasi%29+{terms}+when:1d&hl=id&gl=ID&ceid=ID:id", region)
    for region, terms in _REGION_QUERY_TERMS.items()
]

HOURS_LOOKBACK = 24  # ambil berita dari X jam terakhir
PER_FEED_LIMIT = 25  # maks entri diambil DARI TIAP feed (bukan total) - lihat catatan di fetch_recent_entries()

# ---------------------------------------------------------------------------
# 1c. Whitelist sumber media - disepakati dengan user (30 media nasional +
# 48 media regional per wilayah dari daftar terlampir). HANYA berita dari
# media di daftar ini yang diteruskan ke Claude - selain itu dibuang di
# fetch_recent_entries(). Nasional berlaku lintas semua feed (nasional maupun
# wilayah); regional HANYA berlaku utk feed wilayah yang bersangkutan (media
# Bali tidak otomatis lolos di feed Sumatera, dst).
# ---------------------------------------------------------------------------
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


def _normalize_source(name: str) -> str:
    """Lowercase + buang semua non-alfanumerik, supaya 'Bali Post' cocok dgn
    'BALIPOST.com', 'ANTARA News Bali' cocok dgn 'Antara News', dst."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


_NATIONAL_SOURCES_NORM = {_normalize_source(n) for n in NATIONAL_SOURCES}
_REGIONAL_SOURCES_NORM = {
    region: {_normalize_source(n) for n in names} for region, names in REGIONAL_SOURCES.items()
}


def _source_matches_any(norm_source: str, allowed_norms: set) -> bool:
    return any(norm_source == a or a in norm_source or norm_source in a for a in allowed_norms)


def _is_allowed_source(source_name: str, feed_region: str) -> bool:
    """Nasional selalu boleh (lintas feed manapun); regional hanya boleh kalau
    beritanya datang dari feed wilayah yang sesuai."""
    norm = _normalize_source(source_name)
    if not norm:
        return False
    if _source_matches_any(norm, _NATIONAL_SOURCES_NORM):
        return True
    if feed_region and _source_matches_any(norm, _REGIONAL_SOURCES_NORM.get(feed_region, set())):
        return True
    return False

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

        # PENTING: batasi jumlah entri PER FEED di sini (bukan total gabungan nanti).
        # Ada 7 feed sekarang (2 umum + 5 wilayah); feed umum saja bisa balikin ~100
        # entri. Kalau baru dipotong belakangan (mis. entries[:60] dari daftar
        # gabungan), feed umum yang diambil duluan akan menghabiskan jatah itu
        # duluan dan hampir semua berita wilayah/LU/inflasi dari 5 feed wilayah
        # kepotong sebelum sempat sampai ke Claude. Batasi per-feed di sini supaya
        # tiap feed - termasuk yang wilayah - selalu kebagian jatah adil.
        for e in feed.entries[:PER_FEED_LIMIT]:
            pub = None
            if getattr(e, "published_parsed", None):
                pub = datetime.datetime(*e.published_parsed[:6], tzinfo=datetime.timezone.utc)

            # kalau tidak ada tanggal, tetap sertakan (beberapa feed tidak isi published)
            if pub is not None and pub < cutoff:
                continue

            title = e.get("title", "").strip()
            if not title or title in seen_titles:
                continue

            # Google News RSS punya tag <source url="...">Nama Media</source> -
            # ini nama media aslinya (CNBC Indonesia, Bloomberg, dst), beda dari
            # e['link'] yang cuma link redirect Google News.
            source = e.get("source", {}) or {}
            source_name = (source.get("title") or "").strip()
            source_href = (source.get("href") or "").strip()

            total_seen += 1
            if not _is_allowed_source(source_name, feed_region):
                total_filtered_out += 1
                continue
            seen_titles.add(title)

            # Tanggal terbit asli (WIB, format singkat "23 Jul") - dihitung di sini
            # dari data RSS asli, BUKAN ditebak Claude nanti. Model tinggal salin
            # field ini apa adanya, jadi tanggal per-berita selalu akurat.
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

    print(f"  Filter sumber: {total_seen} entri diperiksa, {total_filtered_out} dibuang (bukan media whitelist).")
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
    "date": {"type": "string", "description": "Tanggal terbit berita, disalin PERSIS dari field Tanggal pada data sumber (mis. '23 Jul') - jangan dihitung/ditebak sendiri, kosongkan kalau field Tanggal kosong."},
    "province": {"type": "string", "description": "Provinsi spesifik yang disebut di berita (mis. 'Sumatera Barat', 'Jawa Barat'). Kosongkan kalau berita tidak menyebut provinsi spesifik (mis. cuma bicara wilayah/nasional secara umum) - jangan menebak/mengarang."},
    "title": {"type": "string"},
    "body": {"type": "string", "description": "1-3 kalimat. Kuantitatif (angka/persentase) kalau tersedia di berita, kualitatif/naratif kalau tidak - yang penting ada indikasi perkembangan/update."},
    "source_id": {"type": "integer", "description": "Salin PERSIS angka ID dari data sumber berita ini (field 'ID' pada data - bukan menghitung/menebak sendiri). Dipakai sistem utk mengambil link asli - JANGAN menulis ulang link itu sendiri."},
    "source_name": {"type": "string", "description": "Nama media, disalin persis dari field Sumber pada data (mis. 'CNBC Indonesia') - jangan dikarang, kosongkan jika tidak ada."},
}
_ITEM_REQUIRED = ["date", "province", "title", "body", "source_id", "source_name"]

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
        "global_summary": {
            "type": "string",
            "description": (
                "Ringkasan 2-4 kalimat berisi FAKTA dari seluruh item scope 'Global' - WAJIB muat angka "
                "kuantitatif spesifik kalau tersedia (harga & satuan, persentase perubahan, level "
                "tertinggi/terendah dalam periode tertentu, dst), bukan cuma naratif kualitatif. Contoh "
                "gaya yang benar: 'Kontrak berjangka Brent naik sekitar 3,4% dan ditutup di US$94,07 per "
                "barel, level tertinggi dalam lebih dari satu bulan. Sementara itu, West Texas "
                "Intermediate (WTI) menguat sekitar 3% ke US$86,83 per barel.' NETRAL, tanpa kata "
                "penilaian/tone (dilarang: 'positif', 'negatif', 'mixed', 'risiko', 'optimis', dst) - "
                "rekap fakta dgn angka, bukan simpulan. Kosongkan kalau tidak ada item Global."
            ),
        },
        "national_summary": {
            "type": "string",
            "description": (
                "Ringkasan 2-4 kalimat berisi FAKTA dari seluruh item scope 'Nasional' (rekap angka/nama "
                "kebijakan/lembaga, dst). NETRAL, tanpa kata penilaian/tone. Kosongkan kalau tidak ada "
                "item Nasional."
            ),
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
                "required": ["scope"] + _ITEM_REQUIRED,
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
                    "region_summary": {
                        "type": "string",
                        "description": (
                            "Ringkasan 2-4 kalimat berisi fakta dari demand+sectors+inflation wilayah ini - "
                            "NETRAL, tanpa kata penilaian/tone (dilarang: 'positif', 'negatif', 'mixed', "
                            "'risiko', 'optimis', dst). Cukup rekap apa yang terjadi/diberitakan per item "
                            "(angka, nama proyek/kebijakan, provinsi, status - mis. 'masih tahap penjajakan'), "
                            "bukan simpulan arah/penilaian. WAJIB sebutkan nama KATEGORI tiap item yang "
                            "disinggung (mis. 'dari sisi Investasi...', 'sisi Pertambangan mencatat...') supaya "
                            "konteks kategorinya jelas tanpa harus lihat badge. Contoh gaya yang benar: 'Dari "
                            "sisi Investasi, LiuGong memulai konstruksi pabrik kendaraan listrik di Karawang, "
                            "Jawa Barat, dengan investasi awal Rp1,3 triliun. Dua inisiatif Investasi lain masih "
                            "tahap penjajakan: kerja sama Jawa Tengah dengan Iran, dan minat Sembcorp terhadap "
                            "KEK Kendal. Dari sisi Inflasi Inti, DKI Jakarta semester I-2026 tercatat 2,78%, "
                            "terendah di Pulau Jawa berdasarkan pemberitaan yang dipantau.' "
                            "JANGAN isi kalau demand, sectors, dan inflation wilayah ini semuanya kosong."
                        ),
                    },
                    "demand": {
                        "type": "array",
                        "description": "Sisi Permintaan. Kosongkan kalau tidak ada berita relevan utk wilayah ini.",
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
                            "required": ["category"] + _ITEM_REQUIRED,
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
    "required": ["caption", "report_title", "global_summary", "national_summary", "global_national", "regions"],
    "additionalProperties": False,
}


DEFAULT_TITLE = "Rangkuman Berita Ekonomi Harian"

# ---------------------------------------------------------------------------
# Kamus keyword per kategori (dari user) - dipakai sbg ACUAN klasifikasi di
# prompt Claude, bukan syarat mutlak/exact-match. Membantu konsistensi
# klasifikasi demand/sectors/inflation antar-run, terutama utk kategori yang
# gampang tertukar (mis. Perdagangan vs Konsumsi RT, Ekspor vs berita nasional).
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
              "permintaan Tiongkok, permintaan global, tarif impor AS, safeguard. CATATAN: pasangkan dengan "
              "nama komoditas (CPO, batu bara, feronikel, karet, kopi, udang, tuna, tekstil, alas kaki) supaya "
              "tidak menangkap kata 'ekspor' dalam konteks nasional/global saja",
    "Pertanian": "panen raya, luas tanam, luas panen, produktivitas, GKP, GKG, gabah, padi, jagung, pupuk "
                 "subsidi, alsintan, El Nino, La Nina, kekeringan, banjir, sawah, OPT, hama, TBS, harga TBS, "
                 "replanting, PSR, kakao, cengkeh, perikanan tangkap, budidaya udang, tambak, musim tanam, "
                 "Bulog, serapan gabah, HPP gabah, food estate, cuaca ekstrem",
    "Perdagangan": "perdagangan besar dan eceran, distribusi barang, pasar tradisional, grosir, distributor, "
                   "arus barang, bongkar muat, stok barang, ritel modern, pusat perbelanjaan, wholesale, "
                   "penjualan wholesale, omzet ritel, pasokan barang, rantai pasok. ATURAN: kalau angkanya "
                   "unit penjualan/omzet distributor -> Perdagangan; kalau konteksnya daya beli/perilaku "
                   "konsumen -> Konsumsi RT",
    "Pertambangan": "RKAB, IUP, IUPK, produksi batu bara, HBA, harga batubara acuan, lifting minyak, lifting "
                    "gas, SKK Migas, bijih nikel, ore nikel, bauksit, timah, emas, tembaga, konsentrat, kuota "
                    "produksi, royalti mineral, PNBP mineral, HMA, tambang rakyat, reklamasi, DMO batu bara, "
                    "Grasberg, Batu Hijau, Blok Rokan, MinerbaOne",
    "Konstruksi": "proyek infrastruktur, jalan tol, bendungan, jembatan, pembangunan bandara, pembangunan "
                  "pelabuhan, progres fisik, kontraktor BUMN karya, tender proyek, lelang proyek, konsumsi "
                  "semen, penjualan semen, properti residensial, IHPR, perumahan subsidi, FLPP, PBG, backlog "
                  "perumahan, pengerjaan proyek, serah terima proyek",
    "Industri Pengolahan": "pabrik, utilisasi kapasitas, PMI manufaktur, smelter, feronikel, NPI, katoda "
                            "tembaga, MHP, alumina, refinery, oleochemical, kilang, petrokimia, pabrik pupuk, "
                            "produksi semen, TPT, tekstil, garmen, alas kaki, industri makanan minuman, "
                            "perakitan otomotif, galangan kapal, relokasi pabrik, PHK pabrik, antidumping, "
                            "bahan baku impor, ramp-up",
    "Akmamin": "tingkat penghunian kamar, TPK, okupansi hotel, PHRI, wisatawan mancanegara, wisman, wisnus, "
               "kunjungan wisatawan, MICE, restoran, kafe, kuliner, homestay, villa, low season, high season, "
               "long weekend, cuti bersama, festival daerah, event konser, tarif kamar, ADR, RevPAR",
    "Inflasi Inti": "inflasi inti, core inflation, emas perhiasan, sewa rumah, kontrak rumah, upah asisten "
                    "rumah tangga, biaya pendidikan, SPP, tarif kesehatan, ekspektasi inflasi, imported "
                    "inflation, pass-through nilai tukar, harga mobil, tarif komunikasi",
    "Inflasi VF": "harga beras, cabai merah, cabai rawit, bawang merah, bawang putih, daging ayam ras, telur "
                  "ayam ras, minyak goreng, MinyaKita, ikan segar, tomat, daging sapi, pasokan pangan, "
                  "distribusi pangan, TPID, operasi pasar, SPHP, gerakan pangan murah (GPM), panel harga "
                  "pangan Bapanas, gagal panen",
    "Inflasi AP": "BBM, Pertalite, Pertamax, Solar, LPG 3 kg, tarif listrik, TDL, tarif angkutan udara, tiket "
                  "pesawat, tarif kereta, angkutan dalam kota, angkutan antar kota, tarif tol, rokok, cukai "
                  "hasil tembakau, air PDAM, tarif parkir, STNK, BPKB",
}


def _format_category_keywords() -> str:
    lines = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        lines.append(f"- {category}: {keywords}")
    return "\n".join(lines)


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
        "global_summary": data.get("global_summary") or "",
        "national_summary": data.get("national_summary") or "",
        "global_national": data.get("global_national") if isinstance(data.get("global_national"), list) else [],
        "regions": data.get("regions") if isinstance(data.get("regions"), list) else [],
    }


def _resolve_source_ids(data: dict, entries: list) -> None:
    """Ganti "source_id" (angka pendek yg ditulis Claude) jadi "source_url" (link
    asli, diambil dari 'entries' by index) - dilakukan di Python, BUKAN oleh Claude,
    supaya model tidak perlu "mengetik ulang" link Google News yang panjang
    (~500+ karakter) di output - itu mahal krn token output dibilling ~5x token
    input. In-place: memodifikasi tiap item dict langsung."""
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


def summarize_with_claude(entries: list) -> dict:
    if not entries:
        return {
            "caption": "Tidak ada berita ekonomi baru yang terdeteksi pagi ini.",
            "report_title": DEFAULT_TITLE,
            "global_summary": "",
            "national_summary": "",
            "global_national": [],
            "regions": [],
        }

    # Batas 150 (naik dari sebelumnya) krn sekarang PER_FEED_LIMIT sudah menjamin
    # tiap feed (termasuk 5 feed wilayah) kebagian jatah adil - jadi limit di sini
    # cuma jaring pengaman biar prompt tidak membengkak liar, bukan lagi penyebab
    # utama berita wilayah/LU/inflasi hilang.
    #
    # CATATAN HEMAT TOKEN (penting):
    # - Field "summary" mentah dari Google News RSS SELALU cuma HTML kosong berisi
    #   judul yang dibungkus <a href=link>...</a> + nama sumber - tidak ada info
    #   tambahan sama sekali di luar Judul/Sumber/Link yang sudah dikirim terpisah.
    #   SENGAJA tidak diikutkan ke prompt (dulu boros ratusan token/entri tanpa nilai).
    # - Link Google News asli ~500+ karakter (encoded, mahal utk di-generate ulang
    #   di OUTPUT - 5x lebih mahal dari input). Ganti dengan ID pendek (index list
    #   ini) - Claude cukup salin angkanya, link asli dipetakan balik di Python
    #   sesudahnya (lihat _resolve_source_ids).
    entries = entries[:150]
    raw_text = "\n\n".join(
        f"ID: {idx}\nJudul: {it['title']}\nTanggal: {it['date_label'] or '(tidak diketahui)'}"
        f"\nSumber: {it['source_name'] or '(tidak diketahui)'}"
        for idx, it in enumerate(entries)
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

  KHUSUS "Global": WAJIB muat angka/data KUANTITATIF spesifik kalau tersedia di berita - harga
  (level & satuan), persentase perubahan, dan pembanding (level tertinggi/terendah dalam periode
  tertentu, dst) - jangan cuma naratif kualitatif kalau angkanya ada di sumber. Contoh gaya yang
  benar: "Kontrak berjangka Brent naik sekitar 3,4% dan ditutup di US$94,07 per barel, level
  tertinggi dalam lebih dari satu bulan, bahkan sempat menembus US$95. Sementara itu, West Texas
  Intermediate (WTI) menguat sekitar 3% ke US$86,83 per barel." Kalau berita sumber memang tidak
  menyebut angka pasti, baru boleh naratif kualitatif - tapi utamakan cari & pakai angkanya kalau
  ada di teks Cuplikan.

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
  Maks 2 item PER KATEGORI per wilayah (mis. maks 2 item utk "Investasi" di wilayah Jawa) -
  kalau ada lebih dari 2 berita relevan utk kategori yg sama, pilih 2 yang paling penting/baru,
  jangan sertakan semuanya.

KAMUS KEYWORD PER KATEGORI (acuan klasifikasi - bantu tentukan kategori mana yang paling cocok
untuk suatu berita; berita TETAP boleh masuk kategori tsb walau tidak persis memakai kata-kata
ini, selama konteksnya relevan - kamus ini bukan syarat exact-match):
{_format_category_keywords()}

  Setiap item (di section 1, demand, sectors, maupun inflation) WAJIB punya:
  - "date": salin PERSIS dari field "Tanggal" di data sumber berita itu - jangan hitung/tebak
    sendiri. Kosongkan kalau field Tanggal-nya "(tidak diketahui)".
  - "province": provinsi spesifik yang disebut berita (mis. "Sumatera Barat", "Jawa Timur").
    Kosongkan kalau beritanya tidak menyebut provinsi spesifik - JANGAN menebak/mengarang
    provinsi hanya karena tahu wilayahnya.

  KHUSUS item demand/sectors/inflation (yang punya "category"/"component"): "title" dan "body"
  WAJIB menyinggung nama kategorinya secara ALAMI di dalam kalimat - JANGAN pakai prefix/label
  seperti "Sisi Investasi:" atau "Sektor Pertambangan:" di depan judul (itu sudah ada di badge
  PDF, jadi redundan kalau diulang sbg label). Rangkai jadi satu kalimat utuh yang menyebut
  kategorinya secara natural. Contoh BENAR (kategori "Pertambangan"): title = "Ekonomi
  Kalimantan Selatan Tumbuh, Didorong Sektor Pertambangan"; body = "Pertumbuhan ekonomi
  Kalimantan Selatan ditopang kinerja sektor pertambangan yang tetap solid meski nilai tukar
  dolar AS menguat." Contoh SALAH (jangan begini): title = "Sektor Pertambangan: Ekonomi
  Kalimantan Selatan Tumbuh". Berlaku utk SEMUA kategori (Fiskal, Investasi, Pertanian, Inflasi
  Inti, dst) - pembaca harus tahu kategorinya dari alur kalimat, bukan dari label tempelan.

  Setelah demand+sectors+inflation satu wilayah selesai diisi, tulis "region_summary" - ringkasan
  2-4 kalimat berisi FAKTA dari item-item wilayah tsb (angka, nama proyek/kebijakan, provinsi,
  status spt "masih tahap penjajakan"). JANGAN memakai kata penilaian/tone seperti "positif",
  "negatif", "mixed", "risiko", "optimis", dst - ini rekap fakta, BUKAN kesimpulan arah/penilaian.
  Kosongkan "region_summary" kalau demand, sectors, DAN inflation wilayah itu semuanya kosong.

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

"global_summary" DAN "national_summary" (utk Executive Summary halaman 2):
  Setelah "global_national" selesai diisi, tulis DUA ringkasan terpisah:
  - "global_summary": ringkasan 2-4 kalimat FAKTA dari seluruh item scope "Global" saja.
  - "national_summary": ringkasan 2-4 kalimat FAKTA dari seluruh item scope "Nasional" saja.
  Sama seperti "region_summary": NETRAL, tanpa kata penilaian/tone ("positif"/"negatif"/"mixed"/
  "risiko"/"optimis" dst) - rekap fakta (angka, nama kebijakan/lembaga/negara), bukan simpulan.
  Kosongkan salah satu/keduanya kalau tidak ada item dgn scope tsb.

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

2. "global_summary" dan "national_summary" - lihat instruksi di atas (netral, tanpa tone).

3. "regions" - lihat Section 2 & 3 di atas. Utk tiap item: "date", "province", judul,
   1-3 kalimat penjelasan (yang menyebut nama kategorinya secara eksplisit), "source_id"
   (salin PERSIS angka ID dari data sumber - jangan menghitung/menebak sendiri) dan
   "source_name" (nama media dari field Sumber) - salin persis, jangan dikarang. Plus
   "region_summary" per wilayah (lihat instruksi di atas - netral, tanpa tone, sebutkan
   kategori tiap item yang disinggung).

4. "caption" - versi singkat gaya pesan WhatsApp pagi hari, MERANGKUM LINTAS semua section
   di atas (global/nasional + wilayah yang ada beritanya):
   - JANGAN tulis baris tanggal/sapaan di awal (sistem akan menambahkannya otomatis) - langsung
     mulai dari poin berita pertama.
   - 5-8 poin berita terpenting (gabungkan berita duplikat/topik sama jadi satu poin).
   - Tiap poin: judul singkat (bold pakai *asterisk*, gaya WhatsApp) + 1-2 kalimat inti.
   - Tutup dengan 1 baris highlight paling penting hari ini.
   - Total maksimal ±300-400 kata, bahasa Indonesia, ringkas, tanpa markdown heading (#).

Isi juga "report_title" (judul laporan, mis. 'Rangkuman Ekonomi Harian')."""

    # Sonnet 5 dgn thinking ringan (effort low): tugas ini adalah PENILAIAN
    # (memilih & mengurutkan berita terpenting, klasifikasi ke wilayah/kategori,
    # mensintesis region_summary/global_summary/national_summary netral) - bukan
    # sekadar meringkas, Sonnet jauh lebih baik menimbang ini drpd Haiku.
    # max_tokens dinaikkan lagi (date/province per item + summary per section
    # bikin output jauh lebih panjang) - pakai streaming krn di atas ~16rb token
    # non-streaming berisiko timeout HTTP SDK.
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
        # JSON kepotong (mis. kena max_tokens). Jangan crash & buang seluruh run -
        # selamatkan minimal caption (field pertama, biasanya utuh) via regex, kirim
        # tanpa detail section. Caption WhatsApp tetap terkirim.
        print(
            f"  JSON tidak lengkap (panjang={len(raw_json)}, stop_reason={resp.stop_reason}) - "
            f"pakai fallback caption-only.",
            file=sys.stderr,
        )
        salvaged = {
            "caption": _extract_scalar(raw_json, "caption"),
            "report_title": _extract_scalar(raw_json, "report_title"),
            "global_summary": "",
            "national_summary": "",
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


def _build_highlight_page_html(data: dict) -> str:
    """Halaman 2: Executive Summary - 7 paragraf (Global, Nasional, 5 wilayah),
    masing-masing subjudul + satu paragraf ringkasan netral. Tanpa box, murni
    subjudul+teks ala buku, supaya cukup dibaca halaman ini saja utk gambaran
    lengkap; detail ada di halaman-halaman berikutnya."""
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
    """Halaman 3: detail lengkap Perkembangan Ekonomi Global dan Nasional."""
    global_items = [i for i in global_national if i.get("scope") == "Global"]
    national_items = [i for i in global_national if i.get("scope") == "Nasional"]
    # category_key="" -> tidak ada badge kategori per-item (judul sub-grup "Global"/
    # "Nasional" sudah cukup, badge lagi cuma redundan)
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
    """Satu halaman penuh per wilayah - menggabungkan perkembangan ekonomi (sisi
    permintaan & penawaran, dua kolom) dan inflasi wilayah, ditutup ringkasan
    wilayah yang NETRAL (rekap fakta, bukan simpulan arah/tone). SELALU dibuat
    utk 5 wilayah (bukan cuma yang ada datanya) - kalau tidak ada berita relevan
    utk suatu sub-bagian, tetap jujur tulis "belum ada/ditemukan", bukan
    disembunyikan."""
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
    /* Cover: full-bleed, tanpa margin & tanpa header/footer */
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
        padding: 9px 16px 10px 16px;
        margin-bottom: 9px;
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

    /* Halaman 2: Executive Summary - subjudul + 1 paragraf, tanpa box */
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

    .section {{ margin-bottom: 24px; }}
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
        display: block;
        font-family: Georgia, 'Times New Roman', 'Liberation Serif', serif;
        font-style: italic;
        font-variant: small-caps;
        font-size: 15pt;
        font-weight: 700;
        color: #0a2342;
        letter-spacing: 0.03em;
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
    /* Narasi "Arah Perkembangan" di akhir tiap region-block (section 2) */
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
    /* Section 4: Highlight untuk Pimpinan */
    .leadership-item {{
        margin-bottom: 14px;
        padding: 12px 16px;
        background: #f7f9fc;
        border-radius: 6px;
        border: 1px solid #e6ebf3;
    }}
    .leadership-theme {{
        font-weight: 800;
        font-size: 10pt;
        color: #0a2342;
        margin-bottom: 4px;
    }}
    .leadership-text {{
        font-size: 10pt;
        color: #3c4a63;
        text-align: justify;
    }}
    .footer-note {{
        margin-top: 30px;
        padding-top: 12px;
        border-top: 1px solid #e2e8f2;
        font-size: 7.5pt;
        color: #94a1b8;
        line-height: 1.5;
    }}

    /* ============ Halaman terpisah (satu .content-page = satu halaman) ============ */
    .content-page {{
        page-break-before: always;
        padding-top: 2px;
    }}

    /* Halaman 2: Ringkasan Eksekutif */
    .brief-section {{ margin-bottom: 4px; }}
    .brief-section-title {{
        font-size: 8.5pt;
        font-weight: 800;
        color: #0a2342;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 2px;
        padding-bottom: 1px;
        border-bottom: 1px solid #e2e8f2;
    }}
    .brief-item {{
        margin-bottom: 3px;
        padding-left: 10px;
        border-left: 2px solid #d9e2f0;
        font-size: 8pt;
        line-height: 1.22;
        page-break-inside: avoid;
        text-align: justify;
        text-justify: inter-word;
    }}
    .brief-tag {{
        display: inline-block;
        font-size: 6.8pt;
        font-weight: 800;
        color: #b9863f;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-right: 5px;
    }}
    .brief-title {{ font-weight: 700; color: #142743; margin-right: 4px; }}
    .brief-text {{ color: #3c4a63; text-align: justify; text-justify: inter-word; }}
    .brief-region-table {{ width: 100%; border-collapse: separate; border-spacing: 0; table-layout: fixed; }}
    .brief-region-cell {{ width: 50%; vertical-align: top; padding: 0 4px 2px 0; }}
    .brief-region-cell:last-child {{ padding-left: 4px; padding-right: 0; }}
    .brief-region {{
        padding: 2px 8px;
        background: #f7f9fc;
        border-radius: 5px;
        border: 1px solid #e6ebf3;
        font-size: 7.7pt;
        line-height: 1.16;
        page-break-inside: avoid;
        text-align: justify;
        text-justify: inter-word;
    }}
    .brief-region-name {{
        display: block;
        font-weight: 800;
        color: #0a2342;
        margin-bottom: 2px;
        font-size: 9pt;
    }}

    /* Halaman per wilayah: dua kolom (Permintaan | Penawaran) supaya muat 1 halaman */
    .region-cols {{ display: flex; gap: 28px; margin-bottom: 2px; }}
    .region-cols .region-col {{ flex: 1; min-width: 0; }}
    .region-cols .region-col:first-child {{ padding-right: 6px; border-right: 1px solid #e6ebf3; }}
    .region-cols .region-col:last-child {{ padding-left: 6px; }}

    /* Halaman wilayah lebih padat dari halaman lain (2 kolom + inflasi + narasi
    harus muat 1 halaman) - font sedikit lebih kecil drpd default. */
    .region-page .item {{ margin-bottom: 9px; }}
    .region-page .item-title {{ font-size: 10.2pt; margin-bottom: 2px; }}
    .region-page .item-body {{ font-size: 9.2pt; line-height: 1.42; margin-bottom: 3px; }}
    .region-page .item-meta {{ font-size: 7.5pt; margin-bottom: 2px; }}
    .region-page .subgroup-label {{ margin-bottom: 5px; }}
    .region-page .direction-summary {{ font-size: 9pt; padding: 7px 11px; margin-top: 0; margin-bottom: 14px; }}

    /* Halaman metodologi */
    .method-block {{ margin-bottom: 16px; }}
    .method-heading {{
        font-weight: 800;
        color: #0a2342;
        font-size: 10.5pt;
        margin-bottom: 6px;
    }}
    .method-text {{
        font-size: 9.5pt;
        color: #3c4a63;
        text-align: justify;
        line-height: 1.6;
        margin-bottom: 8px;
    }}
    .method-list {{
        margin: 0 0 4px 0;
        padding-left: 18px;
        font-size: 9.5pt;
        color: #3c4a63;
        line-height: 1.55;
    }}
    .method-list li {{ margin-bottom: 6px; text-align: justify; }}

    /* Kamus keyword (tabel 2 kolom) */
    .keyword-table {{ width: 100%; border-collapse: separate; border-spacing: 0; table-layout: fixed; margin-top: 6px; }}
    .keyword-cell {{ width: 50%; vertical-align: top; padding: 0 10px 10px 0; }}
    .keyword-cell:last-child {{ padding-left: 6px; padding-right: 0; }}
    .keyword-block {{
        display: block;
        padding: 7px 10px;
        background: #f7f9fc;
        border-radius: 5px;
        border: 1px solid #e6ebf3;
        page-break-inside: avoid;
    }}
    .keyword-cat {{
        display: block;
        font-weight: 800;
        font-size: 8.5pt;
        color: #0a2342;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 3px;
    }}
    .keyword-list {{
        display: block;
        font-size: 7.5pt;
        color: #5a6a85;
        line-height: 1.4;
        text-align: justify;
        text-justify: inter-word;
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
