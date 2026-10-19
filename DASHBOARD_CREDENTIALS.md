# Credential Supabase untuk Web Dashboard

Status: **sudah terpasang dan sudah diuji langsung ke database produksi**
pada 19 Oktober 2026.

## Nilai yang dipakai

```
SUPABASE_URL       = https://jzcmkkswkxjzxzdzffmb.supabase.co
REST base          = https://jzcmkkswkxjzxzdzffmb.supabase.co/rest/v1
SUPABASE_ANON_KEY  = sb_publishable_wz-dhyjioOxprYEDEu6icg_4DrL8sgh
```

Catatan: Supabase sudah memindahkan kunci lama. Pengganti `anon` sekarang
bernama **publishable key** dengan awalan `sb_publishable_`. Fungsinya sama,
yaitu memetakan ke role `anon` di Postgres, jadi semua policy yang kita pasang
tetap berlaku. Kunci ini memang dirancang untuk terlihat publik dan boleh masuk
ke bundle frontend.

Kunci ini sudah saya tulis ke `.env` lokal Anda. `.env` tetap diabaikan git,
sudah saya pastikan.

## Kunci mana untuk apa

| Kunci | Dipakai di | Boleh publik | Bisa apa |
|---|---|---|---|
| publishable (`sb_publishable_...`) | frontend, backend dashboard | ya | SELECT pada tabel yang dibuka policy saja |
| secret / service_role | pipeline, GitHub Actions | tidak | segalanya, menembus RLS |

Satu aturan saja: **secret key tidak boleh keluar dari server.** Kalau pernah
masuk ke bundle frontend, langsung Reset di Project Settings > API Keys.

## Hasil uji akses, 19 Oktober 2026

Diuji dengan publishable key langsung ke REST API.

| Tabel | Baris di DB | Terlihat oleh kunci publik | Kesimpulan |
|---|---|---|---|
| `articles` | 3.402 | 0 | tertutup, benar |
| `scores` | 3.402 | 0 | tertutup, benar |
| `ai_decisions` | 3.402 | 0 | tertutup, benar |
| `logs` | 198 | 0 | tertutup, benar |
| `run_payloads` | 0 | 0 | tertutup, benar |
| `report_items` | 0 | 0 | terbuka, masih kosong |
| `koran_pages` | 0 | 0 | terbuka, masih kosong |

**Penting dipahami:** PostgREST yang kena RLS menjawab `200 OK` dengan array
kosong, bukan `403`. Jadi jangan menilai keamanan dari status code. Buktinya ada
di kolom tengah tabel di atas: `articles` berisi 3.402 baris, tetapi kunci
publik melihat nol. Itu baru bukti bahwa policy bekerja.

## Semua endpoint bisa dipanggil dengan GET

Sudah diuji, bukan asumsi. Kedua function ditandai `STABLE`, sehingga PostgREST
mengizinkan GET.

```
GET /rest/v1/rpc/get_daily_digest              -> 200
GET /rest/v1/rpc/get_daily_digest?p_date=2026-10-19
GET /rest/v1/rpc/search_koran?q=ekonomi&limit_n=2  -> 200
GET /rest/v1/report_items?report_date=eq.2026-10-19&order=section.asc,position.asc
GET /rest/v1/report_summaries?select=report_date,report_title&order=report_date.desc&limit=30
GET /rest/v1/koran_editions?report_date=eq.2026-10-19&select=paper_name,page_count,ocr_chars
GET /rest/v1/koran_pages?report_date=eq.2026-10-19&paper_key=eq.kompas&order=page_number
```

Header untuk semua permintaan:

```
apikey: sb_publishable_wz-dhyjioOxprYEDEu6icg_4DrL8sgh
Authorization: Bearer sb_publishable_wz-dhyjioOxprYEDEu6icg_4DrL8sgh
```

## Uji sendiri dari terminal

```bash
BASE=https://jzcmkkswkxjzxzdzffmb.supabase.co/rest/v1
KEY=sb_publishable_wz-dhyjioOxprYEDEu6icg_4DrL8sgh

# laporan terbaru, utuh dan sudah nested
curl -s "$BASE/rpc/get_daily_digest" -H "apikey: $KEY" | head -c 400

# daftar tanggal
curl -s "$BASE/report_summaries?select=report_date,report_title&order=report_date.desc&limit=10" -H "apikey: $KEY"

# arsip koran
curl -s "$BASE/koran_editions?select=report_date,paper_name,ocr_chars&order=report_date.desc&limit=10" -H "apikey: $KEY"

# uji negatif: articles harus balas array kosong meski isinya ribuan baris
curl -s "$BASE/articles?select=id&limit=1" -H "apikey: $KEY" -H "Prefer: count=exact" -D - -o /dev/null | grep -i content-range
# yang benar: content-range: */0
```

## Konfigurasi di BE dashboard Anda

```bash
SUPABASE_URL=https://jzcmkkswkxjzxzdzffmb.supabase.co
SUPABASE_ANON_KEY=sb_publishable_wz-dhyjioOxprYEDEu6icg_4DrL8sgh
```

Contoh endpoint FastAPI berikut cache 30 menit ada di `DASHBOARD_API.md`, bagian
"Pola yang disarankan untuk BE yang sudah ada". Karena data hanya berubah sekali
sehari, satu panggilan ke Supabase per hari sudah cukup melayani semua
pengunjung dashboard.
