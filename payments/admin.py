from django.contrib import admin

from .models import Order, OrderItem, Payment


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = (
        "product",
        "product_name",
        "unit_price",
        "quantity",
        "line_total",
        "created_at",
    )
    can_delete = False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = (
        "provider",
        "reference",
        "currency",
        "amount",
        "amount_minor",
        "status",
        "authorization_url",
        "access_code",
        "paid_at",
        "created_at",
        "updated_at",
    )
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "email",
        "full_name",
        "phone_number",
        "status",
        "currency",
        "total_amount",
        "shipping_country",
        "shipping_city",
        "created_at",
    )
    list_filter = ("status", "currency", "shipping_country", "created_at")
    search_fields = (
        "email",
        "full_name",
        "phone_number",
        "shipping_address",
        "shipping_city",
        "shipping_postal_code",
    )
    readonly_fields = ("created_at", "updated_at")
    inlines = [OrderItemInline, PaymentInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "reference", "provider", "status", "currency", "amount", "order", "created_at")
    list_filter = ("provider", "status", "currency", "created_at")
    search_fields = ("reference", "order__email", "order__full_name", "order__phone_number")
    readonly_fields = ("created_at", "updated_at", "paid_at")


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "product_name", "quantity", "unit_price", "line_total", "created_at")
    list_filter = ("created_at",)
    search_fields = ("order__email", "product_name")
