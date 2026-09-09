# Dokumentasi Sistem News Scraping AI — Departemen Regional Bank Indonesia
**Ringkasan Ekonomi Harian Otomatis**

Versi: 1.0 | Tanggal: 31 Agustus 2026 | Status: Production Live

---

## 1. Ringkasan Eksekutif

Sistem News Scraping AI adalah pipeline otomatis harian yang mengumpulkan berita ekonomi Indonesia & global dari berbagai sumber, menganalisisnya dengan kerangka pemikiran ekonom Departemen Regional Bank Indonesia (DR-BI), dan mengirimkan ringkasan berformat PDF profesional kepada pemangku kepentingan melalui WhatsApp — setiap hari pukul **07.00 WIB**, tanpa intervensi manual.

**Manfaat utama:**
- Menghemat 60–90 menit waktu analis per hari yang biasanya digunakan untuk pemantauan berita manual.
- Konsistensi mutu ringkasan dengan kerangka analitik yang tetap (5 wilayah kerja, 6 Lapangan Usaha, 4 komponen sisi permintaan CIGX, 3 komponen inflasi).
- Cakupan multi-sumber: RSS berita online, koran cetak digital (OCR), dan (opsional) foto berita dari lapangan.
- Sistem penilaian berbasis aturan (bukan skor sederhana) untuk memastikan berita strategis (RKAB, BI Rate, bencana, dsb.) tidak terlewat.
- Auditability penuh: distribusi bucket, keputusan AI, dan sumber ditampilkan di log agar tim dapat memverifikasi.

**Angka kunci pemakaian per generate (rata-rata, berbasis log produksi):**
| Metrik | Nilai | Sumber |
|---|---|---|
| Berita RSS ter-fetch (semua feed) | 175–250 entri | Log RSS fetch |
| Berita lolos filter whitelist + disambiguasi | 90–120 | Log filter |
| Koran cetak (halaman OCR) | 40–48 halaman × 4 media | Log OCR |
| Karakter OCR koran (setelah kompresi) | ± 250.000–280.000 karakter | Log OCR |
| **Token input Claude summarize** | **40.000–47.000** | Log `input_tokens` |
| **Token output Claude summarize** | **6.700–7.500** | Log `output_tokens` |
| Token per halaman OCR koran | ± 2.500–4.500 in + 2.000–5.000 out | Log per halaman |
| Total token input harian (OCR + summarize) | ± 160.000–200.000 | Perhitungan |
| Total token output harian | ± 45.000–60.000 | Perhitungan |
| Waktu total generate + send | 20–25 menit | Log GitHub Actions |
| **Biaya AI per generate (hari)** | **$1.0–$1.5** (± Rp 16.500–24.750) | Anthropic pricing |

---

## 2. Latar Belakang & Tujuan Bisnis

**Konteks:** Departemen Regional Bank Indonesia bertanggung jawab memantau perkembangan ekonomi di 5 wilayah kerja (Sumatera, Jawa, Balinusra, Kalimantan, Sulampua) dan menyajikan analisis harian untuk mendukung perumusan kebijakan moneter dan pemantauan stabilitas ekonomi daerah.

**Masalah yang diselesaikan:**
1. **Volume informasi** — ratusan berita ekonomi terbit setiap hari, tidak mungkin dibaca semua.
2. **Fragmentasi sumber** — berita tersebar di RSS media online, koran cetak, dan siaran pers.
3. **Kerangka analitik yang konsisten** — analis butuh output yang mengikuti kerangka BI (LU + Sisi Permintaan + Inflasi + Wilayah), bukan sekadar daftar berita acak.
4. **Kecepatan** — kebutuhan briefing pagi jam 07.00–07.30 sebelum agenda rapat.

**Prinsip Desain:**
- **Berpikir sebagai ekonom, bukan mesin keyword** — sistem memahami relasi antar berita (global → nasional → wilayah → kebijakan).
- **Prioritas impact ekonomi nyata** di atas match kata kunci.
- **Kualitas > kuantitas** — 1 item lokal tajam lebih berharga dari 3 item generik.
- **Auditability** — semua keputusan bisa ditelusuri.

---

## 3. Arsitektur Sistem

### 3.1 Alur End-to-End

```
┌──────────────────────────────────────────────────────────────────────┐
│  cron-job.org (07:00 WIB — trigger eksternal)                        │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ HTTPS trigger workflow_dispatch
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  GitHub Actions (Ubuntu container, Python 3.11)                      │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  TAHAP 1  — Ambil Berita RSS                                    │  │
│  │  • 9 feed Google News (query Boolean)                            │  │
│  │  • Filter whitelist sumber (60+ media)                           │  │
│  │  • Disambiguasi (buang judul luar negeri)                        │  │
│  │  • Filter negatif (buang ritual keagamaan, gosip)                │  │
│  │  Output: ± 100 entri berita ekonomi Indonesia                    │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  TAHAP 2  — Sistem Scoring Berbasis Aturan (YAML)               │  │
│  │  • Tier sumber (T1–T4)                                          │  │
│  │  • Materialitas (angka/nilai/perubahan)                          │  │
│  │  • Entitas bernama (korporasi/lembaga)                           │  │
│  │  • Kategori LU (13 kategori)                                     │  │
│  │  • Deteksi wilayah (38 provinsi)                                 │  │
│  │  Output: bucket wajib_baca / perlu_dicek / kebijakan / arsip     │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  TAHAP 3  — Download & OCR Koran Cetak                          │  │
│  │  • Login Click n Read                                            │  │
│  │  • Download 4 koran (Bisnis Indonesia, Neraca, Kontan,           │  │
│  │    Investor Daily) — 40–50 halaman total                         │  │
│  │  • OCR per halaman via Claude Vision → teks                      │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  TAHAP 4  — AI Summarization (Claude Sonnet 5)                  │  │
│  │  • Prompt struktur "Mindset Ekonom DR-BI"                        │  │
│  │  • Input: RSS ter-skor (sort DESC) + teks koran                  │  │
│  │  • Output: JSON schema terstruktur                               │  │
│  │    - global_national (Global & Nasional, ≤10 item)               │  │
│  │    - regions × 5 (demand + sectors + inflation)                  │  │
│  │    - executive_summary + caption                                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  TAHAP 5  — Render PDF (WeasyPrint + HTML/CSS)                  │  │
│  │  • Cover berlogo BI + Departemen Regional                        │  │
│  │  • Executive Summary                                             │  │
│  │  • Global & Nasional                                             │  │
│  │  • 5 Halaman Wilayah (Sumatera/Jawa/Kalimantan/Balinusra/        │  │
│  │    Sulampua)                                                     │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  TAHAP 6  — Distribusi WhatsApp Business API                    │  │
│  │  • Upload PDF ke Meta Cloud                                      │  │
│  │  • Kirim ke daftar penerima (WA_RECIPIENT — 3 nomor)             │  │
│  │  • Format template "news_report" (approved Meta)                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  TAHAP 7  — Commit PDF ke Git repo (arsip publik URL)           │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.2 Komponen Teknologi

| Komponen | Teknologi | Peran |
|---|---|---|
| Scheduler | cron-job.org | Trigger eksternal jam 07.00 WIB |
| Runtime | GitHub Actions (Ubuntu, Python 3.11) | Eksekusi pipeline serverless |
| RSS Parser | `feedparser` + Google News RSS | Fetch berita 24 jam terakhir |
| Koran OCR | Click n Read (login HTTP) + Claude Vision | Download PDF & ekstrak teks |
| AI Model | Anthropic Claude Sonnet 5 (JSON schema mode) | Analisis & sintesis ringkasan |
| PDF Render | WeasyPrint (HTML/CSS → PDF) | Layout dokumen final |
| Delivery | WhatsApp Business Cloud API v25.0 | Kirim PDF ke penerima |
| Arsip | Git repository (GitHub) | Backup PDF harian dengan URL publik |
| Repo | `github.com/msyechan03-commits/News-Scraping` | Source code + PDF history |

---

## 4. Sumber Data

### 4.1 RSS Google News (9 feed)

Sistem menggunakan query Boolean multi-topik untuk menangkap berita relevan:

| # | Kategori Feed | Query String |
|---|---|---|
| 1 | Ekonomi umum | `ekonomi indonesia when:1d` |
| 2 | Bisnis & pasar modal | `bisnis OR market OR bursa indonesia when:1d` |
| 3 | Tambang & regulasi minerba | `tambang OR RKAB OR IUP OR ESDM OR minerba indonesia when:1d` |
| 4 | Komoditas mineral | `nikel OR "batu bara" OR bauksit OR timah OR emas OR tembaga indonesia when:1d` |
| 5 | Regional Sumatera | `(investasi OR ekonomi OR inflasi) Sumatera when:1d` |
| 6 | Regional Jawa | `(investasi OR ekonomi OR inflasi) Jawa when:1d` |
| 7 | Regional Kalimantan | `(investasi OR ekonomi OR inflasi) Kalimantan when:1d` |
| 8 | Regional Balinusra | `(investasi OR ekonomi OR inflasi) Bali OR "Nusa Tenggara" when:1d` |
| 9 | Regional Sulampua | `(investasi OR ekonomi OR inflasi) Sulawesi OR Maluku OR Papua when:1d` |

Setiap feed mengembalikan hingga 100 entri. Rata-rata **200–400 entri** ter-fetch, lalu difilter menjadi ± 100 berita valid.

**Contoh Boolean query lengkap (referensi MIT Libraries):**

```
(RKAB OR IUP OR "kuota tambang" OR "batu bara" OR nikel OR bauksit OR timah OR emas OR tembaga OR HBA OR "SKK Migas" OR ESDM OR minerba OR "DHE SDA") 
AND Indonesia 
NOT (smelter OR kilang OR refinery OR petrokimia OR PLTP)
```

Prinsip operator Boolean yang dipakai:
- **AND** — semua kata wajib ada (narrow, presisi)
- **OR** — sinonim / alternatif (broaden, kejar recall)
- **NOT** (`-`) — buang noise (mis. -Malaysia, -"sepak bola")
- **`"..."`** — frasa eksak (mis. `"kuota tambang"`)
- **`( )`** — grouping saat kombinasi AND+OR (mis. `(nikel OR bauksit) AND Sulawesi`)

Dokumen terpisah `KEYWORDS_BOOLEAN_v1.md` berisi query Boolean komprehensif per topik (Keseluruhan, per LU, CIGX, Global, National, per 5 wilayah) — total 8 template siap-pakai.

### 4.2 Whitelist Sumber (60+ media)

Sistem hanya menerima berita dari media terverifikasi. Struktur:

**Media Nasional (43 sumber):** CNBC Indonesia, Bisnis Indonesia, Kontan, Bloomberg Technoz, Katadata, Databoks, Investor Daily, detikFinance, CNN Indonesia, Kompas.com, Tempo.co, Antara News, Reuters, Wall Street Journal, dsb.

**Media Regional (per wilayah, 8–12 sumber):**
- Sumatera: Serambi Indonesia, Waspada, Riau Pos, Sumatera Ekspres, Tribun Pekanbaru, dsb.
- Jawa: Jawa Pos, Pikiran Rakyat, Suara Merdeka, Solopos, Tribun Jabar/Jateng, dsb.
- Balinusra: Bali Post, NusaBali, Lombok Post, Pos Kupang, dsb.
- Kalimantan: Kaltim Post, Banjarmasin Post, Pontianak Post, Tribun Kaltim, dsb.
- Sulampua: Fajar, Tribun Timur, Manado Post, Cenderawasih Pos, dsb.

**Media Spesialis Energi/Mining (14 sumber):** Petromindo, Dunia-Energi, Tambang.co.id, GAPKI, Infosawit, dsb.

Berita dari media di luar whitelist otomatis dibuang untuk menjaga kualitas.

### 4.3 Koran Cetak Digital

**Sumber:** Click n Read (eperpus.dotsolution.net) — layanan berlangganan koran digital.

**4 koran yang diambil setiap hari:**
- Bisnis Indonesia (± 8 halaman)
- Harian Neraca (± 12 halaman)
- Harian Kontan (± 12 halaman)
- Investor Indonesia (± 16 halaman)

**Alur OCR:**
1. Login HTTP → session cookie
2. Download PDF viewer per koran
3. Ekstrak URL PDF asli dari HTML JavaScript pattern (PDF.js viewer)
4. Download PDF (2–15 MB)
5. Ekstrak gambar per halaman via PyMuPDF
6. Resize ke 986×1600 px (hemat token)
7. OCR per halaman via Claude Vision — return teks terstruktur
8. Gabungkan semua teks → format "KORAN: {nama}, HAL {n} (CETAK)"

Total teks koran ± 250.000–280.000 karakter per hari → dipotong ke 50.000 karakter sebelum diinjeksi ke AI summarize (hemat token).

### 4.4 Foto Berita Lapangan (opsional, one-off)

Untuk kasus khusus (mis. atasan kirim foto koran cetak fisik), sistem punya varian `pipeline_image_test.py` yang menerima folder foto → OCR via Claude Vision → inject ke prompt sebagai sumber tambahan. Bukan fitur harian.

---

## 5. Sistem Scoring (Berbasis Aturan)

Sistem tidak menggunakan skor sederhana (jumlah keyword match). Sebaliknya, memakai **sistem bucket berbasis aturan** yang diadopsi dari kerangka analitik BI-DR (file YAML rekan analis).

### 5.1 5 Dimensi Skoring

Setiap berita RSS di-skor pada 5 dimensi:

| Dimensi | Range | Penjelasan |
|---|---|---|
| Tier Sumber | 0–3 | T1 (bps.go.id, bi.go.id, esdm.go.id, dsb.) = 3; T2 (Katadata, Kontan, CNBC) = 2; T3 (Petromindo, Tambang.co.id) = 1; T4 (lain) = 0 |
| Materialitas | 0–3 | Jumlah token kuantitatif: ton, persen, %, triliun, miliar, Rp, USD, naik, turun, tumbuh, anjlok, melonjak, hektare, km, barel, MMSCFD, dsb. |
| Entitas | 0–6 | Jumlah nama korporasi/lembaga bernama (dari database 250+ nama: Adaro, PTBA, Vale, Antam, Freeport, Amman, Waskita, Indofood, Krakatau Steel, dsb.) |
| Kategori LU | 0–4 | Jumlah kategori Lapangan Usaha yang match (13 kategori) |
| Region | 0 atau 2 | 2 bila judul menyebut provinsi/kota dari 38 provinsi |

Total skor 0–18. Namun, **skor total bukan penentu masuk PDF** — bucket yang menentukan.

### 5.2 Sistem Bucket (Aturan, bukan Skor)

Bucket ditentukan aturan Boolean, bukan threshold skor. Ini mengikuti prinsip _shared.yaml BI-DR — **skor mudah dimanipulasi dengan menumpuk fitur kecil, aturan lebih robust**.

| Bucket | Aturan | Perilaku di PDF |
|---|---|---|
| **wajib_baca** | Ada entitas bernama **DAN** ada materialitas | Prioritas tertinggi — dipilih Claude duluan |
| **perlu_dicek** | Ada kategori LU **DAN** ada materialitas (tanpa entitas) | Prioritas menengah — biasanya masuk |
| **kebijakan** | Ada kategori LU (tanpa materialitas) | Konteks pelengkap — masuk bila relevan |
| **arsip** | Tidak lolos semua di atas | Tidak masuk PDF (namun tetap disimpan untuk audit) |

**Kelas Entitas** (dimensi terpisah, membatasi posisi maksimum):
- Nasional/Kebijakan (regulasi lintas wilayah)
- Korporasi Besar (nama masuk daftar korporasi LU)
- Usaha Formal Non-Daftar (pola "PT ..." tanpa masuk daftar)
- Informal Skala Kecil (tambang ilegal, PKL) — otomatis ke arsip

### 5.3 Filter Pra-Skoring

Sebelum masuk skoring, berita disaring dulu:

1. **Whitelist sumber** — hanya media yang terdaftar.
2. **Disambiguasi negara asing** — judul yang menyebut Malaysia, Filipina, Papua Nugini, Tiongkok, Australia, dsb. secara dominan (bukan Indonesia) dibuang. Karena RSS Google Indonesia kadang mencampur berita luar.
3. **Filter negatif** — judul dengan frasa "doa bersama", "sholawat", "istighosah", "tahlil", "misa arwah" dibuang (bukan berita ekonomi).

### 5.4 Distribusi Bucket Tipikal

Contoh distribusi hasil scoring pada 100 berita (Senin 31 Agustus 2026):

| Bucket | Jumlah | Persentase |
|---|---|---|
| wajib_baca | 3 | 3% |
| perlu_dicek | 6 | 6% |
| kebijakan | 43 | 43% |
| arsip | 48 | 48% |

Sekitar 50% berita dianggap arsip (opini, wacana, tidak material). Ini normal — sistem dirancang selektif.

---

## 6. AI Prompting (Kerangka Ekonom DR-BI)

Ini adalah komponen paling kompleks dan strategis. Prompt disusun bertingkat agar Claude berperilaku seperti ekonom BI-DR yang berpengalaman, bukan mesin ekstraksi keyword.

### 6.1 Struktur Prompt (Ringkas)

```
Berita ekonomi Indonesia & global 24 jam terakhir. Tiap item SUDAH DI-SKOR:
  - Bucket (wajib_baca > perlu_dicek > kebijakan > arsip)
  - Skor total (tier + materialitas + entitas + kategori + region)
  - Region terdeteksi dari judul
Sudah diurutkan skor DESC.

[Daftar berita RSS ter-skor, 100 item]
[Teks koran cetak hasil OCR — max 50k karakter]

TUGAS: Susun laporan ekonomi kerangka Bank Indonesia — Departemen Regional (DR).

═══════════════════════════════════════════════════
MINDSET: EKONOM DR BANK INDONESIA
═══════════════════════════════════════════════════
Anda menyusun laporan untuk analis kebijakan moneter/fiskal & pemantau
ekonomi daerah.

Perspektif HOLISTIK: hubungkan global → nasional → wilayah → kebijakan →
dampak sektor riil.

Bedakan level impact: peristiwa mikro (1 gerai buka) vs makro (RKAB
nasional dipangkas).

Prioritaskan berita yg mengubah OUTLOOK: regulasi minerba (RKAB/IUP/kuota),
moneter (BI-Rate/inflasi/rupiah), fiskal (APBN/APBD/subsidi), gangguan
pasokan (bencana/insiden), dinamika permintaan (konsumsi RT/investasi swasta).

═══════════════════════════════════════════════════
TOPIK STRATEGIS WAJIB MASUK
═══════════════════════════════════════════════════
★ RKAB — PRIORITAS SANGAT TINGGI (setara bencana alam).
  Semua berita menyebut "RKAB" WAJIB masuk output: RKAB disetujui/ditolak/
  direvisi, kuota produksi, target volume, RKAB 2026/2027, revisi RKAB,
  konsolidasi RKAB. Tandai ★ di judul PDF.

• Regulasi minerba lain: IUP/IUPK, moratorium tambang, relaksasi ekspor
  ore, PNBP minerba, tarif royalti, HBA
• Regulasi fiskal: perubahan pagu APBN/APBD, DAU/DAK/DBH, insentif pajak
  baru, subsidi BBM/listrik
• Regulasi moneter: BI-Rate, kebijakan makroprudensial, DHE SDA, GIRO wajib
• Peristiwa strategis: force majeure tambang/kilang, kebijakan hilirisasi,
  PSN baru, kepailitan korporasi besar

ATURAN ANTI-DUPLIKASI (untuk topik strategis, khususnya RKAB):
  - 1 peristiwa RKAB yg sama diliput banyak media → ambil HANYA 1 item
    dari sumber tier tertinggi (T1>T2>T3>T4)
  - PENGECUALIAN: RKAB dari korporasi/wilayah BERBEDA boleh dobel
    (mis. "PTBA susun RKAB 2027" + "ITMG revisi RKAB 2026 kuota 23 juta
    ton" = 2 item terpisah)
  - Kalau RKAB nasional (kebijakan umum) + RKAB spesifik korporasi = boleh
    1 item nasional di global_national + 1 item korporasi di
    sectors:Pertambangan wilayah terkait

═══════════════════════════════════════════════════
ATURAN SELEKSI (BUCKET = PANDUAN, BUKAN CUTOFF ABSOLUT)
═══════════════════════════════════════════════════
1. PRIORITASKAN item bucket "wajib_baca" & "perlu_dicek".
2. Item bucket "kebijakan" TETAP dipertimbangkan bila menyebut proyek/
   entitas/event bernama yg RELEVAN ke kategori LU tertentu.
3. Item bucket "arsip" boleh diangkat bila menyebut proyek infrastruktur
   bernama (Tol/Bandara/Kereta Cepat/Trans Papua/IKN), event bernama,
   atau angka konkret di badan.
4. Duplikat: pilih tier tertinggi (T1>T2>T3>T4), buang sisanya.
5. SKIP TOTAL berita ritual keagamaan tanpa dampak ekonomi terukur.

WAJIB KELENGKAPAN:
6. 5 wilayah HARUS punya region_summary.
7. Setiap wilayah HARUS punya minimal 1 item "demand" & 1 item "sectors"
   selama ada kandidat relevan (bucket apapun).

═══════════════════════════════════════════════════
BERPIKIR SEBAGAI EKONOM (BUKAN MESIN KEYWORD)
═══════════════════════════════════════════════════
1. Prioritas berita = tingkat IMPACT EKONOMI REAL, bukan sekedar match
   keyword.
2. Berita LOKAL SPESIFIK didahulukan atas replikasi topik nasional.
3. Peristiwa HOTS real-time HARUS masuk meski keyword mismatch.
4. Lihat KONEKSI antar berita: global → nasional → wilayah → kebijakan.
   Contoh: suhu laut naik (global) → sektor perikanan tertekan → wilayah
   pesisir NTT/Sultra terpengaruh → kebijakan BMKG/KKP.
5. Kualitas > kuantitas.

REPLIKASI REGIONAL (KONDISIONAL — bukan otomatis):
Boleh replikasi topik nasional ke wilayah HANYA bila:
  (a) Ada berita/data pendukung spesifik menyebut wilayah tsb, ATAU
  (b) Implikasi finansial jelas & angka spesifik ke wilayah, ATAU
  (c) Wilayah = produsen dominan komoditas DAN belum ada berita lokal
      yg lebih strong.

JANGAN replikasi kalau:
  - Wilayah sudah punya berita lokal yg lebih strong.
  - Cuma tempel di semua wilayah produsen tanpa proporsionalitas.
  - Data tidak cukup (hindari hallucinate).

═══════════════════════════════════════════════════
INTEGRASI BENCANA ALAM (JANGAN section terpisah)
═══════════════════════════════════════════════════
Berita bencana TIDAK punya kategori sendiri — klasifikasi berdasar
DAMPAK EKONOMI (dominan sectors):
  Karhutla lahan/kebun     → sectors:Pertanian
  Pabrik/smelter rusak     → sectors:Industri Pengolahan
  Tambang setop banjir     → sectors:Pertambangan
  Jalan/jembatan rusak     → sectors:Konstruksi
  Erupsi tutup destinasi   → sectors:Akmamin
  Distribusi terganggu     → sectors:Perdagangan
  Lonjakan harga pangan    → inflation:Inflasi VF
  Tarif angkutan/BBM naik  → inflation:Inflasi AP
  Tanggap darurat BNPB     → demand:Fiskal
Bencana skala nasional (>1 provinsi) → global_national scope=Nasional.

═══════════════════════════════════════════════════
ATURAN KHUSUS LU
═══════════════════════════════════════════════════
Konstruksi: PISAH REALISASI (progres/kontrak baru/diresmikan — sinyal
  output) vs RENCANA (groundbreaking/MoU/lelang — leading indicator,
  JANGAN dimasukkan sbg output triwulan).
Pertambangan: smelter/hilirisasi/refinery = konteks HILIR → masuk Industri
  Pengolahan, BUKAN Pertambangan.
Pertanian: pabrik CPO/gula/minyak goreng/pengolahan ikan → Industri
  Pengolahan, bukan Pertanian.

═══════════════════════════════════════════════════
STRUKTUR OUTPUT (JSON Schema — Ketat)
═══════════════════════════════════════════════════
SECTION 1 "global_national" (maks 10 item):
  Global: ekonomi global, bank sentral (Fed/ECB), komoditas, geopolitik.
    WAJIB kuantitatif.
  Nasional: PDB, inflasi, rupiah, BI rate, neraca perdagangan, fiskal
    pusat, bencana skala nasional.
  MIN 3-5 item dari koran cetak wajib masuk (bila tersedia).

SECTION 2 "regions" per 5 wilayah, maks 2 item/kategori:
  demand    → Fiskal, Konsumsi RT, Investasi, Ekspor
  sectors   → Pertanian, Perdagangan, Pertambangan, Konstruksi,
              Industri Pengolahan, Akmamin
  inflation → Inflasi Inti, Inflasi VF, Inflasi AP

FORMAT:
Semua summary NETRAL faktual. body: 1-3 kalimat, utamakan ANGKA.
Caption WhatsApp: numbered list 5-8 poin, *bold* judul + 1-2 kalimat,
pisah \n tiap poin. Koran cetak pakai 📰, bencana berdampak ekonomi
pakai ⚠️, RKAB pakai ★. ±300 kata.
```

### 6.2 Filosofi Prompting

Empat prinsip utama:

1. **Bucket = panduan, bukan cutoff absolut.** Sistem tidak memaksa AI mengikuti bucket secara mekanis. AI diberi keleluasaan mengangkat item bucket rendah bila memang strategis (mis. proyek infrastruktur bernama, angka konkret).

2. **Wajib kelengkapan wilayah.** 5 wilayah HARUS punya minimal 1 item demand + 1 item sectors. Ini mencegah kesalahan "wilayah kosong" yang membuat laporan tidak seimbang.

3. **Anti-hallucination.** Prompt eksplisit melarang AI mengangkat topik ke wilayah bila tidak ada data pendukung. Lebih baik kosong daripada rekayasa.

4. **Rantai koneksi.** AI didorong berpikir global → nasional → wilayah → kebijakan, bukan sekadar mencocokkan kata kunci per item.

### 6.3 Model AI & Konfigurasi

| Parameter | Nilai |
|---|---|
| Model | `claude-sonnet-5` (Anthropic) |
| Max tokens output | 32.000 |
| Thinking mode | Adaptive |
| Effort | Low (hemat biaya untuk task terstruktur) |
| Format output | JSON Schema (strict) |
| Retry logic | Max 6 retries dengan exponential backoff |
| Timeout | Stream (tidak ada timeout hard) |
| Error handling | Try/except → fallback caption bila API down |

---

## 7. Struktur Output

### 7.1 Skema JSON

Setiap generate menghasilkan JSON yang wajib mengikuti schema ketat berikut:

```jsonc
{
  "caption": "String — WhatsApp caption, 5-8 poin numbered, ±300 kata",
  "report_title": "Rangkuman Berita Ekonomi Harian",
  "global_summary": "String — 2-3 kalimat kondisi ekonomi global",
  "national_summary": "String — 2-3 kalimat kondisi ekonomi nasional",
  "global_national": [
    {
      "scope": "Global | Nasional",
      "date": "24 Agu",
      "province": "",
      "title": "Judul singkat",
      "body": "1-3 kalimat, utamakan angka",
      "source_name": "Nama media atau NAMA KORAN, HAL X (CETAK)",
      "source_url": "URL RSS atau kosong untuk koran"
    }
    // ... maks 10 item
  ],
  "regions": [
    {
      "region_name": "Sumatera | Jawa | Balinusra | Kalimantan | Sulampua",
      "region_summary": "String — 2-3 kalimat kondisi wilayah",
      "demand": [
        {
          "category": "Fiskal | Konsumsi RT | Investasi | Ekspor",
          "date": "24 Agu",
          "province": "Sumut",
          "title": "Judul",
          "body": "Isi ringkas",
          "source_name": "...",
          "source_url": "..."
        }
      ],
      "sectors": [ /* Pertanian, Perdagangan, Pertambangan, Konstruksi, Industri Pengolahan, Akmamin */ ],
      "inflation": [ /* Inflasi Inti, Inflasi VF, Inflasi AP */ ]
    }
    // ... 5 wilayah
  ]
}
```

### 7.2 Struktur PDF

Setiap PDF harian punya struktur:

| Halaman | Isi |
|---|---|
| 1 | Cover — Logo BI + Departemen Regional, tanggal, tagline |
| 2 | Executive Summary — ringkasan Global, Nasional, dan 5 wilayah dalam paragraf pendek |
| 3–4 | Perkembangan Ekonomi Global dan Nasional — sub-bagian Global & Nasional dengan item ber-angka |
| 5 | Sumatera — Ringkasan wilayah + kolom kiri (Sisi Permintaan: Fiskal/Konsumsi RT/Investasi/Ekspor) + kolom kanan (Sisi Penawaran: 6 LU) + Inflasi Wilayah |
| 6 | Jawa — struktur sama |
| 7 | Balinusra — struktur sama |
| 8 | Kalimantan — struktur sama |
| 9 | Sulampua — struktur sama |

Setiap item berita ditampilkan dengan: kategori (badge), tanggal + provinsi (meta), judul (bold), body (paragraph), dan tautan sumber (klikable). Design mengikuti brand guideline BI (biru navy `#0a2342`, aksen emas `#c9a24b`, tipografi Georgia serif).

### 7.3 Caption WhatsApp

Setiap kiriman disertai caption WhatsApp singkat (± 300 kata, 5–8 poin numbered) yang menyoroti topik terpenting hari itu, dengan penanda visual:
- ⚠️ Bencana yang berdampak ekonomi
- 📰 Berita dari koran cetak
- ★ Topik strategis RKAB

---

## 8. Deployment & Infrastruktur

### 8.1 Repository & Deployment

- **Repository:** `github.com/msyechan03-commits/News-Scraping` (privat)
- **Runtime:** GitHub Actions — Ubuntu 22.04, Python 3.11
- **Trigger:** `workflow_dispatch` (dari cron-job.org, bukan GitHub cron karena timing lebih presisi)

### 8.2 Scheduler

- **cron-job.org** (eksternal) — trigger HTTP GET ke GitHub Actions API setiap hari pukul 07.00 WIB
- Alasan pilihan: GitHub cron sering delay 5–30 menit; cron-job.org lebih presisi
- Otentikasi: GitHub Personal Access Token (PAT) — perlu perpanjangan periodik (setiap 90 hari)

### 8.3 Kredensial (GitHub Secrets)

Semua kredensial disimpan di GitHub Secrets (terenkripsi, tidak visible di code):
- `ANTHROPIC_API_KEY` — akses Claude AI
- `WA_ACCESS_TOKEN` — WhatsApp Business Cloud API
- `WA_PHONE_NUMBER_ID` — ID nomor bot WA
- `WA_RECIPIENT` — daftar penerima (comma-separated, saat ini 3 nomor)
- `CNR_USERNAME` / `CNR_PASSWORD` — login Click n Read

### 8.4 Distribusi WhatsApp

- **Platform:** WhatsApp Business Cloud API (Meta), versi API v25.0
- **Template:** `news_report` (approved Meta, kategori Utility)
- **Nama Bot:** "DRangers Bot" (dalam proses review Meta)
- **Deskripsi Bot:** "Rangkuman berita ekonomi harian."
- **Payload:** Document header (PDF) + text body (caption 800 karakter max)
- **Response tracking:** message_id dari Meta untuk audit

---

## 9. Biaya Operasional

### 9.1 Estimasi Biaya per Hari (berbasis log produksi & harga Anthropic Sept 2026)

**Harga Claude Sonnet 5:** input $3/juta token, output $15/juta token.

| Komponen | Perhitungan | Biaya USD | Biaya Rp (kurs 16.500) |
|---|---|---|---|
| Claude — OCR koran (± 44 halaman × 3.500 in + 3.500 out) | 154k input + 154k output | ± $0.50–0.75 | Rp 8.250–12.375 |
| Claude — Summarize (± 45k in + 7.5k out) | dari log `input_tokens=46.835, output_tokens=7.530` | ± $0.25–0.35 | Rp 4.125–5.775 |
| Cache read (bila prompt caching aktif) | menurunkan biaya input hingga 90% | Bervariasi | — |
| WhatsApp Business API | 3 pesan template Utility (Indonesia — gratis skala kecil) | $0 | Rp 0 |
| GitHub Actions | Free tier (public repo) — 2.000 menit/bulan | $0 | Rp 0 |
| cron-job.org | Free tier — 3 job aktif | $0 | Rp 0 |
| Click n Read (langganan koran digital) | Bulanan | — | Rp 200.000/bulan |
| **Total AI per hari** | | **$1.0–$1.5** | **Rp 16.500–24.750** |
| **Total AI per bulan (30 hari)** | | **$30–$45** | **Rp 500.000–750.000** |
| **Total operasional per bulan** | AI + koran | **± $42–$57** | **± Rp 700.000–950.000** |

**Catatan pengoptimalan biaya:**
- Beberapa halaman koran yang minim teks (mis. iklan penuh) tetap kena biaya token image — sekitar 1.500–2.000 token per halaman.
- Ada peluang menurunkan biaya 20–30% dengan menerapkan **prompt caching** untuk system prompt yang tidak berubah harian.
- Bila `effort: low` diganti `effort: medium` → biaya naik 2–3× tapi kualitas analisis marginal. Sekarang pakai `low` untuk hemat.

### 9.2 Biaya One-Off (Setup Awal)

- Meta Business Verification: gratis
- WhatsApp Business template review: gratis
- Domain / hosting: tidak dibutuhkan (semua di GitHub free tier)

---

## 10. Monitoring, Audit, dan Reliabilitas

### 10.1 Model Penyimpanan Data (Log-Only, Belum Ada Database)

**Prinsip arsitektur saat ini:** sistem TIDAK memakai database (RDBMS/NoSQL). Semua jejak berita hanya disimpan di **log GitHub Actions** dan artefak Git.

Artefak yang di-generate per run:
- **CLI log GitHub Actions** — semua tahap terekam (fetch RSS, filter whitelist, disambiguasi, scoring, AI call, render PDF, kirim WA). Log persistensi 90 hari di GitHub.
- **Tabel Skoring per berita** — distribusi bucket + skor tiap RSS entry, tampil di log CLI (tapi tidak disimpan terstruktur).
- **Tabel Keputusan Claude** — mana item RSS yang ✅ dipilih AI, mana yang di-drop.
- **`report_data.json`** — data mentah AI (item yg terpilih saja) — di-commit ke repo tapi hanya versi terakhir yg tersimpan.
- **PDF harian** — di-commit ke `output/*.pdf` dengan URL publik permanen.

**Alur Kurasi Bertingkat (QC-per-Prompt):**

Sistem sebenarnya menarik **ratusan berita RSS + puluhan halaman koran** setiap generate, tapi tidak semua muncul di PDF. Mekanisme QC bertingkat:

```
  200-400 entri RSS ter-fetch (semua feed Google News)
              ↓  Filter whitelist sumber
  ± 100 entri lolos (60% berita dropped karena sumber tidak resmi)
              ↓  Disambiguasi negara asing + filter negatif ritual
  ± 90-100 entri lolos ke scoring
              ↓  Sistem scoring bucket (5 dimensi + aturan YAML)
  Bucket: wajib_baca (3) + perlu_dicek (6) + kebijakan (43) + arsip (48)
              ↓  Sort DESC + kirim ke Claude sebagai konteks
  AI membaca semua, tapi pilih ± 20-25 item saja
              ↓  Cross-check dgn koran cetak (± 40 halaman OCR)
  Output final PDF: 10 global/nasional + 5x (2-3 item per wilayah)
              = ± 25-30 item ter-highlight (dari ratusan berita mentah)
```

**Konsekuensi model log-only:**
- Berita yang tidak masuk PDF tetap ada di log (bisa dicek manual di GitHub Actions) — tapi hanya 90 hari.
- Tidak bisa query historis: "berapa kali RKAB muncul bulan lalu?" atau "tren berita karhutla per wilayah 6 bulan terakhir?"
- Tidak ada dashboard analytics — semua audit manual dari log/PDF harian.
- Setiap generate independen — sistem tidak "mengingat" berita hari sebelumnya (kecuali via file JSON terakhir yang di-commit).

**Rasional pilihan log-only saat ini:**
- Cepat dibangun, tidak butuh maintain DB server.
- Free tier GitHub Actions cukup untuk operasi harian.
- MVP: prioritaskan kualitas output daripada infrastruktur data persistence.

**Roadmap: migrasi ke database** ada di Bab 12.3.

### 10.2 Mekanisme Fail-Safe

| Skenario Gagal | Respons Sistem |
|---|---|
| RSS feed satu sumber down | Skip feed itu, lanjut sumber lain |
| Login koran gagal | Log warning, lanjut tanpa koran (RSS only) |
| OCR koran satu halaman gagal | Skip halaman itu, lanjut halaman berikutnya |
| Claude API error (rate limit / credit habis) | Try/except → fallback caption "Ada gangguan saat merangkum berita otomatis" — PDF tetap ter-generate & terkirim |
| WA API error untuk 1 nomor | Log gagal, coba nomor berikutnya |
| Git push conflict | `pull --rebase` otomatis sebelum push |

Filosofi: **workflow tidak boleh crash total** — lebih baik output berkurang tapi tetap terkirim.

### 10.3 Verifikasi Kualitas Harian

Tim monitor bisa cek:
1. WhatsApp — PDF tiba jam 07.20–07.25 WIB
2. GitHub Actions — status hijau (semua step sukses)
3. Repo `output/*.pdf` — file harian ada
4. Meta Business Manager — delivery status pesan WA (delivered/read)

---

## 11. Struktur Kode

### 11.1 File Utama

```
News-Scraping/
├── pipeline.py                    # Main pipeline (production, ± 1.700 baris)
├── koran_scraper.py               # Modul Click n Read (login + download PDF)
├── koran_ocr.py                   # Modul OCR koran via Claude Vision
├── scrape_and_send.py             # Legacy, tidak dipakai lagi (arsip)
├── requirements.txt               # Dependency Python
├── .github/workflows/
│   └── daily-brief.yml            # GitHub Actions workflow definition
├── assets/
│   ├── bi_logo.png
│   └── dr_logo.png
└── output/                        # PDF harian ter-commit di sini
```

### 11.2 Modul Utama di pipeline.py

| Bagian | Fungsi |
|---|---|
| Config RSS_FEEDS | 9 URL Google News dengan Boolean query |
| Config NATIONAL/REGIONAL_SOURCES | 60+ whitelist media |
| Config CATEGORY_KEYWORDS | 13 kategori (4 demand + 6 sectors + 3 inflation) |
| Config KORPORASI_ALL | 250+ nama entitas per LU |
| Config WILAYAH_PROVINSI | 38 provinsi + kota utama per wilayah |
| Config FOREIGN_COUNTRY_BLOCKLIST | 8 negara asing untuk disambiguasi |
| Config NEGATIVE_TITLE_KEYWORDS | 13 frasa non-ekonomi (ritual, dsb.) |
| Function `fetch_recent_entries()` | Fetch RSS + filter |
| Function `fetch_koran_articles()` | Download + OCR 4 koran |
| Function `score_entry()` | Skoring 5 dimensi → bucket |
| Function `print_scoring_summary()` | Log distribusi bucket |
| Function `summarize_with_claude()` | Panggil Claude API + prompt lengkap |
| Function `build_html()` / `build_pdf()` | Render HTML → PDF |
| Function `send_whatsapp()` | Upload PDF + kirim template |
| Function `cmd_generate()` / `cmd_send()` | Entry points CLI |

### 11.3 Versi & Variant

| File | Kegunaan |
|---|---|
| `pipeline.py` | Production — jalan otomatis di GitHub Actions |
| `pipeline_test.py` | Test lokal — variant untuk pengembangan, sync dgn production tapi WA_TEST_RECIPIENT saja |
| `pipeline_image_test.py` | Special use-case — kalau atasan kirim foto berita cetak fisik, sistem OCR + inject ke pipeline |
| `pipeline.py.bak_pre_yaml_upgrade` | Backup versi sebelum upgrade YAML (fallback rollback) |

---

## 12. Roadmap Pengembangan

### 12.1 Sudah Dikerjakan

- ✅ Filosofi ekonom DR-BI (bukan mesin keyword)
- ✅ Sistem scoring bucket berbasis aturan YAML
- ✅ Integrasi koran cetak (4 media)
- ✅ Filter negatif ritual keagamaan
- ✅ Deteksi wilayah otomatis (38 provinsi)
- ✅ Disambiguasi berita luar negeri
- ✅ Anti-duplikasi topik strategis
- ✅ RKAB naik prioritas ke level tertinggi (setara bencana)
- ✅ Multi-recipient WA (3 nomor)
- ✅ Robustness: try/except + fallback caption bila API down

### 12.2 Sedang Dievaluasi

- 🔄 Perluasan Boolean query per wilayah (dokumen `KEYWORDS_BOOLEAN_v1.md`)
- 🔄 Feed spesialis energi/mining (Petromindo, Beritambang) — kandidat pilotable
- 🔄 Window fetch 2 hari untuk topik strategis (RKAB sering lag)

### 12.3 Rencana Jangka Menengah — Prioritas Pengembangan

**A. Migrasi ke Database (prioritas TINGGI)**

Alasan: model log-only saat ini menyulitkan pengecekan manual dan analisis historis. Rencana:
- **Database ringan** (SQLite atau PostgreSQL managed) untuk:
  - Menyimpan **setiap berita RSS** yang lolos filter (bukan hanya yang masuk PDF)
  - Skor + bucket per berita
  - Metadata (source, tier, region, kategori, timestamp)
  - Keputusan AI (item terpilih vs di-drop, alasan)
- **Manfaat langsung**:
  - Cek manual: "berita RKAB apa saja yang muncul minggu lalu?" — via query SQL
  - Rewind: audit keputusan AI dari kejadian error / hasil kurang optimal
  - Reporting: distribusi berita per LU/wilayah bulanan
  - Retensi: histori berita 1–5 tahun (log GitHub Actions cuma 90 hari)

**B. Upgrade Prompt Berkala (prioritas TINGGI)**

Sistem prompting sekarang mengikuti kerangka DR-BI, tapi kebutuhan kantor bergerak (topik strategis berubah, fokus wilayah berubah, kebijakan baru, dsb.). Rencana:
- **Review prompt bulanan** bersama tim analis:
  - Kategori LU mana yang under-represented?
  - Ada topik strategis baru yang harus WAJIB masuk (mis. selama minggu ini fokus stabilitas rupiah)?
  - Ada media/sumber yang perlu ditambah/dikurangi whitelist?
  - Apakah format PDF sudah sesuai kebutuhan atasan?
- **Prompt versioning**: setiap upgrade dicatat versi + tanggal + rationale, biar bisa A/B test hasil sebelum/sesudah.
- **Feedback loop**: analis memberi rating harian (👍/👎) — dikumpulkan untuk membaiki prompt bulan berikutnya.

**C. Fitur Analitik & Distribusi Tambahan**

- Dashboard web sederhana (analytics: jumlah item per LU per wilayah per hari) — bisa Streamlit atau Metabase, terkoneksi ke DB (A).
- Kirim email harian ke tim (backup WA + arsip mail).
- Integrasi dengan platform monitor internal BI.
- Alert khusus bila muncul topik strategis kritis (RKAB dipangkas, BI-Rate berubah, bencana besar) — via WA/telegram/email real-time.
- Sentiment analysis per item (positif/negatif/netral) — untuk tren pandangan media.
- Multi-recipient by role: analis vs pimpinan dapat versi PDF berbeda (ringkas vs detail).

---

## 13. Ringkasan Untuk Pimpinan

**Apa yang dilakukan sistem?**
Setiap pagi jam 07.00 WIB, sistem otomatis membaca ratusan berita ekonomi dari online + koran cetak, memilih yang paling penting dengan kerangka pikir ekonom BI-Departemen Regional, dan mengirimkan ringkasan PDF profesional ke WhatsApp tim.

**Kelebihan utama:**
1. Menghemat 1–1,5 jam waktu analis per hari.
2. Cakupan sumber yang tidak mungkin dilakukan manual (100+ berita, 40+ halaman koran, dalam 20 menit).
3. Kualitas konsisten mengikuti kerangka analitik BI (5 wilayah × LU × CIGX × Inflasi).
4. Tidak pernah lupa topik strategis (RKAB, BI-Rate, bencana, dsb.) — sistem punya aturan wajib angkat.
5. Semua keputusan dapat ditelusuri (audit trail lengkap).

**Batasan yang perlu disadari:**
1. Sistem tidak menggantikan analisis mendalam analis — output adalah **briefing awal**, bukan laporan final.
2. Kualitas tergantung sumber (bila media tidak liput topik penting, sistem tidak bisa mengangkatnya).
3. Perlu monitoring credit Anthropic API secara berkala (± Rp 300–400 ribu/bulan).
4. Bila WhatsApp Meta mengubah kebijakan template, format kirim mungkin perlu penyesuaian.

**Biaya operasional:**
± Rp 350.000–400.000 per bulan (Anthropic AI + langganan koran digital), di luar itu semua tools gratis.

**Roadmap ke depan:**
Perluasan cakupan Boolean query, integrasi dashboard monitoring, dan alert khusus untuk topik strategis kritis.

---

*Dokumen ini dibuat sebagai backup knowledge sistem News Scraping AI Departemen Regional Bank Indonesia. Untuk pertanyaan teknis atau usulan pengembangan, silakan hubungi tim pengembang.*
