import re

from datetime import datetime

from django.test import TestCase

from corehq.apps.domain.shortcuts import create_domain
from corehq.apps.custom_data_fields.models import (
    CustomDataFieldsDefinition,
    CustomDataFieldsProfile,
    Field,
    PROFILE_SLUG,
)
from corehq.apps.users.views.mobile.custom_data_fields import UserFieldsView
from corehq.apps.users.models import CommCareUser
from corehq.apps.users.bulk_download import parse_mobile_users
from corehq.apps.user_importer.importer import GroupMemoizer


class TestDownloadMobileWorkers(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.domain = 'bookshelf'
        cls.other_domain = 'book'
        cls.domain_obj = create_domain(cls.domain)
        cls.other_domain_obj = create_domain(cls.other_domain)

        cls.definition = CustomDataFieldsDefinition(domain=cls.domain_obj.name,
                                                    field_type=UserFieldsView.field_type)
        cls.definition.save()
        cls.definition.set_fields([
            Field(
                slug='born',
                label='Year of Birth',
            ),
            Field(
                slug='_type',
                label='Type',
                choices=['fiction', 'non-fiction'],
            ),
        ])
        cls.definition.save()

        cls.profile = CustomDataFieldsProfile(
            name='Novelist',
            fields={'_type': 'fiction'},
            definition=cls.definition,
        )
        cls.profile.save()

        cls.user1 = CommCareUser.create(
            cls.domain_obj.name,
            'edith',
            'badpassword',
            None,
            None,
            first_name='Edith',
            last_name='Wharton',
            metadata={'born': 1862}
        )
        cls.user2 = CommCareUser.create(
            cls.domain_obj.name,
            'george',
            'anotherbadpassword',
            None,
            None,
            first_name='George',
            last_name='Eliot',
            metadata={'born': 1849, PROFILE_SLUG: cls.profile.id},
        )
        cls.user3 = CommCareUser.create(
            cls.other_domain_obj.name,
            'emily',
            'anothersuperbadpassword',
            None,
            None,
            first_name='Emily',
            last_name='Bronte',
        )

    @classmethod
    def tearDownClass(cls):
        cls.user1.delete(deleted_by=None)
        cls.user2.delete(deleted_by=None)
        cls.user3.delete(deleted_by=None)
        cls.domain_obj.delete()
        cls.other_domain_obj.delete()
        cls.definition.delete()
        super().tearDownClass()

    def test_download(self):
        (headers, rows) = parse_mobile_users(self.domain_obj.name, {})
        self.assertNotIn('user_profile', headers)

        rows = list(rows)
        self.assertEqual(2, len(rows))

        spec = dict(zip(headers, rows[0]))
        self.assertEqual('edith', spec['username'])
        self.assertTrue(re.search(r'^\*+$', spec['password']))
        self.assertEqual('True', spec['is_active'])
        self.assertEqual('Edith Wharton', spec['name'])
        self.assertTrue(spec['registered_on (read only)'].startswith(datetime.today().strftime("%Y-%m-%d")))
        self.assertEqual('', spec['data: _type'])
        self.assertEqual(1862, spec['data: born'])

    def test_multiple_domain_download(self):
        (headers, rows) = parse_mobile_users(self.domain_obj.name, {'domains': ['bookshelf', 'book']})

        rows = list(rows)
        self.assertEqual(3, len(rows))
        spec = dict(zip(headers, rows[2]))
        self.assertEqual('emily', spec['username'])
        self.assertEqual('True', spec['is_active'])
        self.assertEqual('Emily Bronte', spec['name'])
