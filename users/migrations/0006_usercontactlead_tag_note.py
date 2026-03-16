from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0005_userportfolioitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="usercontactlead",
            name="note",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="usercontactlead",
            name="tag",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("follow_up", "Follow up"),
                    ("contacted", "Contacted"),
                    ("closed", "Closed"),
                    ("lost", "Lost"),
                ],
                default="new",
                max_length=24,
            ),
        ),
    ]
