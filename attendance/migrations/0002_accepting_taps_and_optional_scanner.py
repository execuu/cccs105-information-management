from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancesession",
            name="is_accepting_taps",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="scanevent",
            name="scanner_code",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
