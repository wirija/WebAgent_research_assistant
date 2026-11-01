from __future__ import annotations

import asyncio

from .web import Web, validate_public_url


class Browser:
    """One isolated browser session per run. The model gets IDs, never arbitrary JS."""
    def __init__(self, web: Web):
        self.web = web
        self.manager = self.browser = self.context = self.page = None
        self.elements = []
        self.snapshot_id = ""
        self.downloads: list[str] = []

    async def start(self):
        if self.page:
            return
        from playwright.async_api import async_playwright
        self.manager = await async_playwright().start()
        try:
            self.browser = await self.manager.chromium.launch(headless=True)
        except Exception as exc:
            await self.manager.stop()
            self.manager = None
            raise RuntimeError("Browser unavailable. Run: uv run playwright install chromium") from exc
        self.context = await self.browser.new_context(accept_downloads=False, service_workers="block",
                                                      viewport={"width": 1280, "height": 900})

        async def guard(route):
            try:
                if route.request.resource_type in {"image", "media", "font"}:
                    await route.abort()
                    return
                await validate_public_url(route.request.url)
                if route.request.is_navigation_request():
                    await self.web.allowed(route.request.url)
                await route.continue_()
            except Exception:
                await route.abort()

        await self.context.route("**/*", guard)
        self.page = await self.context.new_page()
        self.page.on("download", lambda download: self.downloads.append(download.url))
        self.page.on("dialog", lambda dialog: dialog.dismiss())

    async def open(self, url: str):
        await self.start()
        await validate_public_url(url)
        await self.web.allowed(url)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=40000)
        await self.settle()
        return await self.capture()

    async def settle(self):
        try:
            await self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            await asyncio.sleep(0.5)

    async def capture(self):
        # IDs are bound to this exact snapshot and refreshed after each action.
        self.elements = await self.page.evaluate("""() => {
          document.querySelectorAll('[data-webagent-id]').forEach(e => e.removeAttribute('data-webagent-id'));
          const controls = [...document.querySelectorAll('button,select,input,[role="button"]')];
          const links = [...document.querySelectorAll('a')];
          return [...new Set([...controls, ...links])]
            .filter(e => e.getClientRects().length && !e.disabled).slice(0, 300).map((e, i) => {
              e.setAttribute('data-webagent-id', String(i));
              return {id:i, tag:e.tagName.toLowerCase(), type:e.type || '',
                label:(e.innerText || e.getAttribute('aria-label') || e.placeholder || e.value || '').slice(0,180),
                href:e.href || null,
                options:e.tagName === 'SELECT' ? [...e.options].map(o=>({label:o.label,value:o.value})) : []};
            });
        }""")
        return self.page.url, await self.page.content(), self.elements

    async def act(self, observation_id: str, action: str, element_id: int | None, value: str | None):
        if not self.page or observation_id != self.snapshot_id:
            raise ValueError("Stale browser observation. Render the page again to obtain current element IDs.")
        if action == "scroll":
            await self.page.mouse.wheel(0, 800)
        elif action == "back":
            await self.page.go_back(wait_until="domcontentloaded")
        else:
            element = next((e for e in self.elements if e["id"] == element_id), None)
            if not element:
                raise ValueError("Unknown element ID.")
            locator = self.page.locator(f'[data-webagent-id="{element_id}"]')
            if action == "click":
                # Read-only browsing: disallow file inputs and sensitive submission controls.
                label = element["label"].lower()
                if element["type"] in {"password", "file"} or any(x in label for x in ["purchase", "subscribe", "send message", "delete"]):
                    raise ValueError("This control is outside read-only collection scope.")
                try:
                    async with self.context.expect_page(timeout=1500) as popup:
                        await locator.click(timeout=12000)
                    self.page = await popup.value
                    self.page.on("download", lambda download: self.downloads.append(download.url))
                except Exception as exc:
                    # expect_page timing out is normal when a click stays in the same tab.
                    if "Timeout 1500ms" not in str(exc):
                        raise
            elif action == "select":
                await locator.select_option(value=value or "", timeout=10000)
            elif action == "fill":
                if element["type"] in {"password", "file", "email"}:
                    raise ValueError("Only public search/filter inputs may be filled.")
                await locator.fill(value or "", timeout=10000)
            elif action == "enter":
                await locator.press("Enter", timeout=10000)
            else:
                raise ValueError("Unsupported action.")
        await self.settle()
        return await self.capture()

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.manager:
            await self.manager.stop()
