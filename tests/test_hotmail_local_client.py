import unittest

from refresh_sub2api_401 import HotmailLocalClient


class FakeResponse:
    status_code = 200
    text = '{"ok": true, "code": "654321", "nextRefreshToken": "next-rt"}'

    def json(self):
        return {"ok": True, "code": "654321", "nextRefreshToken": "next-rt"}


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse()


class HotmailLocalClientTest(unittest.TestCase):
    def test_wait_otp_posts_gujumpgate_code_payload(self):
        session = FakeSession()
        client = HotmailLocalClient(
            "http://127.0.0.1:17373",
            {"user@example.com": {"client_id": "cid", "refresh_token": "rt"}},
            session=session,
        )

        code = client.wait_otp("user@example.com", 1710000000.5, {"111111"}, timeout=1, interval=0)

        self.assertEqual(code, "654321")
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "http://127.0.0.1:17373/code")
        self.assertEqual(kwargs["json"]["email"], "user@example.com")
        self.assertEqual(kwargs["json"]["clientId"], "cid")
        self.assertEqual(kwargs["json"]["refreshToken"], "rt")
        self.assertEqual(kwargs["json"]["mailboxes"], ["INBOX", "Junk"])
        self.assertEqual(kwargs["json"]["excludeCodes"], ["111111"])
        self.assertEqual(kwargs["json"]["filterAfterTimestamp"], 1710000000500)


if __name__ == "__main__":
    unittest.main()
