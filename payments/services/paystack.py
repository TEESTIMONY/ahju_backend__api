import hashlib
import hmac
import requests
from urllib.parse import quote_plus

from django.conf import settings


class PaystackServiceError(Exception):
    def __init__(self, message: str, status_code: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}


def _base_url() -> str:
    return (getattr(settings, "PAYSTACK_BASE_URL", "https://api.paystack.co") or "https://api.paystack.co").strip().rstrip("/")


def _secret_key() -> str:
    return (getattr(settings, "PAYSTACK_SECRET_KEY", "") or "").strip()


def _request(method: str, endpoint: str, payload: dict | None = None) -> dict:
    secret_key = _secret_key()
    if not secret_key:
        raise PaystackServiceError("PAYSTACK_SECRET_KEY is not configured")

    url = f"{_base_url()}{endpoint}"
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
        "User-Agent": "AHJU-Payments/1.0",
    }

    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=30)
        else:
            response = requests.post(url, json=payload or {}, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise PaystackServiceError(f"Could not connect to Paystack: {exc}")

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 400:
        message = body.get("message") or response.text or "Unknown Paystack error"
        raise PaystackServiceError(
            f"Paystack request failed ({response.status_code}): {message}",
            status_code=response.status_code,
            payload=body,
        )

    return body


def initialize_transaction(*, email: str, amount_minor: int, reference: str, callback_url: str = "", metadata: dict | None = None) -> dict:
    payload = {
        "email": email,
        "amount": amount_minor,
        "reference": reference,
    }
    if callback_url:
        payload["callback_url"] = callback_url
    if metadata:
        payload["metadata"] = metadata
    return _request("POST", "/transaction/initialize", payload)


def verify_transaction(reference: str) -> dict:
    return _request("GET", f"/transaction/verify/{quote_plus(reference)}")


def validate_webhook_signature(raw_body: bytes, signature: str) -> bool:
    secret_key = _secret_key()
    if not secret_key or not signature:
        return False
    computed = hmac.new(secret_key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(signature.strip(), computed)
