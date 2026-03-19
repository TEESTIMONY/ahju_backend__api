from django.contrib.auth import get_user_model
from urllib.parse import urlparse
from rest_framework import serializers

from django.conf import settings
from django.core.files.storage import default_storage

from .models import UserAppearance, UserContactLead, UserLink, UserPortfolioItem


User = get_user_model()


def _absolute_media_url(request, value: str) -> str:
    """Return an absolute URL for a stored image reference."""

    raw = (value or "").strip()
    if not raw:
        return ""

    # Already absolute. Rewrite to the current request host if the path lives under MEDIA_URL
    if raw.startswith("http://") or raw.startswith("https://"):
        try:
            parsed = urlparse(raw)
            if parsed.path and (
                parsed.path.startswith(settings.MEDIA_URL) or parsed.path.startswith("/media/")
            ):
                relative = parsed.path
                if parsed.query:
                    relative = f"{relative}?{parsed.query}"
                if request is None:
                    return relative
                return request.build_absolute_uri(relative)
        except Exception:
            pass
        return raw

    try:
        storage_url = default_storage.url(raw)
    except Exception:
        storage_url = raw

    if storage_url.startswith("http://") or storage_url.startswith("https://"):
        return storage_url

    if request is None:
        return storage_url

    return request.build_absolute_uri(storage_url)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "date_joined"]
        read_only_fields = ["id", "email", "date_joined", "username"]


class UpdateMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["first_name", "last_name"]


class GoogleAuthSerializer(serializers.Serializer):
    id_token = serializers.CharField()


class UsernameSerializer(serializers.Serializer):
    username = serializers.RegexField(
        regex=r"^[a-zA-Z0-9_]{3,30}$",
        error_messages={
            "invalid": "Username must be 3-30 chars and contain only letters, numbers, or underscores.",
        },
    )


class DashboardTrackSerializer(serializers.Serializer):
    event_type = serializers.ChoiceField(choices=["view", "click", "card_tap"])
    count = serializers.IntegerField(min_value=1, default=1)


class PublicTrackSerializer(serializers.Serializer): 
    username = serializers.RegexField(
        regex=r"^[a-zA-Z0-9_]{3,30}$",
        error_messages={
            "invalid": "Username must be 3-30 chars and contain only letters, numbers, or underscores.",
        },
    )
    event_type = serializers.ChoiceField(choices=["view", "click"])
    link_id = serializers.IntegerField(required=False, min_value=1)


class UserAppearanceSerializer(serializers.ModelSerializer):
    profile_image_url = serializers.SerializerMethodField()
    hero_image_url = serializers.SerializerMethodField()

    def get_profile_image_url(self, obj):
        return _absolute_media_url(self.context.get("request"), obj.profile_image_url)

    def get_hero_image_url(self, obj):
        return _absolute_media_url(self.context.get("request"), obj.hero_image_url)

    class Meta:
        model = UserAppearance
        fields = [
            "display_name",
            "short_bio",
            "profile_image_url",
            "hero_image_url",
            "selected_theme",
            "name_font",
            "name_color",
        ]


class UserLinkSerializer(serializers.ModelSerializer):
    def validate_url(self, value):
        raw = (value or '').strip()
        if not raw:
            raise serializers.ValidationError('URL is required.')

        parsed = urlparse(raw)
        if not parsed.scheme:
            raw = f'https://{raw}'

        return raw

    class Meta:
        model = UserLink
        fields = ["id", "title", "url", "clicks", "is_active", "sort_order", "created_at"]
        read_only_fields = ["id", "clicks", "sort_order", "created_at"]


class PublicContactLeadSubmitSerializer(serializers.Serializer):
    username = serializers.RegexField(
        regex=r"^[a-zA-Z0-9_]{3,30}$",
        error_messages={
            "invalid": "Username must be 3-30 chars and contain only letters, numbers, or underscores.",
        },
    )
    name = serializers.CharField(max_length=120)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=32)
    where_we_met = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class UserContactLeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserContactLead
        fields = ["id", "name", "email", "phone", "source", "tag", "note", "created_at"]



class UserPortfolioItemSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    def get_image_url(self, obj):
        return _absolute_media_url(self.context.get("request"), obj.image_url)

    class Meta:
        model = UserPortfolioItem
        fields = [
            "id",
            "kind",
            "title",
            "image_url",
            "source_url",
            "embed_html",
            "description",
            "is_active",
            "sort_order",
            "created_at",
        ]
        read_only_fields = ["id", "sort_order", "created_at"]

class UserPortfolioImportSerializer(serializers.Serializer):
    source_url = serializers.URLField(max_length=500)
    max_images = serializers.IntegerField(min_value=1, max_value=10, required=False, default=10)
    preview_only = serializers.BooleanField(required=False, default=False)
    selected_images = serializers.ListField(
        child=serializers.URLField(max_length=1000),
        required=False,
        allow_empty=True,
        default=list,
    )
