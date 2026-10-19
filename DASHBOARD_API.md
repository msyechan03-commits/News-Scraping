# API untuk Web Dashboard

Dokumen ini menjelaskan cara dashboard menarik berita dan isu strategis dari
pipeline yang sudah berjalan, tanpa scraping ulang.

## Ringkasnya

Anda tidak perlu membangun backend API sendiri. Supabase otomatis menyediakan
REST API (PostgREST) untuk setiap tabel di schema `public`. Yang kurang selama
ini bukan API-nya, melainkan datanya: hasil akhir rangkuman tidak pernah masuk
ke database.

Yang sudah tersimpan di DB (skema v1):

| Tabel | Isi |
|---|---|
| `runs` | metadata tiap kali pipeline jalan |
| `articles` | artikel mentah dari RSS |
| `scores` | skor dan bucket per artikel |
| `ai_decisions` | artikel mana yang dipakai AI dan ditaruh di mana |
| `logs` | event log |

Dua celah yang membuat tabel di atas belum cukup untuk dashboard:

1. Kolom `ai_decisions.ai_title` dan `ai_decisions.ai_body` ada di skema, tetapi
   tidak pernah diisi. Fungsi `insert_ai_decisions()` hanya menulis `run_id`,
   `article_id`, `is_selected`, `placement`, `category`, dan `scope`. Jadi judul
   dan narasi hasil tulisan AI, yang justru ingin ditampilkan dashboard, tidak
   ada di database.
2. Item yang berasal dari koran cetak tidak punya baris di `articles`, karena
   `articles` diisi dari daftar RSS saja. Akibatnya item cetak tidak bisa
   dirujuk oleh `ai_decisions` sama sekali. Pada contoh run yang saya cek, 4
   dari 25 item laporan berasal dari cetak, jadi sekitar 16 persen isi laporan
   hilang kalau dashboard hanya membaca tabel v1.

Satu-satunya tempat yang memuat laporan utuh adalah `output/report_data.json`,
dan file itu masuk `.gitignore` serta ditimpa setiap hari.

## Yang ditambahkan

Tiga tabel baru, satu view, dua function. Skema v1 tidak diubah sama sekali.

| Objek | Isi |
|---|---|
| `report_summaries` | 1 baris per run: judul laporan, caption, ringkasan global dan nasional, URL PDF |
| `report_regions` | 1 baris per wilayah per run: ringkasan wilayah |
| `report_items` | 1 baris per item berita yang tampil di laporan |
| `v_daily_digest` | view gabungan item dengan konteks run-nya |
| `get_daily_digest(p_date)` | RPC, mengembalikan satu laporan utuh dalam bentuk nested JSON |
| `search_digest(q, limit_n)` | RPC pencarian teks pada judul dan isi |

Kolom penting di `report_items`: `report_date`, `section`
(`global_national`, `demand`, `sectors`, `inflation`), `region`, `scope`,
`province`, `category`, `title`, `body`, `source_name`, `source_url`,
`is_print`, `position`.

`is_print` bernilai TRUE bila `source_name` mengandung "(CETAK)". Ini yang
memisahkan berita koran cetak dari berita online, dan berguna kalau dashboard
ingin menandai sumber eksklusif cetak.

## Cara pasang

1. Buka Supabase SQL Editor, jalankan `sql/002_dashboard_report_items.sql`.
   Script ini idempoten, aman diulang.
2. Uji penulisan tanpa menyentuh produksi:
   ```
   python pipeline_v2.py generate
   ```
   `pipeline_v2.py` adalah salinan `pipeline.py` yang hanya berbeda di dua
   tempat: import `save_report` dan satu pemanggilan sebelum `tracker.finish_run`.
   `pipeline.py` asli tidak disentuh.
3. Cek hasilnya di Table Editor atau lewat `check_dashboard_api.py`.
4. Kalau sudah yakin, barulah pindahkan dua baris itu ke `pipeline.py`, atau
   ganti perintah di workflow dari `pipeline.py` ke `pipeline_v2.py`.

Untuk cek cepat tanpa jaringan, modul ini bisa diuji offline:

```
python supabase_dashboard.py output/report_data.json
```

Perintah itu mencetak jumlah baris yang akan dibuat, tanpa menyentuh database.

## Arsitektur: siapa yang menulis, siapa yang melayani

Ini sering jadi kesalahpahaman, jadi perlu dipisahkan tegas.

```
GitHub Actions (cron-job.org, 1x sehari)       Supabase (hidup 24 jam)
  pipeline.py generate                           PostgREST  -> REST API
     scraping RSS + OCR koran                    Storage    -> PDF, MD, JSON
     rangkum dengan Claude            tulis
     save_report()  ------------------------>    report_summaries
                                                 report_regions          baca
     selesai, runner mati                        report_items     <-------------  BE web Anda
```

Benar bahwa pipeline scraping tidak punya server yang siap menerima request.
Runner GitHub Actions hidup beberapa menit lalu mati. Tetapi yang melayani
request dashboard bukan runner itu, melainkan Supabase, dan Supabase adalah
layanan terkelola yang hidup terus. Jadi tidak ada yang perlu Anda jalankan
sendiri untuk sisi baca.

Pembagiannya: pipeline adalah penulis yang jalan sekali sehari, Supabase adalah
pembaca yang selalu siap, dan BE web Anda cukup menjadi perantara.

## Semua endpoint bisa dipanggil dengan GET

PostgREST mengizinkan function dipanggil lewat GET selama function-nya ditandai
`STABLE` atau `IMMUTABLE`. Kedua function di migrasi ini sengaja ditandai
`STABLE`, jadi dashboard Anda tidak perlu POST sama sekali.

```
GET /rest/v1/rpc/get_daily_digest
GET /rest/v1/rpc/get_daily_digest?p_date=2026-10-19
GET /rest/v1/rpc/search_digest?q=batu%20bara&limit_n=50
```

Bentuk POST di bawah tetap berlaku dan setara. Pakai yang mana saja yang lebih
cocok dengan BE Anda.

## Pola yang disarankan untuk BE yang sudah ada

Karena web Anda sudah punya backend, lebih baik BE itu yang memanggil Supabase,
bukan browser langsung. Alasannya:

1. Data hanya berubah sekali sehari, jadi cache di BE sangat efektif. Satu
   panggilan ke Supabase per hari sudah cukup untuk melayani semua pengunjung.
2. Kunci API tidak perlu ikut ke browser sama sekali.
3. Anda bebas membentuk ulang payload sesuai kebutuhan komponen frontend, tanpa
   mengubah apa pun di sisi pipeline.
4. Tidak perlu memikirkan CORS.

Contoh satu endpoint di BE Anda:

```python
# FastAPI, cache sederhana per tanggal
import os, time, httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()
SUPA = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
_cache: dict = {}
TTL = 1800  # 30 menit, data cuma berubah sekali sehari

@router.get("/api/news/digest")
async def digest(date: str | None = None):
    key = date or "latest"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]

    params = {"p_date": date} if date else {}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{SUPA}/rest/v1/rpc/get_daily_digest",
            params=params,
            headers={"apikey": ANON, "Authorization": f"Bearer {ANON}"},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"Supabase menolak: {r.status_code}")

    data = r.json()
    _cache[key] = (time.time(), data)
    return data
```

Kalau pipeline suatu hari gagal, endpoint ini tetap mengembalikan laporan
terakhir yang berhasil, karena `get_daily_digest` tanpa parameter selalu ambil
`report_date` terbaru yang ada. Dashboard tidak akan kosong, hanya tidak
bertambah. Tampilkan `report_date` di layar supaya pembaca tahu laporan itu
tanggal berapa.

## Tabel hasil AI atau tabel mentah?

Ambil yang hasil AI, yaitu `report_items` dan `get_daily_digest`. Alasannya
bukan sekadar lebih rapi.

| | `articles` + `scores` (mentah) | `report_items` (hasil AI) |
|---|---|---|
| Volume per hari | sampai 125 baris (5 feed x 25) | sekitar 25 baris |
| Judul | judul asli RSS, sering panjang dan clickbait | judul ringkas tulisan AI |
| Isi | potongan RSS mentah | narasi 1 sampai 2 kalimat, sudah diringkas |
| Wilayah | hasil deteksi kata kunci, bisa meleset | sudah ditempatkan AI per wilayah |
| Kategori | `category_hits` berupa daftar kata kunci | satu kategori final, mis. Fiskal, Investasi |
| Berita koran cetak | **tidak ada sama sekali** | ada, ditandai `is_print` |
| Duplikat antar feed | masih ada | sudah disaring AI |

Poin ketiga dari bawah itu yang paling menentukan. Tabel `articles` hanya diisi
dari daftar RSS, sehingga berita koran cetak hasil OCR tidak punya baris di
sana. Pada run contoh yang saya periksa, 4 dari 25 item laporan berasal dari
cetak. Kalau dashboard membaca tabel mentah, berita cetak hilang seluruhnya,
padahal itu justru sumber berbayar yang tidak dimiliki pembaca lain.

Satu hal yang hanya ada di tabel mentah dan mungkin berguna nanti: kolom
`scores.bucket` (`wajib_baca`, `perlu_dicek`, `kebijakan`, `arsip`) dan
`total_score`. Kalau suatu saat dashboard ingin menu "isu strategis" berbasis
peringkat, sinyal itu ada di sana. Tetapi untuk menampilkan berita harian,
`report_items` sudah cukup dan jauh lebih bersih.

## Endpoint untuk frontend

Base URL: `https://<PROJECT>.supabase.co/rest/v1`

Header wajib pada setiap request, pakai **anon key**, bukan service_role:

```
apikey: <ANON_KEY>
Authorization: Bearer <ANON_KEY>
```

### 1. Laporan terbaru, utuh dan sudah nested

Bentuk JSON-nya sama persis dengan `report_data.json` yang dipakai pipeline
untuk membangun PDF, sehingga renderer dashboard bisa memakai struktur yang sama.

```
POST /rest/v1/rpc/get_daily_digest
Content-Type: application/json

{}
```

Untuk tanggal tertentu:

```json
{ "p_date": "2026-10-19" }
```

### 2. Daftar tanggal yang tersedia

```
GET /rest/v1/report_summaries?select=report_date,report_title,pdf_url&order=report_date.desc&limit=30
```

### 3. Semua item pada satu tanggal

```
GET /rest/v1/report_items?report_date=eq.2026-10-19&order=section.asc,position.asc
```

### 4. Item satu wilayah

```
GET /rest/v1/report_items?report_date=eq.2026-10-19&region=eq.Sumatera&order=section.asc,position.asc
```

### 5. Isu strategis per kategori, lintas tanggal

```
GET /rest/v1/report_items?category=eq.Fiskal&order=report_date.desc&limit=100
```

### 6. Hanya berita dari koran cetak

```
GET /rest/v1/report_items?is_print=is.true&order=report_date.desc&limit=50
```

### 7. Pencarian teks

```
POST /rest/v1/rpc/search_digest
Content-Type: application/json

{ "q": "batu bara", "limit_n": 50 }
```

Pencarian sengaja dibuat sebagai RPC, bukan filter `ilike`, supaya index GIN
yang dibuat di migrasi benar-benar terpakai.

### Contoh pemanggilan dari frontend

```js
const SUPABASE_URL = "https://<PROJECT>.supabase.co";
const ANON_KEY = "<ANON_KEY>";

async function getDigest(date) {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/get_daily_digest`, {
    method: "POST",
    headers: {
      apikey: ANON_KEY,
      Authorization: `Bearer ${ANON_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(date ? { p_date: date } : {}),
  });
  if (!res.ok) throw new Error(`Digest gagal: ${res.status}`);
  return res.json();
}
```

Kalau dashboard Anda memakai React, `@supabase/supabase-js` juga bisa:

```js
const { data } = await supabase.rpc("get_daily_digest", { p_date: "2026-10-19" });
const { data: items } = await supabase
  .from("report_items")
  .select("*")
  .eq("report_date", "2026-10-19")
  .order("position");
```

### 8. Arsip koran cetak

Teks OCR koran per halaman kini ikut tersimpan. Ini yang dulu hilang setiap run
selesai, karena pipeline menghapus gambar dan PDF koran untuk hemat disk.

```
GET /rest/v1/koran_editions?report_date=eq.2026-10-19&select=paper_name,page_count,ocr_pages,ocr_chars
GET /rest/v1/koran_pages?report_date=eq.2026-10-19&paper_key=eq.kompas&order=page_number
GET /rest/v1/koran_pages?report_date=eq.2026-10-19&paper_key=eq.kompas&page_number=eq.9&select=ocr_text
GET /rest/v1/rpc/search_koran?q=obligasi%20daerah&limit_n=30
```

`search_koran` mengembalikan tanggal, nama koran, nomor halaman, dan cuplikan
teks dengan kata kunci yang disorot. Ini membuat seluruh arsip koran berbayar
Anda bisa dicari, bukan sekadar tersimpan.

### 9. Narasi Second Brain

```
GET /rest/v1/second_brain_docs?report_date=eq.2026-10-19&select=markdown,char_count
```

Catatan: modul Second Brain saat ini non-aktif karena migrasi ke OpenRouter
belum mencakup `second_brain.py`. Tabelnya sudah siap, jadi begitu modul itu
dihidupkan lagi hasilnya langsung masuk.

## Catatan keamanan

- Yang dipasang di frontend adalah **anon key**. Kunci itu memang dirancang
  untuk publik, dan dengan RLS pada migrasi ini perannya hanya bisa SELECT pada
  tiga tabel baru.
- `service_role` key jangan pernah masuk ke frontend. Itu bypass RLS.
- Tabel `articles`, `scores`, `ai_decisions`, `logs`, dan `runs` tetap tertutup
  untuk anon. Isinya skor internal dan log, tidak perlu terbuka.
- Kalau nanti dashboard dipakai internal saja dan ingin lebih ketat, ganti
  `TO anon, authenticated` menjadi `TO authenticated` pada policy di migrasi,
  lalu pasang Supabase Auth di dashboard.

## Batasan yang perlu diketahui

- **Tidak ada data historis.** Tabel baru terisi mulai run berikutnya setelah
  `save_report` aktif. Data lama tidak bisa di-backfill dari database, karena
  `report_data.json` ditimpa setiap hari dan tidak diarsipkan. Yang tersisa dari
  masa lalu hanya PDF di folder `output/`. Kalau riwayat memang dibutuhkan,
  opsinya mem-parse PDF lama, dan itu pekerjaan terpisah.
- **`source_url` kosong untuk item cetak.** Wajar, karena koran cetak tidak
  punya URL. Dashboard sebaiknya menampilkan `source_name` saja bila
  `source_url` kosong, jangan merender link mati.
- **`source_url` RSS berupa link Google News**, bukan link langsung ke media.
  Link itu tetap bisa dibuka, tetapi melewati redirect Google News.
- **Pipeline jalan sekali sehari** lewat cron-job.org yang memicu
  `workflow_dispatch`. Jadi data di dashboard bergerak harian, bukan real time.

- **Supabase free tier menidurkan project** yang tidak aktif sekitar seminggu.
  Selama pipeline harian tetap jalan, project tidak pernah idle, jadi API-nya
  tetap hidup. Tapi kalau pipeline berhenti lebih dari seminggu, panggilan
  pertama setelah itu bisa lambat atau gagal sampai project bangun.

## Alternatif yang lebih sederhana

Kalau ternyata dashboard hanya perlu menampilkan laporan hari ini apa adanya
tanpa filter atau pencarian, tidak perlu tabel baru sama sekali. Cukup unggah
`report_data.json` ke Storage bucket publik, lalu dashboard menarik satu file
statis. Fungsinya sudah disediakan:

```python
from supabase_dashboard import upload_report_json
upload_report_json(tracker, date_iso, data)
```

Hasilnya satu URL publik per tanggal. Kelemahannya tidak bisa query, tidak bisa
filter wilayah atau kategori, dan tidak bisa cari. Untuk dashboard yang ingin
menampilkan isu strategis lintas waktu, jalur tabel jauh lebih berguna.
