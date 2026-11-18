from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conference', '0006_conference_bidding_end_conference_bidding_start'),
    ]

    operations = [
        migrations.AddField(
            model_name='conference',
            name='camera_ready_end',
            field=models.DateField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='conference',
            name='camera_ready_start',
            field=models.DateField(blank=True, default=None, null=True),
        ),
    ]
