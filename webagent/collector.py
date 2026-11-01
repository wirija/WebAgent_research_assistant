"""Durable, deterministic collection after the model discovers an archive.

The model locates sources and can supply CSS selectors for unfamiliar layouts.
Enumeration, pagination, downloads and completion accounting operate on ALL
captured links, independently of the model's paginated excerpts.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .dates import link_date
from .state import DocumentInput
from .web import canonical, document_url, extract_html


def page_link(link, source, start, end):
    """Follow only observed archive controls, not constructed year/page URLs."""
    target = urlsplit(link["url"])
    origin = urlsplit(source)
    if target.netloc != origin.netloc:
        return False
    label = link.get("label", "").strip()
    if re.search(r"/(?:print|printview)(?:/|$)", target.path, re.I) or 'print' in parse_qs(target.query):
        return False
    years = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", label)
    if re.fullmatch(r"(?:19|20)\d{2}", label):
        return start.year <= int(label) <= end.year
    params = parse_qs(target.query)
    path_year = re.search(r"/(?:year|archive)/(\d{4})(?:/|$)", target.path, re.I)
    year = (params.get("year") or [path_year.group(1) if path_year else ""])[0]
    if year.isdigit() and len(year) == 4:
        return start.year <= int(year) <= end.year
    if link.get("selected_pagination"):
        return not years or any(start.year <= int(y) <= end.year for y in years)
    if "next" in link.get("rel", []) or re.fullmatch(r"(?:next(?:\s+page)?|older(?:\s+(?:posts|news|announcements))?|load more|show more|[›»→])", label, re.I):
        return True
    return bool(re.fullmatch(r"\d{1,3}", label) and (target.path == origin.path or re.search(r"/(?:page|p)/\d+", target.path)))


def archive_control(link, source):
    """Filters excluded by the date window must never become documents."""
    target, origin = urlsplit(link['url']), urlsplit(source)
    if target.netloc != origin.netloc:
        return False
    base = lambda path: re.split(r"/(?:year|page|cat|category|print)/", path, flags=re.I)[0]
    return (base(target.path) == base(origin.path)
            or bool(re.fullmatch(r"(?:19|20)\d{2}|All|Latest|Print(?:.*)", link.get('label', '').strip(), re.I)))


def entry_link(link):
    if link.get("selected_item"):
        return True
    if link.get("navigation") or link.get("destination_url"):
        return False
    if link_date(link)[0] or link.get("is_file") or document_url(link["url"]):
        return True
    path = urlsplit(link["url"]).path
    # Detail-page paths are a hint, not a company/domain-specific recipe.
    return bool(link.get("content_hint") or re.search(r"/(?:news|announcements?|press[-_]?releases?)(?:\.html)?/(?:id/)?[^/]+", path, re.I))


class CollectionQueue:
    def enqueue(self, kind, url, *, link_id=None, archive=None, parent=None, category="announcement", date_link_id=None):
        url = canonical(url)
        key = kind + ":" + url
        item = self.run.queue.get(key)
        if item:
            if archive and archive not in item["archives"]:
                item["archives"].append(archive)
            if parent and parent not in item["parents"]:
                item["parents"].append(parent)
            if link_id:
                new_link = self.links[link_id]
                record = next((d for d in self.run.documents if d['id'] == item['document_id']), None)
                if record:
                    source = self.observations[new_link['observation_id']]['url']
                    if source not in record['sources']:
                        record['sources'].append(source)
                old_link = self.links.get(item.get('link_id'), {})
                if link_date(new_link)[0] and not link_date(old_link)[0]:
                    item['link_id'] = link_id
                    if item['status'] == 'completed' and record and not record.get('published_date'):
                        item['status'] = 'pending'
            return item
        link = self.links.get(link_id, {})
        item = {"id": key, "kind": kind, "url": url, "link_id": link_id,
                "title": link.get("label") or url, "category": category,
                "date_link_id": date_link_id, "status": "pending", "error": None,
                "archives": [archive] if archive else [], "parents": [parent] if parent else [],
                "document_id": None, "attempts": 0}
        self.run.queue[key] = item
        return item

    def scan_observation(self, oid, root):
        obs = self.observations[oid]
        if not (obs["trusted"] or self.is_official(obs["url"]) or obs["url"] in self.trusted):
            raise ValueError("Archive must be reached from the official company website first.")
        before = len(self.run.queue)
        entries, pages = 0, 0
        for link in obs["links"]:
            if link["url"] == obs["url"]:
                continue
            if page_link(link, obs["url"], self.run.start_date, self.run.end_date):
                self.enqueue("archive", link["url"], archive=root, link_id=link["link_id"])
                pages += 1
            elif (link.get("is_file") or document_url(link["url"])) and not link.get("navigation") and not link.get("destination_url"):
                self.enqueue("document", link["url"], archive=root, link_id=link["link_id"])
                entries += 1
            elif archive_control(link, obs["url"]):
                # Follow separate disclosure categories, but not out-of-window
                # year filters, print views or an unbounded "all years" view.
                path = urlsplit(link['url']).path
                if re.search(r'/(?:cat|category)/', path) and not re.search(r'/(?:year|print)/', path):
                    self.enqueue('archive', link['url'], archive=root, link_id=link['link_id'])
                    pages += 1
            elif entry_link(link):
                self.enqueue("document", link["url"], archive=root, link_id=link["link_id"])
                entries += 1
        scan = {"observation_id": oid, "entries": entries, "page_links": pages, "new_queue_items": len(self.run.queue) - before}
        self.run.archives[root].setdefault("scans", []).append(scan)
        self.run.event("queue", f"Enumerated full archive: {entries} entries, {pages} year/page links; {scan['new_queue_items']} new queued items.", obs["url"])
        return scan

    async def scan_archive(self, observation_id, item_selector, pagination_selector):
        """Optional model-selected layout, executed across the entire HTML."""
        obs = self.observations.get(observation_id)
        if not obs or observation_id not in self.page_html:
            raise ValueError("Read/render the HTML archive before selecting its layout.")
        if not item_selector.strip():
            raise ValueError("Supply a CSS selector matching all announcement rows or links.")
        data = extract_html(self.page_html[observation_id], obs["url"], item_selector, pagination_selector)
        oid = self.observe(obs["url"], data, trusted=obs["trusted"])
        root = obs["url"]
        self.run.archives.setdefault(root, {"category": "announcements", "status": "exploring", "evidence": ""})
        self.archive_recipes[root] = {"item_selector": item_selector, "pagination_selector": pagination_selector}
        scan = self.scan_observation(oid, root)
        self.enqueue("archive", root, archive=root).update(status="completed", observation_id=oid)
        await self.drain_queue()
        return {**scan, "queue": self.run.queue_counts()}

    async def drain_queue(self):
        if self.draining or not self.run.identity:
            return
        self.draining = True
        try:
            while item := next((x for x in self.run.queue.values() if x["status"] == "pending"), None):
                # Cancellation and GUI polling must remain responsive even for
                # cached pages and large queues.
                await asyncio.sleep(0)
                item.update(status="processing", attempts=item["attempts"] + 1)
                self.run.stage = "Processing collection queue"
                self.run.save()
                try:
                    if item["kind"] == "archive":
                        result = await self.read(item["url"])
                        oid = result["observation_id"]
                        for root in list(item["archives"]):
                            recipe = self.archive_recipes.get(root)
                            if recipe and oid in self.page_html:
                                obs = self.observations[oid]
                                oid = self.observe(obs["url"], extract_html(self.page_html[oid], obs["url"], **recipe), trusted=obs["trusted"])
                            self.scan_observation(oid, root)
                        item.update(status="completed", observation_id=oid)
                    else:
                        await self.process_queued_document(item)
                except Exception as exc:
                    from .agent import safe_error
                    item.update(status="failed", error=safe_error(exc))
                    self.run.event("warning", f"Queued {item['kind']} failed: {item['title']} — {item['error']}", item["url"])
                self.run.save()
        finally:
            self.draining = False

    async def process_queued_document(self, item):
        link = self.links[item["link_id"]]
        published, evidence = link_date(link)
        doc = DocumentInput.model_validate(item["selection"]) if item.get("selection") else DocumentInput(url=link["url"], observation_id=link["observation_id"],
                            title=item["title"][:400], category=item["category"],
                            published_date=published, date_evidence=evidence)
        date_source = self.links.get(item.get("date_link_id"))
        record = await self.save_document(doc, link, date_source=date_source)
        if record["status"] == "failed":
            record = await self.recover(doc, link, record, date_source=date_source)
        item.update(document_id=record["id"], outcome=record["status"],
                    status="failed" if record["status"] == "failed" else "excluded_date" if record["status"] == "excluded_date" else "completed",
                    error=record.get("error"))
        if not record.get("file") or record["status"] == "excluded_date":
            return
        if Path(record["file"]).suffix == ".html":
            body = (self.run.folder / record["file"]).read_bytes()
            oid = self.observe(record.get("final_url", link["url"]), extract_html(body, record.get("final_url", link["url"])), trusted=True)
            obs = self.observations[oid]
            attachments = [x for x in obs["links"] if not x.get("navigation") and not x.get("destination_url")
                           and (x.get("is_file") or document_url(x["url"])) and x["url"] != link["url"]]
            for attachment in attachments:
                child = self.enqueue("document", attachment["url"], link_id=attachment["link_id"], parent=item["id"],
                                     category=item["category"], date_link_id=item.get("date_link_id") or item["link_id"])
                for root in item["archives"]:
                    if root not in child["archives"]:
                        child["archives"].append(root)
                child["title"] = (item["title"] + " — " + (attachment["label"] or "Attachment"))[:400]
            item["attachment_count"] = len(attachments)
            if attachments:
                self.run.event("queue", f"Found {len(attachments)} attached documents: {item['title']}", link["url"])

    def queue_problems(self):
        return [{"url": x["url"], "kind": x["kind"], "status": x["status"], "error": x["error"]}
                for x in self.run.queue.values() if x["status"] in {"pending", "processing", "failed"}]
