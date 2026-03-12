from django.contrib import admin

from .models import UserAnalyticsDaily, UserAppearance, UserContactLead, UserLink


@admin.register(UserAnalyticsDaily)
class UserAnalyticsDailyAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "views", "clicks", "card_taps")
    list_filter = ("date",)
    search_fields = ("user__username", "user__email")
    ordering = ("-date",)


@admin.register(UserAppearance)
class UserAppearanceAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "selected_theme", "name_font")
    search_fields = ("user__username", "user__email", "display_name")


@admin.register(UserLink)
class UserLinkAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "url", "clicks", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("user__username", "user__email", "title", "url")
    ordering = ("user", "sort_order", "-created_at")


@admin.register(UserContactLead)
class UserContactLeadAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "email", "phone", "source", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("user__username", "user__email", "name", "email", "phone")
    ordering = ("-created_at",)
