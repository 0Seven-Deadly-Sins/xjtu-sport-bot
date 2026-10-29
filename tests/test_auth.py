import logging
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cas_auth import AuthObserver, AuthenticationError, CredentialsRejected, InteractionRequired, load_browser_state, school_state
import sport_bot


def response(url, payload, status=200):
    item = Mock(url=url, status=status)
    item.json = AsyncMock(return_value=payload)
    return item


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def test_additional_verification_is_respected(self):
        observer = AuthObserver()
        await observer.response(response('https://login.xjtu.edu.cn/cas/mfa/detect', {'code': 0, 'data': {'need': True, 'state': 'example'}}))
        self.assertIsInstance(observer.error, InteractionRequired)
        self.assertIsNone(observer.token)

    async def test_normal_safety_check_is_allowed_to_continue(self):
        observer = AuthObserver()
        await observer.response(response('https://login.xjtu.edu.cn/cas/mfa/detect', {'code': '0', 'data': {'need': False, 'state': 'example'}}))
        self.assertIsNone(observer.error)
        self.assertFalse(observer.finished.is_set())

    async def test_only_sports_platform_can_supply_token(self):
        observer = AuthObserver()
        await observer.response(response('https://other.example/szjy-boot/sso/codeLogin', {'data': {'token': 'example'}}))
        self.assertIsNone(observer.token)
        await observer.response(response('https://ipahw.xjtu.edu.cn/szjy-boot/sso/codeLogin', {'data': {'token': 'example'}}))
        self.assertEqual(observer.token, 'example')

    async def test_no_token_is_not_a_success(self):
        observer = AuthObserver()
        await observer.response(response('https://ipahw.xjtu.edu.cn/szjy-boot/sso/codeLogin', {'success': False}))
        self.assertIsNotNone(observer.error)
        self.assertIsNone(observer.token)

    async def test_server_error_message_is_not_echoed(self):
        observer = AuthObserver()
        await observer.response(response('https://login.xjtu.edu.cn/cas/mfa/detect', {'code': 1, 'message': 'example_private_username password incorrect'}))
        self.assertIsInstance(observer.error, CredentialsRejected)
        self.assertNotIn('example_private_username', str(observer.error))

    async def test_interactive_mfa_waits_for_the_user(self):
        observer = AuthObserver(interactive=True)
        await observer.response(response('https://login.xjtu.edu.cn/cas/mfa/detect', {'code': 0, 'data': {'need': True}}))
        self.assertIsNone(observer.error)
        self.assertFalse(observer.finished.is_set())


class StateTests(unittest.TestCase):
    def test_export_is_scoped_and_omits_saved_passwords(self):
        state = school_state({
            'cookies': [{'domain': '.xjtu.edu.cn', 'name': 'example'}, {'domain': '.other.example', 'name': 'private'}],
            'origins': [
                {'origin': 'https://login.xjtu.edu.cn', 'localStorage': [{'name': 'fpVisitorId', 'value': 'example'}, {'name': 'password', 'value': 'private'}]},
                {'origin': 'https://other.example', 'localStorage': []},
            ],
        })
        self.assertEqual(len(state['cookies']), 1)
        self.assertEqual(len(state['origins']), 1)
        self.assertEqual([item['name'] for item in state['origins'][0]['localStorage']], ['fpVisitorId'])

    def test_empty_or_invalid_state_is_handled(self):
        self.assertIsNone(load_browser_state(''))
        with self.assertRaises(AuthenticationError):
            load_browser_state('not json')
        with self.assertRaises(AuthenticationError):
            load_browser_state('{"cookies": [], "origins": []}')


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.multiple(sport_bot.Config, USER='example_student', PASSWORD='example_password', SEND_EMAIL=False)
        self.config.start()
        self.addCleanup(self.config.stop)

    @patch('sport_bot.sign_operation')
    @patch('sport_bot.authenticate', return_value='example_token')
    def test_auth_only_cannot_sign(self, auth, operation):
        self.assertEqual(sport_bot.run(auth_only=True), 0)
        operation.assert_not_called()

    @patch('sport_bot.sign_operation')
    @patch('sport_bot.authenticate', side_effect=InteractionRequired('Example verification required'))
    def test_challenge_has_distinct_exit_status(self, auth, operation):
        self.assertEqual(sport_bot.run(auth_only=True), 2)
        operation.assert_not_called()

    @patch('sport_bot.time.sleep')
    @patch('sport_bot.sign_operation', return_value=False)
    @patch('sport_bot.authenticate', return_value='example_token')
    def test_failed_signin_cannot_trigger_signout(self, auth, operation, sleep):
        self.assertEqual(sport_bot.run(), 1)
        self.assertEqual(operation.call_count, 1)
        sleep.assert_not_called()

    @patch('sport_bot.time.sleep')
    @patch('sport_bot.sign_operation', side_effect=[True, True])
    @patch('sport_bot.authenticate', return_value='example_token')
    def test_successful_run_preserves_the_wait(self, auth, operation, sleep):
        self.assertEqual(sport_bot.run(), 0)
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(31 * 60)

    def test_secret_redaction(self):
        record = logging.LogRecord('example', logging.INFO, '', 0, 'User=%s password=%s', ('example_student', 'example_password'), None)
        sport_bot.RedactSecrets(['example_student', 'example_password']).filter(record)
        self.assertEqual(record.getMessage(), 'User=*** password=***')

    @patch('sport_bot.requests.post')
    def test_false_string_cannot_report_success(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {'success': 'false', 'msg': 'Example rejected'}
        self.assertFalse(sport_bot.sign_operation('https://example.invalid', {}, 'example_token', 'Example operation'))


if __name__ == '__main__':
    unittest.main()
