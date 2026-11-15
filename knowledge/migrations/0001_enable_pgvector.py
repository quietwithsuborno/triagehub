from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):

    dependencies = []

    operations = [
        # Turn on pgvector in this database (CREATE EXTENSION vector).
        # It must run before any table with a vector column is created.
        VectorExtension(),
    ]