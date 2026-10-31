import logging
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cas_auth import AuthObserver, AuthenticationError, CredentialsRejected, InteractionRequired, initial_platform_tokens, load_browser_state, read_new_platform_token, school_state
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

    async def test_redirects_wait_for_the_final_auth_response(self):
        observer = AuthObserver()
        redirect = response('https://ipahw.xjtu.edu.cn/szjy-boot/sso/codeLogin', {}, status=302)
        redirect.json.side_effect = ValueError('Example HTML redirect')
        await observer.response(redirect)
        self.assertIsNone(observer.error)
        self.assertFalse(observer.finished.is_set())
        redirect.json.assert_not_called()

    async def test_interactive_non_json_keeps_the_login_window_open(self):
        observer = AuthObserver(interactive=True)
        item = response('https://login.xjtu.edu.cn/cas/mfa/detect', {}, status=200)
        item.json.side_effect = ValueError('Example private response')
        await observer.response(item)
        self.assertIsNone(observer.error)
        self.assertFalse(observer.finished.is_set())

    async def test_unreadable_response_does_not_claim_success_or_abort_navigation(self):
        observer = AuthObserver()
        item = response('https://ipahw.xjtu.edu.cn/szjy-boot/sso/codeLogin', {}, status=200)
        item.headers = {'content-type': 'text/html; charset=utf-8'}
        item.json.side_effect = ValueError('example private body')
        await observer.response(item)
        self.assertIsNone(observer.token)
        self.assertIsNone(observer.error)
        self.assertIsInstance(observer.response_problem, AuthenticationError)
        self.assertNotIn('example private body', str(observer.response_problem))


class StorageTokenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.page = Mock(url='https://ipahw.xjtu.edu.cn/pages/tabbar/index')
        self.page.evaluate = AsyncMock(return_value=['new_example_token', None])
        self.context = Mock()
        self.context.request.get = AsyncMock(return_value=response('https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/system/user/getInfo', {'success': True, 'code': 200}))

    async def test_new_storage_token_requires_a_real_read_only_check(self):
        self.assertEqual(await read_new_platform_token(self.page, self.context, set()), 'new_example_token')
        self.context.request.get.assert_awaited_once()
        self.assertIn('/api/v1/system/user/getInfo', self.context.request.get.call_args.args[0])

    async def test_existing_seed_token_cannot_prove_renewal(self):
        self.assertIsNone(await read_new_platform_token(self.page, self.context, {'new_example_token'}))
        self.context.request.get.assert_not_called()

    async def test_rejected_token_does_not_complete_login(self):
        self.context.request.get.return_value.json.return_value = {'success': False, 'code': 401}
        self.assertIsNone(await read_new_platform_token(self.page, self.context, set()))

    async def test_other_origins_cannot_supply_tokens(self):
        self.page.url = 'https://other.example/'
        self.assertIsNone(await read_new_platform_token(self.page, self.context, set()))
        self.page.evaluate.assert_not_called()

    def test_seed_tokens_are_scoped_and_accept_both_storage_names(self):
        state = {'origins': [
            {'origin': 'https://ipahw.xjtu.edu.cn', 'localStorage': [{'name': 'token', 'value': 'example_old'}, {'name': '_token', 'value': '{"data":"example_wrapped"}'}]},
            {'origin': 'https://other.example', 'localStorage': [{'name': 'token', 'value': 'untrusted'}]},
        ]}
        self.assertEqual(initial_platform_tokens(state), {'example_old', 'example_wrapped'})


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
        self.config = patch.multiple(sport_bot.Config, TOKEN='', USER='example_student', PASSWORD='example_password', SEND_EMAIL=False)
        self.config.start()
        self.addCleanup(self.config.stop)
        self.verify = patch('sport_bot.verify_token', return_value='example_token')
        self.verify.start()
        self.addCleanup(self.verify.stop)

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
