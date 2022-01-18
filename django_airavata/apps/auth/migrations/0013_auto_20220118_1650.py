# Generated by Django 3.2.10 on 2022-01-18 16:50

from django.db import migrations


def default_username_initialized(apps, schema_editor):
    UserProfile = apps.get_model("django_airavata_auth", "UserProfile")
    # Update all existing user's user_profiles to have username_initialized=True
    UserProfile.objects.all().update(username_initialized=True)


class Migration(migrations.Migration):

    dependencies = [
        ('django_airavata_auth', '0012_merge_20211210_2041'),
    ]

    operations = [
        migrations.RunPython(default_username_initialized, migrations.RunPython.noop)
    ]
