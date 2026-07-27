"""
Koran PDF Scraper — Click n Read (eperpus.dotsolution.net)
==========================================================
Script ini TERPISAH dari project existing (scrape_and_send.py).

Alur:
  1. Login ke Click n Read via HTTP (requests, bukan browser)
  2. Download PDF koran hari ini (4 koran)
  3. Ekstrak gambar per halaman dari PDF (PyMuPDF)
  4. Siap dikirim ke OCR (lihat koran_ocr.py)

Cara pakai:
  python koran_scraper.py                  -> download semua koran hari ini
  python koran_scraper.py --date 2026-07-25  -> download edisi tanggal tertentu
  python koran_scraper.py --list           -> lihat daftar koran yang dikonfigurasi
  python koran_scraper.py --extract-only   -> ekstrak gambar dari PDF yang sudah ada

Environment variables (dari .env):
  CNR_USERNAME  - Username login Click n Read
  CNR_PASSWORD  - Password login Click n Read
"""

import argparse
import datetime
import json
import os
import sys

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Konfigurasi koran
# ---------------------------------------------------------------------------
# ID koran di URL /epaperpdf/{id}/{date}
# Untuk menambah koran: buka koran di browser, catat angka di URL-nya.
#
# CATATAN: ID di bawah ini perlu diisi setelah kamu cek URL masing-masing
# koran di browser. Bisnis Indonesia sudah diketahui (812219 dari screenshot),
# sisanya perlu dicek.
NEWSPAPERS = {
    "bisnis_indonesia": {
        "name": "Bisnis Indonesia",
        "epaper_id": "812219",
        "dashboard_id": "1002",
    },
    "harian_neraca": {
        "name": "Harian Neraca",
        "epaper_id": "812043",
        "dashboard_id": "",
    },
    "harian_kontan": {
        "name": "Harian Kontan",
        "epaper_id": "812055",
        "dashboard_id": "",
    },
    "investor_daily": {
        "name": "Investor Indonesia",
        "epaper_id": "812183",
        "dashboard_id": "",
    },
}

BASE_URL = "https://eperpus.dotsolution.net"
LOGIN_URL = f"{BASE_URL}/login"  # mungkin perlu disesuaikan

# Folder output (di dalam sandbox, bukan di project existing)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KORAN_DIR = os.path.join(SCRIPT_DIR, "koran_output")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "koran_images")


# ---------------------------------------------------------------------------
# 1. Login
# ---------------------------------------------------------------------------
def create_session() -> requests.Session:
    """Login ke Click n Read, return session dengan cookies."""
    username = os.environ.get("CNR_USERNAME", "")
    password = os.environ.get("CNR_PASSWORD", "")

    if not username or not password:
        print("ERROR: CNR_USERNAME dan CNR_PASSWORD belum diisi di .env")
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    })

    # Langkah 1: Ambil halaman login (untuk dapat cookies & CSRF token)
    print("Mengakses halaman login...")
    resp = session.get(BASE_URL, timeout=15)
    print(f"  Status: {resp.status_code}")

    # Cari CSRF token dari HTML (Laravel-style _token, atau meta tag)
    import re
    csrf_token = ""
    # Coba dari hidden input
    match = re.search(r'name="_token"\s+value="([^"]+)"', resp.text)
    if not match:
        match = re.search(r'value="([^"]+)"\s+name="_token"', resp.text)
    if not match:
        # Coba dari meta tag
        match = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', resp.text)
    if match:
        csrf_token = match.group(1)
        print(f"  CSRF token ditemukan: {csrf_token[:20]}...")

    # Langkah 2: Login — endpoint return JSON, field pakai "email"
    print(f"Login sebagai {username}...")

    # Coba dua pendekatan: JSON API dan form POST
    # Pendekatan 1: JSON (banyak SPA pakai ini)
    login_json = {"email": username, "password": password}
    if csrf_token:
        login_json["_token"] = csrf_token

    headers_json = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    if csrf_token:
        headers_json["X-CSRF-TOKEN"] = csrf_token

    resp = session.post(LOGIN_URL, json=login_json, headers=headers_json,
                        timeout=15, allow_redirects=True)
    print(f"  Response: {resp.status_code}")

    try:
        data = resp.json()
        print(f"  JSON: {data}")
        if data.get("status") == "ok" or data.get("success"):
            print("  Login berhasil! (JSON)")
            return session
    except ValueError:
        pass

    # Pendekatan 2: Form POST biasa (kalau JSON gagal)
    print("  JSON login tidak berhasil, coba form POST...")
    login_form = {"email": username, "password": password}
    if csrf_token:
        login_form["_token"] = csrf_token

    resp = session.post(LOGIN_URL, data=login_form, timeout=15, allow_redirects=True)
    print(f"  Response: {resp.status_code}, URL: {resp.url}")

    try:
        data = resp.json()
        print(f"  JSON: {data}")
        if data.get("status") in ("ok", "success") or data.get("success"):
            print("  Login berhasil!")
            return session
    except ValueError:
        pass

    # Cek apakah redirect ke dashboard (login berhasil tanpa JSON)
    if "dashboard" in resp.url:
        print("  Login berhasil! (redirect)")
        return session

    # Kalau masih gagal, cek cookies
    print(f"  Cookies: {dict(session.cookies)}")
    print("  PERINGATAN: Login mungkin gagal. Lanjut coba download...")
    return session


# ---------------------------------------------------------------------------
# 2. Download PDF koran
# ---------------------------------------------------------------------------
def _find_pdf_url(html: str, page_url: str) -> str:
    """Cari URL PDF asli dari HTML halaman viewer Click n Read.

    Viewer ini pakai PDF.js dengan pola JavaScript:
      var linknya = '2026-07-26-1002-0001.pdf';
      halamanutama("https://eperpus.dotsolution.net/", linknya);

    Kita perlu tangkap KEDUA value ini — base URL dari halamanutama() dan
    filename dari var linknya — lalu gabungkan.
    """
    import re

    # Strategi 1 (utama): Tangkap pola Click n Read
    # var linknya = 'FILENAME.pdf';
    link_match = re.search(r"""var\s+linknya\s*=\s*['"]([^'"]+\.pdf)['"]""", html)
    if link_match:
        pdf_filename = link_match.group(1)
        print(f"  Ditemukan var linknya = '{pdf_filename}'")

        # Cari base URL dari halamanutama("BASE_URL", linknya)
        # myviewer.js menunjukkan: PDFViewerApplication.open(a+'custom/ori/'+b)
        # Jadi path lengkap = base + 'custom/ori/' + filename
        base_match = re.search(r"""halamanutama\s*\(\s*['"]([^'"]+)['"]""", html)
        if base_match:
            base = base_match.group(1)
            if not base.endswith("/"):
                base += "/"
            full_url = base + "custom/ori/" + pdf_filename
            print(f"  Ditemukan halamanutama base = '{base}'")
            return full_url
        else:
            return BASE_URL + "/custom/ori/" + pdf_filename

    # Strategi 2: Cari <embed>, <iframe>, <object> dengan src PDF
    for pattern in [
        r'<embed[^>]+src="([^"]+\.pdf[^"]*)"',
        r'<iframe[^>]+src="([^"]+\.pdf[^"]*)"',
        r'<object[^>]+data="([^"]+\.pdf[^"]*)"',
    ]:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)

    # Strategi 3: Cari URL .pdf generik di JavaScript
    match = re.search(r"""['"]([^'"]*\.pdf)['"]""", html)
    if match:
        return match.group(1)

    return ""


def download_pdf(session: requests.Session, newspaper_key: str, date_str: str) -> str:
    """Download PDF koran untuk tanggal tertentu. Return path file PDF."""
    paper = NEWSPAPERS.get(newspaper_key)
    if not paper:
        print(f"ERROR: Koran '{newspaper_key}' tidak ditemukan di konfigurasi")
        return ""

    epaper_id = paper["epaper_id"]
    if not epaper_id:
        print(f"SKIP: {paper['name']} — epaper_id belum diisi (cek URL di browser)")
        return ""

    viewer_url = f"{BASE_URL}/epaperpdf/{epaper_id}/{date_str}"
    print(f"\nDownload {paper['name']} ({date_str})...")
    print(f"  Viewer URL: {viewer_url}")

    try:
        # Langkah 1: Ambil halaman viewer
        resp = session.get(viewer_url, timeout=30)
        print(f"  Viewer status: {resp.status_code}, Content-Type: {resp.headers.get('content-type', '?')}")

        if resp.status_code != 200:
            print(f"  GAGAL: HTTP {resp.status_code}")
            return ""

        content_type = resp.headers.get("content-type", "")

        # Kalau ternyata langsung PDF (bukan HTML), simpan langsung
        if "pdf" in content_type or "octet" in content_type:
            print("  Langsung PDF — simpan.")
            return _save_pdf(resp.content, paper, date_str)

        # Halaman HTML — cari URL PDF asli di dalamnya
        html = resp.text

        # Simpan HTML untuk debug (bisa dihapus nanti)
        os.makedirs(KORAN_DIR, exist_ok=True)
        debug_path = os.path.join(KORAN_DIR, f"debug_{newspaper_key}.html")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  HTML viewer disimpan untuk debug: {debug_path}")

        # Langkah 2: Cari URL PDF asli dari HTML
        pdf_url = _find_pdf_url(html, viewer_url)

        if not pdf_url:
            print("  GAGAL: Tidak bisa menemukan URL PDF di dalam HTML viewer.")
            print("  Cek file debug HTML di atas, lalu share ke saya untuk analisis.")
            return ""

        # Resolve relative URL
        if pdf_url.startswith("/"):
            pdf_url = BASE_URL + pdf_url
        elif not pdf_url.startswith("http"):
            # Relative to current page
            from urllib.parse import urljoin
            pdf_url = urljoin(viewer_url, pdf_url)

        print(f"  PDF URL ditemukan: {pdf_url}")

        # Langkah 3: Download PDF asli
        resp2 = session.get(pdf_url, timeout=120, stream=True)
        print(f"  PDF download status: {resp2.status_code}, Content-Type: {resp2.headers.get('content-type', '?')}")

        if resp2.status_code != 200:
            print(f"  GAGAL download PDF: HTTP {resp2.status_code}")
            return ""

        # Simpan PDF
        return _save_pdf_stream(resp2, paper, date_str)

    except requests.RequestException as exc:
        print(f"  ERROR: {exc}")
        return ""


def _save_pdf(content: bytes, paper: dict, date_str: str) -> str:
    """Simpan bytes sebagai file PDF."""
    os.makedirs(KORAN_DIR, exist_ok=True)
    safe_name = paper["name"].replace(" ", "_").lower()
    filename = f"{date_str}_{safe_name}.pdf"
    filepath = os.path.join(KORAN_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(content)
    size_mb = len(content) / (1024 * 1024)
    print(f"  Tersimpan: {filepath} ({size_mb:.1f} MB)")
    return filepath


def _save_pdf_stream(resp: requests.Response, paper: dict, date_str: str) -> str:
    """Simpan streaming response sebagai file PDF."""
    os.makedirs(KORAN_DIR, exist_ok=True)
    safe_name = paper["name"].replace(" ", "_").lower()
    filename = f"{date_str}_{safe_name}.pdf"
    filepath = os.path.join(KORAN_DIR, filename)
    total = 0
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            total += len(chunk)
    size_mb = total / (1024 * 1024)
    print(f"  Tersimpan: {filepath} ({size_mb:.1f} MB)")
    return filepath


# ---------------------------------------------------------------------------
# 3. Ekstrak gambar dari PDF
# ---------------------------------------------------------------------------
def extract_images(pdf_path: str) -> list:
    """Ekstrak gambar per halaman dari PDF. Return list path gambar."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("ERROR: PyMuPDF belum terinstall. Jalankan: pip install pymupdf")
        return []

    if not pdf_path or not os.path.exists(pdf_path):
        print(f"File tidak ditemukan: {pdf_path}")
        return []

    doc = fitz.open(pdf_path)
    pdf_basename = os.path.splitext(os.path.basename(pdf_path))[0]
    output_dir = os.path.join(IMAGES_DIR, pdf_basename)
    os.makedirs(output_dir, exist_ok=True)

    image_paths = []
    print(f"\nEkstrak gambar dari {os.path.basename(pdf_path)} ({doc.page_count} halaman)...")

    for page_num in range(doc.page_count):
        page = doc[page_num]
        images = page.get_images(full=True)

        if not images:
            # Fallback: render halaman sebagai gambar (untuk PDF yang embed image dgn cara aneh)
            pix = page.get_pixmap(dpi=200)
            img_path = os.path.join(output_dir, f"page_{page_num + 1:02d}.png")
            pix.save(img_path)
            image_paths.append(img_path)
            print(f"  Halaman {page_num + 1}: rendered {pix.width}x{pix.height} px")
            continue

        # Ambil gambar terbesar di halaman (biasanya cuma 1 = scan koran)
        largest = None
        largest_size = 0
        for img_info in images:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
                if len(base_image["image"]) > largest_size:
                    largest = base_image
                    largest_size = len(base_image["image"])
            except Exception:
                continue

        if largest:
            ext = largest["ext"]
            img_path = os.path.join(output_dir, f"page_{page_num + 1:02d}.{ext}")
            with open(img_path, "wb") as f:
                f.write(largest["image"])
            image_paths.append(img_path)
            print(f"  Halaman {page_num + 1}: {largest['width']}x{largest['height']} px, "
                  f"{largest_size / 1024:.0f} KB")

    doc.close()
    print(f"  Total: {len(image_paths)} gambar diekstrak ke {output_dir}/")
    return image_paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Download koran dari Click n Read")
    parser.add_argument("--date", default=None, help="Tanggal edisi (YYYY-MM-DD, default: hari ini)")
    parser.add_argument("--list", action="store_true", help="Tampilkan daftar koran yang dikonfigurasi")
    parser.add_argument("--extract-only", action="store_true", help="Hanya ekstrak gambar dari PDF yang sudah ada")
    parser.add_argument("--paper", default=None, help="Hanya proses koran tertentu (key, mis: bisnis_indonesia)")
    args = parser.parse_args()

    if args.list:
        print("Koran yang dikonfigurasi:")
        for key, info in NEWSPAPERS.items():
            status = "OK" if info["epaper_id"] else "BELUM DIISI"
            print(f"  {key}: {info['name']} (epaper_id: {info['epaper_id'] or '???'}) [{status}]")
        return

    # Tentukan tanggal
    if args.date:
        date_str = args.date
    else:
        # Default: hari ini WIB
        wib = datetime.timezone(datetime.timedelta(hours=7))
        date_str = datetime.datetime.now(wib).strftime("%Y-%m-%d")

    # Tentukan koran mana yang diproses
    papers_to_process = {}
    if args.paper:
        if args.paper in NEWSPAPERS:
            papers_to_process[args.paper] = NEWSPAPERS[args.paper]
        else:
            print(f"ERROR: Koran '{args.paper}' tidak ditemukan. Gunakan --list untuk lihat daftar.")
            sys.exit(1)
    else:
        papers_to_process = NEWSPAPERS

    if args.extract_only:
        # Hanya ekstrak gambar dari PDF yang sudah ada
        for key, info in papers_to_process.items():
            safe_name = info["name"].replace(" ", "_").lower()
            pdf_path = os.path.join(KORAN_DIR, f"{date_str}_{safe_name}.pdf")
            if os.path.exists(pdf_path):
                extract_images(pdf_path)
            else:
                print(f"PDF belum ada: {pdf_path} — download dulu tanpa --extract-only")
        return

    # Login dan download
    session = create_session()

    results = {}
    for key, info in papers_to_process.items():
        pdf_path = download_pdf(session, key, date_str)
        if pdf_path:
            image_paths = extract_images(pdf_path)
            results[key] = {
                "pdf_path": pdf_path,
                "image_paths": image_paths,
                "page_count": len(image_paths),
            }

    # Simpan manifest (dipakai oleh koran_ocr.py)
    os.makedirs(KORAN_DIR, exist_ok=True)
    manifest_path = os.path.join(KORAN_DIR, f"{date_str}_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nManifest disimpan: {manifest_path}")

    # Ringkasan
    print(f"\n{'='*50}")
    print(f"Ringkasan download {date_str}:")
    for key, info in results.items():
        print(f"  {NEWSPAPERS[key]['name']}: {info['page_count']} halaman")
    failed = [k for k in papers_to_process if k not in results]
    if failed:
        print(f"  GAGAL: {', '.join(NEWSPAPERS[k]['name'] for k in failed)}")


if __name__ == "__main__":
    main()
