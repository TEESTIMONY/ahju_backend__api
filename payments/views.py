from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import CartItem

from .models import Order, OrderItem, Payment
from .serializers import (
    OrderSerializer,
    PaymentInitializeSerializer,
    PaymentSerializer,
    to_minor_units,
)
from .services.fulfillment import (
    mark_payment_abandoned,
    mark_payment_failed,
    mark_payment_success,
)
from .services.paystack import (
    PaystackServiceError,
    initialize_transaction,
    validate_webhook_signature,
    verify_transaction,
)


def _normalize_session_key(raw_value: str) -> str:
    return (raw_value or "").strip()[:64]


def _resolve_cart_queryset(request, session_key: str):
    if request.user and request.user.is_authenticated:
        return CartItem.objects.filter(user=request.user).select_related("product"), "", request.user
    normalized = _normalize_session_key(session_key)
    if not normalized:
        return None, "", None
    return CartItem.objects.filter(user__isnull=True, session_key=normalized).select_related("product"), normalized, None


def _build_reference() -> str:
    return f"AHJU-{timezone.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:10].upper()}"


def _validate_gateway_success(payment: Payment, gateway_response: dict) -> tuple[bool, str]:
    gateway_ok = bool(gateway_response.get("status"))
    gateway_data = gateway_response.get("data") or {}

    if not gateway_ok:
        return False, gateway_response.get("message") or "Gateway verify returned unsuccessful status"

    gateway_reference = str(gateway_data.get("reference") or "")
    gateway_amount_minor = int(gateway_data.get("amount") or 0)
    gateway_currency = str(gateway_data.get("currency") or "").upper()
    gateway_tx_status = str(gateway_data.get("status") or "").lower()

    if gateway_reference != payment.reference:
        return False, "Reference mismatch"
    if gateway_amount_minor != payment.amount_minor:
        return False, "Amount mismatch"
    if gateway_currency != payment.currency.upper():
        return False, "Currency mismatch"
    if gateway_tx_status != "success":
        return False, f"Payment status is {gateway_tx_status or 'unknown'}"

    return True, "ok"


class PaymentInitializeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PaymentInitializeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        cart_queryset, session_key, user = _resolve_cart_queryset(request, payload.get("session_key", ""))
        if cart_queryset is None:
            return Response({"detail": "session_key is required for guest checkout"}, status=status.HTTP_400_BAD_REQUEST)

        cart_items = list(cart_queryset.order_by("-updated_at", "-created_at"))
        if not cart_items:
            return Response({"detail": "Cannot checkout with an empty cart"}, status=status.HTTP_400_BAD_REQUEST)

        total_amount = Decimal("0.00")
        for item in cart_items:
            if not item.product or not item.product.is_active:
                return Response({"detail": f"Product is unavailable in cart item #{item.id}"}, status=status.HTTP_400_BAD_REQUEST)
            line_total = Decimal(item.product.price) * item.quantity
            total_amount += line_total

        if total_amount <= 0:
            return Response({"detail": "Invalid cart total"}, status=status.HTTP_400_BAD_REQUEST)

        amount_minor = to_minor_units(total_amount)
        reference = _build_reference()
        callback_url = (payload.get("callback_url") or getattr(settings, "PAYSTACK_CALLBACK_URL", "") or "").strip()

        with transaction.atomic():
            order = Order.objects.create(
                user=user,
                session_key=session_key,
                email=payload["email"],
                full_name=payload["full_name"],
                phone_number=payload["phone_number"],
                shipping_country=payload["shipping_country"],
                shipping_address=payload["shipping_address"],
                shipping_city=payload["shipping_city"],
                shipping_postal_code=payload["shipping_postal_code"],
                billing_same_as_shipping=payload["billing_same_as_shipping"],
                billing_country=payload.get("billing_country", ""),
                billing_address=payload.get("billing_address", ""),
                billing_city=payload.get("billing_city", ""),
                billing_postal_code=payload.get("billing_postal_code", ""),
                currency="NGN",
                total_amount=total_amount,
                status=Order.STATUS_PENDING,
                metadata={"source": "api/payments/initialize"},
            )

            order_items = []
            for item in cart_items:
                unit_price = Decimal(item.product.price)
                order_items.append(
                    OrderItem(
                        order=order,
                        product=item.product,
                        product_name=item.product.name,
                        unit_price=unit_price,
                        quantity=item.quantity,
                        line_total=unit_price * item.quantity,
                    )
                )
            OrderItem.objects.bulk_create(order_items)

            payment = Payment.objects.create(
                order=order,
                provider=Payment.PROVIDER_PAYSTACK,
                reference=reference,
                currency="NGN",
                amount=total_amount,
                amount_minor=amount_minor,
                status=Payment.STATUS_INITIALIZED,
            )

        try:
            gateway_response = initialize_transaction(
                email=order.email,
                amount_minor=amount_minor,
                reference=reference,
                callback_url=callback_url,
                metadata={"order_id": order.id, "payment_id": payment.id},
            )
        except PaystackServiceError as exc:
            mark_payment_failed(payment.id, gateway_payload={"error": exc.message, "payload": exc.payload}, reason=exc.message)
            return Response({"detail": exc.message}, status=status.HTTP_502_BAD_GATEWAY)

        gateway_data = gateway_response.get("data") or {}
        authorization_url = (gateway_data.get("authorization_url") or "").strip()
        access_code = (gateway_data.get("access_code") or "").strip()
        gateway_reference = (gateway_data.get("reference") or "").strip()

        if not gateway_response.get("status") or not authorization_url:
            mark_payment_failed(payment.id, gateway_payload=gateway_response, reason="initialize_failed")
            return Response({"detail": gateway_response.get("message") or "Could not initialize payment"}, status=status.HTTP_502_BAD_GATEWAY)

        if gateway_reference and gateway_reference != payment.reference:
            mark_payment_failed(payment.id, gateway_payload=gateway_response, reason="initialize_reference_mismatch")
            return Response({"detail": "Gateway returned mismatched reference"}, status=status.HTTP_502_BAD_GATEWAY)

        payment.gateway_initialize_response = gateway_response
        payment.authorization_url = authorization_url
        payment.access_code = access_code
        payment.status = Payment.STATUS_PENDING
        payment.save(update_fields=["gateway_initialize_response", "authorization_url", "access_code", "status", "updated_at"])

        return Response(
            {
                "authorization_url": authorization_url,
                "access_code": access_code,
                "reference": payment.reference,
                "order": OrderSerializer(order).data,
                "payment": PaymentSerializer(payment).data,
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentVerifyView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        reference = (request.query_params.get("reference") or "").strip()
        if not reference:
            return Response({"detail": "reference query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        payment = Payment.objects.select_related("order").filter(reference=reference).first()
        if not payment:
            return Response({"detail": "Payment not found"}, status=status.HTTP_404_NOT_FOUND)

        if payment.status == Payment.STATUS_SUCCESS and payment.order.status == Order.STATUS_PAID:
            return Response(
                {
                    "verified": True,
                    "reference": payment.reference,
                    "order": OrderSerializer(payment.order).data,
                    "payment": PaymentSerializer(payment).data,
                    "gateway": payment.gateway_verify_response,
                },
                status=status.HTTP_200_OK,
            )

        try:
            gateway_response = verify_transaction(reference)
        except PaystackServiceError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_502_BAD_GATEWAY)

        is_valid, reason = _validate_gateway_success(payment, gateway_response)
        if not is_valid:
            mark_payment_failed(payment.id, gateway_payload=gateway_response, reason=reason)
            payment.refresh_from_db()
            return Response(
                {
                    "verified": False,
                    "reference": payment.reference,
                    "order": OrderSerializer(payment.order).data,
                    "payment": PaymentSerializer(payment).data,
                    "gateway": gateway_response,
                    "detail": reason,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        mark_payment_success(payment.id, gateway_payload=gateway_response, source="verify")
        payment.refresh_from_db()
        return Response(
            {
                "verified": True,
                "reference": payment.reference,
                "order": OrderSerializer(payment.order).data,
                "payment": PaymentSerializer(payment).data,
                "gateway": gateway_response,
            },
            status=status.HTTP_200_OK,
        )


class PaymentWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        signature = request.headers.get("x-paystack-signature", "")
        if not validate_webhook_signature(request.body, signature):
            return Response({"detail": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return Response({"detail": "Invalid JSON payload"}, status=status.HTTP_400_BAD_REQUEST)

        event = str(payload.get("event") or "").strip().lower()
        data = payload.get("data") or {}
        reference = str(data.get("reference") or "").strip()

        if not reference:
            return Response({"status": "ignored", "reason": "missing_reference"}, status=status.HTTP_200_OK)

        payment = Payment.objects.select_related("order").filter(reference=reference).first()
        if not payment:
            return Response({"status": "ignored", "reason": "payment_not_found"}, status=status.HTTP_200_OK)

        payment.last_webhook_payload = payload
        payment.save(update_fields=["last_webhook_payload", "updated_at"])

        if event == "charge.success":
            try:
                gateway_response = verify_transaction(reference)
            except PaystackServiceError:
                return Response({"status": "retry_later"}, status=status.HTTP_200_OK)

            is_valid, reason = _validate_gateway_success(payment, gateway_response)
            if is_valid:
                mark_payment_success(payment.id, gateway_payload=gateway_response, source="webhook")
                return Response({"status": "ok"}, status=status.HTTP_200_OK)

            mark_payment_failed(payment.id, gateway_payload=gateway_response, reason=reason)
            return Response({"status": "ignored", "reason": reason}, status=status.HTTP_200_OK)

        if event == "charge.abandoned":
            mark_payment_abandoned(payment.id, gateway_payload=payload, reason=event)
            return Response({"status": "ok"}, status=status.HTTP_200_OK)

        if event == "charge.failed":
            mark_payment_failed(payment.id, gateway_payload=payload, reason=event)
            return Response({"status": "ok"}, status=status.HTTP_200_OK)

        return Response({"status": "ignored", "event": event}, status=status.HTTP_200_OK)
