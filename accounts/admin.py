from django.contrib import admin

from .models import GoogleConnection, ProcessedEmail, SublabelReview, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "agent_enabled", "cleanup_enabled", "work_start", "work_end", "date_joined")
    fields = ("email", "first_name", "last_name", "agent_enabled", "cleanup_enabled", "work_start", "work_end", "is_active", "is_staff", "is_superuser")
    search_fields = ("email", "first_name", "last_name")


@admin.register(GoogleConnection)
class GoogleConnectionAdmin(admin.ModelAdmin):
    list_display = ("user", "google_email", "status", "connected_at", "last_run_at", "last_error", "last_cleanup_at")
    exclude = ("refresh_token",)
    readonly_fields = ("connected_at", "last_run_at", "last_cleanup_at")


@admin.register(ProcessedEmail)
class ProcessedEmailAdmin(admin.ModelAdmin):
    list_display = ("processed_at", "user", "subject", "labels", "urgency", "status")
    list_filter = ("status", "urgency")
    search_fields = ("subject", "user__email")


@admin.register(SublabelReview)
class SublabelReviewAdmin(admin.ModelAdmin):
    list_display = ("reviewed_at", "user", "label_name", "decision", "threads", "last_email_at", "recheck_after", "reason")
    list_filter = ("decision",)
    search_fields = ("label_name", "user__email")
