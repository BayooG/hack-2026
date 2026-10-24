import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_processedemail_labels"),
    ]

    operations = [
        migrations.CreateModel(
            name="Label",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveIntegerField(default=0)),
                ("emoji", models.CharField(blank=True, max_length=16)),
                ("name", models.CharField(help_text="The Gmail label name, without the emoji", max_length=60)),
                ("sublabels", models.CharField(
                    blank=True, help_text='"never", or a rule like "one per trip". Blank means when useful', max_length=80,
                )),
                ("include", models.TextField(blank=True, help_text="What belongs under the label", max_length=500)),
                ("exclude", models.TextField(
                    blank=True, help_text="What doesn't, and where it goes instead", max_length=300,
                )),
                ("cleanup", models.CharField(blank=True, max_length=120)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="labels", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["position", "id"]},
        ),
    ]
