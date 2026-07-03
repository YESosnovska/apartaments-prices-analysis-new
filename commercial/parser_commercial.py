import asyncio
import csv
import os
import re
from dataclasses import dataclass, field, fields
from playwright.async_api import async_playwright, Page, Browser

# parser.py must be in the same folder — ми перевикористовуємо вже готовий
# перелік міст/районів (той самий сайт, ті самі слаги районів для Києва).
from flats.parser import CITIES, CityConfig, chunk_list

# ========================
# MAPS
# ========================

HOUSE_TYPE_MAP = {
    "чеський проект": "czech_project",
    "гостинка": "hostel",
    "хрущівка": "khrushchivka",
    "дореволюційний": "pre_revolutionary",
    "новобудова": "new_build",
    "сталінка": "stalinka",
}

HEATING_MAP = {
    "автономне опалення": "autonomous",
    "індивідуальне опалення": "individual",
    "централізоване опалення": "centralized",
}

WALL_TYPE_MAP = {
    "блочна технологія": "block",
    "монолітно-каркасна": "monolithic_frame",
    "панельна технологія": "panel",
    "утеплена панель": "insulated_panel",
    "цегляна технологія": "brick",
}

# UA-тег призначення -> назва бінарної колонки (усі варіанти зі скріншота)
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
PURPOSE_FIELDS = list(PURPOSE_MAP.values())


def map_text_or_other(text_lower: str, mapping: dict[str, str]) -> str:
    """Той самий підхід, що і в parser.py: якщо значення поля не входить
    у відомий словник — пишемо 'Other', а не залишаємо None."""
    for ua, en in mapping.items():
        if ua in text_lower:
            return en
    return "Other"


def parse_purpose_flags(text: str) -> dict[str, int]:
    """Розбирає рядок типу 'Торгівельне, Вільне, Офіс, Банк' на бінарні
    прапорці. Невідомі/нові теги просто ігноруються (не ламають парсинг)."""
    flags = {f: 0 for f in PURPOSE_FIELDS}
    for token in text.split(","):
        token = token.strip().lower()
        field_name = PURPOSE_MAP.get(token)
        if field_name:
            flags[field_name] = 1
    return flags


# ========================
# DATA STRUCTURES
# ========================

@dataclass
class Commercial:
    area: float | None = None
    house_type: str | None = None
    year_of_building: int | None = None
    wall_type: str | None = None
    ceiling_height: float | None = None
    heating: str | None = None
    is_bank: int = 0
    is_office: int = 0
    is_services: int = 0
    is_warehouse: int = 0
    is_production: int = 0
    is_free: int = 0
    is_retail: int = 0
    is_garage: int = 0
    is_parking_spot: int = 0
    price: float | None = None
    url: str | None = None
    district: str = ""
    city: str = ""
    geo_region: str = ""


LinkRow = tuple[str, str, str, str]  # (url, district, city, geo_region)
LINK_CSV_HEADER = ["url", "district", "city", "geo_region"]




@dataclass
class CommercialCityConfig:
    city: str
    geo_region: str
    district_urls: dict[str, list[str]] = field(default_factory=dict)
    center_urls: list[str] = field(default_factory=list)
    residential_urls: list[str] = field(default_factory=list)
    outskirts_urls: list[str] = field(default_factory=list)


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
# МІСТА (перевірено вручну на сайті)
# ========================
# Київ — окремо по районах (district_urls), як і для квартир.
# Решта міст, де є реальний поділ по районах — розбито на 3 категорії
# (center/residential/outskirts), так само як для квартир: класифікація
# району зроблена або за прямим збігом з тим, як цей самий район
# класифікований у CITIES (parser.py), або (де прямого збігу нема) —
# за географічним глуздом (історичний центр → center, супутні
# села/містечка → outskirts).
# Міста без реального поділу по районах (сайт віддає лише одну міську
# сторінку) лишені як center_urls=[базовий URL] (+ супутні села в
# outskirts_urls, де вони є).

COMMERCIAL_CITIES: list[CommercialCityConfig] = [
    # --- Київ: окремо по районах ---
    CommercialCityConfig(
        city="Kyiv",
        geo_region="Center",
        district_urls=_kyiv_commercial_district_urls(),
    ),

    # --- Великі міста: реальний поділ по районах, розбитий на 3 категорії ---
    CommercialCityConfig(
        city="Lviv",
        geo_region="West",
        center_urls=["https://lun.ua/sale/lviv/commercial-halytskyi-district"],
        residential_urls=[
            "https://lun.ua/sale/lviv/commercial-sykhivskyi-district",
            "https://lun.ua/sale/lviv/commercial-zaliznychnyi-district",
            "https://lun.ua/sale/lviv/commercial-frankivskyi-district",
            "https://lun.ua/sale/lviv/commercial-lychakivskyi-district",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/lviv/commercial-shevchenkivskyi-district",
            "https://lun.ua/sale/lviv/commercial-sokilnyky",
            "https://lun.ua/sale/lviv/commercial-vynnyky",
            "https://lun.ua/sale/lviv/commercial-briukhovychi",
            "https://lun.ua/sale/lviv/commercial-solonka",
            "https://lun.ua/sale/lviv/commercial-malekhiv",
            "https://lun.ua/sale/lviv/commercial-zubra",
        ],
    ),
    CommercialCityConfig(
        city="Kharkiv",
        geo_region="East",
        center_urls=[
            "https://lun.ua/sale/kharkiv/commercial-shevchenkivskyi-district",
            "https://lun.ua/sale/kharkiv/commercial-kyivskyi-district",
        ],
        residential_urls=[
            "https://lun.ua/sale/kharkiv/commercial-kholodnohirskyi-district",
            "https://lun.ua/sale/kharkiv/commercial-osnovianskyi-district",
            "https://lun.ua/sale/kharkiv/commercial-slobidskyi-district",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/kharkiv/commercial-saltivskyi-district",
            "https://lun.ua/sale/kharkiv/commercial-nemyshlianskyi-district",
            "https://lun.ua/sale/kharkiv/commercial-industrialnyi-district",
            "https://lun.ua/sale/kharkiv/commercial-novobavarskyi-district",
            "https://lun.ua/sale/kharkiv/commercial-nova-vodolaha",
            "https://lun.ua/sale/kharkiv/commercial-bezliudivka",
        ],
    ),
    CommercialCityConfig(
        city="Dnipro",
        geo_region="East",
        center_urls=[
            "https://lun.ua/sale/dnipro/commercial-sobornyi-district",
            "https://lun.ua/sale/dnipro/commercial-shevchenkivskyi-district",
            "https://lun.ua/sale/dnipro/commercial-tsentralnyi-district",
        ],
        residential_urls=[
            "https://lun.ua/sale/dnipro/commercial-chechelivskyi-district",
            "https://lun.ua/sale/dnipro/commercial-nyzhnodniprovskyi-district",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/dnipro/commercial-novokodatskyi-district",
            "https://lun.ua/sale/dnipro/commercial-industrialnyi-district",
            "https://lun.ua/sale/dnipro/commercial-samarskyi-district",
            "https://lun.ua/sale/dnipro/commercial-obukhivka",
            "https://lun.ua/sale/dnipro/commercial-slobozhanske",
        ],
    ),
    CommercialCityConfig(
        city="Zaporizhzhia",
        geo_region="East",
        center_urls=[
            "https://lun.ua/sale/zp/commercial-voznesenivskyi-district",
            "https://lun.ua/sale/zp/commercial-oleksandrivskyi-district",
        ],
        residential_urls=[
            "https://lun.ua/sale/zp/commercial-zavodskyi-district",
            "https://lun.ua/sale/zp/commercial-dniprovskyi-district",
            "https://lun.ua/sale/zp/commercial-komunarskyi-district",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/zp/commercial-khortytskyi-district",
            "https://lun.ua/sale/zp/commercial-shevchenkivskyi-district",
            "https://lun.ua/sale/zp/commercial-rozumivka",
        ],
    ),
    CommercialCityConfig(
        city="Odesa",
        geo_region="South",
        center_urls=["https://lun.ua/sale/odesa/commercial-prymorskyi-district"],
        residential_urls=["https://lun.ua/sale/odesa/commercial-khadzhybeiskyi-district"],
        outskirts_urls=[
            "https://lun.ua/sale/odesa/commercial-kyivskyi-district",
            "https://lun.ua/sale/odesa/commercial-peresypskyi-district",
            "https://lun.ua/sale/odesa/commercial-lymanka",
            "https://lun.ua/sale/odesa/commercial-kryzhanivka",
            "https://lun.ua/sale/odesa/commercial-chornomorsk",
            "https://lun.ua/sale/odesa/commercial-fontanka",
            "https://lun.ua/sale/odesa/commercial-ovidiopol",
            "https://lun.ua/sale/odesa/commercial-tairove",
            "https://lun.ua/sale/odesa/commercial-nerubaiske",
            "https://lun.ua/sale/odesa/commercial-lisky-odeskyi-district",
        ],
    ),

    # --- Середні міста: теж мають реальний поділ по районах (просто менше
    # районів), розбитий на 3 категорії тим самим принципом ---
    CommercialCityConfig(
        city="Poltava",
        geo_region="Center",
        center_urls=["https://lun.ua/sale/poltava/commercial-shevchenkivskyi-district"],
        residential_urls=[
            "https://lun.ua/sale/poltava/commercial-kyivskyi-district",
            "https://lun.ua/sale/poltava/commercial-podilskyi-district",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/poltava/commercial-rozsoshentsi",
            "https://lun.ua/sale/poltava/commercial-shcherbani-poltavskyi-district",
        ],
    ),
    CommercialCityConfig(
        city="Mykolaiv",
        geo_region="South",
        center_urls=[
            "https://lun.ua/sale/mykolaiv/commercial-tsentralnyi-district",
            "https://lun.ua/sale/mykolaiv/commercial-zavodskyi-district",
        ],
        residential_urls=["https://lun.ua/sale/mykolaiv/commercial-inhulskyi-district"],
        outskirts_urls=[],
    ),
    CommercialCityConfig(
        city="Cherkasy",
        geo_region="Center",
        center_urls=[],
        residential_urls=["https://lun.ua/sale/cherkasy/commercial-prydniprovskyi-district"],
        outskirts_urls=["https://lun.ua/sale/cherkasy/commercial-sosnivskyi-district"],
    ),
    CommercialCityConfig(
        city="Kropyvnytskyi",
        geo_region="Center",
        center_urls=[],
        residential_urls=[
            "https://lun.ua/sale/kr/commercial-podilskyi-district",
            "https://lun.ua/sale/kr/commercial-fortechnyi-district",
        ],
        outskirts_urls=[],
    ),
    CommercialCityConfig(
        city="Sumy",
        geo_region="North",
        center_urls=["https://lun.ua/sale/sumy/commercial-kovpakivskyi-district"],
        residential_urls=["https://lun.ua/sale/sumy/commercial-zarichnyi-district"],
        outskirts_urls=[],
    ),
    CommercialCityConfig(
        city="Chernihiv",
        geo_region="North",
        center_urls=["https://lun.ua/sale/chernihiv/commercial-desnianskyi-district"],
        residential_urls=["https://lun.ua/sale/chernihiv/commercial-novozavodskyi-district"],
        outskirts_urls=[],
    ),
    CommercialCityConfig(
        city="Zhytomyr",
        geo_region="North",
        center_urls=["https://lun.ua/sale/zhytomyr/commercial-bohunskyi-district"],
        residential_urls=["https://lun.ua/sale/zhytomyr/commercial-korolovskyi-district"],
        outskirts_urls=[],
    ),

    # --- Менші міста: реального поділу по районах немає — лише одна
    # міська сторінка (+ супутні села/містечка в outskirts, де є) ---
    CommercialCityConfig(
        city="Lutsk",
        geo_region="West",
        center_urls=["https://lun.ua/sale/volyn/commercial"],
    ),
    CommercialCityConfig(
        city="Ivano-Frankivsk",
        geo_region="West",
        center_urls=["https://lun.ua/sale/if/commercial"],
        outskirts_urls=[
            "https://lun.ua/sale/if/commercial-vovchynets",
            "https://lun.ua/sale/if/commercial-mykytyntsi-ivano-frankivskyi-district",
            "https://lun.ua/sale/if/commercial-tysmenytsia",
        ],
    ),
    CommercialCityConfig(
        city="Ternopil",
        geo_region="West",
        center_urls=["https://lun.ua/sale/ternopil/commercial"],
        outskirts_urls=[
            "https://lun.ua/sale/ternopil/commercial-petrykiv",
            "https://lun.ua/sale/ternopil/commercial-hai-shevchenkivski",
            "https://lun.ua/sale/ternopil/commercial-velyka-berezovytsia",
            "https://lun.ua/sale/ternopil/commercial-velyki-birky",
        ],
    ),
    CommercialCityConfig(
        city="Khmelnytskyi",
        geo_region="West",
        center_urls=["https://lun.ua/sale/khmelnytskyi/commercial"],
    ),
    CommercialCityConfig(
        city="Uzhhorod",
        geo_region="West",
        center_urls=["https://lun.ua/sale/uz/commercial"],
        outskirts_urls=[
            "https://lun.ua/sale/uz/commercial-chop",
            "https://lun.ua/sale/uz/commercial-mynai",
        ],
    ),
    CommercialCityConfig(
        city="Vinnytsia",
        geo_region="Center",
        center_urls=["https://lun.ua/sale/vinnytsia/commercial"],
        outskirts_urls=[
            "https://lun.ua/sale/vinnytsia/commercial-ahronomichne",
            "https://lun.ua/sale/vinnytsia/commercial-stryzhavka",
            "https://lun.ua/sale/vinnytsia/commercial-zarvantsi",
            "https://lun.ua/sale/vinnytsia/commercial-voronovytsia",
        ],
    ),
    CommercialCityConfig(
        city="Rivne",
        geo_region="West",
        center_urls=["https://lun.ua/sale/rivne/commercial"],
        outskirts_urls=["https://lun.ua/sale/rivne/commercial-kvasyliv"],
    ),
    CommercialCityConfig(
        city="Chernivtsi",
        geo_region="West",
        center_urls=["https://lun.ua/sale/chernivtsi/commercial"],
        outskirts_urls=["https://lun.ua/sale/chernivtsi/commercial-luzhany"],
    ),
    CommercialCityConfig(
        city="Kherson",
        geo_region="South",
        center_urls=["https://lun.ua/sale/kherson/commercial"],
    ),
]


# ========================
# DISTRICT DISCOVERY (допоміжний інструмент — запускати окремо, не
# автоматично: `python commercial_parser.py discover`)
# ========================

async def discover_commercial_district_urls(
    city_slug: str, base_url: str, page: Page
) -> list[tuple[str, str]]:
    """Читає посилання на під-райони прямо зі сторінки .../commercial —
    той самий href-патерн з'являється і в картках (breadcrumb），і в блоці
    «Обрати розташування комерції». Це лише кандидати: частина з них може
    вести на порожні/неіснуючі сторінки, тому результат варто звірити
    вручну перед тим, як переносити в MANUAL_COMMERCIAL_DISTRICT_URLS.
    """
    try:
        await page.goto(base_url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(1500)
        html = await page.content()
    except Exception as e:
        print(f"  Discovery failed for {base_url}: {e}")
        return [("Citywide", base_url)]

    pattern = rf'href="(/sale/{re.escape(city_slug)}/commercial[^"]*)"'
    hrefs = sorted(set(re.findall(pattern, html)))

    districts: list[tuple[str, str]] = []
    for href in hrefs:
        if href.endswith("/ru"):
            continue
        full_url = f"https://lun.ua{href}"
        if full_url.rstrip("/") == base_url.rstrip("/"):
            continue
        slug = href.split("/commercial-")[-1] if "/commercial-" in href else ""
        if not slug:
            continue
        label = slug.replace("-district", "").replace("-", " ").strip().title()
        districts.append((label, full_url))

    if not districts:
        # Нічого не знайшли — падаємо назад на весь список міста одним блоком
        return [("Citywide", base_url)]

    return districts


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
    """Той самий підхід, що й для квартир: картки — це <button>, не <a>,
    тому беремо id з data-event-options="...|page_id:XXXX|..." і будуємо
    URL напряму (детальні сторінки комерції теж на /realty/{id})."""
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
# PARSING — DETAIL PAGE
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

            apt = Commercial(url=url)

            try:
                price_el = await page.query_selector('[class*="RealtyDetails_priceSqm"]')
                if price_el:
                    price_text = (await price_el.inner_text()).strip()
                    if "$" in price_text:
                        apt.price = float(
                            price_text.split("$")[0].strip()
                            .replace(" ", "").replace("\xa0", "")
                        )
            except Exception:
                pass

            items = await page.query_selector_all(
                '[class*="RealtyProperties_root"] .PropertyItem_item__b9xcp, '
                '[class*="RealtyListOfDetails_list"] .PropertyItem_item__b9xcp'
            )
            for item in items:
                text_el = await item.query_selector(".PropertyItem_text__IADK7")
                if text_el is None:
                    continue
                text = (await text_el.inner_text()).strip()
                text_lower = text.lower()

                icon_key = None
                use_el = await item.query_selector("use")
                if use_el is not None:
                    href = await use_el.get_attribute("xlink:href")
                    if not href:
                        href = await use_el.get_attribute("href")
                    if href and "#" in href:
                        icon_key = href.split("#")[-1]

                if icon_key == "realty/house-type":
                    apt.house_type = map_text_or_other(text_lower, HOUSE_TYPE_MAP)
                    continue
                elif icon_key == "realty/wall":
                    apt.wall_type = map_text_or_other(text_lower, WALL_TYPE_MAP)
                    continue
                elif icon_key == "realty/heating":
                    apt.heating = map_text_or_other(text_lower, HEATING_MAP)
                    continue
                elif icon_key == "realty/commercial":
                    flags = parse_purpose_flags(text)
                    apt.is_bank = flags["is_bank"]
                    apt.is_office = flags["is_office"]
                    apt.is_services = flags["is_services"]
                    apt.is_warehouse = flags["is_warehouse"]
                    apt.is_production = flags["is_production"]
                    apt.is_free = flags["is_free"]
                    apt.is_retail = flags["is_retail"]
                    apt.is_garage = flags["is_garage"]
                    apt.is_parking_spot = flags["is_parking_spot"]
                    continue

                if "висота стелі" in text:
                    m = re.search(r"\d+(?:[.,]\d+)?", text)
                    if m:
                        try:
                            apt.ceiling_height = float(m.group(0).replace(",", "."))
                        except ValueError:
                            pass

                elif "рік будівництва" in text:
                    m = re.search(r"\d{3,4}", text)
                    if m:
                        try:
                            apt.year_of_building = int(m.group(0))
                        except ValueError:
                            pass

                elif "м²" in text and "/" not in text:
                    m = re.search(r"\d+(?:[.,]\d+)?", text)
                    if m:
                        try:
                            apt.area = float(m.group(0).replace(",", "."))
                        except ValueError:
                            pass

            # Обов'язкова перевірка:
            # 1) площа і тип комерції (хоча б одне призначення) — завжди обов'язкові.
            # 2) house_type/wall_type/heating/year_of_building/ceiling_height —
            #    обов'язкові лише для "приміщень" (банк/офіс/послуги/
            #    виробництво/вільне/торгівельне). Для гаража, паркомісця та
            #    складу ці поля не вимагаються (лишаються None/null, якщо їх
            #    немає) — у таких об'єктів часто немає сенсу типу стін/
            #    опалення/висоти стелі в принципі.
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
            print(f"\n[Scanner {worker_id}] District: {district} — {listing_url}")

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
        else:
            listing_tasks = (
                [(u, "Center") for u in city_config.center_urls]
                + [(u, "Residential") for u in city_config.residential_urls]
                + [(u, "Outskirts") for u in city_config.outskirts_urls]
            )

        if not listing_tasks:
            print(f"  ⚠ No URLs configured for {city_config.city} yet. Skipping.")
            await browser.close()
            return

        # Крок 1 — паралельне сканування, посилання одразу в links_filename
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

        # Крок 2 — паралельний розбір детальних сторінок
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
        await process_city(city_config, num_scan_workers=3, num_detail_workers=5)


async def discover_all() -> None:
    """Допоміжний режим (для майбутньої повторної перевірки): для кожного
    міста, де взагалі немає жодного URL, заходить на .../commercial і
    друкує кандидатів у зручному для копіювання форматі. Кандидатів варто
    відкрити руками й лишити тільки ті, що дійсно мають оголошення."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        try:
            for city_config in COMMERCIAL_CITIES:
                has_urls = (
                    city_config.district_urls
                    or city_config.center_urls
                    or city_config.residential_urls
                    or city_config.outskirts_urls
                )
                if has_urls:
                    continue

                any_flats_url = next(
                    (u for c in CITIES if c.city == city_config.city
                     for u in (list(c.district_urls.values())[0] if c.district_urls
                               else c.center_urls or c.residential_urls or c.outskirts_urls)),
                    None,
                )
                slug = slug_from_url(any_flats_url) if any_flats_url else city_config.city.lower()
                base_url = f"https://lun.ua/sale/{slug}/commercial"

                print(f"\n# {city_config.city}")
                found = await discover_commercial_district_urls(slug, base_url, page)
                print(f'"{city_config.city}": {{')
                for label, u in found:
                    key = label.replace(" ", "_")
                    print(f'    "{key}": ["{u}"],')
                print("},")
        finally:
            await browser.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "discover":
        asyncio.run(discover_all())
    else:
        asyncio.run(main())