import asyncio
import csv
import json
import math
import os
import re
from dataclasses import dataclass, field, fields
from playwright.async_api import async_playwright, Page, Browser
from flats.parser import CITIES, CityConfig, chunk_list


PURPOSE_MAP = {
    "банк": "is_bank",
    "офіс": "is_office",
    "послуги": "is_services",
    "склад": "is_warehouse",
    "виробництво": "is_production",
    "вільне": "is_free",
    "торгівельне": "is_retail",
    "гараж": "is_garage",
    "паркомісце": "is_parking_spot",
}

CITY_CENTERS: dict[str, tuple[float, float]] = {
    "Kyiv": (50.4501, 30.5234),
    "Lviv": (49.8397, 24.0297),
    "Kharkiv": (50.0038, 36.2304),
    "Dnipro": (48.4647, 35.0462),
    "Zaporizhzhia": (47.8388, 35.1396),
    "Odesa": (46.4825, 30.7233),
    "Poltava": (49.5883, 34.5514),
    "Mykolaiv": (46.9750, 31.9946),
    "Cherkasy": (49.4444, 32.0598),
    "Kropyvnytskyi": (48.5079, 32.2623),
    "Sumy": (50.9077, 34.7981),
    "Chernihiv": (51.4982, 31.2893),
    "Zhytomyr": (50.2547, 28.6587),
    "Lutsk": (50.7472, 25.3254),
    "Ivano-Frankivsk": (48.9215, 24.7097),
    "Ternopil": (49.5535, 25.5948),
    "Khmelnytskyi": (49.4230, 26.9871),
    "Uzhhorod": (48.6208, 22.2879),
    "Vinnytsia": (49.2331, 28.4682),
    "Rivne": (50.6199, 26.2516),
    "Chernivtsi": (48.2917, 25.9352),
    "Kherson": (46.6354, 32.6169)
}


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ========================
# DATA STRUCTURES
# ========================

@dataclass
class Commercial:
    # Ідентифікація
    group_id: str | None = None
    has_duplicates: bool | None = None
    url: str | None = None

    # Локація
    city: str = ""
    district: str = ""  # заповнюється лише для Києва
    residential_complex: str | None = None
    lat: float | None = None
    lon: float | None = None
    distance_to_center_km: float | None = None
    poi_name: str | None = None
    poi_distance_m: float | None = None
    geo_region: str = ""

    # Ціна (завжди USD)
    price: float | None = None

    # Фізичні характеристики
    area: float | None = None
    floor: int | None = None
    floor_count: int | None = None
    house_type: str | None = None
    wall_type: str | None = None
    heating: str | None = None
    ceiling_height: float | None = None
    year_of_building: int | None = None

    # Призначення
    is_bank: int = 0
    is_office: int = 0
    is_services: int = 0
    is_warehouse: int = 0
    is_production: int = 0
    is_free: int = 0
    is_retail: int = 0
    is_garage: int = 0
    is_parking_spot: int = 0

    # Дохідні сигнали (regex з тексту — структурованого поля не існує)
    has_tenant: int = 0
    rental_price: float | None = None
    implied_yield: float | None = None

    # Автономність / інженерія (regex з тексту — JSON-поля завжди null)
    has_generator: int = 0
    has_autonomous_heating: int = 0
    has_gas_boiler: int = 0
    separate_entrance: int = 0
    basement_level: int = 0

    # Метадані
    without_commission: int = 0
    is_exclusive: int = 0
    text: str = ""


LinkRow = tuple[str, str, str, str]  # (url, district, city, geo_region)
LINK_CSV_HEADER = ["url", "district", "city", "geo_region"]


@dataclass
class CommercialCityConfig:
    city: str
    geo_region: str
    # Київ: district-level breakdown (як і для квартир)
    district_urls: dict[str, list[str]] = field(default_factory=dict)
    # Решта міст: один базовий URL на все місто (без поділу по районах —
    # ідентифікація локації тепер робиться через координати + відстань
    # до центру, а не через district-фільтри у запиті)
    url: str | None = None


def slug_from_url(url: str) -> str:
    m = re.search(r"/sale/([^/]+)/", url)
    return m.group(1) if m else ""


def _kyiv_commercial_district_urls() -> dict[str, list[str]]:
    """Київ лишається окремо по офіційних районах (як і для квартир) —
    підміняємо 'flats-' на 'commercial-' у вже готових URL з CITIES."""
    kyiv = next(c for c in CITIES if c.city == "Kyiv")
    return {
        district: [u.replace("/flats-", "/commercial-") for u in urls]
        for district, urls in kyiv.district_urls.items()
    }


# ========================
# МІСТА
# ========================

COMMERCIAL_CITIES: list[CommercialCityConfig] = [
    CommercialCityConfig(
        city="Kyiv",
        geo_region="Center",
        district_urls=_kyiv_commercial_district_urls(),
    ),
    CommercialCityConfig(city="Lviv", geo_region="West", url="https://lun.ua/sale/lviv/commercial"),
    CommercialCityConfig(city="Kharkiv", geo_region="East", url="https://lun.ua/sale/kharkiv/commercial"),
    CommercialCityConfig(city="Dnipro", geo_region="East", url="https://lun.ua/sale/dnipro/commercial"),
    CommercialCityConfig(city="Zaporizhzhia", geo_region="East", url="https://lun.ua/sale/zp/commercial"),
    CommercialCityConfig(city="Odesa", geo_region="South", url="https://lun.ua/sale/odesa/commercial"),
    CommercialCityConfig(city="Poltava", geo_region="Center", url="https://lun.ua/sale/poltava/commercial"),
    CommercialCityConfig(city="Mykolaiv", geo_region="South", url="https://lun.ua/sale/mykolaiv/commercial"),
    CommercialCityConfig(city="Cherkasy", geo_region="Center", url="https://lun.ua/sale/cherkasy/commercial"),
    CommercialCityConfig(city="Kropyvnytskyi", geo_region="Center", url="https://lun.ua/sale/kr/commercial"),
    CommercialCityConfig(city="Sumy", geo_region="North", url="https://lun.ua/sale/sumy/commercial"),
    CommercialCityConfig(city="Chernihiv", geo_region="North", url="https://lun.ua/sale/chernihiv/commercial"),
    CommercialCityConfig(city="Zhytomyr", geo_region="North", url="https://lun.ua/sale/zhytomyr/commercial"),
    CommercialCityConfig(city="Lutsk", geo_region="West", url="https://lun.ua/sale/volyn/commercial"),
    CommercialCityConfig(city="Ivano-Frankivsk", geo_region="West", url="https://lun.ua/sale/if/commercial"),
    CommercialCityConfig(city="Ternopil", geo_region="West", url="https://lun.ua/sale/ternopil/commercial"),
    CommercialCityConfig(city="Khmelnytskyi", geo_region="West", url="https://lun.ua/sale/khmelnytskyi/commercial"),
    CommercialCityConfig(city="Uzhhorod", geo_region="West", url="https://lun.ua/sale/uz/commercial"),
    CommercialCityConfig(city="Vinnytsia", geo_region="Center", url="https://lun.ua/sale/vinnytsia/commercial"),
    CommercialCityConfig(city="Rivne", geo_region="West", url="https://lun.ua/sale/rivne/commercial"),
    CommercialCityConfig(city="Chernivtsi", geo_region="West", url="https://lun.ua/sale/chernivtsi/commercial"),
    CommercialCityConfig(city="Kherson", geo_region="South", url="https://lun.ua/sale/kherson/commercial"),
]


# ========================
# PARSING — LIST PAGE
# ========================

async def switch_to_usd_list(page: Page) -> None:
    try:
        prices = await page.query_selector_all('[class*="RealtyCard_priceSqm"]')
        if prices:
            first_text = await prices[0].inner_text()
            if "$" in first_text:
                return
        usd_btn = await page.wait_for_selector(
            'button:has(span:text("$"))', timeout=5000
        )
        if usd_btn:
            await usd_btn.click()
            await page.wait_for_function(
                '''() => {
                    const els = document.querySelectorAll('[class*="RealtyCard_priceSqm"]');
                    return els.length > 0 && els[0].textContent.includes("$");
                }''',
                timeout=10000
            )
    except Exception:
        pass


async def get_commercial_urls_from_page(page: Page) -> list[str]:
    urls = []
    try:
        html = await page.content()
        ids = re.findall(r"page_id:(\d+)", html)
        for pid in dict.fromkeys(ids):
            urls.append(f"https://lun.ua/realty/{pid}")
    except Exception as e:
        print(f"  Error getting URLs: {e}")
    return urls


async def get_urls_from_listing(listing_url: str, page: Page, on_page_urls=None) -> list[str]:
    all_urls: list[str] = []
    page_number = 1

    while True:
        paginated_url = f"{listing_url}?page={page_number}"
        print(f"  Scanning page: {paginated_url}")
        try:
            await page.goto(paginated_url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"  Failed to load page {page_number}: {e}")
            break

        try:
            await page.wait_for_selector('[class*="RealtyCard_propertyGrid"]', timeout=10000)
            await page.wait_for_timeout(1500)
        except Exception:
            print(f"  No cards on page {page_number}, stopping")
            break

        await switch_to_usd_list(page)

        urls = await get_commercial_urls_from_page(page)
        print(f"  Apartment ids found on page: {len(urls)}")
        if not urls:
            print(f"  No URLs found on page {page_number}, stopping")
            break

        if on_page_urls is not None:
            await on_page_urls(urls)

        all_urls.extend(urls)
        print(f"  Found {len(urls)} objects on page {page_number}, total: {len(all_urls)}")
        page_number += 1

    return all_urls


# ========================
# PARSING — DETAIL PAGE (JSON-based)
# ========================

async def switch_to_usd_detail(page: Page) -> None:
    try:
        price_el = await page.query_selector('[class*="RealtyDetails_priceSqm"]')
        if price_el:
            text = await price_el.inner_text()
            if "$" in text:
                return
        usd_btn = await page.wait_for_selector(
            'button:has(span:text("$"))', timeout=5000
        )
        if usd_btn:
            await usd_btn.click()
            await page.wait_for_function(
                '''() => {
                    const el = document.querySelector('[class*="RealtyDetails_priceSqm"]');
                    return el && el.textContent.includes("$");
                }''',
                timeout=10000
            )
    except Exception:
        pass


def extract_realty_json(html: str) -> dict | None:
    for marker, escaped in (('\\"data\\":{\\"id\\"', True), ('"data":{"id"', False)):
        idx = html.find(marker)
        if idx == -1:
            continue

        start = idx + (len('\\"data\\":') if escaped else len('"data":'))
        depth = 0
        in_str = False
        i = start
        n = len(html)
        end = None

        while i < n:
            ch = html[i]
            if escaped:
                if ch == "\\" and i + 1 < n and html[i + 1] == '"':
                    in_str = not in_str
                    i += 2
                    continue
                if ch == "\\" and i + 1 < n and html[i + 1] == "\\":
                    i += 2
                    continue
            else:
                if ch == '"':
                    in_str = not in_str
                    i += 1
                    continue
                if ch == "\\":
                    i += 2
                    continue

            if not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            i += 1

        if end is None:
            continue

        raw = html[start:end]
        if escaped:
            raw = raw.replace('\\"', '"').replace("\\\\", "\\")

        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

    return None


def resolve_text_reference(html: str, text_value: str | None) -> str:
    if not text_value:
        return ""

    m = re.match(r"^\$([0-9a-f]+)$", text_value)
    if not m:
        return text_value

    ref_id = m.group(1)
    decl_match = re.search(re.escape(ref_id) + r":T[0-9a-f]+,", html)
    if not decl_match:
        return ""

    push_marker = 'self.__next_f.push([1,"'
    start = html.find(push_marker, decl_match.end())
    if start == -1:
        return ""
    start += len(push_marker)

    i = start
    n = len(html)
    while i < n:
        if html[i] == "\\":
            i += 2
            continue
        if html[i] == '"':
            break
        i += 1
    raw = html[start:i]

    try:
        return json.loads('"' + raw + '"')
    except (json.JSONDecodeError, ValueError):
        return raw


def parse_income_signals(text_lower: str, price: float | None) -> dict:
    result = {"has_tenant": 0, "rental_price": None, "implied_yield": None}

    if re.search(r"орендує|орендар|здає[а-я]* в оренду|працює як", text_lower):
        result["has_tenant"] = 1

    rent_match = re.search(
        r"оренд[а-я]*[^\d\n]{0,15}(\d[\d\s]{1,6})\s*(\$|у\.?о\.?|usd)",
        text_lower,
    )
    if rent_match:
        try:
            result["rental_price"] = float(rent_match.group(1).replace(" ", ""))
        except ValueError:
            pass

    if result["rental_price"] and price:
        result["implied_yield"] = (result["rental_price"] * 12) / price

    return result


def parse_autonomy_signals(text_lower: str) -> dict:
    return {
        "has_generator": int(bool(re.search(r"генератор", text_lower))),
        "has_autonomous_heating": int(bool(
            re.search(r"автономн[а-я]* опаленн", text_lower)
        )),
        "has_gas_boiler": int(bool(
            re.search(r"газов[а-я]* котельн|газовий котел", text_lower)
        )),
        "separate_entrance": int(bool(
            re.search(r"окрем[а-я]* вхід|власний вхід", text_lower)
        )),
        "basement_level": int(bool(
            re.search(r"цокол|напівпідвал|підвальн", text_lower)
        )),
    }


async def parse_commercial_detail_page(url: str, page: Page, retries: int = 3) -> Commercial | None:
    for attempt in range(retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_function(
                '''() => {
                    const el = document.querySelector('[class*="RealtyDetails_priceSqm"]')
                        || document.querySelector('.PropertyItem_text__IADK7');
                    return !!el;
                }''',
                timeout=10000
            )

            if await page.query_selector(".error-content"):
                print(f"  Deleted: {url}")
                return None

            await switch_to_usd_detail(page)

            html = await page.content()
            data = extract_realty_json(html)
            if data is None:
                print(f"  JSON not found (page structure changed?): {url}")
                return None

            apt = Commercial(url=url)

            apt.group_id = data.get("groupId")
            apt.has_duplicates = data.get("hasDuplicates")

            apt.price = data.get("price")
            apt.area = data.get("areaTotal")
            apt.floor = data.get("floor")
            apt.floor_count = data.get("floorCount")
            apt.house_type = data.get("houseTypeName")
            apt.wall_type = data.get("wallTypeName")
            apt.heating = data.get("heatingSystemName")
            apt.ceiling_height = data.get("ceilingHeight")
            apt.year_of_building = data.get("builtYear")

            apt.without_commission = int(bool(data.get("withoutCommission")))
            apt.is_exclusive = int(bool(data.get("isExclusive")))

            text = resolve_text_reference(html, data.get("text"))
            apt.text = text
            text_lower = text.lower()

            location = data.get("location")
            if location and len(location) == 2:
                apt.lon, apt.lat = location[0], location[1]

            poi = data.get("poi")
            if poi:
                apt.poi_name = poi.get("name")
                apt.poi_distance_m = poi.get("distance")

            for entity in data.get("geoEntities") or []:
                if entity.get("type") == "residential_complex":
                    apt.residential_complex = entity.get("name")

            # Призначення — з JSON-поля aim[] (список {"name":.., "id":..})
            for aim_item in data.get("aim") or []:
                name = (aim_item.get("name") or "").strip().lower()
                field_name = PURPOSE_MAP.get(name)
                if field_name:
                    setattr(apt, field_name, 1)

            # Дохідні та автономні сигнали — лише regex по тексту
            income = parse_income_signals(text_lower, apt.price)
            apt.has_tenant = income["has_tenant"]
            apt.rental_price = income["rental_price"]
            apt.implied_yield = income["implied_yield"]

            autonomy = parse_autonomy_signals(text_lower)
            apt.has_generator = autonomy["has_generator"]
            apt.has_autonomous_heating = autonomy["has_autonomous_heating"]
            apt.has_gas_boiler = autonomy["has_gas_boiler"]
            apt.separate_entrance = autonomy["separate_entrance"]
            apt.basement_level = autonomy["basement_level"]

            has_purpose = any([
                apt.is_bank, apt.is_office, apt.is_services, apt.is_warehouse,
                apt.is_production, apt.is_free, apt.is_retail, apt.is_garage,
                apt.is_parking_spot,
            ])
            if apt.area is None or not has_purpose:
                print(f"  Skipped (missing area or purpose): {url}")
                return None

            is_garage_parking_or_warehouse = (
                apt.is_garage or apt.is_parking_spot or apt.is_warehouse
            )
            if not is_garage_parking_or_warehouse:
                if (
                    apt.house_type is None
                    or apt.wall_type is None
                    or apt.heating is None
                    or apt.year_of_building is None
                    or apt.ceiling_height is None
                ):
                    print(f"  Skipped (premises missing required fields): {url}")
                    return None

            return apt

        except Exception as e:
            print(f"  Attempt {attempt + 1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2)

    return None


# ========================
# CSV
# ========================

def write_commercial_to_csv(obj: Commercial, filename: str) -> None:
    file_exists = os.path.exists(filename)
    fieldnames = [f.name for f in fields(Commercial)]
    with open(filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(obj.__dict__)


def append_links_to_csv(rows: list[LinkRow], filename: str) -> None:
    if not rows:
        return
    file_exists = os.path.exists(filename)
    with open(filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(LINK_CSV_HEADER)
        writer.writerows(rows)


def read_links_from_csv(filename: str) -> list[LinkRow]:
    rows: list[LinkRow] = []
    seen: set[str] = set()
    if not os.path.exists(filename):
        return rows
    with open(filename, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) != 4:
                continue
            url = row[0]
            if url in seen:
                continue
            seen.add(url)
            rows.append((row[0], row[1], row[2], row[3]))
    return rows


# ========================
# WORKERS
# ========================

async def scan_worker(
    tasks: list[tuple[str, str]],
    worker_id: int,
    city: str,
    geo_region: str,
    links_filename: str,
    lock: asyncio.Lock,
    browser: Browser,
) -> int:
    page = await browser.new_page()
    total_found = 0
    try:
        for listing_url, district in tasks:
            label = district if district else "(без поділу по районах)"
            print(f"\n[Scanner {worker_id}] {label} — {listing_url}")

            async def on_page_urls(urls: list[str], _district: str = district) -> None:
                rows: list[LinkRow] = [(u, _district, city, geo_region) for u in urls]
                async with lock:
                    append_links_to_csv(rows, links_filename)

            urls_found = await get_urls_from_listing(listing_url, page, on_page_urls=on_page_urls)
            total_found += len(urls_found)
    finally:
        await page.close()
    return total_found


async def detail_worker(
    urls: list[tuple[str, str, str, str]],
    worker_id: int,
    csv_filename: str,
    lock: asyncio.Lock,
    browser: Browser,
) -> None:
    page = await browser.new_page()
    try:
        for url, district, city, geo_region in urls:
            apt = await parse_commercial_detail_page(url, page)
            if apt is None:
                continue

            apt.district = district
            apt.city = city
            apt.geo_region = geo_region

            center = CITY_CENTERS.get(city)
            if center and center != (0.0, 0.0) and apt.lat is not None and apt.lon is not None:
                apt.distance_to_center_km = haversine(apt.lat, apt.lon, center[0], center[1])

            async with lock:
                write_commercial_to_csv(apt, csv_filename)

            print(f"[Worker {worker_id}] ✓ saved: {url}")
    finally:
        await page.close()


async def process_city(
    city_config: CommercialCityConfig,
    num_scan_workers: int = 3,
    num_detail_workers: int = 5,
) -> None:
    csv_filename = f"{city_config.city}_commercial.csv"
    links_filename = f"{city_config.city}_commercial_links.csv"
    print(f"\n{'='*50}")
    print(f"Processing (commercial): {city_config.city}")
    print(f"{'='*50}")

    if os.path.exists(csv_filename):
        os.remove(csv_filename)
    if os.path.exists(links_filename):
        os.remove(links_filename)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

        if city_config.district_urls:
            listing_tasks: list[tuple[str, str]] = [
                (listing_url, district)
                for district, urls in city_config.district_urls.items()
                for listing_url in urls
            ]
        elif city_config.url:
            listing_tasks = [(city_config.url, "")]
        else:
            listing_tasks = []

        if not listing_tasks:
            print(f"  ⚠ No URL configured for {city_config.city} yet. Skipping.")
            await browser.close()
            return

        scan_chunks = chunk_list(listing_tasks, num_scan_workers)
        scan_lock = asyncio.Lock()
        results = await asyncio.gather(*[
            scan_worker(
                chunk, worker_id, city_config.city, city_config.geo_region,
                links_filename, scan_lock, browser,
            )
            for worker_id, chunk in enumerate(scan_chunks)
        ])
        print(f"\nScanning done — {sum(results)} links written to {links_filename}")

        all_detail_urls = read_links_from_csv(links_filename)
        print(f"Total unique objects to parse: {len(all_detail_urls)}")

        detail_chunks = chunk_list(all_detail_urls, num_detail_workers)
        detail_lock = asyncio.Lock()
        await asyncio.gather(*[
            detail_worker(chunk, worker_id, csv_filename, detail_lock, browser)
            for worker_id, chunk in enumerate(detail_chunks)
        ])

        await browser.close()

    print(f"\n✓ {city_config.city} (commercial) done — saved to {csv_filename}")


# ========================
# MAIN
# ========================

async def main() -> None:
    for city_config in COMMERCIAL_CITIES:
        await process_city(city_config, num_scan_workers=8, num_detail_workers=12)


if __name__ == "__main__":
    asyncio.run(main())
