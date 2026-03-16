import os
import json
import tempfile
import re
from pathlib import Path
from datetime import timedelta
from uuid import uuid4
from io import StringIO
from urllib.parse import quote_plus, urlparse, parse_qs

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Sum
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

from .models import UserAnalyticsDaily, UserAppearance, UserContactLead, UserLink, UserPortfolioItem
from .serializers import (
    DashboardTrackSerializer,
    GoogleAuthSerializer,
    PublicTrackSerializer,
    PublicContactLeadSubmitSerializer,
    UserContactLeadSerializer,
    UserPortfolioItemSerializer,
    UserAppearanceSerializer,
    UserLinkSerializer,
    UpdateMeSerializer,
    UserSerializer,
    UsernameSerializer,
)


User = get_user_model()


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
            total_card_taps=Coalesce(Sum("card_taps"), 0),
        )

        total_views = totals["total_views"]
        total_clicks = totals["total_clicks"]
        total_card_taps = totals["total_card_taps"]
        ctr = round((total_clicks / total_views) * 100, 1) if total_views else 0.0

        return Response(
            {
                "total_views": total_views,
                "total_clicks": total_clicks,
                "total_card_taps": total_card_taps,
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

        data = []
        for i in range(7):
            date = start_date + timedelta(days=i)
            row = by_date.get(date)
            data.append(
                {
                    "day": date.strftime("%a"),
                    "views": row.views if row else 0,
                    "clicks": row.clicks if row else 0,
                    "cardTaps": row.card_taps if row else 0,
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
        )

        return Response(UserContactLeadSerializer(lead).data, status=status.HTTP_201_CREATED)


class UserContactLeadsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        leads = UserContactLead.objects.filter(user=request.user)
        return Response(UserContactLeadSerializer(leads, many=True).data)


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
            "appearance": UserAppearanceSerializer(appearance).data,
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
        return Response(UserAppearanceSerializer(appearance).data)

    def patch(self, request):
        appearance, _ = UserAppearance.objects.get_or_create(user=request.user)
        serializer = UserAppearanceSerializer(instance=appearance, data=request.data, partial=True)
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

        ext = os.path.splitext(file.name)[1] or ".jpg"
        relative_path = f"appearance/{request.user.id}/{uuid4().hex}{ext}"
        saved_path = default_storage.save(relative_path, file)
        image_url = request.build_absolute_uri(f"/media/{saved_path}")

        appearance, _ = UserAppearance.objects.get_or_create(user=request.user)
        if target == "profile":
            appearance.profile_image_url = image_url
            appearance.save(update_fields=["profile_image_url"])
        else:
            appearance.hero_image_url = image_url
            appearance.save(update_fields=["hero_image_url"])

        return Response({"url": image_url}, status=status.HTTP_201_CREATED)


class UserAppearanceImageUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file = request.FILES.get("image")
        if not file:
            return Response({"detail": "No image file provided"}, status=status.HTTP_400_BAD_REQUEST)

        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            return Response({"detail": "Only image files are allowed"}, status=status.HTTP_400_BAD_REQUEST)

        ext = os.path.splitext(file.name)[1] or ".jpg"
        relative_path = f"appearance/{request.user.id}/{uuid4().hex}{ext}"
        saved_path = default_storage.save(relative_path, file)
        image_url = request.build_absolute_uri(f"/media/{saved_path}")

        return Response({"url": image_url}, status=status.HTTP_201_CREATED)


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


class UserPortfolioItemsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        items = UserPortfolioItem.objects.filter(user=request.user)
        return Response(UserPortfolioItemSerializer(items, many=True).data)

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
        return Response(UserPortfolioItemSerializer(item).data, status=status.HTTP_201_CREATED)


class UserPortfolioUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        file = request.FILES.get("image")
        if not file:
            return Response({"detail": "No image file provided"}, status=status.HTTP_400_BAD_REQUEST)

        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            return Response({"detail": "Only image files are allowed"}, status=status.HTTP_400_BAD_REQUEST)

        ext = os.path.splitext(file.name)[1] or ".jpg"
        relative_path = f"portfolio/{request.user.id}/{uuid4().hex}{ext}"
        saved_path = default_storage.save(relative_path, file)
        image_url = request.build_absolute_uri(f"/media/{saved_path}")

        next_sort_order = UserPortfolioItem.objects.filter(user=request.user).aggregate(
            max_order=Coalesce(Max("sort_order"), 0)
        )["max_order"] + 1

        item = UserPortfolioItem.objects.create(
            user=request.user,
            kind=UserPortfolioItem.KIND_UPLOAD,
            title=request.data.get("title", "").strip() or os.path.splitext(file.name)[0],
            image_url=image_url,
            is_active=True,
            sort_order=next_sort_order,
        )
        return Response(UserPortfolioItemSerializer(item).data, status=status.HTTP_201_CREATED)


class UserPortfolioItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, item_id):
        item = UserPortfolioItem.objects.filter(user=request.user, id=item_id).first()
        if not item:
            return Response({"detail": "Portfolio item not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = UserPortfolioItemSerializer(instance=item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()

        if updated.kind == UserPortfolioItem.KIND_SOCIAL and updated.source_url and not updated.embed_html:
            updated.embed_html = _build_embed_html(updated.source_url)
            updated.save(update_fields=["embed_html"])

        return Response(UserPortfolioItemSerializer(updated).data)

    def delete(self, request, item_id):
        item = UserPortfolioItem.objects.filter(user=request.user, id=item_id).first()
        if not item:
            return Response({"detail": "Portfolio item not found"}, status=status.HTTP_404_NOT_FOUND)

        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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

        return Response(
            {
                "username": user.username,
                "display_name": appearance.display_name if appearance else f"@{user.username}",
                "short_bio": appearance.short_bio if appearance else "",
                "profile_image_url": appearance.profile_image_url if appearance else "",
                "hero_image_url": appearance.hero_image_url if appearance else "",
                "selected_theme": appearance.selected_theme if appearance else "minimal-light",
                "name_font": appearance.name_font if appearance else "Inter, sans-serif",
                "name_color": appearance.name_color if appearance else "#223136",
                "links": [
                    {
                        "id": link.id,
                        "title": link.title,
                        "url": link.url,
                    }
                    for link in active_links
                ],
                "portfolio": [
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "title": item.title,
                        "image_url": item.image_url,
                        "source_url": item.source_url,
                        "embed_html": item.embed_html,
                        "description": item.description,
                    }
                    for item in active_portfolio
                ],
            }
        )
