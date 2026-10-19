"""
Generator pipeline_v2.py
========================
pipeline_v2.py adalah salinan pipeline.py plus hook penyimpanan ke Supabase.
Karena isinya salinan, setiap kali pipeline.py berubah (misalnya setelah
git pull dari rekan), pipeline_v2.py jadi basi.

Jalankan script ini untuk membangun ulang pipeline_v2.py dari pipeline.py
terbaru:

    python make_pipeline_v2.py

Script berhenti dengan pesan jelas bila salah satu titik sisip tidak lagi
ditemukan, jadi tidak akan diam-diam menghasilkan file yang salah.
"""
import sys

SRC, DST = "pipeline.py", "pipeline_v2.py"

HEADER = '''"""PIPELINE V2 — salinan pipeline.py + penyimpanan seluruh hasil ke Supabase.

File ini DIBANGUN OTOMATIS oleh make_pipeline_v2.py. Jangan diedit manual;
edit pipeline.py atau make_pipeline_v2.py lalu bangun ulang.

Jalankan untuk menguji:
    python pipeline_v2.py generate

Prasyarat SQL: sql/002_dashboard_report_items.sql lalu sql/003_full_archive.sql
"""
'''

# (anchor, tambahan, posisi, label)
PATCHES = [
    ('    from supabase_client import SupabaseTracker\n    from second_brain import generate_second_brain\n',
     '    from supabase_dashboard import save_report\n'
     '    from supabase_archive import (save_koran, save_second_brain,\n'
     '                                  save_payload, save_rss_entries)\n',
     'after', 'import modul tambahan'),

    ('def fetch_koran_articles(date_str: str) -> str:',
     '# v2: hasil OCR mentah per koran, diisi fetch_koran_articles, dibaca cmd_generate.\n'
     '_KORAN_RESULTS = []\n\n\n',
     'before', 'penampung hasil OCR'),

    ('    session = cnr_login()\n\n    all_ocr_texts = []',
     '    _KORAN_RESULTS.clear()\n',
     'split_reset', 'reset penampung'),

    ('        result = ocr_newspaper(client, key, image_paths, info["name"])\n',
     '\n        # v2: simpan hasil OCR mentah supaya bisa diarsipkan ke database.\n'
     '        result["key"] = key\n'
     '        result["newspaper"] = info["name"]\n'
     '        result["page_count"] = len(image_paths)\n'
     '        result["pdf_filename"] = os.path.basename(pdf_path or "")\n'
     '        _KORAN_RESULTS.append(result)\n',
     'after', 'kumpulkan hasil OCR'),

    ('    koran_text = fetch_koran_articles(date_iso)\n',
     '\n    # v2: arsipkan teks OCR koran per halaman ke DB. Gambar dan PDF koran\n'
     '    # dihapus pipeline, jadi tanpa ini teksnya hilang setelah run selesai.\n'
     '    if tracker:\n'
     '        save_koran(tracker, run_id, date_iso, _KORAN_RESULTS)\n'
     '        save_payload(tracker, run_id, date_iso, "koran_text", koran_text)\n',
     'after', 'arsip koran'),

    ('            tracker.insert_ai_decisions(run_id, article_id_map, entries, data)\n',
     '            save_payload(tracker, run_id, date_iso, "report_data", data)\n'
     '            save_rss_entries(tracker, run_id, date_iso, entries)\n',
     'after', 'arsip report_data dan entri RSS'),

    ('    with open(os.path.join(captions_dir, f"{date_iso}.txt"), "w", encoding="utf-8") as f:\n        f.write(caption)\n',
     '\n    # v2: caption ikut diarsipkan ke DB.\n'
     '    if tracker:\n'
     '        save_payload(tracker, run_id, date_iso, "caption", caption)\n',
     'after', 'arsip caption'),

    ('            sb_out_tokens = sb.get("output_tokens", 0)\n',
     '            save_second_brain(tracker, run_id, date_iso, sb)\n',
     'after', 'arsip Second Brain'),

    ('            tracker.finish_run(\n                run_id,\n                status="success",',
     '            # v2: tulis hasil rangkuman ke tabel dashboard (report_summaries /\n'
     '            # report_regions / report_items) supaya bisa ditarik web dashboard.\n'
     '            save_report(tracker, run_id, date_iso, data, pdf_url=pdf_url)\n',
     'before', 'tulis tabel dashboard'),
]


def main():
    try:
        src = open(SRC, encoding="utf-8").read()
    except FileNotFoundError:
        print(f"GAGAL: {SRC} tidak ditemukan. Jalankan dari folder proyek.")
        sys.exit(1)

    applied = []
    for anchor, addition, where, label in PATCHES:
        n = src.count(anchor)
        if n != 1:
            print(f"GAGAL pada patch '{label}': titik sisip ditemukan {n} kali, "
                  f"seharusnya tepat 1.")
            print("pipeline.py sepertinya berubah. Perbarui anchor di make_pipeline_v2.py.")
            print(f"\nAnchor yang dicari:\n{anchor}")
            sys.exit(1)
        if where == "after":
            src = src.replace(anchor, anchor + addition)
        elif where == "before":
            src = src.replace(anchor, addition + anchor)
        elif where == "split_reset":
            head, tail = anchor.split("\n\n", 1)
            src = src.replace(anchor, head + "\n\n" + addition + tail)
        applied.append(label)

    open(DST, "w", encoding="utf-8").write(HEADER + src)
    print(f"{DST} dibangun ulang dari {SRC}.")
    for i, label in enumerate(applied, 1):
        print(f"  {i}. {label}")
    print("\nVerifikasi: python -m py_compile pipeline_v2.py")


if __name__ == "__main__":
    main()
