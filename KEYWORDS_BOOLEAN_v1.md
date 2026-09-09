# Keyword Boolean untuk News AI — Departemen Regional Bank Indonesia
**Versi:** v1 | **Tanggal:** 31 Agustus 2026 | **Basis:** Prinsip Boolean MIT LibGuides + kerangka BI-DR

---

## Prinsip Boolean (referensi MIT Libraries)

| Operator | Fungsi | Contoh |
|---|---|---|
| **AND** | Narrow — semua kata harus ada | `RKAB AND Indonesia` |
| **OR** | Broaden — untuk sinonim/alternatif | `batubara OR "batu bara" OR coal` |
| **NOT** (`-`) | Exclude — buang noise | `Papua -"Papua Nugini"` |
| `""` | Frasa eksak | `"kereta cepat"` |
| `( )` | Grouping AND+OR | `(nikel OR bauksit) AND Sulawesi` |

**Aturan urutan:** DB memproses AND dulu, jadi WAJIB pakai `( )` bila mix AND+OR.

**Format Google News RSS:** OR = `+OR+`, AND = `+`, NOT = `+-`, phrase = `%22...%22`, group = `%28...%29`

---

## 1. Overall News AI (Comprehensive)

**Tujuan:** Payung utama — berita ekonomi Indonesia yg bisa relevan ke DR BI, spektrum luas.

**Boolean:**
```
(ekonomi OR bisnis OR fiskal OR moneter OR investasi OR "sektor riil") 
AND Indonesia 
NOT (olahraga OR "sepak bola" OR selebriti OR gosip OR "doa bersama" OR sholawat)
```

**Rationale:**
- Payung ekonomi + bisnis makro
- Exclude noise umum (olahraga, hiburan, ritual keagamaan)

**Query string RSS (siap pakai):**
```
q=%28ekonomi+OR+bisnis+OR+fiskal+OR+moneter+OR+investasi%29+Indonesia+-olahraga+-sholawat+when:1d
```

---

## 2. Per Lapangan Usaha (LU) — 6 kelompok

### 2.1 LU Pertanian

**Boolean:**
```
(padi OR gabah OR beras OR CPO OR "kelapa sawit" OR TBS OR kopi OR kakao OR karet 
 OR perikanan OR udang OR "hama tikus" OR "serangan hama" OR "gagal panen" OR karhutla) 
AND (Indonesia OR Bulog OR GAPKI OR Kementan OR BPS) 
NOT (pertambangan OR tambang)
```

**Query RSS:**
```
q=%28padi+OR+gabah+OR+CPO+OR+%22kelapa+sawit%22+OR+kopi+OR+kakao+OR+karet+OR+perikanan+OR+%22hama+tikus%22+OR+karhutla%29+%28Indonesia+OR+Bulog+OR+GAPKI%29+-tambang+when:2d
```

### 2.2 LU Perdagangan

**Boolean:**
```
(ritel OR "penjualan mobil" OR "penjualan motor" OR Gaikindo OR AISI OR Alfamart 
 OR Indomaret OR Transmart OR "e-commerce" OR Tokopedia OR Shopee OR mal 
 OR "indeks penjualan riil" OR SPE) 
AND (Indonesia OR Bapanas) 
NOT (opini OR "artikel opini")
```

**Query RSS:**
```
q=%28ritel+OR+%22penjualan+mobil%22+OR+Gaikindo+OR+Alfamart+OR+Indomaret+OR+Tokopedia+OR+Shopee+OR+mal+OR+SPE%29+Indonesia+when:1d
```

### 2.3 LU Pertambangan

**Boolean (KRITIS — regulasi minerba wajib tertangkap):**
```
(RKAB OR IUP OR IUPK OR "kuota tambang" OR "kuota batu bara" OR "batu bara" OR batubara 
 OR nikel OR bauksit OR timah OR emas OR tembaga OR HBA OR "lifting minyak" 
 OR "lifting gas" OR "SKK Migas" OR ESDM OR minerba OR "DHE SDA" 
 OR Adaro OR PTBA OR Vale OR Antam OR Freeport OR Amman OR Pertamina Hulu) 
AND Indonesia 
NOT (smelter OR kilang OR refinery OR petrokimia OR PLTP)
```

**Catatan:** smelter/kilang di-exclude karena masuk Industri Pengolahan (double-count).

**Query RSS:**
```
q=%28RKAB+OR+IUP+OR+%22kuota+tambang%22+OR+%22batu+bara%22+OR+nikel+OR+bauksit+OR+timah+OR+emas+OR+tembaga+OR+HBA+OR+minerba+OR+ESDM+OR+%22DHE+SDA%22%29+Indonesia+-smelter+-kilang+when:2d
```

### 2.4 LU Konstruksi

**Boolean (PISAH REALISASI vs RENCANA):**

**a) Realisasi (sinyal output PDRB):**
```
("progres fisik" OR "topping off" OR diresmikan OR beroperasi 
 OR "kontrak baru" OR "order book" OR "realisasi belanja modal" 
 OR "pencairan termin" OR "capaian fisik") 
AND (proyek OR infrastruktur OR "jalan tol" OR IKN OR bendungan OR bandara OR "kereta cepat") 
AND Indonesia
```

**b) Rencana (leading indicator):**
```
(groundbreaking OR MoU OR tender OR "rencana investasi" OR "peletakan batu pertama" 
 OR "studi kelayakan" OR lelang) 
AND (proyek OR "jalan tol" OR IKN OR bendungan OR bandara OR "kereta cepat" 
     OR "trans papua" OR "trans sumatera" OR "trans jawa") 
AND Indonesia
```

**Query RSS (realisasi):**
```
q=%28%22progres+fisik%22+OR+diresmikan+OR+%22kontrak+baru%22+OR+%22order+book%22%29+%28%22jalan+tol%22+OR+IKN+OR+bendungan+OR+bandara+OR+%22kereta+cepat%22%29+Indonesia+when:2d
```

### 2.5 LU Industri Pengolahan

**Boolean:**
```
(pabrik OR smelter OR kilang OR "kawasan industri" OR KEK OR IMIP OR IWIP 
 OR "PMI manufaktur" OR utilisasi OR "kapasitas produksi" 
 OR Indofood OR Mayora OR "Krakatau Steel" OR Inalum OR Tsingshan OR Huayou 
 OR "Chandra Asri" OR "Pupuk Indonesia" OR Semen Indonesia OR SIG OR Indocement) 
AND Indonesia 
NOT (tambang OR mining)
```

**Query RSS:**
```
q=%28pabrik+OR+smelter+OR+kilang+OR+%22kawasan+industri%22+OR+KEK+OR+%22PMI+manufaktur%22+OR+utilisasi+OR+Indofood+OR+%22Krakatau+Steel%22+OR+%22Pupuk+Indonesia%22%29+Indonesia+-mining+when:2d
```

### 2.6 LU Akmamin (Akomodasi & Makan-Minum)

**Boolean:**
```
(TPK OR "okupansi hotel" OR "tingkat penghunian" OR wisman OR wisnus 
 OR "load factor" OR MICE OR festival OR konser OR marathon OR "sport tourism" 
 OR "Maybank Marathon" OR MotoGP OR "rute penerbangan" OR "extra flight" 
 OR Bali OR "Labuan Bajo" OR Mandalika OR Borobudur OR "Danau Toba" 
 OR Garuda OR "Lion Air" OR PHRI OR ASITA OR Kemenparekraf) 
AND Indonesia
```

**Query RSS:**
```
q=%28TPK+OR+%22okupansi+hotel%22+OR+wisman+OR+MICE+OR+marathon+OR+%22sport+tourism%22+OR+Bali+OR+%22Labuan+Bajo%22+OR+Mandalika+OR+%22Garuda+Indonesia%22%29+Indonesia+when:1d
```

---

## 3. CIGX (Sisi Permintaan — Consumption, Investment, Government, eXport)

### 3.1 Konsumsi RT (C — Consumption)

**Boolean:**
```
("daya beli" OR "penjualan eceran" OR IKK OR IPR OR SSSG OR THR 
 OR UMP OR UMK OR "upah minimum" OR "kartu prakerja" OR "bansos" 
 OR "kendaraan bermotor" OR KPR OR "e-commerce" OR "harga pangan" 
 OR "konsumsi rumah tangga") 
AND Indonesia
```

**Query RSS:**
```
q=%28%22daya+beli%22+OR+%22penjualan+eceran%22+OR+IKK+OR+THR+OR+UMP+OR+bansos+OR+%22kendaraan+bermotor%22+OR+KPR+OR+%22konsumsi+rumah+tangga%22%29+Indonesia+when:1d
```

### 3.2 Investasi (I — Investment)

**Boolean:**
```
(PMA OR PMDN OR "penanaman modal" OR BKPM OR groundbreaking OR "MoU investasi" 
 OR capex OR "ekspansi pabrik" OR KEK OR "kawasan industri" OR OSS OR PSN 
 OR IKN OR "Ibu Kota Nusantara" OR Danantara OR DSI OR hilirisasi 
 OR "kontrak baru" OR "COD pembangkit" OR "Yosun Tire") 
AND Indonesia
```

**Query RSS:**
```
q=%28PMA+OR+PMDN+OR+BKPM+OR+groundbreaking+OR+capex+OR+KEK+OR+PSN+OR+IKN+OR+Danantara+OR+DSI+OR+hilirisasi%29+Indonesia+when:1d
```

### 3.3 Fiskal / Government (G — Government spending)

**Boolean:**
```
(APBN OR APBD OR "belanja modal" OR "belanja pegawai" OR TKD OR DAU OR DAK OR DBH 
 OR "dana desa" OR PAD OR "pajak daerah" OR DIPA OR KPPN OR bansos OR PKH OR BLT 
 OR subsidi OR "Kopdes Merah Putih" OR Himbara OR PMK OR SAL 
 OR Menkeu OR "Purbaya Yudhi" OR "Sri Mulyani" OR "tanggap darurat" OR BNPB) 
AND (Indonesia OR nasional OR "pemerintah pusat" OR "pemerintah daerah")
```

**Query RSS:**
```
q=%28APBN+OR+APBD+OR+%22belanja+modal%22+OR+TKD+OR+DAU+OR+bansos+OR+subsidi+OR+%22Kopdes+Merah+Putih%22+OR+Himbara+OR+Menkeu%29+Indonesia+when:1d
```

### 3.4 Ekspor (X — eXport)

**Boolean:**
```
(ekspor OR "neraca perdagangan" OR "bea keluar" OR DMO OR "DHE SDA" 
 OR kontainer OR TEUs OR "harga komoditas" OR "tarif impor AS" OR safeguard 
 OR IEU-CEPA OR "Indonesia-Uni Eropa" OR "portal ekspor" 
 OR DSI OR Danantara OR "3 komoditas strategis") 
AND Indonesia
```

**Query RSS:**
```
q=%28ekspor+OR+%22neraca+perdagangan%22+OR+DMO+OR+%22DHE+SDA%22+OR+kontainer+OR+%22harga+komoditas%22+OR+safeguard+OR+IEU-CEPA+OR+DSI+OR+Danantara%29+Indonesia+when:2d
```

---

## 4. Global & National

### 4.1 Global (ekonomi dunia, dampak ke Indonesia)

**Boolean:**
```
(Fed OR "Federal Reserve" OR "Jerome Powell" OR ECB OR BoJ OR PBoC 
 OR OPEC OR "harga minyak" OR Brent OR WTI OR "harga emas" OR "harga CPO global" 
 OR "harga batubara global" OR "harga nikel global" OR "Wall Street" 
 OR "S&P 500" OR Nasdaq OR yuan OR "trade war" OR tariff OR sanksi 
 OR "geopolitik" OR "Selat Hormuz" OR OPEC+) 
NOT ("Papua Nugini" OR Malaysia OR Filipina OR Australia OR Tiongkok)
```

**Catatan:** NOT tidak strict — kata negara boleh muncul di body, cuma exclude bila DOMINAN di judul yg bukan Indonesia.

**Query RSS:**
```
q=%28Fed+OR+ECB+OR+OPEC+OR+%22harga+minyak%22+OR+Brent+OR+%22harga+emas%22+OR+%22Wall+Street%22+OR+yuan+OR+tariff+OR+%22Selat+Hormuz%22%29+global+when:1d
```

### 4.2 National (makro Indonesia)

**Boolean:**
```
("BI Rate" OR "BI-Rate" OR "Bank Indonesia" OR "Perry Warjiyo" 
 OR "inflasi Indonesia" OR "inflasi nasional" OR rupiah OR IHSG 
 OR "neraca perdagangan Indonesia" OR "cadangan devisa" OR "PDB Indonesia" 
 OR "pertumbuhan ekonomi Indonesia" OR "PMI Indonesia" 
 OR OJK OR "Sri Mulyani" OR Airlangga OR "Menko Perekonomian" 
 OR Prabowo OR Danantara) 
AND Indonesia
```

**Query RSS:**
```
q=%28%22BI+Rate%22+OR+%22Bank+Indonesia%22+OR+%22inflasi+Indonesia%22+OR+rupiah+OR+IHSG+OR+%22cadangan+devisa%22+OR+%22PDB+Indonesia%22+OR+OJK+OR+%22Sri+Mulyani%22+OR+Danantara%29+Indonesia+when:1d
```

---

## 5. Ringkasan: 11 Feed RSS yang Diusulkan

Kalau semua di atas diimplementasi, `pipeline.py` akan punya **11 feed**:

| # | Kategori | Feed |
|---|---|---|
| 1 | Overall | Payung ekonomi + bisnis (existing) |
| 2 | LU Pertanian | Padi/CPO/hama/karhutla |
| 3 | LU Perdagangan | Ritel/e-commerce/otomotif |
| 4 | LU Pertambangan | **RKAB/IUP/minerba** (already ada, diperkaya) |
| 5 | LU Konstruksi | Realisasi + rencana proyek |
| 6 | LU Industri Pengolahan | Pabrik/smelter/kilang |
| 7 | LU Akmamin | Hotel/wisata/MICE/event |
| 8 | CIGX Konsumsi RT | Daya beli/UMP/bansos |
| 9 | CIGX Investasi | PMA/PMDN/DSI/PSN |
| 10 | CIGX Fiskal | APBN/Kopdes/Menkeu |
| 11 | CIGX Ekspor | Neraca dagang/DHE SDA |
| 12 | Global | Fed/OPEC/komoditas dunia |
| 13 | National | BI Rate/rupiah/IHSG |
| + | Regional × 5 | Sumatera/Jawa/Kalimantan/Balinusra/Sulampua (existing) |

**Total: ~18 feed** (naik dari 9 sekarang).

---

## 6. Estimasi Cost Impact

| Metrik | Sekarang (9 feed) | Usulan (18 feed) | Delta |
|---|---|---|---|
| Entri RSS diambil | ~250-400 | ~500-800 | +2x |
| Setelah filter whitelist | ~100 | ~150-200 | +50-100% |
| Input tokens ke Claude | ~35k | ~50-60k | +15-25k |
| Cost per generate | ~$0.30 | ~$0.45-0.55 | +$0.15-0.25 |

Kalau daily cron 30 hari = tambahan biaya **~$4.5-7.5/bulan**. Trade-off untuk coverage minerba/CIGX/global yg lebih rapat.

---

## 7. Rekomendasi Implementasi Bertahap

**Fase 1 (aman, low-risk):** Tambahkan 4 feed strategis — LU Pertambangan (sudah), CIGX Fiskal, CIGX Ekspor, National → +2 feed baru. Total 11 feed.

**Fase 2 (setelah 1 minggu monitoring):** Bila cost stabil & kualitas naik → tambahkan sisanya per LU (Konstruksi realisasi, Industri Pengolahan).

**Fase 3 (opsional):** Global feed khusus + Akmamin — bila atasan minta coverage lebih luas.

---

## 8. Cara Test Sebelum Push

Setelah Boolean di-approve, saya bisa:
1. Update `RSS_FEEDS` di `pipeline_test.py` dulu
2. Anda run lokal `python3.13 pipeline_test.py generate` (tanpa send)
3. Cek distribusi bucket + KEPUTUSAN CLAUDE apakah topik RKAB/DHE SDA/Kopdes benar-benar tertangkap lebih rapat
4. Baru sync ke `pipeline.py` production + push

---

**Silakan review Boolean di atas. Kalau ada revisi:**
- Kata kunci mana yg mau ditambah/kurangi?
- Feed mana yg PRIORITAS tinggi (Fase 1)?
- Ada exclusion khusus dari kantor Anda?
