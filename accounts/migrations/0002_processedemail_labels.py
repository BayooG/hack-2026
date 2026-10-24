from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(model_name="processedemail", old_name="label", new_name="labels"),
        migrations.AlterField(
            model_name="processedemail",
            name="labels",
            field=models.CharField(blank=True, help_text="Applied labels, one per line", max_length=255),
        ),
    ]
