from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


def ping(_request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("ping", ping),
    path("admin/", admin.site.urls),
    path("", include("ledger.urls")),
]
