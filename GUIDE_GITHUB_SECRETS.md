# Guide Setup GitHub Secrets — Supabase Integration

Setelah test lokal berhasil dan siap push, ikuti langkah ini untuk aktifkan Supabase tracking di GitHub Actions production.

---

## Step 1 — Buka halaman Secrets

1. Buka https://github.com/msyechan03-commits/News-Scraping/settings/secrets/actions
2. Login dgn akun `BenayaObed` (kalau belum)

Anda akan lihat daftar Repository secrets yang sudah ada:
- `ANTHROPIC_API_KEY`
- `CNR_USERNAME` / `CNR_PASSWORD`
- `WA_ACCESS_TOKEN` / `WA_PHONE_NUMBER_ID` / `WA_RECIPIENT`

---

## Step 2 — Tambah 3 Secret Baru

Klik **"New repository secret"** untuk setiap secret di bawah:

### Secret 1: `SUPABASE_URL`
- **Name:** `SUPABASE_URL`
- **Value:**
  ```
  https://jzcmkkswkxjzxzdzffmb.supabase.co
  ```
- Klik **Add secret**

### Secret 2: `SUPABASE_SERVICE_ROLE_KEY`
- **Name:** `SUPABASE_SERVICE_ROLE_KEY`
- **Value:**
  ```
  <AMBIL_DARI_SUPABASE_SETTINGS_API_KEYS__JANGAN_DITULIS_DI_FILE_INI>
  ```
- Klik **Add secret**

### Secret 3: `ENABLE_SECOND_BRAIN`
- **Name:** `ENABLE_SECOND_BRAIN`
- **Value:** `true` (kalau mau MD Second Brain aktif) atau `false` (kalau mau nonaktif dulu untuk hemat token)
- Klik **Add secret**

---

## Step 3 — Update `.github/workflows/daily-brief.yml`

Buka file `.github/workflows/daily-brief.yml` di repo — tambah 3 environment variable di step "Generate rangkuman":

```yaml
      - name: Generate rangkuman & PDF (RSS + Koran OCR)
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          CNR_USERNAME: ${{ secrets.CNR_USERNAME }}
          CNR_PASSWORD: ${{ secrets.CNR_PASSWORD }}
          # ↓ TAMBAH 3 BARIS INI ↓
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          ENABLE_SECOND_BRAIN: ${{ secrets.ENABLE_SECOND_BRAIN }}
        run: python pipeline.py generate
```

---

## Step 4 — Push perubahan

Setelah 3 secret di-set + workflow.yml diperbarui:

```bash
cd ~/Documents/News-Git/News-Scraping
rm -f .git/index.lock
git add .github/workflows/daily-brief.yml pipeline.py koran_scraper.py requirements.txt supabase_client.py second_brain.py
git status --short
```

Pastikan output:
```
M .github/workflows/daily-brief.yml
M pipeline.py
M koran_scraper.py
M requirements.txt
?? supabase_client.py
?? second_brain.py
```

Add file baru:
```bash
git add supabase_client.py second_brain.py
```

Commit + push:
```bash
git commit -m "Feat: integrasi Supabase (DB + Storage) + Second Brain markdown generator"
git pull --rebase
git push
```

---

## Step 5 — Verify di GitHub Actions

1. Trigger workflow manual: https://github.com/msyechan03-commits/News-Scraping/actions
2. Klik "Rangkuman Berita Ekonomi Harian" → "Run workflow" → main → Run
3. Buka run yang baru → cek log step "Generate rangkuman":
   - Cari: `SupabaseTracker: connected → https://jzcmkkswkxjzxzdzffmb.supabase.co`
   - Cari: `SupabaseTracker: X articles + scores inserted`
   - Cari: `SupabaseTracker: uploaded 2026-09-XX.pdf → https://...`
   - Cari (kalau Second Brain aktif): `Second Brain saved: ... (X in + Y out tokens)`

Kalau semua muncul → Supabase tracking aktif di production ✅.

---

## Step 6 — Verify data masuk Supabase

1. Buka https://supabase.com/dashboard/project/jzcmkkswkxjzxzdzffmb
2. Klik **Table Editor** → cek tabel `runs` → harus ada row baru
3. Klik `articles` → harus ada ~100 rows
4. Klik `scores` → harus ada ~100 rows
5. Klik `ai_decisions` → harus ada rows dgn `is_selected = true` untuk item yg masuk PDF
6. Klik `logs` → cek event log
7. Klik **Storage** → `pdfs` bucket → harus ada file `2026-09-XX.pdf`
8. Kalau Second Brain aktif → `markdown` bucket → harus ada file `2026-09-XX.md`

---

## ⚠️ SECURITY REMINDER

Credentials Supabase yang sudah Anda kirim ke chat (Project URL + Service Role Key) SUDAH TER-EXPOSE di log percakapan kita. Setelah setup selesai dan berjalan normal:

### Cara rotate Service Role Key (recommended):
1. Buka https://supabase.com/dashboard/project/jzcmkkswkxjzxzdzffmb/settings/api
2. Scroll ke section **Reset**
3. Klik **Reset service_role secret**
4. Copy secret BARU
5. Update di 2 tempat:
   - Local `.env` → ganti `SUPABASE_SERVICE_ROLE_KEY`
   - GitHub Secrets → edit `SUPABASE_SERVICE_ROLE_KEY` → paste baru

Ini menghindari resiko kalau chat log ini tersebar / di-index search engine.

### Publishable/anon key
- Publishable key aman disebar (read-only anon access dgn RLS enforcement)
- Namun **service_role_key = super admin**, harus rahasia

---

## Estimasi Biaya Bulanan Baru

| Komponen | Sebelum | Sesudah |
|---|---|---|
| Anthropic (PDF summarize) | $30-45 | $30-45 (sama) |
| Anthropic (Second Brain, kalau aktif) | — | +$6 |
| Supabase DB + Storage | — | $0 (free tier) |
| Langganan koran | $12 | $12 (sama) |
| **Total per bulan** | ~$42-57 | **~$48-63** |

Delta tambahan: ~$6/bulan (Rp 100 ribu) untuk Second Brain saja. Bisa dimatikan kapan saja dgn set `ENABLE_SECOND_BRAIN=false`.

---

## Rollback Plan

Kalau ada masalah setelah deploy:

**Rollback ringan** — matikan Supabase saja (pipeline tetap jalan tanpa audit):
- Set GitHub Secret `SUPABASE_URL` = `""` (kosong)
- Set GitHub Secret `SUPABASE_SERVICE_ROLE_KEY` = `""`
- Trigger workflow → Supabase auto-skip (tracker.enabled=False)
- Pipeline tetap normal: RSS → koran → Claude → PDF → WA

**Rollback berat** — revert commit:
```bash
git revert HEAD
git push
```

---

## Struktur Data Supabase

Untuk referensi cepat:

**Tabel `runs`** — 1 row per generate harian
**Tabel `articles`** — ~100 rows per hari (semua RSS lolos filter)
**Tabel `scores`** — sama jumlah dgn articles
**Tabel `ai_decisions`** — sama jumlah dgn articles, tapi `is_selected=true` untuk yg masuk PDF (~20-30 rows)
**Tabel `logs`** — event trace per generate

**Storage buckets:**
- `pdfs/2026-09-XX.pdf` — public URL, arsip PDF
- `markdown/2026-09-XX.md` — public URL, Second Brain narasi
- `logs/2026-09-XX.log` — private, signed URL 30 hari

---

Selamat coba! Kalau ada error di setup atau setelah push, kirim screenshot log ke Claude.
