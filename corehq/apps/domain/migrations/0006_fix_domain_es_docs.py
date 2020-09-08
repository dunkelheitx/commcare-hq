# Generated by Django 2.2.13 on 2020-09-01 11:32

from django.conf import settings
from django.db import migrations
from corehq.apps.es.domains import DomainES
from corehq.elastic import send_to_elasticsearch


def fix_domain_es_docs(apps, schema_editor):
    if settings.UNIT_TESTING:
        # This is not necessary in tests
        return
    bool_props = ['cp_sms_ever', 'cp_sms_30_d', 'cp_j2me_90_d_bool']
    for doc in DomainES().source(bool_props).run().hits:
        for prop in bool_props:
            doc[prop] = bool(doc.get(prop, False))
        send_to_elasticsearch('domains', doc, delete=False, es_merge_update=True)


class Migration(migrations.Migration):

    dependencies = [
        ('domain', '0005_ga_opt_out'),
    ]

    operations = [
        migrations.RunPython(fix_domain_es_docs),
    ]
