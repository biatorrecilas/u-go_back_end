import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv, find_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(find_dotenv())

# Suas funções originais restauradas
def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

def env_list(name, default=None):
    value = os.getenv(name)
    if value is None:
        return default or []
    return [item.strip() for item in str(value).split(',') if item.strip()]

# Quick-start development settings
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-o)yka!m-r^fl4%1chxz&@&omh_)ymu#kgj+p2(0-g8e1f^i&#)')

DEBUG = env_bool('DJANGO_DEBUG', True)

if not DEBUG and SECRET_KEY.startswith('django-insecure-'):
    raise ImproperlyConfigured('DJANGO_SECRET_KEY must be set to a strong secret when DJANGO_DEBUG=False.')

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', ['127.0.0.1', 'localhost'])

# Application definition
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'website',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# --- CORS & CSRF Settings ---

# Fixes the CSRF Origin checking failed error
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS', ["http://localhost:5173", "http://127.0.0.1:5173"])

# Allows the browser to make cross-origin requests
CORS_ALLOWED_ORIGINS = env_list('DJANGO_CORS_ALLOWED_ORIGINS', ["http://localhost:5173", "http://127.0.0.1:5173"])

# Optional but highly recommended if you are sending authentication cookies/sessions
CORS_ALLOW_CREDENTIALS = True

# Production-safe cookie and transport defaults. They remain developer-friendly
# locally and can be overridden explicitly by environment variables.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = env_bool('CSRF_COOKIE_SECURE', not DEBUG)
SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', not DEBUG)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = int(os.getenv('DJANGO_SECURE_HSTS_SECONDS', '0' if DEBUG else '31536000'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

ROOT_URLCONF = 'u_go.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'u_go.wsgi.application'
ASGI_APPLICATION = 'u_go.asgi.application'

REDIS_URL = os.getenv('REDIS_URL', '')
if REDIS_URL:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [REDIS_URL],
                'capacity': int(os.getenv('NOTIFICATION_CHANNEL_CAPACITY', '1000')),
                'expiry': int(os.getenv('NOTIFICATION_CHANNEL_EXPIRY', '60')),
            },
        },
    }
else:
    # Safe for local development and tests only. Production checks require Redis.
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

# O BANCO DE DADOS FINAL
use_sqlite = env_bool('USE_SQLITE', False)

if use_sqlite:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('POSTGRES_DB', 'u_go_db'),
            'USER': os.getenv('POSTGRES_USER', 'postgres'),
            'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'root'), # Pega a senha 'root' do seu .env
            'HOST': os.getenv('POSTGRES_HOST', '127.0.0.1'),
            'PORT': os.getenv('POSTGRES_PORT', '5432'),
            'CONN_MAX_AGE': int(os.getenv('POSTGRES_CONN_MAX_AGE', '0')),
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.getenv('DJANGO_MEDIA_ROOT') or (BASE_DIR / 'media'))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('DJANGO_MAX_REQUEST_BYTES', str(12 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('DJANGO_FILE_MEMORY_BYTES', str(3 * 1024 * 1024)))

AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME', '')
if AWS_STORAGE_BUCKET_NAME:
    INSTALLED_APPS.append('storages')
    storage_options = {
        'bucket_name': AWS_STORAGE_BUCKET_NAME,
        'region_name': os.getenv('AWS_S3_REGION_NAME') or None,
        'endpoint_url': os.getenv('AWS_S3_ENDPOINT_URL') or None,
        'default_acl': None,
        'file_overwrite': False,
        'querystring_auth': True,
        'querystring_expire': int(os.getenv('AWS_QUERYSTRING_EXPIRE', '3600')),
        'object_parameters': {'CacheControl': 'private, max-age=3600'},
    }
    STORAGES = {
        'default': {'BACKEND': 'storages.backends.s3.S3Storage', 'OPTIONS': storage_options},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
else:
    STORAGES = {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }

FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://127.0.0.1:5173')
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', True)
EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', False)
EMAIL_TIMEOUT = int(os.getenv('EMAIL_TIMEOUT', '10'))
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'U-Go <nao-responda@u-go.app>')
PASSWORD_RESET_TIMEOUT = int(os.getenv('PASSWORD_RESET_TIMEOUT', '3600'))
CO2_PER_AVOIDED_KM_KG = float(os.getenv('CO2_PER_AVOIDED_KM_KG', '0.192'))
TREE_CO2_SEQUESTRATION_KG_PER_YEAR = float(os.getenv('TREE_CO2_SEQUESTRATION_KG_PER_YEAR', '60'))

if TREE_CO2_SEQUESTRATION_KG_PER_YEAR <= 0:
    raise ImproperlyConfigured('TREE_CO2_SEQUESTRATION_KG_PER_YEAR must be greater than zero.')

if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured('EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be enabled.')

AUTH_USER_MODEL = 'website.User'

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.getenv('THROTTLE_ANON_RATE', '120/hour'),
        'user': os.getenv('THROTTLE_USER_RATE', '1200/hour'),
        'signup': os.getenv('THROTTLE_SIGNUP_RATE', '5/hour'),
        'login': os.getenv('THROTTLE_LOGIN_RATE', '10/minute'),
        'verification': os.getenv('THROTTLE_VERIFICATION_RATE', '10/minute'),
        'password_reset': os.getenv('THROTTLE_PASSWORD_RESET_RATE', '5/hour'),
    },
}
