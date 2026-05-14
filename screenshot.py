#!/usr/bin/env python3
"""Local screenshot helper for the dashboard. Renders index.html (loading
progress.json from the same folder) and writes screenshot.png + screenshot_mock.png.
"""
import asyncio, sys, json, shutil, http.server, socketserver, threading
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

HERE = Path(__file__).resolve().parent


def serve_static(directory: Path, port: int = 0):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)
        def log_message(self, *a, **kw): pass
    httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
    actual_port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, actual_port

MOCK = {
    "generated_at": "2026-05-13T22:15:00+00:00",
    "campaign": "Vote Paele",
    "phases": [
        {
            "id": "ph_moywoyi4060e",
            "name": "PAELE-SUPER-VTR",
            "channel": "door",
            "universe": 1812,
            "passes": 1,
            "start": "2026-05-11",
            "end": "2026-07-21",
            "count": 134,
            "count_week": 134,
            "count_month": 134,
            "week_window": ["2026-05-11", "2026-05-17"],
            "month_window": ["2026-05-11", "2026-05-31"],
            "total_goal": 1812,
            "weekly_goal": 176,
            "monthly_goal": 755,
            "daily_goal": 25.2,
            "days_total": 72,
            "days_elapsed": 3,
            "pct_total": 7.4,
            "expected_to_date": 76,
            "on_pace_pct": 176.3,
        },
        {
            "id": "ph_moywqwn6w5uu",
            "name": "LĀHUI-REGULAR-PPL",
            "channel": "phone",
            "universe": 8489,
            "passes": 1,
            "start": "2026-05-11",
            "end": "2026-07-21",
            "count": 281,
            "count_week": 281,
            "count_month": 281,
            "week_window": ["2026-05-11", "2026-05-17"],
            "month_window": ["2026-05-11", "2026-05-31"],
            "total_goal": 8489,
            "weekly_goal": 825,
            "monthly_goal": 3537,
            "daily_goal": 117.9,
            "days_total": 72,
            "days_elapsed": 3,
            "pct_total": 3.3,
            "expected_to_date": 354,
            "on_pace_pct": 79.4,
        },
    ],
}


async def shoot(playwright, url, out_path, width=1440, height=900, grain=None):
    browser = await playwright.chromium.launch()
    ctx = await browser.new_context(viewport={"width": width, "height": height},
                                    device_scale_factor=2)
    page = await ctx.new_page()
    await page.goto(url)
    await page.wait_for_selector(".card", timeout=10_000)
    if grain:
        await page.click(f'.granularity-btn[data-grain="{grain}"]')
        await page.wait_for_timeout(300)
    await page.wait_for_timeout(400)
    await page.screenshot(path=str(out_path), full_page=True)
    await browser.close()
    print(f"wrote {out_path}")


async def main():
    from playwright.async_api import async_playwright
    real_path = HERE / "progress.json"
    backup = HERE / "progress.real.json"
    mock_file = HERE / "progress.mock.json"

    # back up real, swap in mock for the mock screenshot
    if real_path.exists():
        shutil.copy(real_path, backup)
    mock_file.write_text(json.dumps(MOCK, indent=2), encoding="utf-8")

    httpd, port = serve_static(HERE)
    base = f"http://127.0.0.1:{port}/index.html"

    try:
        async with async_playwright() as p:
            # shot 1: real data, phase total view
            await shoot(p, base, HERE / "screenshot_real.png")

            # swap to mock and shoot in all three grains
            shutil.copy(mock_file, real_path)
            await shoot(p, base, HERE / "screenshot_mock_total.png")
            await shoot(p, base, HERE / "screenshot_mock_week.png",  grain="week")
            await shoot(p, base, HERE / "screenshot_mock_month.png", grain="month")

            # restore real
            if backup.exists():
                shutil.copy(backup, real_path)
                backup.unlink()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
