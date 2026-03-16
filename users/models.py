from django.conf import settings
from django.db import models


class UserAnalyticsDaily(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="analytics_daily")
    date = models.DateField()
    views = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    card_taps = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("user", "date")
        ordering = ["date"]


class UserAppearance(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="appearance")
    display_name = models.CharField(max_length=120, blank=True, default="")
    short_bio = models.CharField(max_length=280, blank=True, default="")
    profile_image_url = models.URLField(blank=True, default="")
    hero_image_url = models.URLField(blank=True, default="")
    selected_theme = models.CharField(max_length=64, blank=True, default="minimal-light")
    name_font = models.CharField(max_length=120, blank=True, default="Inter, sans-serif")
    name_color = models.CharField(max_length=16, blank=True, default="#223136")

    def __str__(self):
        return f"Appearance<{self.user}>"


class UserLink(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile_links")
    title = models.CharField(max_length=120)
    url = models.URLField(max_length=500)
    clicks = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "-created_at"]

    def __str__(self):
        return f"{self.user.username} -> {self.title}"


class UserContactLead(models.Model):
    TAG_NEW = "new"
    TAG_FOLLOW_UP = "follow_up"
    TAG_CONTACTED = "contacted"
    TAG_CLOSED = "closed"
    TAG_LOST = "lost"
    TAG_CHOICES = (
        (TAG_NEW, "New"),
        (TAG_FOLLOW_UP, "Follow up"),
        (TAG_CONTACTED, "Contacted"),
        (TAG_CLOSED, "Closed"),
        (TAG_LOST, "Lost"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contact_leads")
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    source = models.CharField(max_length=64, default="public_profile")
    tag = models.CharField(max_length=24, choices=TAG_CHOICES, default=TAG_NEW)
    note = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} <- {self.name}"



class UserPortfolioItem(models.Model):
    KIND_UPLOAD = "upload"
    KIND_SOCIAL = "social"
    KIND_CHOICES = (
        (KIND_UPLOAD, "Upload"),
        (KIND_SOCIAL, "Social"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portfolio_items")
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_UPLOAD)
    title = models.CharField(max_length=120, blank=True, default="")
    image_url = models.URLField(blank=True, default="")
    source_url = models.URLField(max_length=500, blank=True, default="")
    embed_html = models.TextField(blank=True, default="")
    description = models.CharField(max_length=280, blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "-created_at"]

    def __str__(self):
        return f"{self.user.username} portfolio<{self.kind}>"
