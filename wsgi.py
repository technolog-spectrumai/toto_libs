import os
from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'toto.settings')

application = get_wsgi_application()

# Serve static files from STATIC_ROOT
application = WhiteNoise(application)
