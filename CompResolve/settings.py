import os
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(os.path.join(BASE_DIR, '.env'))

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-fallback-key-for-migration-only')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() == 'true'

# Allow hosts via env var for deployment; default to all hosts for prototype
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',') if os.environ.get('ALLOWED_HOSTS') else ['*']

# AWS Settings
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_REGION_NAME = os.environ.get('AWS_REGION', 'us-east-1')

# Cognito Settings
COGNITO_APP_CLIENT_ID = os.environ.get('COGNITO_APP_CLIENT_ID')
COGNITO_CLIENT_SECRET = os.environ.get('COGNITO_CLIENT_SECRET')
COGNITO_USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID')
# Application definition

comprehendEndpointArn = os.environ.get('comprehendEndpointArn')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'Complaints_App',
    'corsheaders',
    'rest_framework',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'corsheaders.middleware.CorsMiddleware',
]

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",  # Your frontend URL
    "https://dotcgxqsplf8p.cloudfront.net",  # CloudFront distribution
]

# CSRF Trusted Origins - Required for CloudFront/Elastic Beanstalk deployment
# Add your CloudFront and Elastic Beanstalk domains here
CSRF_TRUSTED_ORIGINS = [
    "https://dotcgxqsplf8p.cloudfront.net",  # Your CloudFront distribution
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://CompresolveNew-env.eba-tmbad6mq.us-east-1.elasticbeanstalk.com",
]

# Allow additional trusted origins from environment variable (comma-separated)
extra_csrf_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
if extra_csrf_origins:
    CSRF_TRUSTED_ORIGINS.extend([origin.strip() for origin in extra_csrf_origins.split(',') if origin.strip()])

ROOT_URLCONF = 'CompResolve.urls'


TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # Optional global templates
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


WSGI_APPLICATION = 'CompResolve.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# Deafult SQLite DB 
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

#RDS db when running locally
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    try:
        import dj_database_url
        DATABASES['default'] = dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    except Exception:
        from urllib.parse import urlparse
        url = urlparse(DATABASE_URL)
        DATABASES['default'] = {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': url.path[1:],
            'USER': url.username,
            'PASSWORD': url.password,
            'HOST': url.hostname,
            'PORT': url.port or '5432',
        }

# AWS RDS env override (alternative to DATABASE_URL)
RDS_HOST = os.environ.get('RDS_HOST') or os.environ.get('RDS_ENDPOINT')
if RDS_HOST and not DATABASE_URL:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('RDS_DB_NAME') or os.environ.get('RDS_DATABASE') or os.environ.get('POSTGRES_DB'),
        'USER': os.environ.get('RDS_USERNAME') or os.environ.get('POSTGRES_USER'),
        'PASSWORD': os.environ.get('RDS_PASSWORD') or os.environ.get('POSTGRES_PASSWORD'),
        'HOST': RDS_HOST,
        'PORT': os.environ.get('RDS_PORT', '5432'),
    }


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [] # App-level static files are found automatically
STATIC_ROOT = BASE_DIR / 'staticfiles'
# Use WhiteNoise for static file serving on Elastic Beanstalk
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
WHITENOISE_USE_FINDERS = True
# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Session settings for Elastic Beanstalk
SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
SESSION_COOKIE_HTTPONLY = True
# Only send cookies via HTTPS if not in DEBUG mode
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

#Login
LOGIN_REDIRECT_URL = 'user_home'
LOGOUT_REDIRECT_URL = 'login'
LOGIN_URL = '/login/'