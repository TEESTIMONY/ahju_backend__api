from decimal import Decimal

from django.conf import settings
from django.db import models


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
        related_name="payment_orders",
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
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order#{self.id} {self.email} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "users.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_order_items",
    )
    product_name = models.CharField(max_length=180)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"Order#{self.order_id} {self.product_name} x{self.quantity}"


class Payment(models.Model):
    PROVIDER_PAYSTACK = "paystack"

    STATUS_INITIALIZED = "initialized"
    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_ABANDONED = "abandoned"
    STATUS_CHOICES = (
        (STATUS_INITIALIZED, "Initialized"),
        (STATUS_PENDING, "Pending"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_ABANDONED, "Abandoned"),
    )

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=24, default=PROVIDER_PAYSTACK)
    reference = models.CharField(max_length=120, unique=True, db_index=True)
    currency = models.CharField(max_length=8, default="NGN")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_minor = models.PositiveBigIntegerField(default=0)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_INITIALIZED)
    authorization_url = models.URLField(max_length=1000, blank=True, default="")
    access_code = models.CharField(max_length=255, blank=True, default="")

    gateway_initialize_response = models.JSONField(default=dict, blank=True)
    gateway_verify_response = models.JSONField(default=dict, blank=True)
    last_webhook_payload = models.JSONField(default=dict, blank=True)

    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["order", "created_at"]),
        ]

    def __str__(self):
        return f"{self.provider}:{self.reference} ({self.status})"
