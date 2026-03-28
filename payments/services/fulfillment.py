from __future__ import annotations

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
        return

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


@transaction.atomic
def mark_payment_success(payment_id: int, *, gateway_payload: dict | None = None, source: str = "verify") -> tuple[Payment, bool]:
    """
    Idempotent success transition.
    Returns (payment, changed) where changed=False means it was already successful.
    """
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
