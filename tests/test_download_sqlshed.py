import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from scripts.download_sqlshed import download_file


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class DownloadSqlShedTests(unittest.TestCase):
    def test_network_failure_removes_partial_file(self) -> None:
        def opener(request, timeout):
            raise TimeoutError('network timeout')
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'sample.parquet'
            with self.assertRaises(TimeoutError):
                download_file(destination, 'https://example.test/file', 2, None, opener)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_rejects_changed_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'sample.parquet'
            record = download_file(destination, 'https://example.test/file', 2, None,
                                   lambda *args, **kwargs: FakeResponse(b'original'))
            destination.write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, '不一致'):
                download_file(destination, 'https://example.test/file', 2, record)

    def test_downloads_and_reuses_verified_file(self) -> None:
        calls = 0

        def opener(request, timeout):
            nonlocal calls
            calls += 1
            return FakeResponse(b"parquet-test-data")

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "sample.parquet"
            record = download_file(destination, "https://example.test/file", 2, None, opener)
            reused = download_file(
                destination,
                "https://example.test/file",
                2,
                record,
                opener,
            )

        self.assertEqual(calls, 1)
        self.assertEqual(reused, record)

    def test_refuses_unverified_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "sample.parquet"
            destination.write_bytes(b"unknown")
            with self.assertRaisesRegex(RuntimeError, "拒绝覆盖"):
                download_file(destination, "https://example.test/file", 2, None)


if __name__ == "__main__":
    unittest.main()
