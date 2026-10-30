"""
URL configuration for comfy_chair project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve

urlpatterns = [
    path('api/admin/', admin.site.urls),
    path('api/user/', include('user.urls')),
    path('api/', include('article.urls')),
    path('api/', include('conference.urls')),
    path('api/', include('conference_session.urls')),
    path('api/', include('chair.urls')),
    #agregue esta linea para las urls de reviewer
    path('api/', include('reviewer.urls')),
    path('api/notifications/', include('notification.urls')),
]

# Servir archivos subidos (también en producción, el volumen es bajo)
urlpatterns += [
    re_path(r'^api/media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]