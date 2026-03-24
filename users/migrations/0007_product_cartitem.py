from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0006_usercontactlead_tag_note"),
    ]

    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=180)),
                ("slug", models.SlugField(max_length=200, unique=True)),
                ("category", models.CharField(blank=True, default="", max_length=80)),
                ("price", models.DecimalField(decimal_places=2, max_digits=12)),
                ("old_price", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("image_url", models.URLField(blank=True, default="", max_length=1000)),
                ("is_active", models.BooleanField(default=True)),
                ("stock_quantity", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="CartItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("session_key", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "product",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cart_items", to="users.product"),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cart_items",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "-created_at"],
                "indexes": [
                    models.Index(fields=["user", "updated_at"], name="users_carti_user_id_0f6202_idx"),
                    models.Index(fields=["session_key", "updated_at"], name="users_carti_session_9b8115_idx"),
                ],
            },
        ),
    ]
