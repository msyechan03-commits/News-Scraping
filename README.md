# Rangkuman Berita Ekonomi Harian → WhatsApp

Setiap pagi (default 06:30 WIB), sistem otomatis:
1. Scraping RSS berita ekonomi (CNBC Indonesia, CNN Indonesia).
2. Merangkum pakai Claude API.
3. Kirim rangkuman ke WhatsApp Anda lewat Twilio.

Jalan otomatis di GitHub Actions (gratis untuk repo publik/privat pribadi, tidak perlu server).

## Setup (sekali saja, ±15 menit)

### 1. Buat akun Twilio & aktifkan WhatsApp
1. Daftar di https://www.twilio.com/try-twilio (gratis, dapat trial credit).
2. Di dashboard Twilio, buka **Messaging → Try it out → Send a WhatsApp message**
   untuk mengaktifkan **WhatsApp Sandbox**.
3. Ikuti instruksi di sana: kirim pesan `join <kata-sandi-sandbox>` dari WhatsApp
   Anda ke nomor sandbox Twilio (biasanya +1 415 523 8886). Ini WAJIB — kalau
   tidak, pesan otomatis nanti tidak akan sampai.
4. Catat: `Account SID`, `Auth Token` (ada di halaman utama Console Twilio).

   > Catatan: Sandbox itu untuk testing — sesi aktif 24 jam sejak terakhir Anda
   > kirim pesan `join ...`, lalu perlu kirim ulang. Untuk yang benar-benar
   > "set and forget" selamanya, nanti perlu ajukan **WhatsApp Business sender**
   > resmi di Twilio (perlu verifikasi bisnis di Meta, prosesnya beberapa hari).
   > Untuk mulai/coba-coba, sandbox sudah cukup.

### 2. Buat API key Anthropic (untuk Claude merangkum)
1. https://console.anthropic.com/ → **API Keys** → buat key baru.

### 3. Buat repo GitHub baru & upload folder ini
```bash
git init
git add .
git commit -m "init"
git remote add origin <url-repo-anda>
git push -u origin main
```
Repo boleh **private** — Actions tetap jalan gratis untuk private repo pribadi
(2000 menit/bulan gratis, tugas ini hanya butuh ~1 menit/hari).

### 4. Isi Secrets di GitHub
Di repo → **Settings → Secrets and variables → Actions → New repository secret**,
tambahkan 5 secret ini:

| Nama Secret | Contoh isi |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `TWILIO_ACCOUNT_SID` | `ACxxxxxxxx...` |
| `TWILIO_AUTH_TOKEN` | `xxxxxxxx...` |
| `TWILIO_WHATSAPP_FROM` | `whatsapp:+14155238886` |
| `TWILIO_WHATSAPP_TO` | `whatsapp:+62812xxxxxxxx` (nomor Anda, pakai kode negara) |

### 5. Tes manual
Repo → tab **Actions** → pilih workflow **"Rangkuman Berita Ekonomi Harian"** →
**Run workflow** (tombol di kanan). Cek WhatsApp Anda setelah ±30 detik.

Kalau berhasil, mulai besok pagi jam 06:30 WIB akan jalan otomatis sendiri —
tidak perlu buka apa-apa lagi.

## Kustomisasi
- **Ganti jam kirim**: edit baris `cron:` di `.github/workflows/daily-brief.yml`
  (formatnya UTC, WIB = UTC+7).
- **Tambah/kurangi sumber berita**: edit list `RSS_FEEDS` di `scrape_and_send.py`.
- **Ubah gaya rangkuman**: edit bagian `prompt` di fungsi `summarize_with_claude`.

## Kalau Twilio sandbox terasa ribet
Alternatif paling gampang tanpa perlu verifikasi apa pun: ganti tujuan kirim ke
**Telegram Bot** (gratis, resmi, tidak ada sandbox/expiry). Kasih tahu saya kalau
mau saya buatkan versi Telegram-nya — tinggal ganti bagian `send_whatsapp()`.
