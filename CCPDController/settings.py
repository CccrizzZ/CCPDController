from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
# BASE_DIR = Path(__file__).resolve().parent.parent
# BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'whos-your-daddy-django-insecure-x9@&zufge$doq71yfj!wfl*9ke=5&^+e-yjn*p+-97wz1)w)y1'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DEBUG", "False") == "True"

# ALLOWED_HOSTS = [
#     '192.168.2.62',
#     '127.0.0.1',
#     '142.126.96.24',
#     'localhost'
# ]

ALLOWED_HOSTS = ['*']

# JWT Secret
JWT_SECRET_KEY = os.getenv('JWT_SECRET')

# Application definition
INSTALLED_APPS = [
    'werkzeug_debugger_runserver',
    'django_extensions',
    'rest_framework',
    'corsheaders',
    'django_user_agents',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'imageController',
    'inventoryController',
    'userController',
    'adminController'
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django_user_agents.middleware.UserAgentMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'CCPDController.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
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

WSGI_APPLICATION = 'CCPDController.wsgi.application'

# https settings
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = False   # http -> https 

# cookies settings
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'None'
CSRF_COOKIE_SAMESITE = 'None'

# cors settings
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = True
# CORS_ALLOWED_ORIGINS = [
    # "http://192.168.56.1:8100",
    # "http://192.168.56.1:5173",
    # 'http://172.18.208.1',
    # 'https://172.18.208.1',
    # "http://localhost:5173",
    # "https://localhost:5173",
    # "http://142.126.96.24",
    # "https://142.126.96.24",
    # "http://127.0.0.1:8100",
    # "http://127.0.0.1:5173",
    # "http://192.168.2.62:8100",
    # "http://192.168.2.62:5173",
# ]

# csrf settings
CSRF_COOKIE_HTTPONLY = True
CSRF_TRUSTED_ORIGINS = [
    # 'http://172.18.208.1',
    # 'https://172.18.208.1',
    # "http://localhost",
    # "https://localhost",
    # "http://142.126.96.24",
    # "https://142.126.96.24",
    # "http://127.0.0.1:8100",
    # "http://127.0.0.1:5173",
    # "http://192.168.2.62:8100",
    # "http://192.168.2.62:5173",
]

# Django rest framework
REST_FRAMEWORK = {
    # default auth class for all routes
    'DEFAULT_AUTHENTICATION_CLASSES': [
        # 'CCPDController.authentication.JWTAuthentication',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle'
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '20/min',
        'user': '20/min'
    }
}

# Name of cache backend to cache user agents. If it not specified default
# cache alias will be used. Set to `None` to disable caching.
# USER_AGENTS_CACHE = None

# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

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
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

# for manage.py collectstatic command
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'