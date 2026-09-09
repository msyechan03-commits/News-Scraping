"""
Preflight check Supabase — News Scraping AI
============================================
Jalankan SEBELUM push ke production untuk memastikan:
  1. Credential terbaca dari .env
  2. Koneksi ke Supabase berhasil (key masih valid, belum di-rotate)
  3. 5 tabel ada dan bisa ditulis oleh service_role
  4. 3 storage bucket ada dan bisa di-upload

Cara pakai:
    cd ~/Documents/News-Git/News-Scraping
    python check_supabase.py

Script ini TIDAK pernah mencetak nilai credential, hanya panjang & prefix-nya.
Baris uji yang dibuat akan dihapus kembali di akhir (kecuali ada error).
"""
import os
import sys
import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # fallback: baca .env manual
    if os.path.exists(".env"):
        for line in open(".env", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

TABLES = ["runs", "articles", "scores", "ai_decisions", "logs"]
BUCKETS = ["pdfs", "markdown", "logs"]

ok = True


def step(msg):
    print(f"\n{'=' * 60}\n{msg}\n{'=' * 60}")


def good(msg):
    print(f"  [OK]   {msg}")


def bad(msg):
    global ok
    ok = False
    print(f"  [GAGAL] {msg}")


# --------------------------------------------------------------- 1. credential
step("1. Credential")
url = os.environ.get("SUPABASE_URL", "").strip()
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
enable_sb = os.environ.get("ENABLE_SECOND_BRAIN", "").strip()

if url:
    good(f"SUPABASE_URL terbaca — host {url.split('//')[-1].rstrip('/')}")
else:
    bad("SUPABASE_URL kosong / tidak ada di .env")

if key:
    good(f"SUPABASE_SERVICE_ROLE_KEY terbaca — {len(key)} karakter, prefix '{key[:6]}...'")
else:
    bad("SUPABASE_SERVICE_ROLE_KEY kosong / tidak ada di .env")

print(f"  [INFO] ENABLE_SECOND_BRAIN = {enable_sb or '(kosong = nonaktif)'}")

if not ok:
    print("\nHentikan dulu: lengkapi .env sebelum lanjut.")
    sys.exit(1)

# --------------------------------------------------------------- 2. koneksi
step("2. Koneksi Supabase")
try:
    from supabase import create_client
except ImportError:
    bad("paket 'supabase' belum ter-install. Jalankan: pip install -r requirements.txt")
    sys.exit(1)

try:
    client = create_client(url, key)
    good("client Supabase berhasil dibuat")
except Exception as exc:
    bad(f"gagal membuat client: {exc}")
    sys.exit(1)

# --------------------------------------------------------------- 3. tabel
step("3. Tabel (baca)")
for tbl in TABLES:
    try:
        r = client.table(tbl).select("id", count="exact").limit(1).execute()
        good(f"{tbl:14} bisa dibaca — {r.count} baris tersimpan")
    except Exception as exc:
        msg = str(exc)
        hint = ""
        if "401" in msg or "JWT" in msg or "Invalid" in msg:
            hint = "  -> key mungkin sudah di-rotate. Ambil ulang di Settings > API Keys."
        elif "does not exist" in msg or "42P01" in msg:
            hint = "  -> tabel belum dibuat. Jalankan SQL di SETUP_SUPABASE_GUIDE.md."
        elif "403" in msg:
            hint = "  -> akses ditolak (cek key service_role, bukan anon/publishable)."
        bad(f"{tbl:14} {msg[:130]}{hint}")

# --------------------------------------------------------------- 4. tulis-hapus
step("4. Tabel runs (tulis & hapus baris uji)")
test_run_id = None
try:
    resp = client.table("runs").insert({
        "date": datetime.date.today().isoformat(),
        "status": "failed",
        "error_stage": "preflight",
        "error_message": "baris uji check_supabase.py — aman dihapus",
    }).execute()
    test_run_id = resp.data[0]["id"] if resp.data else None
    good(f"insert berhasil — run_id {test_run_id}")
except Exception as exc:
    bad(f"insert ke runs gagal: {str(exc)[:160]}")

if test_run_id:
    try:
        client.table("logs").insert({
            "run_id": test_run_id, "level": "INFO",
            "stage": "preflight", "message": "uji tulis logs", "metadata": {},
        }).execute()
        good("insert ke logs berhasil")
    except Exception as exc:
        bad(f"insert ke logs gagal: {str(exc)[:160]}")
    try:
        client.table("runs").delete().eq("id", test_run_id).execute()
        good("baris uji berhasil dihapus kembali")
    except Exception as exc:
        bad(f"gagal hapus baris uji {test_run_id} — hapus manual: {str(exc)[:120]}")

# --------------------------------------------------------------- 5. storage
step("5. Storage bucket")
try:
    found = {getattr(b, "name", b if isinstance(b, str) else str(b))
             for b in client.storage.list_buckets()}
    for b in BUCKETS:
        if b in found:
            good(f"bucket '{b}' ada")
        else:
            bad(f"bucket '{b}' TIDAK ada — buat di Storage > New bucket")
except Exception as exc:
    bad(f"gagal membaca daftar bucket: {str(exc)[:160]}")

try:
    probe = b"preflight check_supabase.py"
    name = "_preflight_check.txt"
    try:
        client.storage.from_("pdfs").remove([name])
    except Exception:
        pass
    client.storage.from_("pdfs").upload(name, probe, {"content-type": "text/plain"})
    good("upload uji ke bucket 'pdfs' berhasil")
    client.storage.from_("pdfs").remove([name])
    good("berkas uji berhasil dihapus kembali")
except Exception as exc:
    bad(f"upload uji ke bucket 'pdfs' gagal: {str(exc)[:160]}")

# --------------------------------------------------------------- ringkasan
step("RINGKASAN")
if ok:
    print("  Semua pemeriksaan LULUS. Supabase siap dipakai di production.\n")
    print("  Langkah berikutnya: set 3 GitHub Secrets, lalu commit & push.")
    sys.exit(0)
else:
    print("  Ada pemeriksaan yang GAGAL. Perbaiki dulu sebelum push ke production.\n")
    print("  Rujukan: SETUP_SUPABASE_GUIDE.md (SQL + bucket) dan")
    print("           GUIDE_GITHUB_SECRETS.md (secrets + rotate key).")
    sys.exit(1)
