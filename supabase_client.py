"""
Supabase Client Helper — News Scraping AI
=========================================
Modul thin wrapper untuk write ke Supabase DB + Storage.

Semua fungsi bersifat FAIL-SAFE: kalau Supabase down atau credential invalid,
fungsi log ke stderr tapi TIDAK raise exception — pipeline tetap jalan.

Usage:
    from supabase_client import SupabaseTracker

    tracker = SupabaseTracker()
    if tracker.enabled:
        run_id = tracker.start_run(date="2026-09-08")
        tracker.log(run_id, "INFO", "fetch_rss", "Started RSS fetch")
        article_id = tracker.insert_article(run_id, {...})
        tracker.insert_score(article_id, {...})
        tracker.insert_ai_decision(run_id, article_id, {...})
        tracker.finish_run(run_id, status="success", pdf_url="...", md_url="...")
"""
import os
import sys
import json
import datetime
from typing import Optional, Any


class SupabaseTracker:
    """Thin wrapper untuk Supabase DB + Storage operations.
    Fail-safe: semua exception di-log, tidak di-raise.
    """

    def __init__(self):
        self.enabled = False
        self.client = None
        self.url = os.environ.get("SUPABASE_URL", "").strip()
        self.key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

        if not self.url or not self.key:
            print("SupabaseTracker: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY belum diisi — audit tracking OFF", file=sys.stderr)
            return

        try:
            from supabase import create_client
            self.client = create_client(self.url, self.key)
            self.enabled = True
            print(f"SupabaseTracker: connected → {self.url}")
        except ImportError:
            print("SupabaseTracker: paket 'supabase' belum di-install. Run: pip install supabase", file=sys.stderr)
        except Exception as exc:
            print(f"SupabaseTracker: gagal init client — {exc}", file=sys.stderr)

    # =========================================================================
    # RUNS
    # =========================================================================
    def start_run(self, date: str) -> Optional[str]:
        """Buat entry di tabel runs. Return run_id (UUID)."""
        if not self.enabled:
            return None
        try:
            resp = self.client.table("runs").insert({
                "date": date,
                "status": "running",
                "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }).execute()
            run_id = resp.data[0]["id"] if resp.data else None
            print(f"SupabaseTracker: run started → {run_id}")
            return run_id
        except Exception as exc:
            print(f"SupabaseTracker.start_run ERROR: {exc}", file=sys.stderr)
            return None

    def finish_run(self, run_id: str, **fields):
        """Update entry runs. Fields: status, error_stage, error_message,
        rss_count, koran_pages, articles_scored, input_tokens_*,
        output_tokens_*, pdf_url, markdown_url, log_url."""
        if not self.enabled or not run_id:
            return
        try:
            fields["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.client.table("runs").update(fields).eq("id", run_id).execute()
            print(f"SupabaseTracker: run finished → {run_id} ({fields.get('status')})")
        except Exception as exc:
            print(f"SupabaseTracker.finish_run ERROR: {exc}", file=sys.stderr)

    # =========================================================================
    # ARTICLES + SCORES (batch insert untuk efisiensi)
    # =========================================================================
    def insert_articles_batch(self, run_id: str, entries: list) -> dict:
        """Bulk insert articles + scores. Return dict {rss_index → article_id}."""
        if not self.enabled or not run_id:
            return {}

        articles_payload = []
        for idx, e in enumerate(entries):
            scoring = e.get("_scoring", {})
            articles_payload.append({
                "run_id": run_id,
                "title": (e.get("title") or "")[:1000],
                "source_name": e.get("source_name", ""),
                "source_url": e.get("link", ""),
                "source_domain": e.get("domain", ""),
                "tier": scoring.get("tier", "T4"),
                "published_date": e.get("date_label", ""),
                "region_detected": scoring.get("reg_hits", []),
                "category_hits": scoring.get("cat_hits", []),
                "raw_snippet": (e.get("summary") or "")[:2000],
            })

        try:
            resp = self.client.table("articles").insert(articles_payload).execute()
            article_id_map = {}
            for idx, row in enumerate(resp.data or []):
                article_id_map[idx] = row["id"]
        except Exception as exc:
            print(f"SupabaseTracker.insert_articles_batch ERROR: {exc}", file=sys.stderr)
            return {}

        # Insert scores dalam batch juga
        scores_payload = []
        for idx, e in enumerate(entries):
            article_id = article_id_map.get(idx)
            if not article_id:
                continue
            s = e.get("_scoring", {})
            scores_payload.append({
                "article_id": article_id,
                "tier_score": s.get("tier_score", 0),
                "mat_score": s.get("mat_score", 0),
                "ent_score": s.get("ent_score", 0),
                "cat_score": s.get("cat_score", 0),
                "reg_score": s.get("reg_score", 0),
                "total_score": s.get("score", 0),
                "bucket": s.get("bucket", "arsip"),
                "mat_hits": s.get("mat_hits", []),
                "ent_hits": s.get("ent_hits", []),
                "cat_hits": s.get("cat_hits", []),
                "reg_hits": s.get("reg_hits", []),
            })
        try:
            if scores_payload:
                self.client.table("scores").insert(scores_payload).execute()
            print(f"SupabaseTracker: {len(article_id_map)} articles + scores inserted")
        except Exception as exc:
            print(f"SupabaseTracker.insert_scores_batch ERROR: {exc}", file=sys.stderr)

        return article_id_map

    # =========================================================================
    # AI DECISIONS
    # =========================================================================
    def insert_ai_decisions(self, run_id: str, article_id_map: dict, entries: list, report_data: dict):
        """Catat item mana yang dipilih AI + placement-nya."""
        if not self.enabled or not run_id:
            return

        # Bangun set judul yg dipakai Claude dgn placement-nya
        placement_map = {}  # key: lowercase title snippet → placement
        for i in report_data.get("global_national", []):
            t = (i.get("title") or "").strip().lower()
            placement_map[t] = ("global_national", i.get("scope"), i.get("scope"))
        for r in report_data.get("regions", []):
            region_name = r.get("region_name", "")
            for section in ("demand", "sectors", "inflation"):
                for i in r.get(section, []):
                    t = (i.get("title") or "").strip().lower()
                    cat = i.get("category") or i.get("component") or ""
                    placement_map[t] = (f"regions.{region_name.lower()}.{section}", cat, region_name)

        # Match dengan RSS entries
        payloads = []
        for idx, e in enumerate(entries):
            article_id = article_id_map.get(idx)
            if not article_id:
                continue
            rss_title = (e.get("title") or "").strip().lower()

            # Fuzzy match: cari placement yg 30 char pertama-nya overlap
            selected = False
            placement, category, scope = None, None, None
            for used_title, (pl, cat, sc) in placement_map.items():
                if not used_title:
                    continue
                if rss_title[:30] and (rss_title[:30] in used_title or used_title[:30] in rss_title):
                    selected = True
                    placement, category, scope = pl, cat, sc
                    break

            payloads.append({
                "run_id": run_id,
                "article_id": article_id,
                "is_selected": selected,
                "placement": placement,
                "category": category,
                "scope": scope,
            })

        try:
            if payloads:
                # Chunk 500 rows per insert (Supabase batch limit)
                for i in range(0, len(payloads), 500):
                    self.client.table("ai_decisions").insert(payloads[i:i+500]).execute()
                selected_count = sum(1 for p in payloads if p["is_selected"])
                print(f"SupabaseTracker: {selected_count} of {len(payloads)} articles selected by AI")
        except Exception as exc:
            print(f"SupabaseTracker.insert_ai_decisions ERROR: {exc}", file=sys.stderr)

    # =========================================================================
    # LOGS
    # =========================================================================
    def log(self, run_id: Optional[str], level: str, stage: str, message: str, metadata: Optional[dict] = None):
        """Insert row ke tabel logs."""
        if not self.enabled:
            return
        try:
            self.client.table("logs").insert({
                "run_id": run_id,
                "level": level.upper(),
                "stage": stage,
                "message": (message or "")[:5000],
                "metadata": metadata or {},
            }).execute()
        except Exception as exc:
            print(f"SupabaseTracker.log ERROR: {exc}", file=sys.stderr)

    # =========================================================================
    # STORAGE UPLOAD
    # =========================================================================
    def upload_file(self, bucket: str, file_path: str, remote_name: str, content_type: str) -> Optional[str]:
        """Upload file ke Supabase Storage. Return public URL kalau bucket public."""
        if not self.enabled:
            return None
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            # Try upload — kalau file sudah ada, remove + upload
            try:
                self.client.storage.from_(bucket).upload(
                    remote_name, content,
                    {"content-type": content_type, "upsert": "true"}
                )
            except Exception:
                # Fallback: delete lalu upload
                try:
                    self.client.storage.from_(bucket).remove([remote_name])
                except Exception:
                    pass
                self.client.storage.from_(bucket).upload(
                    remote_name, content, {"content-type": content_type}
                )

            # Get public URL (kalau bucket public)
            try:
                url = self.client.storage.from_(bucket).get_public_url(remote_name)
                print(f"SupabaseTracker: uploaded {remote_name} → {url}")
                return url
            except Exception:
                return f"{self.url}/storage/v1/object/public/{bucket}/{remote_name}"
        except Exception as exc:
            print(f"SupabaseTracker.upload_file ERROR ({bucket}/{remote_name}): {exc}", file=sys.stderr)
            return None

    def upload_pdf(self, run_date: str, pdf_path: str) -> Optional[str]:
        """Upload PDF ke bucket pdfs/. Return public URL."""
        remote_name = f"{run_date}.pdf"
        return self.upload_file("pdfs", pdf_path, remote_name, "application/pdf")

    def upload_markdown(self, run_date: str, md_path: str) -> Optional[str]:
        """Upload Second Brain markdown ke bucket markdown/. Return public URL."""
        remote_name = f"{run_date}.md"
        return self.upload_file("markdown", md_path, remote_name, "text/markdown")

    def upload_log(self, run_date: str, log_content: str) -> Optional[str]:
        """Upload CLI log ke bucket logs/. Return signed URL."""
        if not self.enabled:
            return None
        try:
            remote_name = f"{run_date}.log"
            content = log_content.encode("utf-8")
            try:
                self.client.storage.from_("logs").upload(
                    remote_name, content, {"content-type": "text/plain", "upsert": "true"}
                )
            except Exception:
                try:
                    self.client.storage.from_("logs").remove([remote_name])
                except Exception:
                    pass
                self.client.storage.from_("logs").upload(
                    remote_name, content, {"content-type": "text/plain"}
                )
            # Signed URL karena bucket private
            try:
                signed = self.client.storage.from_("logs").create_signed_url(remote_name, 60 * 60 * 24 * 30)
                return signed.get("signedURL") or signed.get("signed_url")
            except Exception:
                return None
        except Exception as exc:
            print(f"SupabaseTracker.upload_log ERROR: {exc}", file=sys.stderr)
            return None
