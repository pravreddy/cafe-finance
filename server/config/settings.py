"""cafe-finance — Django settings.

Same shape as guhya-pos/config/settings.py so the two feel alike: everything
comes from the environment (.env in dev, compose env in prod), Postgres by
default, WhiteNoise for static, TLS terminated in front of us by nginx.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "ledger",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

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

# Postgres in every real environment. USE_SQLITE=1 is for a laptop / CI-less
# quick run only — the dedup logic relies on nothing Postgres-specific.
if os.environ.get("USE_SQLITE") == "1":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "cafe_finance"),
            "USER": os.environ.get("POSTGRES_USER", "cafe_finance"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "cafe_finance"),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Proofs (screenshots, invoice PDFs). Never served as public static files —
# ledger.views.proof_file streams them behind login.
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_URL = "/media/"

DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

# --- Machine-to-machine token for the reconciler (Mac) and mail puller ---
# One shared secret is enough for two scripts we own. Header: X-Api-Token.
API_TOKEN = os.environ.get("API_TOKEN", "")

# --- Document AI (reads payment screenshots and invoices) ---
# Provider: "auto" (Gemini if a key is set, else Ollama) | "gemini" | "ollama" | "mock".
# Same env names as guhya-pos so one .env can drive both.
DOC_AI_PROVIDER = os.environ.get("DOC_AI_PROVIDER", "auto")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "120"))
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "llama3.2-vision")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "240"))

# Optional: pull daily sales from FastPOS for the income side of the P&L.
FASTPOS_BASE_URL = os.environ.get("FASTPOS_BASE_URL", "https://pos.guhya.co.in")
FASTPOS_API_TOKEN = os.environ.get("FASTPOS_API_TOKEN", "")

# Caption template the app forwards to WhatsApp. {tag} is the #CF-000123 tag
# the reconciler uses to match a group post back to the ledger row.
WHATSAPP_CAPTION_TEMPLATE = os.environ.get(
    "WHATSAPP_CAPTION_TEMPLATE", "₹{amount} · {category} · {description} · {tag}"
)
