-- ============================================================================
-- News Scraping DB - Schema v3 (arsip penuh)
-- ============================================================================
-- Tujuan: menyimpan SEMUA hasil olahan pipeline ke database, termasuk teks OCR
-- koran per halaman, narasi Second Brain, dan payload mentah tiap run.
--
-- Skema v1 (runs/articles/scores/ai_decisions/logs) dan v2 (report_*) TIDAK
-- diubah. Script ini hanya menambah 4 tabel baru.
--
-- Jalankan di Supabase SQL Editor SETELAH 002_dashboard_report_items.sql.
-- Aman diulang (idempotent).
-- ============================================================================

-- ------------------------------------------------- 1. KORAN: edisi & halaman
-- Satu baris per koran per run. Ringkasan edisi yang berhasil ditarik.
CREATE TABLE IF NOT EXISTS koran_editions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    report_date   DATE NOT NULL,
    paper_key     TEXT NOT NULL,        -- 'kompas' | 'bisnis_indonesia' | dst
    paper_name    TEXT NOT NULL,        -- 'Kompas' | 'Bisnis Indonesia'
    page_count    INT DEFAULT 0,        -- jumlah halaman yang diekstrak
    ocr_pages     INT DEFAULT 0,        -- jumlah halaman yang berhasil di-OCR
    ocr_chars     INT DEFAULT 0,        -- total karakter hasil OCR
    pdf_filename  TEXT DEFAULT '',      -- nama file PDF sumber (file-nya dihapus pipeline)
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (run_id, paper_key)
);

CREATE INDEX IF NOT EXISTS idx_koran_editions_date ON koran_editions(report_date DESC);

-- Satu baris per HALAMAN koran. Inilah arsip teks OCR yang selama ini hilang
-- begitu pipeline selesai, karena gambar dan PDF-nya dihapus untuk hemat disk.
CREATE TABLE IF NOT EXISTS koran_pages (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    report_date   DATE NOT NULL,
    paper_key     TEXT NOT NULL,
    paper_name    TEXT NOT NULL,
    page_number   INT NOT NULL,
    ocr_text      TEXT,
    char_count    INT DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (run_id, paper_key, page_number)
);

CREATE INDEX IF NOT EXISTS idx_koran_pages_date  ON koran_pages(report_date DESC);
CREATE INDEX IF NOT EXISTS idx_koran_pages_paper ON koran_pages(paper_key);
CREATE INDEX IF NOT EXISTS idx_koran_pages_run   ON koran_pages(run_id);

-- Pencarian teks di seluruh arsip koran.
CREATE INDEX IF NOT EXISTS idx_koran_pages_fts
    ON koran_pages USING GIN (to_tsvector('simple', coalesce(ocr_text, '')));

-- ------------------------------------------------------- 2. SECOND BRAIN
-- Narasi markdown panjang. Saat ini modulnya non-aktif karena migrasi ke
-- OpenRouter belum mencakup second_brain.py, tetapi tabelnya disiapkan supaya
-- begitu diaktifkan hasilnya langsung tersimpan.
CREATE TABLE IF NOT EXISTS second_brain_docs (
    run_id         UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    report_date    DATE NOT NULL,
    markdown       TEXT,
    char_count     INT DEFAULT 0,
    input_tokens   INT DEFAULT 0,
    output_tokens  INT DEFAULT 0,
    error          TEXT DEFAULT '',
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_second_brain_date ON second_brain_docs(report_date DESC);

-- --------------------------------------------------------- 3. PAYLOAD MENTAH
-- Penampung serbaguna: apa pun yang ingin diarsipkan utuh per run disimpan di
-- sini sebagai JSONB, sehingga tidak ada hasil olahan yang hilang meski nanti
-- strukturnya berubah.
--   kind = 'report_data'   -> output AI utuh (sumber PDF)
--   kind = 'rss_entries'   -> daftar entri RSS beserta skornya
--   kind = 'koran_text'    -> teks koran gabungan apa adanya
--   kind = 'caption'       -> caption WhatsApp
CREATE TABLE IF NOT EXISTS run_payloads (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    report_date   DATE NOT NULL,
    kind          TEXT NOT NULL,
    payload       JSONB,
    size_bytes    INT DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (run_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_run_payloads_date ON run_payloads(report_date DESC);
CREATE INDEX IF NOT EXISTS idx_run_payloads_kind ON run_payloads(kind);

-- ------------------------------------------------------------------ 4. RLS
ALTER TABLE koran_editions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE koran_pages       ENABLE ROW LEVEL SECURITY;
ALTER TABLE second_brain_docs ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_payloads      ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read koran_editions" ON koran_editions;
CREATE POLICY "public read koran_editions" ON koran_editions
    FOR SELECT TO anon, authenticated USING (TRUE);

DROP POLICY IF EXISTS "public read koran_pages" ON koran_pages;
CREATE POLICY "public read koran_pages" ON koran_pages
    FOR SELECT TO anon, authenticated USING (TRUE);

DROP POLICY IF EXISTS "public read second_brain_docs" ON second_brain_docs;
CREATE POLICY "public read second_brain_docs" ON second_brain_docs
    FOR SELECT TO anon, authenticated USING (TRUE);

-- run_payloads SENGAJA tidak dibuka untuk anon. Isinya dump mentah termasuk
-- seluruh entri RSS, tidak perlu terbuka ke publik. Baca lewat service_role
-- dari backend bila perlu.
DROP POLICY IF EXISTS "authenticated read run_payloads" ON run_payloads;
CREATE POLICY "authenticated read run_payloads" ON run_payloads
    FOR SELECT TO authenticated USING (TRUE);

GRANT SELECT ON koran_editions    TO anon, authenticated;
GRANT SELECT ON koran_pages       TO anon, authenticated;
GRANT SELECT ON second_brain_docs TO anon, authenticated;
GRANT SELECT ON run_payloads      TO authenticated;

-- -------------------------------------------------------- 5. RPC PENCARIAN
-- Cari teks di seluruh arsip koran. GET /rest/v1/rpc/search_koran?q=...
CREATE OR REPLACE FUNCTION public.search_koran(q TEXT, limit_n INT DEFAULT 30)
RETURNS TABLE (
    report_date DATE,
    paper_name  TEXT,
    page_number INT,
    cuplikan    TEXT
)
LANGUAGE sql
STABLE
AS $fn$
    SELECT
        k.report_date,
        k.paper_name,
        k.page_number,
        ts_headline('simple', k.ocr_text, plainto_tsquery('simple', q),
                    'MaxWords=40, MinWords=15, MaxFragments=2') AS cuplikan
    FROM koran_pages k
    WHERE to_tsvector('simple', coalesce(k.ocr_text, '')) @@ plainto_tsquery('simple', q)
    ORDER BY k.report_date DESC, k.paper_name, k.page_number
    LIMIT limit_n;
$fn$;

GRANT EXECUTE ON FUNCTION public.search_koran(TEXT, INT) TO anon, authenticated;

-- ------------------------------------------------------------- 6. VIEW BANTU
-- Ringkasan satu baris per run: apa saja yang berhasil tersimpan hari itu.
CREATE OR REPLACE VIEW v_run_overview AS
SELECT
    r.id            AS run_id,
    r.date          AS report_date,
    r.status,
    r.rss_count,
    r.articles_scored,
    r.pdf_url,
    r.markdown_url,
    (SELECT COUNT(*) FROM report_items  i WHERE i.run_id = r.id) AS report_items,
    (SELECT COUNT(*) FROM koran_pages   k WHERE k.run_id = r.id) AS koran_pages,
    (SELECT COUNT(*) FROM koran_editions e WHERE e.run_id = r.id) AS koran_editions,
    (SELECT COUNT(*) FROM run_payloads  p WHERE p.run_id = r.id) AS payloads,
    r.started_at,
    r.finished_at
FROM runs r;

ALTER VIEW v_run_overview SET (security_invoker = ON);
GRANT SELECT ON v_run_overview TO authenticated;

-- ============================================================================
-- Selesai. Verifikasi di Table Editor: koran_editions, koran_pages,
-- second_brain_docs, run_payloads.
-- ============================================================================
