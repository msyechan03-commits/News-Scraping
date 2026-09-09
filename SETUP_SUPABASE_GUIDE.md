# Setup Supabase untuk News Scraping AI

Setelah selesai semua langkah, kirim ke saya:
1. **Project URL** (contoh: `https://xxxxxxxxxxx.supabase.co`)
2. **Service Role Key** (JWT panjang, dimulai `eyJhbGc...`)

---

## Step 1 — Buat Project

1. Login https://supabase.com/dashboard
2. Klik **"New project"**
3. Isi:
   - **Name:** `news-scraping-drbi` (bebas, tapi jelas)
   - **Database password:** ⚠️ **CATAT SIMPAN — pakai password manager, tidak bisa recover**
   - **Region:** **Southeast Asia (Singapore)** (paling dekat Jakarta)
   - **Plan:** Free
4. Klik **Create new project** → tunggu 2-3 menit sampai provisioning selesai
5. Setelah selesai, dashboard muncul dengan tab kiri: Home / Table Editor / SQL Editor / Storage / dst.

---

## Step 2 — Buat 5 Tabel via SQL Editor

1. Klik **SQL Editor** di sidebar kiri
2. Klik **"New query"**
3. Copy paste script di bawah — **klik RUN** (button hijau kanan bawah)

```sql
-- ============================================================================
-- News Scraping DB — Schema v1
-- ============================================================================

-- Enable pgcrypto for UUID
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Table 1: runs (meta setiap generate)
CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','partial','failed')),
    error_stage TEXT,
    error_message TEXT,
    rss_count INT DEFAULT 0,
    koran_pages INT DEFAULT 0,
    articles_scored INT DEFAULT 0,
    input_tokens_summarize INT DEFAULT 0,
    output_tokens_summarize INT DEFAULT 0,
    input_tokens_secondbrain INT DEFAULT 0,
    output_tokens_secondbrain INT DEFAULT 0,
    pdf_url TEXT,
    markdown_url TEXT,
    log_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(date DESC);

-- Table 2: articles (semua berita lolos filter)
CREATE TABLE IF NOT EXISTS articles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    source_name TEXT,
    source_url TEXT,
    source_domain TEXT,
    tier TEXT CHECK (tier IN ('T1','T2','T3','T4')),
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    published_date TEXT,
    region_detected JSONB DEFAULT '[]'::jsonb,
    category_hits JSONB DEFAULT '[]'::jsonb,
    raw_snippet TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_articles_run_id ON articles(run_id);
CREATE INDEX IF NOT EXISTS idx_articles_tier ON articles(tier);

-- Table 3: scores (skor & bucket per artikel)
CREATE TABLE IF NOT EXISTS scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tier_score INT DEFAULT 0,
    mat_score INT DEFAULT 0,
    ent_score INT DEFAULT 0,
    cat_score INT DEFAULT 0,
    reg_score INT DEFAULT 0,
    total_score INT DEFAULT 0,
    bucket TEXT NOT NULL CHECK (bucket IN ('wajib_baca','perlu_dicek','kebijakan','arsip')),
    mat_hits JSONB DEFAULT '[]'::jsonb,
    ent_hits JSONB DEFAULT '[]'::jsonb,
    cat_hits JSONB DEFAULT '[]'::jsonb,
    reg_hits JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scores_article_id ON scores(article_id);
CREATE INDEX IF NOT EXISTS idx_scores_bucket ON scores(bucket);

-- Table 4: ai_decisions (item yg dipakai Claude di PDF)
CREATE TABLE IF NOT EXISTS ai_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id UUID REFERENCES articles(id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    is_selected BOOLEAN NOT NULL DEFAULT FALSE,
    placement TEXT,  -- 'global_national' | 'regions.sumatera.demand' | dll
    category TEXT,   -- 'Fiskal' | 'Konsumsi RT' | dll
    scope TEXT,      -- 'Global' | 'Nasional' | nama wilayah
    ai_title TEXT,
    ai_body TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_decisions_run_id ON ai_decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_selected ON ai_decisions(is_selected) WHERE is_selected = TRUE;

-- Table 5: logs (event log untuk audit)
CREATE TABLE IF NOT EXISTS logs (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID REFERENCES runs(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level TEXT NOT NULL CHECK (level IN ('INFO','WARN','ERROR','DEBUG')),
    stage TEXT,  -- 'fetch_rss' | 'koran' | 'scoring' | 'summarize' | 'second_brain' | 'pdf' | 'wa_send'
    message TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_logs_run_id ON logs(run_id);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC);

-- ============================================================================
-- Row Level Security (RLS) — kunci akses hanya untuk service_role
-- ============================================================================
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE logs ENABLE ROW LEVEL SECURITY;

-- Policy: service_role bisa segala hal (kita pakai service_role di GitHub Actions)
-- Anon key TIDAK bisa akses apa-apa (default)

-- ============================================================================
-- Selesai
-- ============================================================================
```

4. Kalau berhasil, muncul pesan "Success. No rows returned."
5. Verify: klik **Table Editor** di sidebar — harus muncul 5 tabel (`runs`, `articles`, `scores`, `ai_decisions`, `logs`)

---

## Step 3 — Buat 3 Storage Buckets

1. Klik **Storage** di sidebar kiri
2. Klik **"New bucket"** — buat 3 bucket:

**Bucket 1 — pdfs:**
- Name: `pdfs`
- Public bucket: ✅ ON (biar PDF bisa dibuka via URL)
- File size limit: 10 MB
- Allowed MIME types: `application/pdf`

**Bucket 2 — markdown:**
- Name: `markdown`
- Public bucket: ✅ ON
- File size limit: 5 MB
- Allowed MIME types: `text/markdown, text/plain`

**Bucket 3 — logs:**
- Name: `logs`
- Public bucket: ❌ OFF (log private, biar aman)
- File size limit: 5 MB
- Allowed MIME types: kosongkan (allow semua)

3. Verify: di sidebar Storage, harus muncul 3 bucket

---

## Step 4 — Ambil API Keys

1. Klik **Project Settings** (gear icon di sidebar kiri paling bawah)
2. Klik **API** di sub-menu
3. Catat 2 nilai penting:

**a. Project URL:**
```
https://XXXXXXXXXXXX.supabase.co
```
(contoh: `https://ffgxnwqzabc.supabase.co`)

**b. `service_role` key** (bukan `anon` key!):
```
<AMBIL_DARI_SUPABASE_SETTINGS_API_KEYS__JANGAN_DITULIS_DI_FILE_INI>  (JWT sangat panjang)
```

⚠️ **`service_role` key = kunci super admin.** Jangan share ke siapa pun, jangan commit ke Git.
- Kita simpan di `.env` local (untuk test)
- Nanti simpan di GitHub Secrets (untuk production)

---

## Step 5 — Verify Setup

Setelah semua selesai:

**Checklist:**
- [ ] Project Supabase live (dashboard bisa dibuka)
- [ ] 5 tabel muncul di Table Editor: runs, articles, scores, ai_decisions, logs
- [ ] 3 bucket muncul di Storage: pdfs, markdown, logs
- [ ] Punya Project URL (`https://xxx.supabase.co`)
- [ ] Punya `service_role` key (JWT panjang)

**Kirim ke Claude:**
1. Project URL
2. Service Role key (kalau nyaman share via chat, atau kita atur via file .env-example)

Setelah saya terima, saya lanjut:
1. Buat modul `supabase_client.py` untuk write DB + upload storage
2. Integrasi ke `pipeline_test.py` (tidak production dulu, biar Anda test)
3. Modul Second Brain markdown generator
4. Test run lokal — validate semua data masuk DB + PDF + MD ter-upload

---

## Catatan Keamanan

- **Password DB** dan **service_role key** = sensitive credentials. Simpan di password manager, jangan share screenshot.
- **anon key** boleh public (nanti dipakai kalau bikin dashboard read-only)
- Kalau service_role bocor, langsung **Reset service_role** di Settings → API → Reset

---

## Estimasi cost Supabase (Free Tier)

| Resource | Free tier | Estimasi pemakaian tahun 1 |
|---|---|---|
| Database size | 500 MB | ~50 MB (36k artikel/tahun × ~1KB row) — masih jauh dari limit |
| Storage size | 1 GB | ~150 MB (PDF 130KB × 365 + MD 30KB × 365 + logs) |
| Bandwidth | 2 GB/bulan | ~50 MB/bulan (Anda + 3 recipient) |
| Auth users | 50.000 | 1 (Anda saja) |

**Kesimpulan: Free tier cukup 3-5 tahun operasional.** Tidak ada biaya extra.

---

**Siap? Silakan lakukan Step 1-5, lalu kirim credentials-nya biar saya lanjut coding.**
