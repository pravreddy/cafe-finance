from django.contrib.auth import views as auth_views
from django.urls import path

from . import api, views

app_name = "ledger"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", auth_views.LoginView.as_view(template_name="ledger/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("share/", views.share, name="share"),
    path("review/<int:proof_pk>/", views.review, name="review"),
    path("done/<int:entry_pk>/", views.done, name="done"),
    path("queue/", views.queue, name="queue"),
    path("entries/<int:entry_pk>/<str:action>/", views.entry_action, name="entry_action"),
    path("group-posts/<int:pk>/dismiss/", views.group_post_dismiss, name="group_post_dismiss"),
    path("invoices/", views.invoices, name="invoices"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("proofs/<int:pk>/file/", views.proof_file, name="proof_file"),

    path("add/", views.add_entry, name="add"),
    path("balance/", views.balance, name="balance"),
    path("pnl/", views.pnl, name="pnl"),
    path("periods/", views.periods, name="periods"),
    path("periods/<int:cafe_pk>/<str:ym>/close/", views.period_close, name="period_close"),
    path("payroll/", views.payroll, name="payroll"),
    path("reports/", views.reports, name="reports"),
    path("export/", views.export, name="export"),

    path("sw.js", views.service_worker, name="sw"),
    path("manifest.json", views.manifest, name="manifest"),
    path("offline/", views.offline, name="offline"),

    path("api/upload/", api.upload, name="api_upload"),
    path("api/reconcile/", api.reconcile, name="api_reconcile"),
    path("api/reconcile/cursor/", api.reconcile_cursor, name="api_reconcile_cursor"),
]
