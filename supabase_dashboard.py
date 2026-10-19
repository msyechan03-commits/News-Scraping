"""
Supabase Dashboard Writer — News Scraping AI
============================================
Modul TAMBAHAN (tidak mengubah supabase_client.py maupun pipeline.py).

Tugasnya satu: menyimpan hasil akhir rangkuman (report_data) ke tabel
report_summaries / report_regions / report_items, supaya web dashboard bisa
menariknya lewat REST API Supabase tanpa perlu scraping ulang.

Latar belakang:
    Skema v1 menyimpan artikel mentah RSS (articles), skornya (scores), dan
    penanda artikel mana yang dipakai AI (ai_decisions). Yang TIDAK tersimpan
    adalah judul dan narasi hasil tulisan AI, serta seluruh item yang berasal
    dari koran cetak (OCR) karena item cetak tidak punya baris di articles.
    Padahal justru itu yang dibutuhkan dashboard. Modul ini menutup celah itu.

Prasyarat:
    Jalankan sql/002_dashboard_report_items.sql dulu di Supabase SQL Editor.

Pemakaian (sudah dipasang di pipeline_v2.py):
    from supabase_dashboard import save_report
    save_report(tracker, run_id, "2026-10-19", data, pdf_url=pdf_url)

Semua fungsi FAIL-SAFE: error di-log ke stderr, tidak pernah raise.
"""
import os
import sys
import json
from typing import Optional

SECTIONS = ("demand", "sectors", "inflation")


def _is_print_source(source_name: str) -> bool:
    """Item koran cetak ditandai '(CETAK)' pada source_name oleh prompt pipeline."""
    return "(CETAK)" in (source_name or "").upper()


def _item_row(run_id: str, report_date: str, section: str, region: str,
              position: int, raw: dict) -> dict:
    source_name = raw.get("source_name") or ""
    placement = ("global_national" if section == "global_national"
                 else f"regions.{region.lower()}.{section}")
    return {
        "run_id": run_id,
        "report_date": report_date,
        "placement": placement,
        "section": section,
        "region": region,
        "scope": raw.get("scope") or region or "",
        "province": raw.get("province") or "",
        "category": raw.get("category") or raw.get("component") or "",
        "item_date": raw.get("date") or "",
        "title": (raw.get("title") or "")[:1000],
        "body": (raw.get("body") or "")[:5000],
        "source_name": source_name[:300],
        "source_url": (raw.get("source_url") or "")[:2000],
        "is_print": _is_print_source(source_name),
        "position": position,
    }


def build_rows(run_id: str, report_date: str, report_data: dict,
               pdf_url: Optional[str] = None) -> tuple:
    """Ubah report_data jadi tiga list baris siap insert.
    Return (summary_row, region_rows, item_rows). Tidak menyentuh jaringan,
    sehingga bisa diuji offline."""
    summary = {
        "run_id": run_id,
        "report_date": report_date,
        "report_title": report_data.get("report_title") or "",
        "caption": report_data.get("caption") or "",
        "global_summary": report_data.get("global_summary") or "",
        "national_summary": report_data.get("national_summary") or "",
        "pdf_url": pdf_url or "",
    }

    item_rows = []
    for pos, raw in enumerate(report_data.get("global_national") or []):
        item_rows.append(_item_row(run_id, report_date, "global_national", "", pos, raw))

    region_rows = []
    for r_pos, region in enumerate(report_data.get("regions") or []):
        name = region.get("region_name") or ""
        if not name:
            continue
        region_rows.append({
            "run_id": run_id,
            "report_date": report_date,
            "region": name,
            "region_summary": region.get("region_summary") or "",
            "position": r_pos,
        })
        for section in SECTIONS:
            for pos, raw in enumerate(region.get(section) or []):
                item_rows.append(_item_row(run_id, report_date, section, name, pos, raw))

    return summary, region_rows, item_rows


def save_report(tracker, run_id: str, report_date: str, report_data: dict,
                pdf_url: Optional[str] = None) -> bool:
    """Tulis report_data ke tiga tabel dashboard. Return True bila sukses.

    tracker: instance SupabaseTracker yang sudah connected (punya .client).
    Idempoten: baris lama untuk run_id yang sama dihapus dulu, jadi aman
    dijalankan ulang untuk run yang sama.
    """
    if tracker is None or not getattr(tracker, "enabled", False) or not run_id:
        print("save_report: tracker nonaktif / run_id kosong — dilewati", file=sys.stderr)
        return False

    client = tracker.client
    summary, region_rows, item_rows = build_rows(run_id, report_date, report_data, pdf_url)

    try:
        # Bersihkan dulu supaya re-run tidak menggandakan baris.
        for table in ("report_items", "report_regions", "report_summaries"):
            client.table(table).delete().eq("run_id", run_id).execute()

        client.table("report_summaries").insert(summary).execute()

        if region_rows:
            client.table("report_regions").insert(region_rows).execute()

        # Chunk 500 baris per insert, batas batch Supabase.
        for i in range(0, len(item_rows), 500):
            client.table("report_items").insert(item_rows[i:i + 500]).execute()

        n_print = sum(1 for r in item_rows if r["is_print"])
        print(f"save_report: {len(item_rows)} item ({n_print} dari koran cetak), "
              f"{len(region_rows)} wilayah tersimpan untuk {report_date}")
        return True
    except Exception as exc:
        print(f"save_report ERROR: {exc}", file=sys.stderr)
        return False


def upload_report_json(tracker, report_date: str, report_data: dict,
                       bucket: str = "markdown") -> Optional[str]:
    """Opsional: unggah report_data.json mentah ke Storage sebagai arsip, dan
    sebagai cadangan kalau dashboard lebih suka menarik satu file statis.
    Pakai bucket publik yang sudah ada (default 'markdown')."""
    if tracker is None or not getattr(tracker, "enabled", False):
        return None
    tmp = f"/tmp/report_{report_date}.json"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        return tracker.upload_file(bucket, tmp, f"{report_date}.json", "application/json")
    except Exception as exc:
        print(f"upload_report_json ERROR: {exc}", file=sys.stderr)
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Uji offline: python supabase_dashboard.py output/report_data.json
# Mencetak jumlah baris yang akan dibuat, tanpa menyentuh database.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "output/report_data.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    s, regions, items = build_rows("00000000-0000-0000-0000-000000000000",
                                   "2026-01-01", data, pdf_url="dummy")
    print(f"report_summaries : 1 baris  (judul: {s['report_title'][:60]})")
    print(f"report_regions   : {len(regions)} baris -> {[r['region'] for r in regions]}")
    print(f"report_items     : {len(items)} baris")
    by_section = {}
    for it in items:
        by_section[it["section"]] = by_section.get(it["section"], 0) + 1
    print(f"  per section    : {by_section}")
    print(f"  dari cetak     : {sum(1 for i in items if i['is_print'])}")
    print(f"  punya URL      : {sum(1 for i in items if i['source_url'])}")
    print("\nContoh satu baris report_items:")
    if items:
        print(json.dumps(items[0], ensure_ascii=False, indent=2)[:700])
