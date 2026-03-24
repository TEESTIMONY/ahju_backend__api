import os
import json
import tempfile
import re
import hmac
import hashlib
from pathlib import Path
from datetime import timedelta
from io import StringIO
from decimal import Decimal
from urllib.parse import quote_plus, urlparse, parse_qs, urljoin, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import IntegrityError, transaction, connection
from django.db.models import Count, F, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.utils.crypto import get_random_string
from django.utils.text import slugify
from django.utils import timezone
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from .models import (
    CartItem,
    Order,
    OrderItem,
    PaymentTransaction,
    Product,
    UserAnalyticsDaily,
    UserAppearance,
    UserContactLead,
    UserLink,
    UserPortfolioItem,
)
from .serializers import (
    CartItemSerializer,
    CartItemUpdateSerializer,
    CartItemUpsertSerializer,
    CheckoutInitializeSerializer,
    DashboardTrackSerializer,
    GoogleAuthSerializer,
    OrderSerializer,
    PaymentTransactionSerializer,
    ProductSerializer,
    PublicTrackSerializer,
    PublicContactLeadSubmitSerializer,
    UserContactLeadSerializer,
    UserPortfolioItemSerializer,
    UserPortfolioImportSerializer,
    UserAppearanceSerializer,
    UserLinkSerializer,
    UpdateMeSerializer,
    UserSerializer,
    UsernameSerializer,
)


User = get_user_model()


def _get_paystack_secret_key() -> str:
    return (os.getenv("PAYSTACK_SECRET_KEY") or "").strip()


def _paystack_initialize_transaction(*, email: str, amount_kobo: int, reference: str, callback_url: str):
    secret_key = _get_paystack_secret_key()
    if not secret_key:
        raise ValueError("PAYSTACK_SECRET_KEY is not configured")

    payload = {
        "email": email,
        "amount": amount_kobo,
        "reference": reference,
    }
    if callback_url:
        payload["callback_url"] = callback_url

    request = Request(
        "https://api.paystack.co/transaction/initialize",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8", errors="ignore"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
        message = body
        try:
            parsed = json.loads(body or "{}")
            message = parsed.get("message") or body
        except Exception:
            pass
        raise ValueError(f"Paystack initialize failed ({exc.code}): {message or str(exc)}")
    except URLError as exc:
        raise ValueError(f"Could not connect to Paystack: {exc}")


def _paystack_verify_transaction(reference: str):
    secret_key = _get_paystack_secret_key()
    if not secret_key:
        raise ValueError("PAYSTACK_SECRET_KEY is not configured")

    request = Request(
        f"https://api.paystack.co/transaction/verify/{quote_plus(reference)}",
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8", errors="ignore"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
        message = body
        try:
            parsed = json.loads(body or "{}")
            message = parsed.get("message") or body
        except Exception:
            pass
        raise ValueError(f"Paystack verify failed ({exc.code}): {message or str(exc)}")
    except URLError as exc:
        raise ValueError(f"Could not connect to Paystack: {exc}")


def _apply_successful_payment(transaction: PaymentTransaction, raw_response: dict):
    if transaction.status == PaymentTransaction.STATUS_SUCCESS:
        return

    now = timezone.now()
    transaction.status = PaymentTransaction.STATUS_SUCCESS
    transaction.paid_at = now
    transaction.raw_response = raw_response
    transaction.save(update_fields=["status", "paid_at", "raw_response", "updated_at"])

    order = transaction.order
    if order.status != Order.STATUS_PAID:
        order.status = Order.STATUS_PAID
        order.save(update_fields=["status", "updated_at"])

    if order.user_id:
        CartItem.objects.filter(user=order.user).delete()
    elif order.session_key:
        CartItem.objects.filter(user__isnull=True, session_key=order.session_key).delete()


def _normalize_session_key(raw_value: str) -> str:
    return (raw_value or "").strip()[:64]


def _resolve_cart_scope(request, session_key: str = ""):
    clean_session_key = _normalize_session_key(session_key)
    if request.user and request.user.is_authenticated:
        return {"user": request.user, "session_key": ""}
    return {"user": None, "session_key": clean_session_key}


def _cart_queryset_for_scope(scope: dict):
    if scope.get("user"):
        return CartItem.objects.filter(user=scope["user"]).select_related("product")
    session_key = scope.get("session_key", "")
    return CartItem.objects.filter(user__isnull=True, session_key=session_key).select_related("product")


def _serialize_cart_response(queryset):
    items = list(queryset.order_by("-updated_at", "-created_at"))
    count = sum(item.quantity for item in items)
    return {
        "items": CartItemSerializer(items, many=True).data,
        "count": count,
    }


def _merge_guest_cart_into_user(user, session_key: str):
    normalized = _normalize_session_key(session_key)
    if not normalized:
        return

    guest_items = CartItem.objects.filter(user__isnull=True, session_key=normalized).select_related("product")
    for guest_item in guest_items:
        existing = CartItem.objects.filter(user=user, product=guest_item.product).first()
        if existing:
            existing.quantity += guest_item.quantity
            existing.save(update_fields=["quantity", "updated_at"])
        else:
            guest_item.user = user
            guest_item.session_key = ""
            guest_item.save(update_fields=["user", "session_key", "updated_at"])


def _truncate_public_tables_except_migrations() -> None:
    """
    Full-overwrite helper for fixture imports.
    Clears all public schema tables except django_migrations and resets identities.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename <> 'django_migrations'
            """
        )
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            return

        quoted_tables = ", ".join(f'"{table}"' for table in tables)
        cursor.execute(f"TRUNCATE TABLE {quoted_tables} RESTART IDENTITY CASCADE;")


def _normalize_credentials_path(raw_path: str) -> Path:
    path = raw_path.strip()

    # Convert Git Bash style path (/c/Users/...) to Windows path (C:/Users/...)
    if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == "/":
        drive = path[1].upper()
        path = f"{drive}:{path[2:]}"

    return Path(path)


def _initialize_firebase() -> None:
    if firebase_admin._apps:
        return

    credentials_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
    if credentials_json:
        try:
            cred = credentials.Certificate(json.loads(credentials_json))
            firebase_admin.initialize_app(cred)
            return
        except Exception as exc:
            raise ValueError(f"Invalid FIREBASE_CREDENTIALS_JSON: {exc}")

    credentials_path = os.getenv("FIREBASE_CREDENTIALS")
    if not credentials_path:
        raise ValueError("Set FIREBASE_CREDENTIALS or FIREBASE_CREDENTIALS_JSON")

    cred_file = _normalize_credentials_path(credentials_path)
    if not cred_file.exists():
        raise ValueError(f"Firebase credentials file not found at: {credentials_path}")

    cred = credentials.Certificate(str(cred_file))
    firebase_admin.initialize_app(cred)


def _build_username(email: str) -> str:
    base = slugify(email.split("@")[0]) or "user"
    candidate = base
    counter = 1
    while User.objects.filter(username=candidate).exists():
        counter += 1
        candidate = f"{base}{counter}"
    return candidate


def _build_temp_username() -> str:
    candidate = f"user_{get_random_string(10).lower()}"
    while User.objects.filter(username=candidate).exists():
        candidate = f"user_{get_random_string(10).lower()}"
    return candidate


def _build_embed_html(source_url: str) -> str:
    if not source_url:
        return ""

    normalized = source_url.strip()
    parsed = urlparse(normalized)
    host = (parsed.netloc or "").lower()

    if "instagram.com" in host:
        match = re.search(r"/((p|reel))/([A-Za-z0-9_-]+)", parsed.path)
        if match:
            shortcode = match.group(3)
            kind = match.group(1)
            embed_src = f"https://www.instagram.com/{kind}/{shortcode}/embed"
            return (
                f'<iframe src="{embed_src}" width="100%" height="480" '
                'style="border:0;border-radius:12px;" loading="lazy" '
                'referrerpolicy="no-referrer-when-downgrade" allowfullscreen></iframe>'
            )

    if "x.com" in host or "twitter.com" in host:
        if "/status/" in parsed.path:
            embed_src = f"https://twitframe.com/show?url={quote_plus(normalized)}"
            return (
                f'<iframe src="{embed_src}" width="100%" height="520" '
                'style="border:0;border-radius:12px;" loading="lazy" allowfullscreen></iframe>'
            )

    if "youtube.com" in host or "youtu.be" in host:
        video_id = ""
        if "youtu.be" in host:
            video_id = parsed.path.strip("/")
        else:
            query = parse_qs(parsed.query)
            video_id = (query.get("v") or [""])[0]

        if video_id:
            embed_src = f"https://www.youtube.com/embed/{video_id}"
            return (
                f'<iframe src="{embed_src}" width="100%" height="360" '
                'style="border:0;border-radius:12px;" loading="lazy" '
                'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
                'referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>'
            )

    return ""

def _fetch_html(url: str, timeout: int = 8) -> str:
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ]

    for user_agent in user_agents:
        request = Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                html = raw.decode(charset, errors="ignore")
                if html:
                    return html
        except Exception:
            continue

    # Some sites block bot-like traffic or return dynamic-only shells.
    # We fail gracefully so API returns a user-friendly 400 instead of 500.
    return ""


def _extract_image_urls_from_html(page_url: str, html: str):
    if not html:
        return []

    urls = []

    # Prefer metadata image hints first
    meta_patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']og:image:secure_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+itemprop=["\']image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
    ]
    for pattern in meta_patterns:
        for match in re.findall(pattern, html, flags=re.IGNORECASE):
            urls.append(match.strip())

    # General <img src="...">
    for match in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        urls.append(match.strip())

    # Lazy-load and modern source attributes
    extra_attr_patterns = [
        r'<img[^>]+data-src=["\']([^"\']+)["\']',
        r'<img[^>]+data-lazy-src=["\']([^"\']+)["\']',
        r'<img[^>]+data-original=["\']([^"\']+)["\']',
        r'<img[^>]+data-zoom-image=["\']([^"\']+)["\']',
        r'<source[^>]+srcset=["\']([^"\']+)["\']',
        r'<img[^>]+srcset=["\']([^"\']+)["\']',
    ]
    for pattern in extra_attr_patterns:
        for match in re.findall(pattern, html, flags=re.IGNORECASE):
            urls.append(match.strip())

    # Parse srcset candidate lists.
    # Split only on commas that likely start a new URL candidate,
    # so Cloudinary transformations like `f_auto,q_auto` are not broken.
    expanded_urls = []
    for candidate in urls:
        if "," in candidate and (" 1x" in candidate or " 2x" in candidate or "w" in candidate):
            parts = [part.strip() for part in re.split(r",\s*(?=(?:https?:|//|/))", candidate) if part.strip()]
            for part in parts:
                expanded_urls.append(part.split(" ")[0].strip())
        else:
            expanded_urls.append(candidate)
    urls = expanded_urls

    # Script-embedded absolute image URLs (JSON/JS blobs)
    for match in re.findall(
        r"https?://[^\s\"'<>]+\.(?:jpg|jpeg|png|webp|gif|avif|bmp|svg)(?:\?[^\s\"'<>]*)?",
        html,
        flags=re.IGNORECASE,
    ):
        urls.append(match.strip())

    # Escaped URLs from JSON strings
    for match in re.findall(
        r"https?:\\/\\/[^\s\"'<>]+\.(?:jpg|jpeg|png|webp|gif|avif|bmp|svg)(?:\\?[^\s\"'<>]*)?",
        html,
        flags=re.IGNORECASE,
    ):
        urls.append(match.replace("\\/", "/").strip())

    normalized = []
    seen = set()
    for raw in urls:
        if not raw or raw.startswith("data:"):
            continue
        absolute = urljoin(page_url, raw)

        # Normalize Next.js image optimizer URLs to the underlying source image URL.
        parsed_absolute = urlparse(absolute)
        if "/_next/image" in (parsed_absolute.path or ""):
            source_param = (parse_qs(parsed_absolute.query).get("url") or [""])[0]
            if source_param:
                absolute = urljoin(page_url, unquote(source_param))

        lowered = absolute.lower()
        if lowered.startswith("javascript:"):
            continue

        # Skip clearly incomplete transformation-only image URLs.
        if lowered.endswith("/f_auto") or lowered.endswith("/q_auto") or lowered.endswith("/image/upload"):
            continue

        # Keep common image formats and CDN image links with query params
        looks_like_image = bool(
            re.search(r"\.(jpg|jpeg|png|webp|gif|avif|bmp|svg)(\?.*)?$", lowered)
            or any(token in lowered for token in ["/image", "/images", "cdn", "cloudfront", "media"]) 
        )
        if not looks_like_image:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        normalized.append(absolute)
        if len(normalized) >= 2000:
            break

    return normalized


def _collect_shop_or_store_images(source_url: str, max_images: int | None = None):
    html = _fetch_html(source_url)
    images = _extract_image_urls_from_html(source_url, html)
    if max_images is None:
        return images
    return images[:max_images]


def _collect_instagram_images_with_graph_api(source_url: str, max_images: int):
    """
    API-ready path: requires env variables configured for Meta Graph access.
    If not configured or request fails, returns [] and caller can fallback.
    """
    token = os.getenv("INSTAGRAM_GRAPH_ACCESS_TOKEN", "").strip()
    business_account_id = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "").strip()
    if not token or not business_account_id:
        return []

    graph_url = (
        f"https://graph.facebook.com/v22.0/{business_account_id}/media"
        f"?fields=id,media_type,media_url,thumbnail_url,timestamp,permalink"
        f"&limit={max_images}&access_token={quote_plus(token)}"
    )

    try:
        request = Request(graph_url, headers={"User-Agent": "AHJU/1.0"})
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8", errors="ignore"))
    except Exception:
        return []

    images = []
    for item in (data.get("data") or []):
        media_type = (item.get("media_type") or "").upper()
        if media_type not in {"IMAGE", "CAROUSEL_ALBUM", "VIDEO"}:
            continue
        url = item.get("media_url") or item.get("thumbnail_url")
        if url:
            images.append(url)
        if len(images) >= max_images:
            break
    return images


def _collect_social_images(source_url: str, max_images: int | None = None):
    host = (urlparse(source_url).netloc or "").lower()
    effective_limit = max_images if isinstance(max_images, int) and max_images > 0 else 100
    if "instagram.com" in host:
        api_images = _collect_instagram_images_with_graph_api(source_url, effective_limit)
        if api_images:
            return api_images

    # Fallback (works for some public pages/sites, not guaranteed for heavily JS pages)
    return _collect_shop_or_store_images(source_url, max_images)

class GoogleAuthView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        id_token = serializer.validated_data["id_token"]

        try:
            _initialize_firebase()
            decoded_token = firebase_auth.verify_id_token(id_token, clock_skew_seconds=60)
        except Exception as exc:
            error_text = str(exc)
            if "Token used too early" in error_text:
                return Response(
                    {
                        "detail": "Invalid Firebase token",
                        "error": "Token time mismatch detected. Sync your computer date/time automatically, then retry Google sign-in.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {"detail": "Invalid Firebase token", "error": error_text},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = decoded_token.get("email")
        if not email:
            return Response(
                {"detail": "Google account email not found in token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        first_name = decoded_token.get("name", "").split(" ")[0] if decoded_token.get("name") else ""
        last_name = " ".join(decoded_token.get("name", "").split(" ")[1:]) if decoded_token.get("name") else ""

        user = User.objects.filter(email=email).first()
        is_new_user = False
        if not user:
            username = _build_temp_username()
            try:
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    password=get_random_string(32),
                )
                is_new_user = True
            except IntegrityError:
                user = User.objects.get(email=email)
        else:
            updated = False
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                updated = True
            if last_name and user.last_name != last_name:
                user.last_name = last_name
                updated = True
            if updated:
                user.save(update_fields=["first_name", "last_name"])

        refresh = RefreshToken.for_user(user)

        merge_session_key = _normalize_session_key((request.data or {}).get("session_key", ""))
        if merge_session_key:
            _merge_guest_cart_into_user(user, merge_session_key)

        return Response(
            {
                "user": UserSerializer(user).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "is_new_user": is_new_user,
                "needs_username": user.username.startswith("user_"),
            },
            status=status.HTTP_200_OK,
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UpdateMeSerializer(instance=request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)


class UsernameAvailabilityView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        serializer = UsernameSerializer(data={"username": request.query_params.get("username", "")})
        if not serializer.is_valid():
            return Response(
                {
                    "available": False,
                    "detail": serializer.errors.get("username", ["Invalid username"])[0],
                },
                status=status.HTTP_200_OK,
            )

        username = serializer.validated_data["username"]
        available = not User.objects.filter(username__iexact=username).exists()
        return Response({"available": available})


class SetUsernameView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = UsernameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        username = serializer.validated_data["username"]
        if User.objects.filter(username__iexact=username).exclude(id=request.user.id).exists():
            return Response(
                {"detail": "Username is already taken"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.user.username = username
        request.user.save(update_fields=["username"])
        return Response({"user": UserSerializer(request.user).data})


class DashboardSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        totals = request.user.analytics_daily.aggregate(
            total_views=Coalesce(Sum("views"), 0),
            total_clicks=Coalesce(Sum("clicks"), 0),
        )
        total_leads = request.user.contact_leads.count()

        total_views = totals["total_views"]
        total_clicks = totals["total_clicks"]
        ctr = round((total_clicks / total_views) * 100, 1) if total_views else 0.0

        return Response(
            {
                "total_views": total_views,
                "total_clicks": total_clicks,
                "total_leads": total_leads,
                "ctr": ctr,
            }
        )


class DashboardTimeseriesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        today = timezone.now().date()
        start_date = today - timedelta(days=6)
        rows = request.user.analytics_daily.filter(date__gte=start_date, date__lte=today)
        by_date = {row.date: row for row in rows}
        leads_by_date = {
            row["created_at__date"]: row["count"]
            for row in request.user.contact_leads.filter(
                created_at__date__gte=start_date,
                created_at__date__lte=today,
            )
            .values("created_at__date")
            .annotate(count=Count("id"))
        }

        data = []
        for i in range(7):
            date = start_date + timedelta(days=i)
            row = by_date.get(date)
            data.append(
                {
                    "day": date.strftime("%a"),
                    "views": row.views if row else 0,
                    "clicks": row.clicks if row else 0,
                    "leads": leads_by_date.get(date, 0),
                }
            )

        return Response({"data": data})


class DashboardTrackEventView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DashboardTrackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        event_type = serializer.validated_data["event_type"]
        count = serializer.validated_data["count"]
        today = timezone.now().date()

        row, _ = UserAnalyticsDaily.objects.get_or_create(user=request.user, date=today)
        if event_type == "view":
            row.views += count
        elif event_type == "click":
            row.clicks += count
        else:
            row.card_taps += count
        row.save(update_fields=["views", "clicks", "card_taps"])

        return Response({"detail": "Event tracked"}, status=status.HTTP_200_OK)


class PublicTrackEventView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PublicTrackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        username = serializer.validated_data["username"]
        event_type = serializer.validated_data["event_type"]
        link_id = serializer.validated_data.get("link_id")
        today = timezone.now().date()

        user = User.objects.filter(username__iexact=username).first()
        if not user:
            return Response({"detail": "Profile not found"}, status=status.HTTP_404_NOT_FOUND)

        row, _ = UserAnalyticsDaily.objects.get_or_create(user=user, date=today)
        if event_type == "view":
            row.views += 1
            row.save(update_fields=["views"])
            return Response({"detail": "View tracked"}, status=status.HTTP_200_OK)

        row.clicks += 1
        row.save(update_fields=["clicks"])

        if link_id:
            UserLink.objects.filter(user=user, id=link_id, is_active=True).update(clicks=F("clicks") + 1)

        return Response({"detail": "Click tracked"}, status=status.HTTP_200_OK)


class PublicContactLeadSubmitView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PublicContactLeadSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        username = serializer.validated_data["username"]
        user = User.objects.filter(username__iexact=username).first()
        if not user:
            return Response({"detail": "Profile not found"}, status=status.HTTP_404_NOT_FOUND)

        lead = UserContactLead.objects.create(
            user=user,
            name=serializer.validated_data["name"].strip(),
            email=serializer.validated_data["email"].strip(),
            phone=serializer.validated_data["phone"].strip(),
            source="public_profile",
            note=serializer.validated_data.get("where_we_met", "").strip(),
        )

        return Response(UserContactLeadSerializer(lead).data, status=status.HTTP_201_CREATED)


class UserContactLeadsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        leads = UserContactLead.objects.filter(user=request.user)
        return Response(UserContactLeadSerializer(leads, many=True).data)


class UserContactLeadDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, lead_id):
        lead = UserContactLead.objects.filter(user=request.user, id=lead_id).first()
        if not lead:
            return Response({"detail": "Contact lead not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = UserContactLeadSerializer(instance=lead, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, lead_id):
        lead = UserContactLead.objects.filter(user=request.user, id=lead_id).first()
        if not lead:
            return Response({"detail": "Contact lead not found"}, status=status.HTTP_404_NOT_FOUND)

        lead.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserDataExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        appearance, _ = UserAppearance.objects.get_or_create(user=user)
        links = UserLink.objects.filter(user=user).order_by("sort_order", "id")
        contacts = UserContactLead.objects.filter(user=user)
        analytics_rows = UserAnalyticsDaily.objects.filter(user=user).order_by("date")

        payload = {
            "meta": {
                "exported_at": timezone.now().isoformat(),
                "format_version": "1.0",
                "service": "ahju-backend",
            },
            "user": UserSerializer(user).data,
            "appearance": UserAppearanceSerializer(appearance, context={"request": request}).data,
            "links": UserLinkSerializer(links, many=True).data,
            "contacts": UserContactLeadSerializer(contacts, many=True).data,
            "analytics_daily": [
                {
                    "date": row.date.isoformat(),
                    "views": row.views,
                    "clicks": row.clicks,
                    "card_taps": row.card_taps,
                }
                for row in analytics_rows
            ],
        }

        filename = f"ahju-export-{user.username}-{timezone.now().date().isoformat()}.json"
        return Response(
            payload,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )


class AdminDatabaseExportView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        if not request.user.is_superuser:
            return Response(
                {"detail": "Superuser access required for full database export."},
                status=status.HTTP_403_FORBIDDEN,
            )

        output = StringIO()
        try:
            call_command("dumpdata", indent=2, stdout=output)
        except Exception as exc:
            return Response(
                {"detail": f"Database export failed: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        filename = f"ahju-full-backup-{timezone.now().date().isoformat()}.json"
        response = HttpResponse(output.getvalue(), content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class AdminDatabaseImportView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        if not request.user.is_superuser:
            return Response(
                {"detail": "Superuser access required for full database import."},
                status=status.HTTP_403_FORBIDDEN,
            )

        backup_file = request.FILES.get("file")
        if not backup_file:
            return Response(
                {"detail": "No backup file uploaded. Use multipart field name: file"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
                for chunk in backup_file.chunks():
                    tmp.write(chunk)
                temp_path = tmp.name

            with transaction.atomic():
                # Full-overwrite behavior: wipe existing data first, then restore fixture.
                _truncate_public_tables_except_migrations()
                call_command("loaddata", temp_path)

            return Response({"detail": "Database import completed successfully."}, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response(
                {"detail": f"Database import failed: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)


class UserAppearanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        appearance, _ = UserAppearance.objects.get_or_create(user=request.user)
        return Response(UserAppearanceSerializer(appearance, context={"request": request}).data)

    def patch(self, request):
        appearance, _ = UserAppearance.objects.get_or_create(user=request.user)
        serializer = UserAppearanceSerializer(
            instance=appearance,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def post(self, request):
        file = request.FILES.get("image")
        if not file:
            return Response({"detail": "No image file provided"}, status=status.HTTP_400_BAD_REQUEST)

        target = request.data.get("target", "profile")
        if target not in {"profile", "hero"}:
            return Response({"detail": "target must be 'profile' or 'hero'"}, status=status.HTTP_400_BAD_REQUEST)

        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            return Response({"detail": "Only image files are allowed"}, status=status.HTTP_400_BAD_REQUEST)

        appearance, _ = UserAppearance.objects.get_or_create(user=request.user)
        if target == "profile":
            appearance.profile_image.save(Path(file.name).name, file, save=False)
            # Keep legacy URL field in sync for backward compatibility.
            appearance.profile_image_url = appearance.profile_image.name
            appearance.save(update_fields=["profile_image", "profile_image_url"])
        else:
            appearance.hero_image.save(Path(file.name).name, file, save=False)
            # Keep legacy URL field in sync for backward compatibility.
            appearance.hero_image_url = appearance.hero_image.name
            appearance.save(update_fields=["hero_image", "hero_image_url"])

        saved_url = ""
        if target == "profile" and appearance.profile_image:
            saved_url = default_storage.url(appearance.profile_image.name)
        elif target == "hero" and appearance.hero_image:
            saved_url = default_storage.url(appearance.hero_image.name)

        if saved_url and not (saved_url.startswith("http://") or saved_url.startswith("https://")):
            saved_url = request.build_absolute_uri(saved_url)
        return Response({"url": saved_url}, status=status.HTTP_201_CREATED)


class UserAppearanceImageUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file = request.FILES.get("image")
        if not file:
            return Response({"detail": "No image file provided"}, status=status.HTTP_400_BAD_REQUEST)

        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            return Response({"detail": "Only image files are allowed"}, status=status.HTTP_400_BAD_REQUEST)

        original_name = Path(file.name).name
        relative_path = f"appearance/{request.user.id}/{original_name}"
        saved_path = default_storage.save(relative_path, file)
        saved_url = default_storage.url(saved_path)
        if not (saved_url.startswith("http://") or saved_url.startswith("https://")):
            saved_url = request.build_absolute_uri(saved_url)
        return Response({"url": saved_url}, status=status.HTTP_201_CREATED)


class UserLinksView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        links = UserLink.objects.filter(user=request.user)
        return Response(UserLinkSerializer(links, many=True).data)

    def post(self, request):
        serializer = UserLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        next_sort_order = UserLink.objects.filter(user=request.user).aggregate(
            max_order=Coalesce(Max("sort_order"), 0)
        )["max_order"] + 1
        link = UserLink.objects.create(
            user=request.user,
            title=serializer.validated_data["title"],
            url=serializer.validated_data["url"],
            is_active=serializer.validated_data.get("is_active", True),
            sort_order=next_sort_order,
        )
        return Response(UserLinkSerializer(link).data, status=status.HTTP_201_CREATED)


class UserLinkDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, link_id):
        link = UserLink.objects.filter(user=request.user, id=link_id).first()
        if not link:
            return Response({"detail": "Link not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = UserLinkSerializer(instance=link, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, link_id):
        link = UserLink.objects.filter(user=request.user, id=link_id).first()
        if not link:
            return Response({"detail": "Link not found"}, status=status.HTTP_404_NOT_FOUND)

        link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserPortfolioItemsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        items = UserPortfolioItem.objects.filter(user=request.user)
        return Response(UserPortfolioItemSerializer(items, many=True, context={"request": request}).data)

    def post(self, request):
        serializer = UserPortfolioItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        next_sort_order = UserPortfolioItem.objects.filter(user=request.user).aggregate(
            max_order=Coalesce(Max("sort_order"), 0)
        )["max_order"] + 1

        kind = serializer.validated_data.get("kind", UserPortfolioItem.KIND_SOCIAL)
        source_url = serializer.validated_data.get("source_url", "")
        embed_html = serializer.validated_data.get("embed_html", "")
        if kind == UserPortfolioItem.KIND_SOCIAL and source_url and not embed_html:
            embed_html = _build_embed_html(source_url)

        item = UserPortfolioItem.objects.create(
            user=request.user,
            kind=kind,
            title=serializer.validated_data.get("title", ""),
            image_url=serializer.validated_data.get("image_url", ""),
            source_url=source_url,
            embed_html=embed_html,
            description=serializer.validated_data.get("description", ""),
            is_active=serializer.validated_data.get("is_active", True),
            sort_order=next_sort_order,
        )
        return Response(
            UserPortfolioItemSerializer(item, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class UserPortfolioUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file = request.FILES.get("image")
        if not file:
            return Response({"detail": "No image file provided"}, status=status.HTTP_400_BAD_REQUEST)

        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            return Response({"detail": "Only image files are allowed"}, status=status.HTTP_400_BAD_REQUEST)

        original_name = Path(file.name).name
        relative_path = f"portfolio/{request.user.id}/{original_name}"
        saved_path = default_storage.save(relative_path, file)
        # Store relative media path so it works across devices/environments.
        # Serializer will return an absolute URL for the current request host.
        stored_path = saved_path

        next_sort_order = UserPortfolioItem.objects.filter(user=request.user).aggregate(
            max_order=Coalesce(Max("sort_order"), 0)
        )["max_order"] + 1

        item = UserPortfolioItem.objects.create(
            user=request.user,
            kind=UserPortfolioItem.KIND_UPLOAD,
            title=request.data.get("title", "").strip() or os.path.splitext(file.name)[0],
            image_url=stored_path,
            is_active=True,
            sort_order=next_sort_order,
        )
        return Response(
            UserPortfolioItemSerializer(item, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

class UserPortfolioImportImagesView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = UserPortfolioImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        source_url = serializer.validated_data["source_url"].strip()
        max_images = min(serializer.validated_data.get("max_images", 10), 10)
        preview_only = serializer.validated_data.get("preview_only", False)
        selected_images = serializer.validated_data.get("selected_images", [])

        host = (urlparse(source_url).netloc or "").lower()
        is_social_source = any(
            token in host
            for token in ["instagram.com", "x.com", "twitter.com", "facebook.com", "tiktok.com", "youtube.com", "youtu.be"]
        )

        # Preview/select mode should inspect a broad set of images from the page,
        # while final import is capped to max_images (<=10).
        should_fetch_broad = preview_only or bool(selected_images)
        fetch_limit = None if should_fetch_broad else max_images

        if is_social_source:
            candidate_urls = _collect_social_images(source_url, fetch_limit)
        else:
            candidate_urls = _collect_shop_or_store_images(source_url, fetch_limit)

        if not candidate_urls:
            return Response(
                {
                    "detail": "Could not extract images from this URL. The site may block automated access or render images only with JavaScript. Try another URL from the same site.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        available_urls = candidate_urls

        if preview_only:
            return Response(
                {
                    "count": len(available_urls),
                    "images": available_urls,
                    "max_images": max_images,
                },
                status=status.HTTP_200_OK,
            )

        if selected_images:
            available_set = set(available_urls)
            chosen_urls = []
            for image_url in selected_images:
                if image_url in available_set and image_url not in chosen_urls:
                    chosen_urls.append(image_url)
            chosen_urls = chosen_urls[:max_images]
            if not chosen_urls:
                return Response(
                    {"detail": "No valid selected images were provided for this source URL."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            chosen_urls = available_urls[:max_images]

        created_items = []
        next_sort_order = UserPortfolioItem.objects.filter(user=request.user).aggregate(
            max_order=Coalesce(Max("sort_order"), 0)
        )["max_order"] + 1

        for image_url in chosen_urls:
            item = UserPortfolioItem.objects.create(
                user=request.user,
                kind=UserPortfolioItem.KIND_UPLOAD,
                title="Imported from source",
                image_url=image_url,
                source_url=source_url,
                is_active=True,
                sort_order=next_sort_order,
            )
            next_sort_order += 1
            created_items.append(item)

        return Response(
            {
                "count": len(created_items),
                "items": UserPortfolioItemSerializer(created_items, many=True, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )

class UserPortfolioItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, item_id):
        item = UserPortfolioItem.objects.filter(user=request.user, id=item_id).first()
        if not item:
            return Response({"detail": "Portfolio item not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = UserPortfolioItemSerializer(
            instance=item,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()

        if updated.kind == UserPortfolioItem.KIND_SOCIAL and updated.source_url and not updated.embed_html:
            updated.embed_html = _build_embed_html(updated.source_url)
            updated.save(update_fields=["embed_html"])

        return Response(UserPortfolioItemSerializer(updated, context={"request": request}).data)

    def delete(self, request, item_id):
        item = UserPortfolioItem.objects.filter(user=request.user, id=item_id).first()
        if not item:
            return Response({"detail": "Portfolio item not found"}, status=status.HTTP_404_NOT_FOUND)

        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        category = (request.query_params.get("category") or "").strip()

        products = Product.objects.filter(is_active=True)
        if category:
            products = products.filter(category__iexact=category)
        if query:
            products = products.filter(Q(name__icontains=query) | Q(category__icontains=query))

        return Response(ProductSerializer(products, many=True, context={"request": request}).data)


class CartView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        session_key = request.query_params.get("session_key", "")
        scope = _resolve_cart_scope(request, session_key)
        queryset = _cart_queryset_for_scope(scope)
        return Response(_serialize_cart_response(queryset))

    def post(self, request):
        serializer = CartItemUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = Product.objects.filter(id=serializer.validated_data["product_id"], is_active=True).first()
        if not product:
            return Response({"detail": "Product not found"}, status=status.HTTP_404_NOT_FOUND)

        quantity = serializer.validated_data.get("quantity", 1)
        scope = _resolve_cart_scope(request, serializer.validated_data.get("session_key", ""))

        if scope.get("user"):
            item = CartItem.objects.filter(user=scope["user"], product=product).first()
        else:
            if not scope.get("session_key"):
                return Response({"detail": "session_key is required for guest cart"}, status=status.HTTP_400_BAD_REQUEST)
            item = CartItem.objects.filter(
                user__isnull=True,
                session_key=scope["session_key"],
                product=product,
            ).first()

        if item:
            item.quantity += quantity
            item.save(update_fields=["quantity", "updated_at"])
        else:
            item = CartItem.objects.create(
                user=scope.get("user"),
                session_key=scope.get("session_key", ""),
                product=product,
                quantity=quantity,
            )

        queryset = _cart_queryset_for_scope(scope)
        return Response(_serialize_cart_response(queryset), status=status.HTTP_201_CREATED)


class CartItemDetailView(APIView):
    permission_classes = [AllowAny]

    def _get_item(self, request, item_id, session_key=""):
        scope = _resolve_cart_scope(request, session_key)
        queryset = _cart_queryset_for_scope(scope)
        item = queryset.filter(id=item_id).first()
        return item, scope

    def patch(self, request, item_id):
        serializer = CartItemUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        item, scope = self._get_item(request, item_id, serializer.validated_data.get("session_key", ""))
        if not item:
            return Response({"detail": "Cart item not found"}, status=status.HTTP_404_NOT_FOUND)

        item.quantity = serializer.validated_data["quantity"]
        item.save(update_fields=["quantity", "updated_at"])

        queryset = _cart_queryset_for_scope(scope)
        return Response(_serialize_cart_response(queryset))

    def delete(self, request, item_id):
        session_key = request.query_params.get("session_key", "")
        item, scope = self._get_item(request, item_id, session_key)
        if not item:
            return Response({"detail": "Cart item not found"}, status=status.HTTP_404_NOT_FOUND)

        item.delete()
        queryset = _cart_queryset_for_scope(scope)
        return Response(_serialize_cart_response(queryset), status=status.HTTP_200_OK)


class CartMergeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        session_key = _normalize_session_key((request.data or {}).get("session_key", ""))
        if not session_key:
            return Response({"detail": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        _merge_guest_cart_into_user(request.user, session_key)
        queryset = CartItem.objects.filter(user=request.user).select_related("product")
        return Response(_serialize_cart_response(queryset), status=status.HTTP_200_OK)


class CheckoutInitializeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if not _get_paystack_secret_key():
            return Response(
                {
                    "detail": "Paystack is not configured on server.",
                    "error": "Set PAYSTACK_SECRET_KEY in backend .env and restart the Django server.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = CheckoutInitializeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        scope = _resolve_cart_scope(request, payload.get("session_key", ""))
        if not scope.get("user") and not scope.get("session_key"):
            return Response({"detail": "session_key is required for guest checkout"}, status=status.HTTP_400_BAD_REQUEST)

        cart_items = list(_cart_queryset_for_scope(scope))
        if not cart_items:
            return Response({"detail": "Cart is empty"}, status=status.HTTP_400_BAD_REQUEST)

        subtotal = Decimal("0.00")
        for item in cart_items:
            subtotal += Decimal(item.product.price) * Decimal(item.quantity)
        subtotal = subtotal.quantize(Decimal("0.01"))
        amount_kobo = int(subtotal * 100)

        callback_url = payload.get("callback_url", "").strip() or os.getenv("PAYSTACK_CALLBACK_URL", "").strip()
        reference = f"AHJU-{get_random_string(18).upper()}"

        with transaction.atomic():
            order = Order.objects.create(
                user=scope.get("user"),
                session_key=scope.get("session_key", ""),
                email=payload["email"],
                full_name=payload["full_name"],
                phone_number=payload["phone_number"],
                shipping_country=payload["shipping_country"],
                shipping_address=payload["shipping_address"],
                shipping_city=payload["shipping_city"],
                shipping_postal_code=payload["shipping_postal_code"],
                billing_same_as_shipping=payload.get("billing_same_as_shipping", True),
                billing_country=payload.get("billing_country", ""),
                billing_address=payload.get("billing_address", ""),
                billing_city=payload.get("billing_city", ""),
                billing_postal_code=payload.get("billing_postal_code", ""),
                currency="NGN",
                total_amount=subtotal,
                status=Order.STATUS_PENDING,
            )

            if order.billing_same_as_shipping:
                order.billing_country = order.shipping_country
                order.billing_address = order.shipping_address
                order.billing_city = order.shipping_city
                order.billing_postal_code = order.shipping_postal_code
                order.save(update_fields=[
                    "billing_country",
                    "billing_address",
                    "billing_city",
                    "billing_postal_code",
                    "updated_at",
                ])

            order_items = []
            for item in cart_items:
                line_total = (Decimal(item.product.price) * Decimal(item.quantity)).quantize(Decimal("0.01"))
                order_items.append(
                    OrderItem(
                        order=order,
                        product=item.product,
                        product_name=item.product.name,
                        unit_price=item.product.price,
                        quantity=item.quantity,
                        line_total=line_total,
                    )
                )
            OrderItem.objects.bulk_create(order_items)

            payment_tx = PaymentTransaction.objects.create(
                order=order,
                gateway=PaymentTransaction.GATEWAY_PAYSTACK,
                reference=reference,
                amount=subtotal,
                amount_kobo=amount_kobo,
                status=PaymentTransaction.STATUS_PENDING,
            )

        try:
            paystack_response = _paystack_initialize_transaction(
                email=order.email,
                amount_kobo=amount_kobo,
                reference=reference,
                callback_url=callback_url,
            )
        except Exception as exc:
            error_text = str(exc)
            payment_tx.status = PaymentTransaction.STATUS_FAILED
            payment_tx.raw_response = {"error": error_text}
            payment_tx.save(update_fields=["status", "raw_response", "updated_at"])
            order.status = Order.STATUS_FAILED
            order.save(update_fields=["status", "updated_at"])

            # Upstream auth/config errors from Paystack should be a 400 (actionable),
            # not a generic gateway outage.
            if "Paystack initialize failed (401)" in error_text or "Paystack initialize failed (403)" in error_text:
                return Response(
                    {
                        "detail": "Paystack rejected your API credentials or account configuration.",
                        "error": error_text,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {"detail": "Could not initialize payment", "error": error_text},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        ok = bool(paystack_response.get("status"))
        data = paystack_response.get("data") or {}
        if not ok or not data.get("authorization_url"):
            payment_tx.status = PaymentTransaction.STATUS_FAILED
            payment_tx.raw_response = paystack_response
            payment_tx.save(update_fields=["status", "raw_response", "updated_at"])
            order.status = Order.STATUS_FAILED
            order.save(update_fields=["status", "updated_at"])
            return Response(
                {
                    "detail": paystack_response.get("message") or "Payment initialization failed",
                    "paystack": paystack_response,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment_tx.raw_response = paystack_response
        payment_tx.save(update_fields=["raw_response", "updated_at"])

        return Response(
            {
                "authorization_url": data.get("authorization_url"),
                "access_code": data.get("access_code"),
                "reference": reference,
                "order": OrderSerializer(order).data,
                "payment": PaymentTransactionSerializer(payment_tx).data,
            },
            status=status.HTTP_200_OK,
        )


class CheckoutVerifyView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        reference = (request.query_params.get("reference") or "").strip()
        if not reference:
            return Response({"detail": "reference is required"}, status=status.HTTP_400_BAD_REQUEST)

        payment_tx = PaymentTransaction.objects.filter(reference=reference).select_related("order").first()
        if not payment_tx:
            return Response({"detail": "Payment reference not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            paystack_response = _paystack_verify_transaction(reference)
        except Exception as exc:
            return Response({"detail": "Could not verify payment", "error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        data = paystack_response.get("data") or {}
        paid_amount = int(data.get("amount") or 0)
        paid_currency = (data.get("currency") or "").upper()
        paid_status = (data.get("status") or "").lower()

        success = (
            bool(paystack_response.get("status"))
            and paid_status == "success"
            and paid_currency in {"", "NGN"}
            and paid_amount == payment_tx.amount_kobo
        )

        if success:
            _apply_successful_payment(payment_tx, paystack_response)
        else:
            payment_tx.status = PaymentTransaction.STATUS_FAILED
            payment_tx.raw_response = paystack_response
            payment_tx.save(update_fields=["status", "raw_response", "updated_at"])
            if payment_tx.order.status != Order.STATUS_PAID:
                payment_tx.order.status = Order.STATUS_FAILED
                payment_tx.order.save(update_fields=["status", "updated_at"])

        return Response(
            {
                "verified": success,
                "reference": reference,
                "order": OrderSerializer(payment_tx.order).data,
                "payment": PaymentTransactionSerializer(payment_tx).data,
                "paystack": paystack_response,
            },
            status=status.HTTP_200_OK,
        )


class PaystackWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        secret = _get_paystack_secret_key()
        if not secret:
            return Response({"detail": "PAYSTACK_SECRET_KEY not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        signature = (request.headers.get("x-paystack-signature") or "").strip()
        computed = hmac.new(secret.encode("utf-8"), request.body, hashlib.sha512).hexdigest()
        if not signature or not hmac.compare_digest(signature, computed):
            return Response({"detail": "Invalid signature"}, status=status.HTTP_400_BAD_REQUEST)

        event = request.data.get("event")
        data = request.data.get("data") or {}

        if event == "charge.success":
            reference = (data.get("reference") or "").strip()
            payment_tx = PaymentTransaction.objects.filter(reference=reference).select_related("order").first()
            if payment_tx:
                paid_amount = int(data.get("amount") or 0)
                paid_status = (data.get("status") or "").lower()
                paid_currency = (data.get("currency") or "").upper()
                if (
                    paid_status == "success"
                    and paid_amount == payment_tx.amount_kobo
                    and paid_currency in {"", "NGN"}
                ):
                    _apply_successful_payment(payment_tx, request.data)

        return Response({"received": True}, status=status.HTTP_200_OK)


class PublicProfileView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        username = (request.query_params.get("id") or "").strip()
        if not username:
            return Response({"detail": "Missing required query parameter: id"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(username__iexact=username).first()
        if not user:
            return Response({"detail": "Profile not found"}, status=status.HTTP_404_NOT_FOUND)

        appearance = UserAppearance.objects.filter(user=user).first()
        active_links = UserLink.objects.filter(user=user, is_active=True).order_by("sort_order", "id")
        active_portfolio = UserPortfolioItem.objects.filter(user=user, is_active=True).order_by("sort_order", "id")

        appearance_payload = None
        if appearance:
            appearance_payload = UserAppearanceSerializer(appearance, context={"request": request}).data

        return Response(
            {
                "username": user.username,
                "display_name": appearance_payload.get("display_name") if appearance_payload else f"@{user.username}",
                "short_bio": appearance_payload.get("short_bio") if appearance_payload else "",
                "profile_image_url": appearance_payload.get("profile_image_url") if appearance_payload else "",
                "hero_image_url": appearance_payload.get("hero_image_url") if appearance_payload else "",
                "selected_theme": appearance_payload.get("selected_theme") if appearance_payload else "minimal-light",
                "name_font": appearance_payload.get("name_font") if appearance_payload else "Inter, sans-serif",
                "name_color": appearance_payload.get("name_color") if appearance_payload else "#223136",
                "links": [
                    {
                        "id": link.id,
                        "title": link.title,
                        "url": link.url,
                    }
                    for link in active_links
                ],
                "portfolio": UserPortfolioItemSerializer(
                    active_portfolio,
                    many=True,
                    context={"request": request},
                ).data,
            }
        )
