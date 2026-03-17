import unittest

from telegram_proxy.config import ProxyConfig
from telegram_proxy.downstream_auth import DownstreamAuthService


class DownstreamAuthTests(unittest.TestCase):
    def setUp(self):
        self.config = ProxyConfig(
            downstream_api_id=123,
            downstream_api_hash='hash123',
            downstream_login_code='55555',
            downstream_password='secret',
        )
        self.auth = DownstreamAuthService(self.config)

    def test_send_code_and_sign_in(self):
        sent = self.auth.send_code(phone='+10000000000', api_id=123, api_hash='hash123')
        principal = self.auth.sign_in(
            phone='+10000000000',
            code='55555',
            phone_code_hash=sent['phone_code_hash'],
            password='secret',
        )
        self.assertEqual(principal.phone, '+10000000000')

    def test_rejects_wrong_code(self):
        sent = self.auth.send_code(phone='+10000000000', api_id=123, api_hash='hash123')
        with self.assertRaises(PermissionError):
            self.auth.sign_in(
                phone='+10000000000',
                code='00000',
                phone_code_hash=sent['phone_code_hash'],
                password='secret',
            )

    def test_rejects_wrong_api_credentials(self):
        with self.assertRaises(PermissionError):
            self.auth.send_code(phone='+10000000000', api_id=999, api_hash='wrong')


if __name__ == '__main__':
    unittest.main()
