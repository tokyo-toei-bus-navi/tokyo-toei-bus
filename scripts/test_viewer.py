import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()

        # Track network requests
        requested_urls = []
        page.on('request', lambda req: requested_urls.append(req.url))

        # 1. Test index.html
        print("Testing index.html...")
        await page.goto("http://localhost:8000/index.html")
        await page.wait_for_selector("#link-area-23")
        await page.wait_for_selector("#link-area-tama")
        await page.screenshot(path="screenshot_index.png")

        # 2. Click 23 Wards
        print("Clicking 23 Wards card...")
        await page.click("#link-area-23")
        await page.wait_for_url("**/map.html?area=23")
        await page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)
        assert any("metadata_23.json" in u for u in requested_urls), "metadata_23.json should be requested"
        assert not any("metadata_tama.json" in u for u in requested_urls), "metadata_tama.json should NOT be requested"
        print("Loaded map.html?area=23 successfully")

        # Search for 東京テレポート
        print("Searching for 東京テレポート...")
        await page.fill("#stop-search-input", "東京テレポート")
        await page.wait_for_selector(".search-item")
        await page.click(".search-item:first-child")

        # Check side panel is open
        await page.wait_for_selector("#side-panel.open")
        current_url = page.url
        print("Current URL after stop select:", current_url)
        assert "stop=2326" in current_url, f"Expected stop=2326 in URL, got {current_url}"

        # Click route 波01 (id: 002)
        print("Selecting route 002 (波01)...")
        await page.wait_for_selector("#chip-002")
        await page.click("#chip-002")

        current_url = page.url
        print("Current URL after route select:", current_url)
        assert "route=002" in current_url, f"Expected route=002 in URL, got {current_url}"

        # Wait for timetable list
        await page.wait_for_selector(".timetable-row")
        rows = await page.query_selector_all(".timetable-row")
        print(f"Timetable rows count for 波01 at 東京テレポート駅前: {len(rows)}")

        # Check no duplicate times in timetable
        times = []
        for row in rows:
            time_el = await row.query_selector(".timetable-time")
            t_text = (await time_el.inner_text()).strip()
            times.append(t_text)

        from collections import Counter
        counts = Counter(times)
        dups = [t for t, c in counts.items() if c > 1]
        print(f"Duplicates in 波01 timetable: {dups}")
        assert len(dups) == 0, f"Found duplicate departures: {dups}"

        # Click first trip row
        first_trip = rows[0]
        trip_id = (await first_trip.get_attribute("id")).replace("trip-row-", "")
        print(f"Clicking trip {trip_id}...")
        await first_trip.click()

        await page.wait_for_selector(".trip-section")
        current_url = page.url
        print("Current URL after trip select:", current_url)
        assert f"trip={trip_id}" in current_url, f"Expected trip={trip_id} in URL, got {current_url}"

        await page.screenshot(path="screenshot_map_23.png")

        # 3. Test Deep-Link directly
        print("Testing direct deep-link URL restoration...")
        deep_link_page = await context.new_page()
        await deep_link_page.goto(current_url)
        await deep_link_page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)
        await deep_link_page.wait_for_selector(".trip-section")
        print("Deep link restored stop, route, and trip successfully!")

        # 4. Test Tama Area
        print("Testing Tama area...")
        tama_page = await context.new_page()
        await tama_page.goto("http://localhost:8000/map.html?area=tama")
        await tama_page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)
        badge_text = await tama_page.inner_text("#area-badge-label")
        print("Tama area badge text:", badge_text)
        assert "多摩" in badge_text

        # Search for 青梅駅
        await tama_page.fill("#stop-search-input", "青梅駅")
        await tama_page.wait_for_selector(".search-item")
        await tama_page.click(".search-item:first-child")
        await tama_page.wait_for_selector("#side-panel.open")
        print("Tama stop selected, current URL:", tama_page.url)
        assert "area=tama&stop=" in tama_page.url
        await tama_page.screenshot(path="screenshot_map_tama.png")

        await browser.close()
        print("All Playwright verification tests passed!")

asyncio.run(run())
