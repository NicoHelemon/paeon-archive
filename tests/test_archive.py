import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock

from archive import Archiver, MANIFEST_FIELDS, verify


class ArchiverResumeTests(unittest.TestCase):
    def test_resume_skips_existing_pages_and_discovers_their_links(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive = root / "archive"
            manifest = root / "archive_manifest.csv"
            page = archive / "www.paeon.de/name/index.html"
            page.parent.mkdir(parents=True)
            page.write_text('<a href="second.html">suite</a>', encoding="utf-8")
            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "original_url": "https://www.paeon.de/name/index.html",
                        "local_path": "www.paeon.de/name/index.html",
                        "resource_type": "text/html",
                        "retrieved_at": "2026-08-27T00:00:00+00:00",
                        "status": "downloaded (200)",
                    }
                )

            archiver = Archiver(archive, manifest, 0, 1, 0, resume=True)
            response = Mock()
            response.url = "https://www.paeon.de/name/second.html"
            response.headers = {"Content-Type": "text/html"}
            response.content = b"<p>fin</p>"
            response.encoding = "utf-8"
            response.text = "<p>fin</p>"
            response.status_code = 200
            archiver.fetch = Mock(return_value=response)
            archiver.discover_html = Mock(
                side_effect=lambda url, _body, _encoding: archiver.enqueue(
                    "https://www.paeon.de/name/second.html"
                )
                if url.endswith("index.html")
                else None
            )
            archiver.rewrite_downloaded_files = Mock()

            archiver.crawl("https://www.paeon.de/name/index.html")

            archiver.fetch.assert_called_once_with("https://www.paeon.de/name/second.html")
            self.assertIn("https://www.paeon.de/name/index.html", archiver.records)
            self.assertIn("https://www.paeon.de/name/second.html", archiver.records)

    def test_missing_file_is_not_resumed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = root / "archive_manifest.csv"
            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "original_url": "https://www.paeon.de/name/index.html",
                        "local_path": "www.paeon.de/name/index.html",
                        "resource_type": "text/html",
                        "retrieved_at": "2026-08-27T00:00:00+00:00",
                        "status": "downloaded (200)",
                    }
                )

            archiver = Archiver(root / "archive", manifest, 0, 1, 0, resume=True)

            self.assertEqual({}, archiver.records)
            self.assertEqual({}, archiver.paths)


class VerifyStatisticsTests(unittest.TestCase):
    def test_verify_prints_resource_and_html_success_rates(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive = root / "archive"
            manifest = root / "archive_manifest.csv"
            page = archive / "www.paeon.de/name/index.html"
            page.parent.mkdir(parents=True)
            page.write_text("<p>archive</p>", encoding="utf-8")
            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "original_url": "https://www.paeon.de/name/index.html",
                            "local_path": "www.paeon.de/name/index.html",
                            "resource_type": "text/html",
                            "retrieved_at": "2026-08-27T00:00:00+00:00",
                            "status": "downloaded (200)",
                        },
                        {
                            "original_url": "https://www.paeon.de/name/missing.jpg",
                            "local_path": "",
                            "resource_type": "unknown",
                            "retrieved_at": "2026-08-27T00:00:01+00:00",
                            "status": "failed: timeout",
                        },
                    ]
                )

            output = io.StringIO()
            with redirect_stdout(output):
                result = verify(archive, manifest)

            self.assertEqual(1, result)
            self.assertIn("Téléchargements réussis : 1/2 (50.00 %)", output.getvalue())
            self.assertIn("Pages HTML identifiées téléchargées : 1/1 (100.00 %)", output.getvalue())


if __name__ == "__main__":
    unittest.main()
