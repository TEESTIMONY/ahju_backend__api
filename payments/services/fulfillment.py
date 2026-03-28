from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from payments.models import Order, Payment
from users.models import CartItem


def _clear_paid_items_from_cart(order: Order) -> None:
    product_quantities = (
        order.items.exclude(product_id__isnull=True)
        .values("product_id")
        .annotate(total_qty=Sum("quantity"))
    )

    if order.user_id:
        for row in product_quantities:
            cart_item = (
                CartItem.objects.select_for_update()
                .filter(user_id=order.user_id, product_id=row["product_id"])
                .first()
            )
            if not cart_item:
                continue

            qty_to_remove = int(row.get("total_qty") or 0)
            if qty_to_remove <= 0:
                continue

            if cart_item.quantity <= qty_to_remove:
                cart_item.delete()
            else:
                cart_item.quantity -= qty_to_remove
                cart_item.save(update_fields=["quantity", "updated_at"])

    if order.session_key:
        for row in product_quantities:
            cart_item = (
                CartItem.objects.select_for_update()
                .filter(user__isnull=True, session_key=order.session_key, product_id=row["product_id"])
                .first()
            )
            if not cart_item:
                continue

            qty_to_remove = int(row.get("total_qty") or 0)
            if qty_to_remove <= 0:
                continue

            if cart_item.quantity <= qty_to_remove:
                cart_item.delete()
            else:
                cart_item.quantity -= qty_to_remove
                cart_item.save(update_fields=["quantity", "updated_at"])


def _format_currency(amount: Decimal, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def _send_order_paid_email(order: Order) -> None:
    recipients = list(getattr(settings, "PAYMENT_ORDER_NOTIFY_EMAILS", []) or [])
    if not recipients:
        fallback = (getattr(settings, "PAYMENT_ORDER_NOTIFY_EMAIL", "") or "").strip()
        if fallback:
            recipients = [fallback]

    if not recipients:
        return

    lines = []
    for item in order.items.all().order_by("id"):
        lines.append(
            f"- {item.product_name} x{item.quantity} @ {_format_currency(item.unit_price, order.currency)} = {_format_currency(item.line_total, order.currency)}"
        )

    subject = f"New paid order #{order.id} - {order.full_name}"
    body = "\n".join(
        [
            "A new order has been paid successfully.",
            "",
            f"Order ID: {order.id}",
            f"Total: {_format_currency(order.total_amount, order.currency)}",
            f"Status: {order.status}",
            "",
            "Customer details:",
            f"Name: {order.full_name}",
            f"Email: {order.email}",
            f"Phone: {order.phone_number}",
            "",
            "Shipping details:",
            f"Country: {order.shipping_country}",
            f"City: {order.shipping_city}",
            f"Address: {order.shipping_address}",
            f"Postal code: {order.shipping_postal_code}",
            "",
            "Order items:",
            *lines,
            "",
            "Billing details:",
            f"Billing same as shipping: {order.billing_same_as_shipping}",
        ]
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception:
        # Never block payment processing due to notification failure
        pass


@transaction.atomic
def mark_payment_success(payment_id: int, *, gateway_payload: dict | None = None, source: str = "verify") -> tuple[Payment, bool]:
    payment = Payment.objects.select_for_update().select_related("order").get(id=payment_id)
    order: Order = payment.order

    if payment.status == Payment.STATUS_SUCCESS and order.status == Order.STATUS_PAID:
        return payment, False

    if gateway_payload:
        payment.gateway_verify_response = gateway_payload

    payment.status = Payment.STATUS_SUCCESS
    if not payment.paid_at:
        payment.paid_at = timezone.now()
    payment.save(update_fields=["status", "paid_at", "gateway_verify_response", "updated_at"])

    if order.status != Order.STATUS_PAID:
        metadata = order.metadata or {}
        metadata["payment_success_source"] = source
        order.metadata = metadata
        order.status = Order.STATUS_PAID
        order.save(update_fields=["status", "metadata", "updated_at"])

    _clear_paid_items_from_cart(order)
    _send_order_paid_email(order)
    return payment, True


@transaction.atomic
def mark_payment_failed(payment_id: int, *, gateway_payload: dict | None = None, reason: str = "") -> Payment:
    payment = Payment.objects.select_for_update().select_related("order").get(id=payment_id)
    order: Order = payment.order

    if payment.status != Payment.STATUS_SUCCESS:
        payment.status = Payment.STATUS_FAILED
        if gateway_payload:
            payment.gateway_verify_response = gateway_payload
        payment.save(update_fields=["status", "gateway_verify_response", "updated_at"])

    if order.status != Order.STATUS_PAID:
        metadata = order.metadata or {}
        if reason:
            metadata["payment_failure_reason"] = reason
        order.metadata = metadata
        order.status = Order.STATUS_FAILED
        order.save(update_fields=["status", "metadata", "updated_at"])

    return payment


@transaction.atomic
def mark_payment_abandoned(payment_id: int, *, gateway_payload: dict | None = None, reason: str = "") -> Payment:
    payment = Payment.objects.select_for_update().select_related("order").get(id=payment_id)
    order: Order = payment.order

    if payment.status != Payment.STATUS_SUCCESS:
        payment.status = Payment.STATUS_ABANDONED
        if gateway_payload:
            payment.gateway_verify_response = gateway_payload
        payment.save(update_fields=["status", "gateway_verify_response", "updated_at"])

    if order.status != Order.STATUS_PAID:
        metadata = order.metadata or {}
        if reason:
            metadata["payment_abandoned_reason"] = reason
        order.metadata = metadata
        order.status = Order.STATUS_FAILED
        order.save(update_fields=["status", "metadata", "updated_at"])

    return payment
