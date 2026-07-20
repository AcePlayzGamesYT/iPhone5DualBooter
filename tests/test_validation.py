import unittest

from iphone5dualbooter.ipsw import base_version, validate_secondary_version


class VersionValidationTests(unittest.TestCase):
    def test_release_versions(self):
        self.assertEqual(validate_secondary_version("8.0"), "8.0")
        self.assertEqual(validate_secondary_version("7.0.6"), "7.0.6")

    def test_beta_version(self):
        self.assertEqual(validate_secondary_version("7.0b1"), "7.0b1")
        self.assertEqual(base_version("7.0b1"), "7.0")

    def test_rejects_decimal_size_style_in_version(self):
        with self.assertRaises(ValueError):
            validate_secondary_version("7.0 beta 1")


if __name__ == "__main__":
    unittest.main()
