"""
Supabase Archive Writer — News Scraping AI
==========================================
Modul TAMBAHAN. Tidak mengubah supabase_client.py maupun pipeline.py.

Tugasnya menyimpan sisa hasil olahan pipeline yang selama ini hilang begitu
run selesai:

  1. Teks OCR koran per halaman. Pipeline menghapus gambar dan PDF koran untuk
     hemat disk, dan teksnya hanya dipakai sebagai input prompt lalu dibuang.
     Setelah modul ini aktif, seluruh teks koran tersimpan permanen.
  2. Narasi Second Brain, bila suatu saat diaktifkan kembali.
  3. Payload mentah per run (report_data, entri RSS, teks koran, caption)
     sebagai JSONB, supaya tidak ada hasil yang hilang meski strukturnya
     berubah di kemudian hari.

Prasyarat: jalankan sql/003_full_archive.sql di Supabase SQL Editor.

Semua fungsi FAIL-SAFE: error di-log ke stderr, tidak pernah raise, sehingga
pipeline tetap jalan walau Supabase sedang bermasalah.
"""
import json
import sys
from typing import Optional

# Batas aman ukuran satu baris payload JSONB. Di atas ini payload dipotong
# supaya tidak membebani free tier Supabase (500 MB database).
MAX_PAYLOAD_BYTES = 2_000_000


def _ready(tracker, run_id: str, what: str) -> bool:
    if tracker is None or not getattr(tracker, "enabled", False) or not run_id:
        print(f"{what}: tracker nonaktif / run_id kosong — dilewati", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# KORAN
# ---------------------------------------------------------------------------
def save_koran(tracker, run_id: str, report_date: str, results: list) -> bool:
    """Simpan hasil OCR koran ke koran_editions + koran_pages.

    results: list dict keluaran ocr_newspaper(), masing-masing berisi
        {"newspaper": nama, "key": kunci, "page_count": n,
         "ocr_pages": {"page_1": teks, ...}, "ocr_success": n}
    """
    if not _ready(tracker, run_id, "save_koran"):
        return False
    if not results:
        print("save_koran: tidak ada hasil OCR koran untuk disimpan")
        return True

    client = tracker.client
    editions, pages = [], []

    for res in results:
        key = res.get("key") or ""
        name = res.get("newspaper") or key
        ocr_pages = res.get("ocr_pages") or {}
        total_chars = sum(len(v or "") for v in ocr_pages.values())

        editions.append({
            "run_id": run_id,
            "report_date": report_date,
            "paper_key": key,
            "paper_name": name,
            "page_count": res.get("page_count", 0),
            "ocr_pages": res.get("ocr_success", len(ocr_pages)),
            "ocr_chars": total_chars,
            "pdf_filename": res.get("pdf_filename", ""),
        })

        for page_key, text in ocr_pages.items():
            try:
                page_no = int(str(page_key).replace("page_", ""))
            except ValueError:
                continue
            pages.append({
                "run_id": run_id,
                "report_date": report_date,
                "paper_key": key,
                "paper_name": name,
                "page_number": page_no,
                "ocr_text": text or "",
                "char_count": len(text or ""),
            })

    try:
        client.table("koran_pages").delete().eq("run_id", run_id).execute()
        client.table("koran_editions").delete().eq("run_id", run_id).execute()

        if editions:
            client.table("koran_editions").insert(editions).execute()
        for i in range(0, len(pages), 200):
            client.table("koran_pages").insert(pages[i:i + 200]).execute()

        total_chars = sum(p["char_count"] for p in pages)
        papers = ", ".join(e["paper_name"] for e in editions)
        print(f"save_koran: {len(pages)} halaman dari {len(editions)} koran "
              f"({papers}), {total_chars} karakter tersimpan")
        return True
    except Exception as exc:
        print(f"save_koran ERROR: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# SECOND BRAIN
# ---------------------------------------------------------------------------
def save_second_brain(tracker, run_id: str, report_date: str, sb_result: dict) -> bool:
    """Simpan narasi Second Brain ke second_brain_docs."""
    if not _ready(tracker, run_id, "save_second_brain"):
        return False
    if not sb_result:
        return True

    md = sb_result.get("markdown") or ""
    row = {
        "run_id": run_id,
        "report_date": report_date,
        "markdown": md,
        "char_count": len(md),
        "input_tokens": sb_result.get("input_tokens", 0) or 0,
        "output_tokens": sb_result.get("output_tokens", 0) or 0,
        "error": (sb_result.get("error") or "")[:1000],
    }
    try:
        tracker.client.table("second_brain_docs").delete().eq("run_id", run_id).execute()
        tracker.client.table("second_brain_docs").insert(row).execute()
        print(f"save_second_brain: {len(md)} karakter tersimpan")
        return True
    except Exception as exc:
        print(f"save_second_brain ERROR: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# PAYLOAD MENTAH
# ---------------------------------------------------------------------------
def save_payload(tracker, run_id: str, report_date: str, kind: str, obj) -> bool:
    """Arsipkan satu objek apa pun sebagai JSONB di run_payloads.

    kind: 'report_data' | 'rss_entries' | 'koran_text' | 'caption' | bebas.
    """
    if not _ready(tracker, run_id, f"save_payload[{kind}]"):
        return False
    if obj is None:
        return True

    try:
        raw = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception as exc:
        print(f"save_payload[{kind}] ERROR serialisasi: {exc}", file=sys.stderr)
        return False

    size = len(raw.encode("utf-8"))
    payload = obj
    if size > MAX_PAYLOAD_BYTES:
        payload = {
            "_truncated": True,
            "_original_size_bytes": size,
            "_note": f"payload melebihi {MAX_PAYLOAD_BYTES} byte, hanya cuplikan disimpan",
            "_head": raw[:200_000],
        }
        print(f"save_payload[{kind}]: {size} byte terlalu besar, dipotong", file=sys.stderr)

    row = {
        "run_id": run_id,
        "report_date": report_date,
        "kind": kind,
        "payload": payload,
        "size_bytes": size,
    }
    try:
        tracker.client.table("run_payloads").delete() \
            .eq("run_id", run_id).eq("kind", kind).execute()
        tracker.client.table("run_payloads").insert(row).execute()
        print(f"save_payload[{kind}]: {size} byte tersimpan")
        return True
    except Exception as exc:
        print(f"save_payload[{kind}] ERROR: {exc}", file=sys.stderr)
        return False


def save_rss_entries(tracker, run_id: str, report_date: str, entries: list) -> bool:
    """Arsipkan entri RSS beserta skornya. Field berat dibuang supaya hemat."""
    if not entries:
        return True
    slim = []
    for e in entries:
        s = e.get("_scoring", {}) or {}
        slim.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "source_name": e.get("source_name", ""),
            "domain": e.get("domain", ""),
            "date_label": e.get("date_label", ""),
            "summary": (e.get("summary") or "")[:1200],
            "score": s.get("score"),
            "bucket": s.get("bucket"),
            "tier": s.get("tier"),
            "cat_hits": s.get("cat_hits"),
            "reg_hits": s.get("reg_hits"),
            "ent_hits": s.get("ent_hits"),
            "mat_hits": s.get("mat_hits"),
        })
    return save_payload(tracker, run_id, report_date, "rss_entries", slim)
