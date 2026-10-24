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

import gmail
import labels
import run_context

from . import cleanup_runner, google_connect, runner
from .forms import LabelFormSet, SettingsForm, SignupForm
from .models import GoogleConnection, Label, seed_labels, user_labels


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
        user = form.save()
        seed_labels(user)
        login(request, user)
        return redirect("dashboard")
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def dashboard(request):
    return _render_dashboard(request, SettingsForm(instance=request.user))


def _render_dashboard(request, form):
    connection = GoogleConnection.objects.filter(user=request.user).first()
    return render(request, "accounts/dashboard.html", {
        "connection": connection,
        "form": form,
        "activity": request.user.processed_emails.all()[:20],
        "cleanup": cleanup_runner.last_result(request.user) if connection else None,
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


@login_required
@require_POST
def cleanup_now(request):
    if not GoogleConnection.objects.filter(
        user=request.user, status=GoogleConnection.Status.CONNECTED
    ).exists():
        messages.error(request, "Connect Google first.")
        return redirect("dashboard")
    request.user.cleanup_requested_at = timezone.now()
    request.user.save(update_fields=["cleanup_requested_at"])
    messages.success(request, "Cleanup requested. It starts within a few seconds.")
    return redirect("dashboard")


@login_required
def labels_page(request):
    user_labels(request.user)
    formset = LabelFormSet(request.POST or None, queryset=request.user.labels.all(), prefix="labels")
    if request.method == "POST" and formset.is_valid():
        _save_labels(request.user, formset)
        messages.success(request, "Labels saved. The next run uses them.")
        return redirect("labels")

    gmail_names, problem = _gmail_label_names(request.user)
    missing = []
    if gmail_names is not None:
        in_gmail = {labels.key(name): name for name in gmail_names if "/" not in name}
        described = {labels.key(label.name) for label in request.user.labels.all()}
        missing = [name for k, name in in_gmail.items() if k not in described]
        for form in formset:
            form.not_in_gmail = bool(form.instance.pk) and labels.key(form.initial.get("name", "")) not in in_gmail
    return render(request, "accounts/labels.html", {"formset": formset, "missing": missing, "problem": problem})


def _save_labels(user, formset) -> None:
    rows = formset.save(commit=False)
    for label in formset.deleted_objects:
        label.delete()
    position = max(user.labels.values_list("position", flat=True), default=-1) + 1
    for label in rows:
        if label.pk is None:
            label.user, label.position = user, position
            position += 1
        label.save()


@login_required
@require_POST
def add_missing_labels(request):
    """Adds an empty row for each Gmail top-level label the user hasn't described."""
    gmail_names, problem = _gmail_label_names(request.user)
    if gmail_names is None:
        messages.error(request, problem)
        return redirect("labels")
    labels_now = user_labels(request.user)
    described = {labels.key(label.name) for label in labels_now}
    position = max((label.position for label in labels_now), default=-1) + 1
    added = []
    for name in gmail_names:
        if "/" in name or labels.key(name) in described:
            continue
        emoji, plain = labels.split_emoji(name)
        added.append(Label(user=request.user, position=position + len(added), emoji=emoji, name=plain))
        described.add(labels.key(name))
    Label.objects.bulk_create(added)
    messages.success(request, f"Added {len(added)} label{'s' if len(added) != 1 else ''}. Describe them below.")
    return redirect("labels")


@login_required
@require_POST
def reset_labels(request):
    request.user.labels.all().delete()
    seed_labels(request.user)
    messages.success(request, "Labels reset to the defaults.")
    return redirect("labels")


def _gmail_label_names(user) -> tuple[list[str] | None, str]:
    """The user's Gmail label names, or None and why Gmail couldn't be read."""
    connection = GoogleConnection.objects.filter(user=user).first()
    if not connection or connection.status != GoogleConnection.Status.CONNECTED:
        return None, "Connect Google to check your labels against Gmail."
    try:
        with run_context.use(runner.context(user)):
            return [label["name"] for label in gmail.list_labels()], ""
    except Exception as e:
        return None, f"Couldn't read your Gmail labels: {e}"


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
