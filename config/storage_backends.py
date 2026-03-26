import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class SupabaseStorage(Storage):
    """
    Minimal Django storage backend for Supabase Storage.

    Uses the service-role key server-side for uploads and returns public URLs.
    """

    def __init__(self, **kwargs):
        self.supabase_url = (kwargs.get("supabase_url") or os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
        self.bucket_name = (kwargs.get("bucket_name") or os.getenv("SUPABASE_STORAGE_BUCKET") or "").strip()
        self.service_role_key = (
            kwargs.get("service_role_key") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
        ).strip()
        self.base_path = (kwargs.get("base_path") or os.getenv("SUPABASE_MEDIA_PREFIX") or "").strip().strip("/")

        if not self.supabase_url or not self.bucket_name or not self.service_role_key:
            raise ValueError("SupabaseStorage requires SUPABASE_URL, SUPABASE_STORAGE_BUCKET and SUPABASE_SERVICE_ROLE_KEY")

    def _normalize_name(self, name: str) -> str:
        normalized = (name or "").replace("\\", "/").lstrip("/")
        if self.base_path:
            return f"{self.base_path}/{normalized}" if normalized else self.base_path
        return normalized

    def _open(self, name, mode="rb"):
        raise NotImplementedError("Reading files from SupabaseStorage is not supported through Django file API.")

    def _save(self, name, content):
        object_path = self._normalize_name(name)
        upload_url = f"{self.supabase_url}/storage/v1/object/{self.bucket_name}/{object_path}"

        if hasattr(content, "seek"):
            content.seek(0)
        payload = content.read()

        request = Request(
            upload_url,
            data=payload,
            headers={
                "apikey": self.service_role_key,
                "Authorization": f"Bearer {self.service_role_key}",
                "Content-Type": getattr(content, "content_type", None) or "application/octet-stream",
                "x-upsert": "true",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=20):
                pass
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            raise ValueError(f"Supabase upload failed ({exc.code}): {body or exc.reason}")

        return object_path

    def exists(self, name):
        # We upload with x-upsert=true, so a pre-check is unnecessary.
        return False

    def url(self, name):
        object_path = self._normalize_name(name)
        return f"{self.supabase_url}/storage/v1/object/public/{self.bucket_name}/{object_path}"
