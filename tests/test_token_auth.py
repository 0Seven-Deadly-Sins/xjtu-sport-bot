import unittest
from unittest.mock import Mock, patch

from cas_auth import AuthenticationError
from token_auth import TokenExpired, VERIFY_URL, parse_token, verify_token
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
        self.assertIsInstance(result.exception, TokenExpired)
        self.assertIn('401', str(result.exception))
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

    @patch.multiple(sport_bot.Config, TOKEN='old_token', USER='example_user', PASSWORD='example_password')
    @patch('sport_bot.authenticate', return_value='new_token')
    @patch('sport_bot.sign_operation')
    @patch('sport_bot.verify_token', side_effect=[TokenExpired('Example 401'), 'new_token'])
    def test_expired_token_renews_and_validates_without_attendance(self, verify, operation, browser):
        self.assertEqual(sport_bot.run(auth_only=True), 0)
        browser.assert_called_once_with('example_user', 'example_password', headless=True)
        self.assertEqual([call.args for call in verify.call_args_list], [('old_token',), ('new_token',)])
        operation.assert_not_called()

    @patch.multiple(sport_bot.Config, TOKEN='old_token', USER='example_user', PASSWORD='example_password')
    @patch('sport_bot.authenticate')
    @patch('sport_bot.verify_token', side_effect=TokenExpired('Example 401'))
    def test_preflight_requests_browser_without_logging_in(self, verify, browser):
        self.assertEqual(sport_bot.check_saved_token(), 3)
        browser.assert_not_called()

    @patch.multiple(sport_bot.Config, TOKEN='old_token', USER='example_user', PASSWORD='example_password')
    @patch('sport_bot.authenticate', return_value='new_token')
    @patch('sport_bot.sign_operation')
    @patch('sport_bot.verify_token', side_effect=[TokenExpired('Example 401'), TokenExpired('Example new token rejected')])
    def test_failed_renewal_cannot_sign_or_repeat_login(self, verify, operation, browser):
        self.assertEqual(sport_bot.run(), 1)
        browser.assert_called_once()
        operation.assert_not_called()

    @patch('token_auth.requests.get')
    def test_permission_denial_is_not_reported_as_expiry(self, get):
        for http, code in [(403, None), (200, 403)]:
            get.return_value.status_code = http
            get.return_value.json.return_value = {'code': code, 'success': False}
            with self.subTest(http=http, code=code), self.assertRaises(AuthenticationError) as result:
                verify_token('example_token')
            self.assertNotIsInstance(result.exception, TokenExpired)


if __name__ == '__main__':
    unittest.main()
