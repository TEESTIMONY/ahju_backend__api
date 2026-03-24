from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0007_product_cartitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="product",
            name="gallery_images",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
