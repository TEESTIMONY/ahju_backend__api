from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps


def _optimize_uploaded_image(file_obj, *, max_width: int, max_height: int, quality: int = 82):
    """Resize/compress image uploads before persistence.

    Keeps transparency with PNG/WebP output and uses JPEG for opaque images.
    Falls back to the original file if processing fails.
    """

    try:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)

        with Image.open(file_obj) as image:
            image = ImageOps.exif_transpose(image)
            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )

            image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

            output = BytesIO()
            original_name = Path(getattr(file_obj, "name", "image")).stem or "image"

            if has_alpha:
                if image.mode not in ("RGBA", "LA", "P"):
                    image = image.convert("RGBA")
                extension = "png"
                image.save(output, format="PNG", optimize=True)
            else:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                extension = "jpg"
                image.save(
                    output,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                    progressive=True,
                )

            output.seek(0)
            return ContentFile(output.read(), name=f"{original_name}.{extension}")
    except Exception:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        return file_obj


class Product(models.Model):
    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    category = models.CharField(max_length=80, blank=True, default="")
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(max_digits=12, decimal_places=2)
    old_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Primary product image upload (recommended).
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    # Optional legacy/fallback URL (kept for backward compatibility).
    image_url = models.URLField(max_length=1000, blank=True, default="")
    gallery_images = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    stock_quantity = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.image and not self.image._committed:
            self.image = _optimize_uploaded_image(
                self.image,
                max_width=1800,
                max_height=1800,
            )
        super().save(*args, **kwargs)


class ProductGalleryImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="gallery_uploads")
    image = models.ImageField(upload_to="products/gallery/")
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.product.name} gallery image #{self.id}"

    def save(self, *args, **kwargs):
        if self.image and not self.image._committed:
            self.image = _optimize_uploaded_image(
                self.image,
                max_width=1800,
                max_height=1800,
            )
        super().save(*args, **kwargs)


class CartItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cart_items",
        null=True,
        blank=True,
    )
    session_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="cart_items")
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        indexes = [
            models.Index(fields=["user", "updated_at"]),
            models.Index(fields=["session_key", "updated_at"]),
        ]

    def __str__(self):
        owner = self.user.username if self.user_id else f"guest:{self.session_key[:8]}"
        return f"{owner} -> {self.product.name} x{self.quantity}"


class Order(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    session_key = models.CharField(max_length=64, blank=True, default="", db_index=True)

    email = models.EmailField()
    full_name = models.CharField(max_length=180)
    phone_number = models.CharField(max_length=32)

    shipping_country = models.CharField(max_length=120)
    shipping_address = models.CharField(max_length=240)
    shipping_city = models.CharField(max_length=120)
    shipping_postal_code = models.CharField(max_length=40)

    billing_same_as_shipping = models.BooleanField(default=True)
    billing_country = models.CharField(max_length=120, blank=True, default="")
    billing_address = models.CharField(max_length=240, blank=True, default="")
    billing_city = models.CharField(max_length=120, blank=True, default="")
    billing_postal_code = models.CharField(max_length=40, blank=True, default="")

    currency = models.CharField(max_length=8, default="NGN")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order#{self.id} {self.email} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    product_name = models.CharField(max_length=180)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"Order#{self.order_id} {self.product_name} x{self.quantity}"


class PaymentTransaction(models.Model):
    GATEWAY_DEFAULT = "manual"

    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    )

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    gateway = models.CharField(max_length=24, default=GATEWAY_DEFAULT)
    reference = models.CharField(max_length=120, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_kobo = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    raw_response = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.gateway}:{self.reference} ({self.status})"


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
    profile_image = models.ImageField(upload_to="appearance/profile/", blank=True, null=True)
    hero_image = models.ImageField(upload_to="appearance/hero/", blank=True, null=True)
    profile_image_url = models.URLField(blank=True, default="")
    hero_image_url = models.URLField(blank=True, default="")
    selected_theme = models.CharField(max_length=64, blank=True, default="minimal-light")
    name_font = models.CharField(max_length=120, blank=True, default="Inter, sans-serif")
    name_color = models.CharField(max_length=16, blank=True, default="#223136")

    def __str__(self):
        return f"Appearance<{self.user}>"

    def save(self, *args, **kwargs):
        if self.profile_image and not self.profile_image._committed:
            self.profile_image = _optimize_uploaded_image(
                self.profile_image,
                max_width=1200,
                max_height=1200,
            )
        if self.hero_image and not self.hero_image._committed:
            self.hero_image = _optimize_uploaded_image(
                self.hero_image,
                max_width=1920,
                max_height=1080,
            )
        super().save(*args, **kwargs)


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
