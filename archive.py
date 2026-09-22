#!/usr/bin/env python3
"""Conservative, rate-limited local archive crawler for Project Paeonia."""

from __future__ import annotations

import argparse
import csv
import hashlib
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

START_URL = "https://www.paeon.de/name/index.html"
ALLOWED_HOSTS = {"paeon.de", "www.paeon.de"}
MANIFEST_FIELDS = ("original_url", "local_path", "resource_type", "retrieved_at", "status")
HTML_TYPES = {"text/html", "application/xhtml+xml"}
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)", re.IGNORECASE)
SRCSET_SPLIT_RE = re.compile(r"\s*,\s*")


@dataclass
class Record:
    original_url: str
    local_path: str
    resource_type: str
    retrieved_at: str
    status: str


def normalize_url(url: str) -> str | None:
    """Return a fragment-free in-scope HTTP(S) URL, or None."""
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        return None
    netloc = host
    if parts.port and not ((parts.scheme == "http" and parts.port == 80) or (parts.scheme == "https" and parts.port == 443)):
        netloc += f":{parts.port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


def local_path_for(url: str, content_type: str = "") -> Path:
    """Map a URL deterministically into archive/, retaining host and URL path."""
    parts = urlsplit(url)
    path = parts.path
    if not path or path.endswith("/"):
        path += "index.html"
    leaf = PurePosixPath(path).name or "index.html"
    parent = PurePosixPath(path).parent
    if "." not in leaf and content_type.split(";", 1)[0].lower() in HTML_TYPES:
        leaf += ".html"
    if parts.query:
        stem, dot, suffix = leaf.partition(".")
        digest = hashlib.sha256(parts.query.encode()).hexdigest()[:12]
        leaf = f"{stem}__q_{digest}{dot}{suffix}" if dot else f"{stem}__q_{digest}"
    return Path(parts.hostname or "unknown", *parent.parts[1:], leaf)


def resource_type(content_type: str, path: Path) -> str:
    media = content_type.split(";", 1)[0].strip().lower()
    if media:
        return media
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class Archiver:
    def __init__(
        self,
        output: Path,
        manifest: Path,
        delay: float,
        timeout: float,
        retries: int,
        manifest_batch: int = 100,
        resume: bool = False,
    ):
        self.output = output
        self.manifest = manifest
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.manifest_batch = max(manifest_batch, 1)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Jurassica-Paeonia-Archive/1.0 (internal conservation copy)"
        self.queue: list[str] = []
        self.queued: set[str] = set()
        self.records: dict[str, Record] = {}
        self.paths: dict[str, Path] = {}
        self.last_request = 0.0
        self.processed_since_manifest = 0
        if resume:
            self.load_manifest()

    def load_manifest(self) -> None:
        """Load usable successful records so a crawl can resume without downloading them again."""
        if not self.manifest.exists():
            return
        with self.manifest.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if not row["status"].startswith("downloaded") or not row["local_path"]:
                    continue
                path = Path(row["local_path"])
                if not (self.output / path).is_file():
                    continue
                url = normalize_url(row["original_url"])
                if not url:
                    continue
                self.records[url] = Record(
                    url,
                    path.as_posix(),
                    row["resource_type"],
                    row["retrieved_at"],
                    row["status"],
                )
                self.paths[url] = path

    def enqueue(self, url: str) -> str | None:
        normalized = normalize_url(url)
        if normalized and normalized not in self.queued:
            self.queued.add(normalized)
            self.queue.append(normalized)
        return normalized

    def fetch(self, url: str) -> requests.Response:
        error: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.delay - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                self.last_request = time.monotonic()
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                error = exc
                self.last_request = time.monotonic()
                if attempt < self.retries:
                    time.sleep(2**attempt)
        assert error is not None
        raise error

    def discover_html(self, url: str, body: bytes, encoding: str | None) -> None:
        soup = BeautifulSoup(body, "html.parser", from_encoding=encoding)
        for tag, attr in (("a", "href"), ("img", "src"), ("script", "src"), ("link", "href"),
                          ("iframe", "src"), ("frame", "src"), ("source", "src"), ("video", "poster"),
                          ("audio", "src"), ("object", "data"), ("form", "action")):
            for node in soup.find_all(tag):
                value = node.get(attr)
                if value:
                    self.enqueue(urljoin(url, value))
        for node in soup.find_all(attrs={"srcset": True}):
            for candidate in SRCSET_SPLIT_RE.split(node["srcset"]):
                if candidate:
                    self.enqueue(urljoin(url, candidate.split()[0]))
        for node in soup.find_all(style=True):
            self.discover_css(url, node["style"])

    def discover_css(self, url: str, text: str) -> None:
        for match in CSS_URL_RE.finditer(text):
            target = match.group(2).strip()
            if target and not target.lower().startswith("data:"):
                self.enqueue(urljoin(url, target))

    def crawl(self, start_url: str) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        self.enqueue(start_url)
        index = 0
        try:
            while index < len(self.queue):
                url = self.queue[index]
                index += 1
                retrieved = datetime.now(timezone.utc).isoformat()
                try:
                    existing = self.records.get(url)
                    if existing and existing.status.startswith("downloaded"):
                        path = self.paths[url]
                        body = (self.output / path).read_bytes()
                        if existing.resource_type in HTML_TYPES:
                            self.discover_html(url, body, None)
                        elif existing.resource_type == "text/css":
                            self.discover_css(url, body.decode("utf-8", errors="replace"))
                        print(f"SKIP {url} -> {path} (déjà téléchargé)")
                    else:
                        response = self.fetch(url)
                        final_url = normalize_url(response.url)
                        if not final_url:
                            raise requests.RequestException(f"redirected outside paeon.de: {response.url}")
                        content_type = response.headers.get("Content-Type", "")
                        path = local_path_for(url, content_type)
                        destination = self.output / path
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(response.content)
                        media = resource_type(content_type, path)
                        self.paths[url] = path
                        self.records[url] = Record(
                            url, path.as_posix(), media, retrieved, f"downloaded ({response.status_code})"
                        )
                        if media in HTML_TYPES:
                            self.discover_html(final_url, response.content, response.encoding)
                        elif media == "text/css":
                            self.discover_css(final_url, response.text)
                        print(f"OK   {url} -> {path}")
                except (requests.RequestException, OSError) as exc:
                    self.records[url] = Record(url, "", "unknown", retrieved, f"failed: {exc}")
                    print(f"FAIL {url}: {exc}")
                self.processed_since_manifest += 1
                if self.processed_since_manifest >= self.manifest_batch:
                    self.write_manifest()
        finally:
            self.write_manifest()
        self.rewrite_downloaded_files()
        self.write_manifest()

    def local_reference(self, source_url: str, source_path: Path, value: str) -> str:
        if not value or value.startswith(("#", "data:", "mailto:", "javascript:", "tel:")):
            return value
        absolute, fragment = urldefrag(urljoin(source_url, value))
        normalized = normalize_url(absolute)
        target = self.paths.get(normalized or "")
        if target is None:
            return value
        relative = Path(os.path.relpath(target, source_path.parent)).as_posix()
        return relative + (f"#{fragment}" if fragment else "")

    def rewrite_downloaded_files(self) -> None:
        for url, path in self.paths.items():
            media = self.records[url].resource_type
            full_path = self.output / path
            if media in HTML_TYPES:
                soup = BeautifulSoup(full_path.read_bytes(), "html.parser")
                for tag, attr in (("a", "href"), ("img", "src"), ("script", "src"), ("link", "href"),
                                  ("iframe", "src"), ("frame", "src"), ("source", "src"), ("video", "poster"),
                                  ("audio", "src"), ("object", "data"), ("form", "action")):
                    for node in soup.find_all(tag):
                        if node.get(attr):
                            node[attr] = self.local_reference(url, path, node[attr])
                for node in soup.find_all(attrs={"srcset": True}):
                    entries = []
                    for candidate in SRCSET_SPLIT_RE.split(node["srcset"]):
                        bits = candidate.split(maxsplit=1)
                        entries.append(self.local_reference(url, path, bits[0]) + ((" " + bits[1]) if len(bits) > 1 else ""))
                    node["srcset"] = ", ".join(entries)
                for node in soup.find_all(style=True):
                    node["style"] = self.rewrite_css(url, path, node["style"])
                full_path.write_text(str(soup), encoding="utf-8")
            elif media == "text/css":
                text = full_path.read_text(encoding="utf-8", errors="replace")
                full_path.write_text(self.rewrite_css(url, path, text), encoding="utf-8")

    def rewrite_css(self, url: str, path: Path, text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            value = self.local_reference(url, path, match.group(2).strip())
            return f"url({match.group(1)}{value}{match.group(1)})"
        return CSS_URL_RE.sub(replace, text)

    def write_manifest(self) -> None:
        with self.manifest.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            for record in self.records.values():
                writer.writerow(record.__dict__)
        self.processed_since_manifest = 0


def verify(archive: Path, manifest: Path) -> int:
    """Check manifest files and locally rewritten HTML/CSS references."""
    problems: list[str] = []
    total_resources = 0
    downloaded_resources = 0
    total_html = 0
    downloaded_html = 0
    if not manifest.exists():
        problems.append(f"manifest absent: {manifest}")
    else:
        with manifest.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                downloaded = row["status"].startswith("downloaded")
                is_html = row["resource_type"].split(";", 1)[0].strip().lower() in HTML_TYPES
                total_resources += 1
                downloaded_resources += downloaded
                total_html += is_html
                downloaded_html += downloaded and is_html
                if downloaded and not (archive / row["local_path"]).is_file():
                    problems.append(f"fichier absent: {row['local_path']}")
                if row["status"].startswith("failed"):
                    problems.append(f"téléchargement échoué: {row['original_url']} ({row['status']})")
        resource_percentage = 100 * downloaded_resources / total_resources if total_resources else 0
        html_percentage = 100 * downloaded_html / total_html if total_html else 0
        print(
            f"Téléchargements réussis : {downloaded_resources}/{total_resources} "
            f"({resource_percentage:.2f} %)"
        )
        print(
            f"Pages HTML identifiées téléchargées : {downloaded_html}/{total_html} "
            f"({html_percentage:.2f} %)"
        )
    for page in archive.rglob("*.htm*"):
        soup = BeautifulSoup(page.read_bytes(), "html.parser")
        for attr in ("href", "src", "data", "poster", "action"):
            for node in soup.find_all(attrs={attr: True}):
                value = node[attr]
                parts = urlsplit(value)
                if parts.scheme in {"http", "https"} and (parts.hostname or "").lower() in ALLOWED_HOSTS:
                    problems.append(f"lien interne non réécrit dans {page}: {value}")
                elif not parts.scheme and not value.startswith(("#", "//")):
                    candidate = (page.parent / parts.path).resolve()
                    if parts.path and not candidate.exists():
                        problems.append(f"cible locale absente dans {page}: {value}")
    if problems:
        print("Vérification incomplète :")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("Archive vérifiée : fichiers du manifeste présents et liens HTML internes consultables hors ligne.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    crawl_parser = subparsers.add_parser("crawl", help="télécharger puis adapter la copie locale")
    crawl_parser.add_argument("--start-url", default=START_URL)
    rate_group = crawl_parser.add_mutually_exclusive_group()
    rate_group.add_argument("--delay", type=float, help="délai minimal entre requêtes (secondes)")
    rate_group.add_argument(
        "--requests-per-second",
        type=float,
        default=1.0,
        help="limite globale de requêtes par seconde (par défaut : 1)",
    )
    crawl_parser.add_argument("--timeout", type=float, default=30.0)
    crawl_parser.add_argument("--retries", type=int, default=3)
    crawl_parser.add_argument("--resume", action="store_true", help="réutiliser les téléchargements réussis du manifeste")
    crawl_parser.add_argument(
        "--manifest-batch",
        type=int,
        default=100,
        help="enregistrer le manifeste toutes les N URLs (par défaut : 100)",
    )
    verify_parser = subparsers.add_parser("verify", help="contrôler la copie sans effectuer de requête réseau")
    for subparser in (crawl_parser, verify_parser):
        subparser.add_argument("--archive", type=Path, default=Path("archive"))
        subparser.add_argument("--manifest", type=Path, default=Path("archive_manifest.csv"))
    args = parser.parse_args()
    if args.command == "verify":
        return verify(args.archive, args.manifest)
    start = normalize_url(args.start_url)
    if not start:
        parser.error("l'URL initiale doit appartenir à paeon.de")
    if args.delay is not None:
        delay = max(args.delay, 0)
    else:
        if args.requests_per_second <= 0:
            parser.error("--requests-per-second doit être strictement positif")
        delay = 1 / args.requests_per_second
    Archiver(
        args.archive,
        args.manifest,
        delay,
        args.timeout,
        max(args.retries, 0),
        args.manifest_batch,
        args.resume,
    ).crawl(start)
    return verify(args.archive, args.manifest)


if __name__ == "__main__":
    raise SystemExit(main())
