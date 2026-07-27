"""
OCR Koran via Claude Vision API
================================
Script ini TERPISAH dari project existing (scrape_and_send.py).

Alur:
  1. Baca manifest dari koran_scraper.py (daftar gambar per koran)
  2. Kirim gambar per halaman ke Claude Vision API
  3. Ekstrak teks berita dari tiap halaman
  4. Gabungkan jadi satu output teks per koran

Cara pakai:
  python koran_ocr.py                       -> OCR semua koran hari ini
  python koran_ocr.py --date 2026-07-25     -> OCR koran tanggal tertentu
  python koran_ocr.py --paper bisnis_indonesia  -> OCR satu koran saja
  python koran_ocr.py --test-single gambar.png  -> Test OCR satu gambar

Environment variables (dari .env):
  ANTHROPIC_API_KEY  - API key Anthropic (sama dengan yang dipakai project existing)

Estimasi biaya:
  ~9 halaman/koran × 4 koran = ~36 halaman/hari
  ~$0.01/halaman (Claude Vision, gambar ~3MB) = ~$0.36/hari = ~$11/bulan
"""

import base64
import datetime
import json
import os
import sys
import time

import anthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KORAN_DIR = os.path.join(SCRIPT_DIR, "koran_output")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "koran_images")
OCR_DIR = os.path.join(SCRIPT_DIR, "koran_ocr_output")


MAX_IMAGE_DIMENSION = 1600  # Resize ke maks 1600px sisi terpanjang (hemat ~60% token Vision)


def _resize_image_if_needed(image_path: str) -> bytes:
    """Resize gambar kalau lebih besar dari MAX_IMAGE_DIMENSION. Return bytes JPEG."""
    from PIL import Image
    import io

    with Image.open(image_path) as img:
        w, h = img.size
        if max(w, h) <= MAX_IMAGE_DIMENSION:
            # Sudah kecil, baca file asli
            with open(image_path, "rb") as f:
                return f.read(), None  # None = pakai media_type asli

        # Hitung skala proporsional
        scale = MAX_IMAGE_DIMENSION / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        print(f"    resize {w}x{h} → {new_w}x{new_h}", end="", flush=True)

        img_resized = img.resize((new_w, new_h), Image.LANCZOS)
        # Convert ke RGB (kalau RGBA/palette) lalu simpan sebagai JPEG
        if img_resized.mode != "RGB":
            img_resized = img_resized.convert("RGB")
        buf = io.BytesIO()
        img_resized.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"


def _image_to_base64(image_path: str) -> tuple:
    """Baca gambar, resize kalau perlu, return (base64_string, media_type)."""
    ext = os.path.splitext(image_path)[1].lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    original_media_type = media_types.get(ext, "image/png")

    img_bytes, override_media_type = _resize_image_if_needed(image_path)
    media_type = override_media_type or original_media_type

    data = base64.standard_b64encode(img_bytes).decode("ascii")
    return data, media_type


def ocr_single_page(client: anthropic.Anthropic, image_path: str, page_num: int,
                     newspaper_name: str) -> str:
    """OCR satu halaman koran via Claude Vision. Return teks terekstrak."""
    b64_data, media_type = _image_to_base64(image_path)
    file_size_mb = os.path.getsize(image_path) / (1024 * 1024)

    print(f"  Halaman {page_num}: {os.path.basename(image_path)} ({file_size_mb:.1f} MB)...", end=" ", flush=True)

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",  # Haiku cukup untuk OCR, jauh lebih murah
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Ini halaman {page_num} dari koran {newspaper_name}. "
                            "Ekstrak SEMUA teks yang bisa dibaca dari halaman koran ini. "
                            "Tulis ulang teks berita lengkap per artikel — judul, subjudul, "
                            "dan isi berita. Pisahkan antar artikel dengan baris kosong dan "
                            "tanda '---'. Abaikan iklan. Tulis dalam bahasa aslinya "
                            "(bahasa Indonesia). Jangan tambahkan komentar atau analisis, "
                            "cukup teks asli yang terekstrak."
                        ),
                    },
                ],
            }],
        )

        text = resp.content[0].text
        tokens = resp.usage.input_tokens + resp.usage.output_tokens
        print(f"OK ({len(text)} karakter, {tokens} tokens)")
        return text

    except anthropic.APIError as exc:
        print(f"ERROR: {exc}")
        return ""


def ocr_newspaper(client: anthropic.Anthropic, newspaper_key: str,
                   image_paths: list, newspaper_name: str) -> dict:
    """OCR semua halaman satu koran. Return dict dengan teks per halaman."""
    print(f"\nOCR {newspaper_name} ({len(image_paths)} halaman)...")

    pages = {}
    for i, img_path in enumerate(image_paths):
        page_num = i + 1
        text = ocr_single_page(client, img_path, page_num, newspaper_name)
        if text:
            pages[f"page_{page_num}"] = text

        # Rate limiting: tunggu sebentar antar request
        if i < len(image_paths) - 1:
            time.sleep(0.5)

    return {
        "newspaper": newspaper_name,
        "key": newspaper_key,
        "page_count": len(image_paths),
        "ocr_pages": pages,
        "ocr_success": len(pages),
    }


def run_ocr(date_str: str, paper_filter: str = None):
    """Jalankan OCR untuk semua koran di manifest tanggal tertentu."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY belum diisi di .env")
        sys.exit(1)

    manifest_path = os.path.join(KORAN_DIR, f"{date_str}_manifest.json")
    if not os.path.exists(manifest_path):
        print(f"Manifest tidak ditemukan: {manifest_path}")
        print(f"Jalankan koran_scraper.py dulu untuk download PDF.")
        sys.exit(1)

    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    client = anthropic.Anthropic(api_key=api_key)
    os.makedirs(OCR_DIR, exist_ok=True)

    all_results = {}
    total_pages = 0

    for key, info in manifest.items():
        if paper_filter and key != paper_filter:
            continue

        # Import newspaper name dari koran_scraper
        from koran_scraper import NEWSPAPERS
        name = NEWSPAPERS.get(key, {}).get("name", key)

        image_paths = info.get("image_paths", [])
        if not image_paths:
            print(f"SKIP {name}: tidak ada gambar")
            continue

        result = ocr_newspaper(client, key, image_paths, name)
        all_results[key] = result
        total_pages += result["ocr_success"]

    # Simpan hasil OCR
    output_path = os.path.join(OCR_DIR, f"{date_str}_ocr.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\nHasil OCR disimpan: {output_path}")

    # Simpan juga versi plain text (lebih mudah dibaca manusia)
    text_path = os.path.join(OCR_DIR, f"{date_str}_ocr.txt")
    with open(text_path, "w", encoding="utf-8") as f:
        for key, result in all_results.items():
            f.write(f"{'='*60}\n")
            f.write(f"{result['newspaper']} — {date_str}\n")
            f.write(f"{'='*60}\n\n")
            for page_key in sorted(result["ocr_pages"].keys()):
                page_num = page_key.replace("page_", "")
                f.write(f"--- Halaman {page_num} ---\n\n")
                f.write(result["ocr_pages"][page_key])
                f.write("\n\n")
    print(f"Plain text disimpan: {text_path}")

    # Ringkasan
    print(f"\n{'='*50}")
    print(f"Ringkasan OCR {date_str}:")
    print(f"  Total halaman di-OCR: {total_pages}")
    for key, result in all_results.items():
        print(f"  {result['newspaper']}: {result['ocr_success']}/{result['page_count']} halaman")


def test_single(image_path: str):
    """Test OCR satu gambar saja (untuk debugging)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY belum diisi di .env")
        sys.exit(1)

    if not os.path.exists(image_path):
        print(f"File tidak ditemukan: {image_path}")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    text = ocr_single_page(client, image_path, 1, "Test")
    print(f"\n{'='*50}")
    print("Hasil OCR:")
    print(f"{'='*50}")
    print(text)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="OCR koran via Claude Vision")
    parser.add_argument("--date", default=None, help="Tanggal (YYYY-MM-DD, default: hari ini)")
    parser.add_argument("--paper", default=None, help="Hanya OCR koran tertentu (key)")
    parser.add_argument("--test-single", default=None, help="Test OCR satu gambar")
    args = parser.parse_args()

    if args.test_single:
        test_single(args.test_single)
        return

    if args.date:
        date_str = args.date
    else:
        wib = datetime.timezone(datetime.timedelta(hours=7))
        date_str = datetime.datetime.now(wib).strftime("%Y-%m-%d")

    run_ocr(date_str, args.paper)


if __name__ == "__main__":
    main()
