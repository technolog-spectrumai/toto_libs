from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('steven', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='agentprofile',
            name='uses_encrypted_chat',
        ),
        migrations.RemoveField(
            model_name='agentprofile',
            name='graph_rag_enabled',
        ),
        migrations.RemoveField(
            model_name='agentprofile',
            name='graph_rag_labels',
        ),
        migrations.RemoveField(
            model_name='agentprofile',
            name='graph_rag_max_nodes',
        ),
        migrations.RemoveField(
            model_name='agentprofile',
            name='graph_rag_depth',
        ),
    ]
