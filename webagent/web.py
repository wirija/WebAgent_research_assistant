from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from .dates import DATE

FILE_SUFFIXES = {".pdf", ".xlsx", ".xls", ".pptx", ".ppt", ".doc", ".docx", ".zip", ".csv"}


def document_url(url):
    from pathlib import PurePosixPath
    return PurePosixPath(urlsplit(url).path).suffix.lower() in FILE_SUFFIXES


def published_destination(url):
    """Unwrap only a literal HTTP(S) document destination published in a link.

    No guessed filenames or redirects are fetched here. The destination still
    passes public-address and robots checks at download time.
    """
    for key, values in parse_qs(urlsplit(url).query).items():
        if key.lower() in {"redirect", "url", "target", "destination"} and len(values) == 1:
            try:
                value = canonical(values[0])
                if document_url(value):
                    return value
            except ValueError:
                pass
    return None

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
MAX_FILE_BYTES = 50 * 1024 * 1024


def canonical(url: str) -> str:
    p = urlsplit(url.strip())
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Only public HTTP(S) URLs without credentials are supported.")
    if p.port not in {None, 80, 443}:
        raise ValueError("Only standard web ports are supported.")
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))


async def validate_public_url(url: str) -> str:
    url = canonical(url)
    host = urlsplit(url).hostname
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Private, local, and reserved network addresses cannot be browsed.")
    return url


@dataclass
class Fetch:
    url: str
    body: bytes
    content_type: str


class Web:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(35), follow_redirects=False,
                                      headers={"User-Agent": USER_AGENT}, trust_env=False)
        self.robots: dict[str, RobotFileParser | None] = {}
        self.last_access: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _raw(self, url: str, max_bytes: int = MAX_FILE_BYTES, respect_robots: bool = False) -> Fetch:
        for _ in range(8):
            url = await validate_public_url(url)
            if respect_robots:
                await self.allowed(url)
            host = urlsplit(url).netloc
            lock = self._locks.setdefault(host, asyncio.Lock())
            async with lock:
                await asyncio.sleep(max(0, 0.4 - (time.monotonic() - self.last_access.get(host, 0))))
                self.last_access[host] = time.monotonic()
                async with self.client.stream("GET", url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("Redirect has no destination.")
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError(f"Download exceeds the {max_bytes // 1024 // 1024} MB limit.")
                        chunks.append(chunk)
                    return Fetch(url, b"".join(chunks), response.headers.get("content-type", "").lower())
        raise ValueError("Too many redirects.")

    async def allowed(self, url: str):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            try:
                result = await self._raw(origin + "/robots.txt", max_bytes=1024 * 1024)
                parser = RobotFileParser()
                parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
                self.robots[origin] = parser
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {404, 410}:
                    self.robots[origin] = None
                else:
                    raise ValueError(f"Could not verify robots.txt ({exc.response.status_code}).") from exc
        parser = self.robots[origin]
        if parser and not parser.can_fetch(USER_AGENT, url):
            raise ValueError("This URL is disallowed by robots.txt.")

    async def fetch(self, url: str, max_bytes: int = MAX_FILE_BYTES) -> Fetch:
        for attempt in range(3):
            try:
                return await self._raw(url, max_bytes, respect_robots=True)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {429, 500, 502, 503, 504}:
                    raise
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("Fetch failed")

    async def close(self):
        await self.client.aclose()


def extract_html(body: bytes | str, url: str, item_selector="", pagination_selector="") -> dict:
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    selected_items = {id(n) for n in soup.select(item_selector)} if item_selector else set()
    selected_pages = {id(n) for n in soup.select(pagination_selector)} if pagination_selector else set()
    links, seen = [], set()
    for node in soup.select("a[href], iframe[src], embed[src], object[data]"):
        href = node.get("href") or node.get("src") or node.get("data")
        try:
            href = canonical(urljoin(url, href))
        except (ValueError, TypeError):
            continue
        if href in seen:
            continue
        seen.add(href)
        parent = node.find_parent(["tr", "li", "article"]) or node.parent
        navigation = bool(node.find_parent(["nav", "header", "footer"]))
        navigation = navigation or any(re.search(r"(?:^|[-_\s])(?:nav|menu|footer|header)(?:$|[-_\s])", " ".join(p.get("class", [])) + " " + p.get("id", ""), re.I)
                                       for p in node.parents if p.name)
        date_context = ""
        if parent:
            times = parent.select("time")
            if times:
                date_context = " ".join(t.get("datetime") or t.get_text(" ", strip=True) for t in times)
            elif parent.name == "tr":
                # Keep the separate date column; do not mistake a period in the
                # linked title for its publication date.
                cells = [c.get_text(" ", strip=True) for c in parent.find_all(["td", "th"], recursive=False)
                         if not c.find(["a", "button"])]
                date_context = " ".join(text for text in cells if re.fullmatch(DATE, text, re.I))
            if not date_context:
                outside = " ".join(str(s).strip() for s in parent.strings
                                   if not any(p.name == "a" for p in s.parents)).strip()
                if re.fullmatch(DATE, outside, re.I):
                    date_context = outside
        label = node.get_text(" ", strip=True) or node.get("title", "") or node.get("aria-label", "")
        context = parent.get_text(" ", strip=True) if parent else ""
        # A whole body/div is not a publication row. Avoid matching every menu
        # link against the first announcement's text.
        if len(context) > 800 or navigation:
            context = label
            date_context = ""
        destination = published_destination(href)
        entry = {"url": href, "label": label[:250], "context": context[:800],
                 "date_context": date_context[:400], "navigation": navigation,
                 "is_file": document_url(href) or bool(destination) or node.has_attr("download")
                            or (node.name in {"iframe", "embed", "object"} and 'pdf' in node.get('type', '').lower())
                            or bool(re.search(r"\b(?:pdf|download|attachment)\b", label, re.I)),
                 "selected_item": id(node) in selected_items or any(id(p) in selected_items for p in node.parents),
                 "selected_pagination": id(node) in selected_pages or any(id(p) in selected_pages for p in node.parents),
                 "rel": node.get("rel", []),
                 "content_hint": bool(node.find_parent(["article", "h2", "h3", "h4"]))}
        links.append(entry)
        if destination and destination not in seen:
            seen.add(destination)
            links.append({**entry, "url": destination, "via_url": href, "is_file": True})
            entry["destination_url"] = destination
    # URL-valued year/pagination options are observed links too. Numeric options
    # remain browser controls; never synthesize URLs from their values.
    for option in soup.select("select option[value]"):
        value = option.get("value", "")
        if not value.startswith(("/", "http://", "https://", "?")):
            continue
        try:
            target = canonical(urljoin(url, value))
        except ValueError:
            continue
        if target not in seen:
            seen.add(target)
            links.append({"url": target, "label": option.get_text(" ", strip=True), "context": "Archive filter", "date_context": "", "filter_option": True})
    metadata = [{"name": x.get("property") or x.get("name"), "content": x.get("content")}
                for x in soup.select("meta[content]") if re.search("date|time|title", x.get("property", "") + x.get("name", ""), re.I)]
    structured = "\n".join(x.get_text() for x in soup.select('script[type="application/ld+json"]'))
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    text = soup.get_text(" ", strip=True)
    # Retained privately by the controller for full-page selector extraction.
    # A small markup sample helps the model adapt to unfamiliar archive layouts.
    structure = "\n".join(str(n)[:2000] for n in soup.select("main, article, table, ul, select")[:12])[:16000]
    return {"title": title, "text": text + "\n" + structured[:10000], "links": links, "metadata": metadata,
            "structure": structure, "html": body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body}
