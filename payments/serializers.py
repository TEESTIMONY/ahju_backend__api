from decimal import Decimal

from rest_framework import serializers

from .models import Order, OrderItem, Payment


class PaymentInitializeSerializer(serializers.Serializer):
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=180)
    phone_number = serializers.CharField(max_length=32)

    shipping_country = serializers.CharField(max_length=120)
    shipping_address = serializers.CharField(max_length=240)
    shipping_city = serializers.CharField(max_length=120)
    shipping_postal_code = serializers.CharField(max_length=40)

    billing_same_as_shipping = serializers.BooleanField(default=True)
    billing_country = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    billing_address = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")
    billing_city = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    billing_postal_code = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")

    session_key = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    callback_url = serializers.URLField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if not attrs.get("billing_same_as_shipping", True):
            required = [
                "billing_country",
                "billing_address",
                "billing_city",
                "billing_postal_code",
            ]
            missing = [key for key in required if not (attrs.get(key) or "").strip()]
            if missing:
                raise serializers.ValidationError(
                    {field: "This field is required when billing address differs." for field in missing}
                )
        return attrs


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["id", "product_name", "unit_price", "quantity", "line_total"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "email",
            "full_name",
            "phone_number",
            "shipping_country",
            "shipping_address",
            "shipping_city",
            "shipping_postal_code",
            "billing_same_as_shipping",
            "billing_country",
            "billing_address",
            "billing_city",
            "billing_postal_code",
            "currency",
            "total_amount",
            "status",
            "items",
            "created_at",
        ]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
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
        ]


class PaymentInitializeResponseSerializer(serializers.Serializer):
    authorization_url = serializers.URLField()
    access_code = serializers.CharField(allow_blank=True)
    reference = serializers.CharField()
    order = OrderSerializer()
    payment = PaymentSerializer()


class PaymentVerifyResponseSerializer(serializers.Serializer):
    verified = serializers.BooleanField()
    reference = serializers.CharField()
    order = OrderSerializer()
    payment = PaymentSerializer()
    gateway = serializers.JSONField()


def to_minor_units(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))
