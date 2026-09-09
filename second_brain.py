"""
Second Brain Markdown Generator — News Scraping AI
===================================================
Modul terpisah untuk generate rangkuman naratif detail (~4k kata)
sebagai basis "second brain" harian.

Beda dgn PDF (yg terstruktur untuk baca cepat), MD ini adalah:
- Narasi mendalam per topik
- Historical context + implikasi kebijakan
- Analisis 5 wilayah dgn konteks strategis
- Cross-reference sumber

Output: string markdown, siap di-save & upload ke Storage.
"""
import os
import sys
import datetime
from typing import Optional

import anthropic


def generate_second_brain(
    entries: list,
    koran_text: str,
    report_data: dict,
    date_str: str,
    api_key: Optional[str] = None,
) -> dict:
    """Panggil Claude sekali lagi untuk generate narasi Second Brain.

    Return dict:
      - "markdown": str (isi .md)
      - "input_tokens": int
      - "output_tokens": int
      - "error": str atau None
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"markdown": "", "input_tokens": 0, "output_tokens": 0, "error": "ANTHROPIC_API_KEY tidak ada"}

    # Ambil ringkasan JSON dari report_data + snippet berita berskala tinggi
    # untuk konteks (tidak full RSS list untuk hemat token)
    top_entries = [e for e in entries if e.get("_scoring", {}).get("bucket") in ("wajib_baca", "perlu_dicek", "kebijakan")][:60]
    entries_snippet = "\n\n".join(
        f"[{it['_scoring']['bucket']}|{it['_scoring']['tier']}] {it['title']}"
        for it in top_entries
    )

    # Truncate koran text agar hemat token
    max_koran = 30000
    koran_snip = koran_text[:max_koran] + ("\n\n[...truncated...]" if len(koran_text) > max_koran else "")

    # Ringkasan JSON output PDF (untuk anchor topik)
    import json as _json
    report_snippet = _json.dumps({
        "global_summary": report_data.get("global_summary", ""),
        "national_summary": report_data.get("national_summary", ""),
        "global_national_titles": [i.get("title", "") for i in report_data.get("global_national", [])],
        "regions": [
            {
                "region_name": r.get("region_name"),
                "region_summary": r.get("region_summary", ""),
                "demand_titles": [i.get("title", "") for i in r.get("demand", [])],
                "sectors_titles": [i.get("title", "") for i in r.get("sectors", [])],
                "inflation_titles": [i.get("title", "") for i in r.get("inflation", [])],
            }
            for r in report_data.get("regions", [])
        ],
    }, ensure_ascii=False, indent=2)[:15000]

    prompt = f"""Anda adalah ekonom senior Departemen Regional Bank Indonesia yang menulis briefing naratif "Second Brain" untuk pengambilan keputusan strategis. Berdasarkan hasil ringkasan PDF hari ini + data mentah, tulis narasi analitik MENENGAH (~4.000 kata) yang bisa dipakai sebagai referensi jangka panjang.

TANGGAL: {date_str}

=== RINGKASAN PDF HARI INI (JSON) ===
{report_snippet}

=== TOP BERITA RSS (60 item bucket tinggi) ===
{entries_snippet}

=== KORAN CETAK (OCR, potongan) ===
{koran_snip}

═══════════════════════════════════════════════════
INSTRUKSI PENULISAN "SECOND BRAIN" HARIAN
═══════════════════════════════════════════════════

Target ~4.000 kata dgn struktur markdown berikut. Tulis NARASI TAJAM & ANALITIK — bukan bullet list. Setiap paragraf berisi angka, konteks, implikasi.

# Rangkuman Eksekutif Harian — {date_str}

## 1. Executive Summary (400-500 kata)
Narasi 4-5 paragraf yg menyimpulkan kondisi hari ini: outlook makro, isu paling strategis, sinyal pergeseran struktural. Sebutkan 3-5 tema utama.

## 2. Kondisi Global (500-600 kata)
Bank sentral (Fed/ECB/BoJ), harga komoditas dunia (minyak, emas, CPO, batubara, nikel), geopolitik. Selalu kaitkan implikasi ke Indonesia (transmisi ke rupiah, ekspor, harga domestik).

## 3. Overview Nasional (600-700 kata)
BI Rate + kebijakan moneter, inflasi, PDB komponen, rupiah, IHSG. Kaitkan dgn kebijakan fiskal terbaru (APBN, subsidi, dsb.). Sebutkan angka konkret.

## 4. Analisis 5 Wilayah (1.500-1.800 kata total; ~300-360 per wilayah)
Untuk TIAP wilayah (Sumatera, Jawa, Balinusra, Kalimantan, Sulampua), tulis 1-2 paragraf mendalam berisi:
- Kondisi Sisi Permintaan (Fiskal, Konsumsi RT, Investasi, Ekspor) — angka spesifik
- Kondisi Sisi Penawaran (LU dominan wilayah tsb) — perkembangan sektor
- Tekanan Inflasi (Inti, VF, AP)
- Isu strategis wilayah + konteks vs bulan/tahun lalu (kalau ada data)
- Prospek ke depan

## 5. Topik Khusus Strategis (400-500 kata)
Bahas 2-3 topik yg akan berdampak >1 minggu ke depan: mis. regulasi minerba (RKAB/DHE SDA), kebijakan hilirisasi, ketahanan pangan, transisi energi, dsb. Dgn historical context (mis. "Ini kelanjutan dari kebijakan X pada Juli 2026...") + implikasi kebijakan.

## 6. Referensi & Sumber Utama
List 8-15 sumber utama yg dipakai (media + koran cetak).

═══════════════════════════════════════════════════
ATURAN TULISAN
═══════════════════════════════════════════════════
- Bahasa Indonesia formal, gaya ekonom BI-DR (tajam, netral faktual).
- WAJIB masukkan ANGKA konkret di setiap paragraf.
- Hubungkan global → nasional → wilayah → kebijakan (chain reasoning).
- Kalau ada data hari ini yg butuh konteks historis, buat asumsi WAJAR (mis. "Dibandingkan Agustus 2026 ketika BI Rate 5,75%...") — TIDAK PERLU akurasi historis mutlak, YANG PENTING narasi mengalir & implikatif.
- Format: markdown murni (# ## paragraf), tidak boleh HTML.
- JANGAN pakai emoji.
- JANGAN pakai "sebagaimana disebutkan sebelumnya" — setiap paragraf self-contained.

Tulis SEKARANG. Output hanya markdown, tidak ada preamble."""

    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        text_blocks = [b.text for b in resp.content if b.type == "text"]
        md = "\n".join(text_blocks).strip()

        return {
            "markdown": md,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "error": None,
        }
    except Exception as exc:
        return {
            "markdown": f"# Rangkuman {date_str}\n\nGagal generate second brain: {exc}\n",
            "input_tokens": 0,
            "output_tokens": 0,
            "error": str(exc),
        }
