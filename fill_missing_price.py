import asyncio
import pandas as pd
from playwright.async_api import async_playwright, Page, Browser


async def switch_to_usd(page: Page) -> None:
    try:
        usd_button = await page.wait_for_selector(
            '//button[.//span[contains(text(), "$")]]',
            timeout=5000
        )
        price_element = await page.query_selector(
            '//*[contains(@class, "RealtyDetails_priceSqm") and contains(text(), "$")]'
        )
        if not price_element:
            await usd_button.click()
            await page.wait_for_function(
                '''() => {
                    const el = document.querySelector('[class*="RealtyDetails_priceSqm"]');
                    return el && el.textContent.includes("$");
                }''',
                timeout=10000
            )
    except Exception:
        pass


async def is_deleted(page: Page) -> bool:
    try:
        el = await page.query_selector(".error-content")
        return el is not None
    except Exception:
        return False


async def parse_price_from_page(url: str, page: Page, retries: int = 3) -> float | None:
    for attempt in range(retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            # Чекаємо або ціну або сторінку помилки
            await page.wait_for_function(
                '''() => {
                    return document.querySelector(".error-content") ||
                           document.querySelector('[class*="RealtyDetails_priceSqm"]');
                }''',
                timeout=10000
            )

            if await is_deleted(page):
                print(f"Deleted listing: {url}")
                return None

            await switch_to_usd(page)

            price_element = await page.query_selector('[class*="RealtyDetails_priceSqm"]')
            price_text = (await price_element.inner_text()).strip()

            if "$" not in price_text:
                raise ValueError(f"Price not in $: {price_text}")

            price = float(
                price_text.split("$")[0].strip().replace(" ", "").replace("\xa0", "")
            )
            return price

        except Exception as e:
            print(f"Attempt {attempt + 1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2)

    return None


async def fill_missing_prices_worker(
    rows: list[tuple],
    thread_id: int,
    csv_filename: str,
    df: pd.DataFrame,
    lock: asyncio.Lock,
    browser: Browser
) -> None:
    page = await browser.new_page()
    try:
        for idx, row in rows:
            if pd.isna(row["url"]):
                print(f"[Worker {thread_id}] Skipping row {idx} — no url")
                continue

            price = await parse_price_from_page(row["url"], page)

            if price is not None:
                async with lock:
                    df.at[idx, "price"] = price
                    df.to_csv(csv_filename, index=False)
                print(f"[Worker {thread_id}] ✓ {row['url']} — {price} $/м²")
            else:
                print(f"[Worker {thread_id}] ✗ {row['url']} — could not parse")
    finally:
        await page.close()


async def fill_missing_prices(csv_filename: str, num_workers: int = 2) -> None:
    df = pd.read_csv(csv_filename)
    missing = df[df["price"].isna()]
    print(f"Found {len(missing)} apartments without price in {csv_filename}")

    if missing.empty:
        print("No missing prices, skipping")
        return

    rows = list(missing.iterrows())
    chunk_size = max(1, len(rows) // num_workers)
    chunks = [rows[i:i + chunk_size] for i in range(0, len(rows), chunk_size)]

    lock = asyncio.Lock()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            await asyncio.gather(*[
                fill_missing_prices_worker(chunk, thread_id, csv_filename, df, lock, browser)
                for thread_id, chunk in enumerate(chunks)
            ])
        finally:
            await browser.close()

    print(f"Done {csv_filename}")


async def main() -> None:
    files = [
        "Cherkasy.csv", "Chernihiv.csv", "Chernivtsi.csv", "Dnipro.csv", "Ivano-Frankivsk.csv",
        "Kharkiv.csv", "Kherson.csv", "Khmelnytskyi.csv", "Kropyvnytskyi.csv", "Kyiv.csv",
        "Lutsk.csv", "Lviv.csv", "Mykolaiv.csv", "Odesa.csv", "Poltava.csv", "Rivne.csv",
        "Sumy.csv", "Ternopil.csv", "Uzhhorod.csv", "Vinnytsia.csv", "Zaporizhzhia.csv",
        "Zhytomyr.csv"
    ]
    for filename in files:
        await fill_missing_prices(filename, num_workers=2)


if __name__ == "__main__":
    asyncio.run(main())
