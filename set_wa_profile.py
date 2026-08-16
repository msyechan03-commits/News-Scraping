"""
Set WhatsApp Business profile picture using the Graph API.
Usage: python set_wa_profile.py
Reads WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID from .env
"""
import os, sys, requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

TOKEN = os.getenv("WA_ACCESS_TOKEN")
PHONE_ID = os.getenv("WA_PHONE_NUMBER_ID")
IMAGE_PATH = Path(__file__).parent / "drangers_logo.png"
API = "https://graph.facebook.com/v25.0"

if not TOKEN or not PHONE_ID:
    sys.exit("ERROR: WA_ACCESS_TOKEN atau WA_PHONE_NUMBER_ID tidak ditemukan di .env")

headers = {"Authorization": f"Bearer {TOKEN}"}

# Step 1: Get App ID from token
print("1. Mengambil App ID...")
r = requests.get(f"{API}/app", headers=headers)
if r.status_code != 200:
    print(f"   Gagal get app info: {r.text}")
    sys.exit(1)
app_id = r.json()["id"]
print(f"   App ID: {app_id}")

# Step 2: Create upload session
file_size = IMAGE_PATH.stat().st_size
print(f"2. Membuat upload session (file size: {file_size} bytes)...")
r = requests.post(
    f"{API}/{app_id}/uploads",
    headers=headers,
    params={
        "file_length": file_size,
        "file_type": "image/png",
        "file_name": "drangers_bot_logo.png",
    },
)
if r.status_code != 200:
    print(f"   Gagal create upload session: {r.text}")
    sys.exit(1)
upload_session_id = r.json()["id"]
print(f"   Upload session: {upload_session_id}")

# Step 3: Upload file
print("3. Uploading gambar...")
with open(IMAGE_PATH, "rb") as f:
    r = requests.post(
        f"{API}/{upload_session_id}",
        headers={
            "Authorization": f"OAuth {TOKEN}",
            "file_offset": "0",
            "Content-Type": "image/png",
        },
        data=f.read(),
    )
if r.status_code != 200:
    print(f"   Gagal upload: {r.text}")
    sys.exit(1)
handle = r.json()["h"]
print(f"   Handle: {handle[:50]}...")

# Step 4: Update profile picture
print("4. Mengupdate profile picture WhatsApp Business...")
r = requests.post(
    f"{API}/{PHONE_ID}/whatsapp_business_profile",
    headers=headers,
    json={
        "messaging_product": "whatsapp",
        "profile_picture_handle": handle,
    },
)
if r.status_code == 200 and r.json().get("success"):
    print("   SUCCESS! Profile picture berhasil diupdate.")
else:
    print(f"   Response: {r.status_code} - {r.text}")

# Cleanup
print("\nSelesai!")
