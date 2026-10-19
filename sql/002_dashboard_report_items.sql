-- ============================================================================
-- News Scraping DB - Schema v2 (dashboard read layer)
-- ============================================================================
-- Tujuan: menyimpan HASIL AKHIR rangkuman (report_data) ke database, supaya
-- web dashboard bisa menariknya lewat REST API Supabase tanpa scraping ulang.
--
-- Skema v1 (runs/articles/scores/ai_decisions/logs) TIDAK diubah sama sekali.
-- Script ini hanya menambah 3 tabel baru, 1 view, dan 1 function.
--
-- Jalankan di Supabase SQL Editor. Aman diulang (idempotent).
-- ============================================================================

-- ---------------------------------------------------------------- 1. TABEL
-- Ringkasan tingkat laporan: 1 baris per run.
CREATE TABLE IF NOT EXISTS report_summaries (
    run_id            UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    report_date       DATE NOT NULL,
    report_title      TEXT,
    caption           TEXT,
    global_summary    TEXT,
    national_summary  TEXT,
    pdf_url           TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_report_summaries_date
    ON report_summaries(report_date DESC);

-- Ringkasan per wilayah: 1 baris per wilayah per run.
CREATE TABLE IF NOT EXISTS report_regions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    report_date     DATE NOT NULL,
    region          TEXT NOT NULL,
    region_summary  TEXT,
    position        INT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (run_id, region)
);

CREATE INDEX IF NOT EXISTS idx_report_regions_date
    ON report_regions(report_date DESC);

-- Item berita yang benar-benar tampil di laporan.
CREATE TABLE IF NOT EXISTS report_items (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    report_date   DATE NOT NULL,
    placement     TEXT,          -- 'global_national' | 'regions.sumatera.demand'
    section       TEXT NOT NULL, -- 'global_national' | 'demand' | 'sectors' | 'inflation'
    region        TEXT DEFAULT '',
    scope         TEXT DEFAULT '',   -- 'Global' | 'Nasional' | nama wilayah
    province      TEXT DEFAULT '',
    category      TEXT DEFAULT '',   -- 'Fiskal' | 'Investasi' | 'Inflasi VF' | dst
    item_date     TEXT DEFAULT '',   -- apa adanya dari laporan, mis. '16 Agu'
    title         TEXT NOT NULL,
    body          TEXT,
    source_name   TEXT DEFAULT '',
    source_url    TEXT DEFAULT '',
    is_print      BOOLEAN DEFAULT FALSE,  -- TRUE bila sumbernya koran cetak
    position      INT DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_report_items_date     ON report_items(report_date DESC);
CREATE INDEX IF NOT EXISTS idx_report_items_run      ON report_items(run_id);
CREATE INDEX IF NOT EXISTS idx_report_items_region   ON report_items(region);
CREATE INDEX IF NOT EXISTS idx_report_items_section  ON report_items(section);
CREATE INDEX IF NOT EXISTS idx_report_items_category ON report_items(category);
CREATE INDEX IF NOT EXISTS idx_report_items_print    ON report_items(is_print) WHERE is_print = TRUE;

-- Pencarian teks sederhana untuk kolom judul + isi.
CREATE INDEX IF NOT EXISTS idx_report_items_fts
    ON report_items USING GIN (to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(body,'')));

-- ------------------------------------------------------------------ 2. VIEW
-- View siap pakai untuk dashboard: item + konteks run-nya.
CREATE OR REPLACE VIEW v_daily_digest AS
SELECT
    i.id,
    i.report_date,
    i.section,
    i.region,
    i.scope,
    i.province,
    i.category,
    i.item_date,
    i.title,
    i.body,
    i.source_name,
    i.source_url,
    i.is_print,
    i.position,
    s.report_title,
    s.pdf_url
FROM report_items i
JOIN report_summaries s ON s.run_id = i.run_id;

-- --------------------------------------------------------------- 3. RLS
-- Tiga tabel baru ini boleh dibaca publik (anon key), tetapi hanya SELECT.
-- Tabel v1 (articles, scores, logs, ai_decisions, runs) TETAP tertutup.
ALTER TABLE report_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_regions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_items     ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read report_summaries" ON report_summaries;
CREATE POLICY "public read report_summaries" ON report_summaries
    FOR SELECT TO anon, authenticated USING (TRUE);

DROP POLICY IF EXISTS "public read report_regions" ON report_regions;
CREATE POLICY "public read report_regions" ON report_regions
    FOR SELECT TO anon, authenticated USING (TRUE);

DROP POLICY IF EXISTS "public read report_items" ON report_items;
CREATE POLICY "public read report_items" ON report_items
    FOR SELECT TO anon, authenticated USING (TRUE);

GRANT SELECT ON report_summaries TO anon, authenticated;
GRANT SELECT ON report_regions   TO anon, authenticated;
GRANT SELECT ON report_items     TO anon, authenticated;
GRANT SELECT ON v_daily_digest   TO anon, authenticated;

-- View mengikuti izin pemanggil, bukan pemilik, supaya RLS di atas tetap berlaku.
ALTER VIEW v_daily_digest SET (security_invoker = ON);

-- ----------------------------------------------------------- 4. RPC FUNCTION
-- Mengembalikan satu laporan utuh dalam bentuk nested JSON, bentuknya sama
-- persis dengan report_data.json yang dipakai pipeline untuk membangun PDF.
-- Dipanggil dashboard sebagai: POST /rest/v1/rpc/get_daily_digest
CREATE OR REPLACE FUNCTION public.get_daily_digest(p_date DATE DEFAULT NULL)
RETURNS JSONB
LANGUAGE sql
STABLE
AS $fn$
WITH target AS (
    SELECT *
    FROM report_summaries
    WHERE (p_date IS NULL OR report_date = p_date)
    ORDER BY report_date DESC
    LIMIT 1
)
SELECT jsonb_build_object(
    'report_date',      t.report_date,
    'report_title',     t.report_title,
    'caption',          t.caption,
    'global_summary',   t.global_summary,
    'national_summary', t.national_summary,
    'pdf_url',          t.pdf_url,
    'global_national',  COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
                   'scope',       i.scope,
                   'date',        i.item_date,
                   'province',    i.province,
                   'title',       i.title,
                   'body',        i.body,
                   'source_name', i.source_name,
                   'source_url',  i.source_url,
                   'is_print',    i.is_print
               ) ORDER BY i.position)
        FROM report_items i
        WHERE i.run_id = t.run_id AND i.section = 'global_national'
    ), '[]'::jsonb),
    'regions', COALESCE((
        SELECT jsonb_agg(reg ORDER BY pos)
        FROM (
            SELECT
                r.position AS pos,
                jsonb_build_object(
                    'region_name',    r.region,
                    'region_summary', r.region_summary,
                    'demand',    COALESCE(sec.demand,    '[]'::jsonb),
                    'sectors',   COALESCE(sec.sectors,   '[]'::jsonb),
                    'inflation', COALESCE(sec.inflation, '[]'::jsonb)
                ) AS reg
            FROM report_regions r
            LEFT JOIN LATERAL (
                SELECT
                    jsonb_agg(obj ORDER BY position) FILTER (WHERE section = 'demand')    AS demand,
                    jsonb_agg(obj ORDER BY position) FILTER (WHERE section = 'sectors')   AS sectors,
                    jsonb_agg(obj ORDER BY position) FILTER (WHERE section = 'inflation') AS inflation
                FROM (
                    SELECT i.section, i.position, jsonb_build_object(
                               'category',    i.category,
                               'date',        i.item_date,
                               'province',    i.province,
                               'title',       i.title,
                               'body',        i.body,
                               'source_name', i.source_name,
                               'source_url',  i.source_url,
                               'is_print',    i.is_print
                           ) AS obj
                    FROM report_items i
                    WHERE i.run_id = r.run_id AND i.region = r.region
                ) x
            ) sec ON TRUE
            WHERE r.run_id = t.run_id
        ) y
    ), '[]'::jsonb)
)
FROM target t;
$fn$;

GRANT EXECUTE ON FUNCTION public.get_daily_digest(DATE) TO anon, authenticated;

-- ------------------------------------------------------- 5. RPC PENCARIAN
-- PostgREST tidak bisa memanggil index ekspresi GIN di atas lewat query string,
-- jadi pencarian teks disediakan sebagai function supaya index-nya terpakai.
-- Dipanggil dashboard sebagai: POST /rest/v1/rpc/search_digest
CREATE OR REPLACE FUNCTION public.search_digest(q TEXT, limit_n INT DEFAULT 50)
RETURNS SETOF report_items
LANGUAGE sql
STABLE
AS $fn$
    SELECT *
    FROM report_items
    WHERE to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(body,''))
          @@ plainto_tsquery('simple', q)
    ORDER BY report_date DESC, position ASC
    LIMIT limit_n;
$fn$;

GRANT EXECUTE ON FUNCTION public.search_digest(TEXT, INT) TO anon, authenticated;

-- ============================================================================
-- Selesai. Verifikasi: Table Editor harus memunculkan report_summaries,
-- report_regions, report_items, dan view v_daily_digest.
-- ============================================================================
