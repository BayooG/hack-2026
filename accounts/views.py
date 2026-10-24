from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from oauthlib.oauth2 import OAuth2Error

from . import google_connect
from .forms import SettingsForm, SignupForm
from .models import GoogleConnection


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def home(request):
    return render(request, "accounts/landing.html")


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save())
        return redirect("dashboard")
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def dashboard(request):
    return _render_dashboard(request, SettingsForm(instance=request.user))


def _render_dashboard(request, form):
    return render(request, "accounts/dashboard.html", {
        "connection": GoogleConnection.objects.filter(user=request.user).first(),
        "form": form,
        "activity": request.user.processed_emails.all()[:20],
    })


@login_required
@require_POST
def save_settings(request):
    form = SettingsForm(request.POST, instance=request.user)
    if not form.is_valid():
        return _render_dashboard(request, form)
    form.save()
    messages.success(request, "Settings saved.")
    return redirect("dashboard")


@login_required
@require_POST
def run_now(request):
    if not GoogleConnection.objects.filter(
        user=request.user, status=GoogleConnection.Status.CONNECTED
    ).exists():
        messages.error(request, "Connect Google first.")
        return redirect("dashboard")
    request.user.run_requested_at = timezone.now()
    request.user.save(update_fields=["run_requested_at"])
    messages.success(request, "Run requested. It starts within a few seconds.")
    return redirect("dashboard")


def _callback_url(request) -> str:
    return request.build_absolute_uri(reverse("google_callback"))


@login_required
def google_connect_start(request):
    if problem := google_connect.setup_problem():
        messages.error(request, problem)
        return redirect("dashboard")
    url, state, code_verifier = google_connect.authorization_url(_callback_url(request))
    request.session["google_oauth"] = {"state": state, "code_verifier": code_verifier}
    return redirect(url)


@login_required
def google_callback(request):
    pending = request.session.pop("google_oauth", None)
    if not pending or request.GET.get("state") != pending["state"]:
        messages.error(request, "The Google sign-in expired. Please try again.")
        return redirect("dashboard")
    if "code" not in request.GET:
        messages.error(request, "Google access was not granted.")
        return redirect("dashboard")

    try:
        creds = google_connect.exchange_code(
            _callback_url(request), request.GET["code"], pending["code_verifier"]
        )
    except OAuth2Error as e:
        messages.error(request, f"Google sign-in failed: {e.description or e.error}")
        return redirect("dashboard")
    if not google_connect.has_all_scopes(creds):
        messages.error(request, "Please tick all the Gmail and Calendar boxes on Google's screen.")
        return redirect("dashboard")
    if not creds.refresh_token:
        messages.error(request, "Google didn't return a long-lived token. Please try again.")
        return redirect("dashboard")

    connection, _ = GoogleConnection.objects.update_or_create(
        user=request.user,
        defaults={
            "google_email": google_connect.account_email(creds),
            "refresh_token": google_connect.encrypt(creds.refresh_token),
            "status": GoogleConnection.Status.CONNECTED,
            "last_error": "",
        },
    )
    messages.success(request, f"Connected {connection.google_email}.")
    return redirect("dashboard")


@login_required
@require_POST
def google_disconnect(request):
    if connection := GoogleConnection.objects.filter(user=request.user).first():
        google_connect.revoke(connection)
        connection.delete()
        messages.success(request, "Google disconnected.")
    return redirect("dashboard")
