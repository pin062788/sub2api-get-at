import unittest

from refresh_sub2api_401 import (
    choose_account_write_operation,
    filter_accounts_with_active_email_guard,
    Sub2ApiClient,
    build_sub2api_login_payload,
)


class Sub2ApiLoginPayloadTest(unittest.TestCase):
    def test_omits_empty_turnstile_token(self):
        self.assertEqual(
            build_sub2api_login_payload("admin@example.com", "secret", ""),
            {"email": "admin@example.com", "password": "secret"},
        )

    def test_includes_turnstile_token_when_provided(self):
        self.assertEqual(
            build_sub2api_login_payload("admin@example.com", "secret", "ts-token"),
            {"email": "admin@example.com", "password": "secret", "turnstile_token": "ts-token"},
        )

    def test_access_token_skips_login_request(self):
        class Session:
            def request(self, *args, **kwargs):
                raise AssertionError("login should not be requested")

        client = Sub2ApiClient(
            "https://example.test",
            "admin@example.com",
            "secret",
            access_token="Bearer existing-token",
            session=Session(),
        )

        self.assertEqual(client.token, "existing-token")

    def test_create_instead_of_update_selects_create(self):
        self.assertEqual(choose_account_write_operation(create_instead_of_update=True), "create")

    def test_default_write_operation_updates(self):
        self.assertEqual(choose_account_write_operation(create_instead_of_update=False), "update")

    def test_active_email_guard_skips_401_when_active_duplicate_exists(self):
        accounts = [
            {"id": 1, "status": "error", "error_message": "401", "credentials": {"email": "a@example.com"}},
            {"id": 2, "status": "active", "error_message": "", "credentials": {"email": "a@example.com"}},
            {"id": 3, "status": "error", "error_message": "401", "credentials": {"email": "b@example.com"}},
        ]

        filtered, skipped = filter_accounts_with_active_email_guard([accounts[0], accounts[2]], accounts)

        self.assertEqual([item["id"] for item in filtered], [3])
        self.assertEqual(skipped, ["a@example.com"])


if __name__ == "__main__":
    unittest.main()
