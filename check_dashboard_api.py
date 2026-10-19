"""
Preflight check tabel Supabase untuk dashboard — READ ONLY.
===========================================================
Memeriksa apakah migrasi 002 dan 003 sudah dijalankan, apakah datanya sudah
terisi, dan apakah hak akses anon sudah benar. Script ini HANYA membaca.

Cara pakai:
    python check_dashboard_api.py
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
anon = os.environ.get("SUPABASE_ANON_KEY", "").strip()

if not url or not key:
    print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY belum ada di .env")
    sys.exit(1)

try:
    from supabase import create_client
except ImportError:
    print("paket 'supabase' belum ter-install. Jalankan: pip install -r requirements.txt")
    sys.exit(1)

client = create_client(url, key)
ok = True


def head(t):
    print("\n" + "=" * 62 + f"\n{t}\n" + "=" * 62)


def count(table):
    global ok
    try:
        r = client.table(table).select("*", count="exact").limit(1).execute()
        print(f"  [OK]   {table:<20} {r.count} baris")
        return r.count
    except Exception as exc:
        ok = False
        msg = str(exc)[:110]
        print(f"  [GAGAL] {table:<20} {msg}")
        return None


head("1. Tabel dashboard (migrasi 002)")
for t in ("report_summaries", "report_regions", "report_items"):
    count(t)

head("2. Tabel arsip penuh (migrasi 003)")
for t in ("koran_editions", "koran_pages", "second_brain_docs", "run_payloads"):
    count(t)

head("3. RPC")
for fn, args in (("get_daily_digest", {}),
                 ("search_digest", {"q": "ekonomi", "limit_n": 3}),
                 ("search_koran", {"q": "ekonomi", "limit_n": 3})):
    try:
        r = client.rpc(fn, args).execute()
        d = r.data
        if fn == "get_daily_digest":
            if not d:
                print(f"  [INFO] {fn}: jalan, tetapi belum ada laporan tersimpan.")
                print("         Jalankan 'python pipeline_v2.py generate' dulu.")
            else:
                regions = d.get("regions") or []
                total = len(d.get("global_national") or []) + sum(
                    len(r_.get(s) or []) for r_ in regions for s in ("demand", "sectors", "inflation"))
                print(f"  [OK]   {fn}: tanggal {d.get('report_date')}, "
                      f"{total} item, {len(regions)} wilayah")
        else:
            print(f"  [OK]   {fn}: {len(d or [])} hasil")
    except Exception as exc:
        ok = False
        print(f"  [GAGAL] {fn}: {str(exc)[:110]}")

head("4. Arsip koran terakhir")
try:
    r = client.table("koran_editions").select(
        "report_date,paper_name,page_count,ocr_pages,ocr_chars"
    ).order("report_date", desc=True).limit(6).execute()
    if not r.data:
        print("  [INFO] belum ada arsip koran. Normal kalau pipeline_v2 belum dijalankan.")
    for row in r.data:
        print(f"  {row['report_date']}  {row['paper_name']:<20} "
              f"{row['ocr_pages']}/{row['page_count']} halaman, {row['ocr_chars']} karakter")
except Exception as exc:
    print(f"  [GAGAL] {str(exc)[:110]}")

head("5. Hak akses anon (yang dipakai dashboard)")
if not anon:
    print("  [LEWAT] SUPABASE_ANON_KEY belum ada di .env.")
    print("          Ambil di Project Settings > API > anon public, lalu tambahkan")
    print("          baris SUPABASE_ANON_KEY=... supaya jalur dashboard ikut teruji.")
else:
    anon_client = create_client(url, anon)
    for t in ("report_items", "koran_pages", "koran_editions"):
        try:
            anon_client.table(t).select("id").limit(1).execute()
            print(f"  [OK]   anon bisa baca {t}")
        except Exception as exc:
            ok = False
            print(f"  [GAGAL] anon tidak bisa baca {t}: {str(exc)[:90]}")
    for t in ("articles", "scores", "logs", "run_payloads"):
        try:
            anon_client.table(t).select("id").limit(1).execute()
            ok = False
            print(f"  [BAHAYA] anon TERNYATA bisa baca '{t}'. Seharusnya tertutup.")
        except Exception:
            print(f"  [OK]   anon tidak bisa baca {t}, sesuai harapan")

print("\n" + ("Semua siap." if ok else "Ada yang perlu dibereskan di atas."))
