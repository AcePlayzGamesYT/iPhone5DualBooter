import unittest

from iphone5dualbooter.network import SSHError, validate_ipv4


class SSHAddressTests(unittest.TestCase):
    def test_accepts_private_ipv4(self):
        self.assertEqual(validate_ipv4(" 192.168.1.123 "), "192.168.1.123")
        self.assertEqual(validate_ipv4("10.0.0.45"), "10.0.0.45")

    def test_rejects_empty(self):
        with self.assertRaises(SSHError):
            validate_ipv4("")

    def test_rejects_hostname(self):
        with self.assertRaises(SSHError):
            validate_ipv4("iphone.local")

    def test_rejects_ipv6(self):
        with self.assertRaises(SSHError):
            validate_ipv4("fe80::1")

    def test_rejects_loopback(self):
        with self.assertRaises(SSHError):
            validate_ipv4("127.0.0.1")


if __name__ == "__main__":
    unittest.main()
