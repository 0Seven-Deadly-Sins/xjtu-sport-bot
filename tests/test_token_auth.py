import unittest
from unittest.mock import Mock, patch

from cas_auth import AuthenticationError
from token_auth import VERIFY_URL, parse_token, verify_token
import sport_bot


class TokenTests(unittest.TestCase):
    def test_plain_and_browser_storage_values(self):
        self.assertEqual(parse_token(' example_token '), 'example_token')
        self.assertEqual(parse_token('{"type":"string","data":"example_token"}'), 'example_token')
        self.assertEqual(parse_token('"example_token"'), 'example_token')
        self.assertEqual(parse_token('Bearer example_token'), 'example_token')

    def test_empty_or_header_injection_is_rejected(self):
        for value in ['', 'null', 'undefined', 'example\r\nHeader:value', '{"data":null}']:
            with self.subTest(value=value), self.assertRaises(AuthenticationError):
                parse_token(value)

    @patch('token_auth.requests.get')
    def test_verification_is_a_read_only_request(self, get):
        get.return_value.status_code = 200
        get.return_value.json.return_value = {'success': True, 'data': {'example': 'private'}}
        self.assertEqual(verify_token('example_token'), 'example_token')
        get.assert_called_once_with(VERIFY_URL, headers={'token': 'example_token'}, timeout=30)

    @patch('token_auth.requests.get')
    def test_expiry_is_detected_inside_http_200(self, get):
        get.return_value.status_code = 200
        get.return_value.json.return_value = {'code': 401, 'success': False, 'msg': 'example_private_data'}
        with self.assertRaises(AuthenticationError) as result:
            verify_token('example_token')
        self.assertIn('过期', str(result.exception))
        self.assertNotIn('example_private_data', str(result.exception))

    @patch('token_auth.requests.get')
    def test_false_string_cannot_validate_a_token(self, get):
        get.return_value.status_code = 200
        get.return_value.json.return_value = {'success': 'false'}
        with self.assertRaises(AuthenticationError):
            verify_token('example_token')

    @patch.multiple(sport_bot.Config, TOKEN='example_token', USER='', PASSWORD='')
    @patch('sport_bot.authenticate')
    @patch('sport_bot.sign_operation')
    @patch('sport_bot.verify_token', return_value='example_token')
    def test_direct_token_needs_neither_password_nor_browser(self, verify, operation, browser):
        self.assertEqual(sport_bot.run(auth_only=True), 0)
        verify.assert_called_once_with('example_token')
        browser.assert_not_called()
        operation.assert_not_called()

    @patch.multiple(sport_bot.Config, TOKEN='example_token', USER='', PASSWORD='')
    @patch('sport_bot.sign_operation')
    @patch('sport_bot.verify_token', side_effect=AuthenticationError('Example expired token'))
    def test_invalid_token_cannot_sign_in(self, verify, operation):
        self.assertEqual(sport_bot.run(), 1)
        operation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
