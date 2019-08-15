# encoding: utf-8
from __future__ import absolute_import, unicode_literals

import doctest

from django.test import SimpleTestCase

import six

import corehq.motech.utils
from corehq.motech.utils import (
    b64_aes_decrypt,
    b64_aes_encrypt,
    pformat_json,
)


class PFormatJSONTests(SimpleTestCase):

    def test_valid_json(self):
        self.assertEqual(
            pformat_json('{"ham": "spam", "eggs": "spam"}'),
            '{\n  "eggs": "spam",\n  "ham": "spam"\n}' if six.PY3 else '{\n  "eggs": "spam", \n  "ham": "spam"\n}'
        )
        self.assertEqual(
            pformat_json({'ham': 'spam', 'eggs': 'spam'}),
            '{\n  "eggs": "spam",\n  "ham": "spam"\n}' if six.PY3 else '{\n  "eggs": "spam", \n  "ham": "spam"\n}'
        )

    def test_invalid_json(self):
        self.assertEqual(
            pformat_json('ham spam eggs spam'),
            'ham spam eggs spam'
        )

    def test_empty_string(self):
        self.assertEqual(
            pformat_json(''),
            ''
        )

    def test_none(self):
        self.assertEqual(
            pformat_json(None),
            ''
        )


class EncryptionTests(SimpleTestCase):

    def assert_message_equals_plaintext(self, message):
        assert isinstance(message, six.text_type)
        ciphertext = b64_aes_encrypt(message)
        plaintext = b64_aes_decrypt(ciphertext)
        self.assertEqual(plaintext, message)
        self.assertIsInstance(ciphertext, six.text_type)
        self.assertIsInstance(plaintext, six.text_type)

    def test_encrypt_decrypt_ascii(self):
        message = 'Around you is a forest.'
        self.assert_message_equals_plaintext(message)

    def test_encrypt_decrypt_utf8(self):
        message = 'आपके आसपास एक जंगल है'
        self.assert_message_equals_plaintext(message)


class DocTests(SimpleTestCase):

    def test_doctests(self):
        results = doctest.testmod(corehq.motech.utils)
        self.assertEqual(results.failed, 0)
