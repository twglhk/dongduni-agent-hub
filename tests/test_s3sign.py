import calendar
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from agent_hub.storage import s3sign
from agent_hub.storage.s3sign import S3, Creds, S3Error

EXAMPLE = Creds("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")


class AwsVectorsTest(unittest.TestCase):

    def s3(self) -> S3:
        t = calendar.timegm((2013, 5, 24, 0, 0, 0))
        return S3("examplebucket", "us-east-1", host="examplebucket.s3.amazonaws.com", creds=lambda: EXAMPLE,
                  clock=lambda: t)

    def signature(self, headers: dict[str, str]) -> str:
        return headers["Authorization"].rsplit("Signature=", 1)[1]

    def test_get_object_with_range(self) -> None:
        hs = self.s3().signed_headers("GET", "test.txt", headers={"Range": "bytes=0-9"})
        self.assertEqual(self.signature(hs), "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")

    def test_put_object(self) -> None:
        hs = self.s3().signed_headers("PUT", "test$file.text", body=b"Welcome to Amazon S3.",
                                      headers={"Date": "Fri, 24 May 2013 00:00:00 GMT",
                                               "x-amz-storage-class": "REDUCED_REDUNDANCY"})
        self.assertEqual(self.signature(hs), "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")
        self.assertIn("SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class", hs["Authorization"])

    def test_get_bucket_lifecycle(self) -> None:
        hs = self.s3().signed_headers("GET", "", {"lifecycle": ""})
        self.assertEqual(self.signature(hs), "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543")

    def test_list_objects(self) -> None:
        hs = self.s3().signed_headers("GET", "", {"max-keys": "2", "prefix": "J"})
        self.assertEqual(self.signature(hs), "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")

    def test_presigned_get(self) -> None:
        url = self.s3().presign("GET", "test.txt", 86400)
        self.assertTrue(url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"))


class PresignTest(unittest.TestCase):
    def test_session_token_and_signed_headers(self) -> None:
        s3 = S3("b", "eu-west-1", creds=lambda: Creds("K", "S", "TOK"))
        q = parse_qs(urlsplit(s3.presign("PUT", "uploads/a b.jpg", 3600,
                                         headers={"content-type": "image/jpeg", "content-length": "5"})).query)
        self.assertEqual(q["X-Amz-Security-Token"], ["TOK"])
        self.assertEqual(q["X-Amz-SignedHeaders"], ["content-length;content-type;host"])
        self.assertIn("b.s3.eu-west-1.amazonaws.com/uploads/a%20b.jpg", s3.presign("GET", "uploads/a b.jpg"))

    def test_expiry_bounds(self) -> None:
        s3 = S3("b", "eu-west-1", creds=lambda: EXAMPLE)
        for bad in (0, 7 * 24 * 3600 + 1):
            with self.assertRaises(S3Error):
                s3.presign("GET", "k", bad)

    def test_temporary_credentials_are_fetched_again_before_they_end(self) -> None:
        now = [1000.0]
        given = []

        def fetch() -> Creds:
            given.append(1)
            return Creds("K", "S", "T", expires=now[0] + 600)

        s3 = S3("b", "eu-west-1", creds=fetch, clock=lambda: now[0])
        s3.creds()
        s3.creds()
        self.assertEqual(len(given), 1)
        now[0] += 400
        s3.creds()
        self.assertEqual(len(given), 2)


class CredentialSourcesTest(unittest.TestCase):
    def test_environment_comes_first_then_the_shared_file(self) -> None:
        d = Path(tempfile.mkdtemp(prefix="aws-test-"))
        (d / "credentials").write_text("[default]\naws_access_key_id = FILEKEY\naws_secret_access_key = FILESECRET\n"
                                       "[work]\naws_access_key_id = WK\naws_secret_access_key = WS\naws_session_token = WT\n")
        base = {"AWS_SHARED_CREDENTIALS_FILE": str(d / "credentials")}
        with mock.patch.dict(os.environ, {**base, "AWS_ACCESS_KEY_ID": "ENVKEY", "AWS_SECRET_ACCESS_KEY": "ENVSECRET"}):
            self.assertEqual(s3sign.standard_creds().key, "ENVKEY")
        with mock.patch.dict(os.environ, base, clear=True):
            self.assertEqual(s3sign.standard_creds(), Creds("FILEKEY", "FILESECRET"))
        with mock.patch.dict(os.environ, {**base, "AWS_PROFILE": "work"}, clear=True):
            self.assertEqual(s3sign.standard_creds(), Creds("WK", "WS", "WT"))

    def test_no_source_says_what_to_do(self) -> None:
        d = Path(tempfile.mkdtemp(prefix="aws-test-"))
        with mock.patch.dict(os.environ, {"AWS_SHARED_CREDENTIALS_FILE": str(d / "none"), "HOME": str(d)}, clear=True), \
                mock.patch.object(s3sign, "_imds", side_effect=OSError("down")):
            with self.assertRaises(S3Error) as got:
                s3sign.standard_creds()
        self.assertIn("AWS_ACCESS_KEY_ID", str(got.exception))


if __name__ == "__main__":
    unittest.main()
