from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Retires the WhatsApp channel and adds a detail field so send failures are
    diagnosable from the admin instead of being lost to stdout.

    Existing WHATSAPP rows are left untouched - they are historical audit
    records of messages that really were sent on that channel.
    """

    dependencies = [
        ('management', '0002_systemsetting_department_head_alertlog_checkin_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='alertlog',
            name='detail',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='alertlog',
            name='channel',
            field=models.CharField(default='SMS', max_length=20),
        ),
        migrations.AlterField(
            model_name='alertlog',
            name='status',
            field=models.CharField(default='PENDING', max_length=20),
        ),
    ]
