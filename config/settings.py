from pathlib import Path
import os
import json
import hashlib
import smtplib
from dotenv import load_dotenv

try:
    import dj_database_url
except Exception:  # pragma: no cover
    dj_database_url = None

try:
    from google.oauth2 import service_account
except Exception:  # pragma: no cover
    service_account = None


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = ""):
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
RENDER = env_bool("RENDER", False)
KOYEB = env_bool("KOYEB", False)
DEPLOYED_ENV = RENDER or KOYEB
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
USE_SUPABASE_STORAGE = bool(SUPABASE_URL and SUPABASE_STORAGE_BUCKET and SUPABASE_SERVICE_ROLE_KEY)
GOOGLE_CLOUD_STORAGE_BUCKET = os.getenv("GOOGLE_CLOUD_STORAGE_BUCKET") or os.getenv("FIREBASE_STORAGE_BUCKET")
GOOGLE_CLOUD_PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT_ID")
GOOGLE_CLOUD_STORAGE_CREDENTIALS_JSON = os.getenv("GOOGLE_CLOUD_STORAGE_CREDENTIALS_JSON") or os.getenv("FIREBASE_CREDENTIALS_JSON")
GOOGLE_CLOUD_STORAGE_CREDENTIALS_PATH = os.getenv("GOOGLE_CLOUD_STORAGE_CREDENTIALS") or os.getenv("FIREBASE_CREDENTIALS")
GOOGLE_CLOUD_STORAGE_CREDENTIALS = None

if service_account:
    if GOOGLE_CLOUD_STORAGE_CREDENTIALS_JSON:
        try:
            GOOGLE_CLOUD_STORAGE_CREDENTIALS = service_account.Credentials.from_service_account_info(
                json.loads(GOOGLE_CLOUD_STORAGE_CREDENTIALS_JSON)
            )
            if not GOOGLE_CLOUD_PROJECT_ID:
                GOOGLE_CLOUD_PROJECT_ID = getattr(GOOGLE_CLOUD_STORAGE_CREDENTIALS, "project_id", None)
        except (ValueError, TypeError):
            GOOGLE_CLOUD_STORAGE_CREDENTIALS = None
    if not GOOGLE_CLOUD_STORAGE_CREDENTIALS and GOOGLE_CLOUD_STORAGE_CREDENTIALS_PATH:
        try:
            GOOGLE_CLOUD_STORAGE_CREDENTIALS = service_account.Credentials.from_service_account_file(
                GOOGLE_CLOUD_STORAGE_CREDENTIALS_PATH
            )
            if not GOOGLE_CLOUD_PROJECT_ID:
                GOOGLE_CLOUD_PROJECT_ID = getattr(GOOGLE_CLOUD_STORAGE_CREDENTIALS, "project_id", None)
        except FileNotFoundError:
            GOOGLE_CLOUD_STORAGE_CREDENTIALS = None

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "users",
    "payments",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if DEPLOYED_ENV:
    MIDDLEWARE.insert(2, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": os.getenv("DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.getenv("DB_NAME", BASE_DIR / "db.sqlite3"),
        "USER": os.getenv("DB_USER", ""),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", ""),
        "PORT": os.getenv("DB_PORT", ""),
    }
}

database_url = os.getenv("DATABASE_URL")
if database_url and dj_database_url:
    DATABASES["default"] = dj_database_url.parse(database_url, conn_max_age=600, ssl_require=True)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Keep local filesystem media as default (Render + persistent disk workflow).
# Enable Google Cloud Storage only when explicitly opted in.
USE_GOOGLE_CLOUD_STORAGE = env_bool("USE_GOOGLE_CLOUD_STORAGE", False) and bool(
    GOOGLE_CLOUD_STORAGE_BUCKET and GOOGLE_CLOUD_STORAGE_CREDENTIALS
)

MEDIA_ROOT = Path(os.getenv("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))
if USE_SUPABASE_STORAGE:
    MEDIA_URL = f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/"
elif USE_GOOGLE_CLOUD_STORAGE:
    MEDIA_URL = f"https://storage.googleapis.com/{GOOGLE_CLOUD_STORAGE_BUCKET}/"
else:
    MEDIA_URL = "/media/"

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage" if DEPLOYED_ENV else "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

if USE_GOOGLE_CLOUD_STORAGE:
    DEFAULT_FILE_STORAGE = "storages.backends.gcloud.GoogleCloudStorage"
    GS_BUCKET_NAME = GOOGLE_CLOUD_STORAGE_BUCKET
    GS_PROJECT_ID = GOOGLE_CLOUD_PROJECT_ID
    GS_CREDENTIALS = GOOGLE_CLOUD_STORAGE_CREDENTIALS
    GS_DEFAULT_ACL = "publicRead"
    STORAGES["default"] = {
        "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        "BUCKET_NAME": GS_BUCKET_NAME,
        "PROJECT_ID": GS_PROJECT_ID,
        "CREDENTIALS": GS_CREDENTIALS,
        "DEFAULT_ACL": GS_DEFAULT_ACL,
    }
elif USE_SUPABASE_STORAGE:
    STORAGES["default"] = {
        "BACKEND": "config.storage_backends.SupabaseStorage",
    }
else:
    STORAGES["default"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "LOCATION": str(MEDIA_ROOT),
        "BASE_URL": MEDIA_URL,
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

raw_jwt_signing_key = os.getenv("JWT_SIGNING_KEY", SECRET_KEY)
JWT_SIGNING_KEY = (
    raw_jwt_signing_key
    if len(raw_jwt_signing_key) >= 32
    else hashlib.sha256(raw_jwt_signing_key.encode("utf-8")).hexdigest()
)

SIMPLE_JWT = {
    "SIGNING_KEY": JWT_SIGNING_KEY,
    "ALGORITHM": "HS256",
}

CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,https://app.myahju.com,https://www.app.myahju.com",
)

CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,https://app.myahju.com,https://www.app.myahju.com",
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", DEPLOYED_ENV and not DEBUG)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", DEPLOYED_ENV and not DEBUG)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", DEPLOYED_ENV and not DEBUG)

# Payments / Paystack
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
PAYSTACK_BASE_URL = os.getenv("PAYSTACK_BASE_URL", "https://api.paystack.co").strip().rstrip("/")
PAYSTACK_CALLBACK_URL = os.getenv("PAYSTACK_CALLBACK_URL", "").strip()

# Optional merchant notification settings (email on paid orders)
PAYMENT_ORDER_NOTIFY_EMAIL = os.getenv("PAYMENT_ORDER_NOTIFY_EMAIL", "").strip()
PAYMENT_ORDER_NOTIFY_EMAILS = env_list("PAYMENT_ORDER_NOTIFY_EMAILS", PAYMENT_ORDER_NOTIFY_EMAIL)

# Minimal SMTP env support (optional)
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "").strip()
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587") or "587")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "").strip()
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "no-reply@myahju.com")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

if EMAIL_HOST and EMAIL_PORT and EMAIL_HOST_USER and not EMAIL_HOST_PASSWORD:
    raise RuntimeError("EMAIL_HOST_PASSWORD is required when EMAIL_HOST_USER is set")

if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise RuntimeError("EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be True")

try:
    if EMAIL_HOST and EMAIL_PORT:
        if EMAIL_USE_SSL:
            with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT, timeout=2):
                pass
except Exception:
    pass
