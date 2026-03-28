from django.urls import path

from .views import PaymentInitializeView, PaymentVerifyView, PaymentWebhookView


urlpatterns = [
    path("initialize/", PaymentInitializeView.as_view(), name="payments-initialize"),
    path("verify/", PaymentVerifyView.as_view(), name="payments-verify"),
    path("webhook/", PaymentWebhookView.as_view(), name="payments-webhook"),
]
