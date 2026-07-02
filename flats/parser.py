import asyncio
import csv
import os
import re
from dataclasses import dataclass, field, fields
from typing import Awaitable, Callable
from playwright.async_api import async_playwright, Page, Browser

# ========================
# DATA STRUCTURES
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


def map_text_or_other(text_lower: str, mapping: dict[str, str]) -> str:
    """Look up a known UA label in `mapping`; any recognized-field value that
    isn't in the map (a house type / wall type / heating type we don't have
    a code for yet) is recorded as "Other" instead of leaving the field
    unset / causing the apartment to be skipped."""
    for ua, en in mapping.items():
        if ua in text_lower:
            return en
    return "Other"


@dataclass
class Apartment:
    num_of_rooms: int | None = None
    freshly_renovated: bool | None = None
    area: float | None = None
    living_area: float | None = None
    kitchen_area: float | None = None
    floor: int | None = None
    floors_in_house: int | None = None
    year_of_building: int | None = None
    price: float | None = None
    house_type: str | None = None
    heating: str | None = None
    wall_type: str | None = None
    url: str | None = None
    district: str = ""
    city: str = ""
    geo_region: str = ""


# ========================
# CITY CONFIGURATION
# ========================

@dataclass
class CityConfig:
    city: str
    geo_region: str
    center_urls: list[str] = field(default_factory=list)
    residential_urls: list[str] = field(default_factory=list)
    outskirts_urls: list[str] = field(default_factory=list)
    district_urls: dict[str, list[str]] = field(default_factory=dict)


CITIES: list[CityConfig] = [
    # KYIV — окремо по районах
    CityConfig(
        city="Kyiv",
        geo_region="Center",
        district_urls={
            "Holosiivskyi": ["https://lun.ua/sale/kyiv/flats-holosiivskyi-district"],
            "Darnytskyi": ["https://lun.ua/sale/kyiv/flats-darnytskyi-district"],
            "Desnianskyi": ["https://lun.ua/sale/kyiv/flats-desnianskyi-district"],
            "Dniprovskyi": ["https://lun.ua/sale/kyiv/flats-dniprovskyi-district"],
            "Obolonskyi": ["https://lun.ua/sale/kyiv/flats-obolonskyi-district"],
            "Pecherskyi": ["https://lun.ua/sale/kyiv/flats-pecherskyi-district"],
            "Podilskyi": ["https://lun.ua/sale/kyiv/flats-podilskyi-district"],
            "Sviatoshynskyi": ["https://lun.ua/sale/kyiv/flats-sviatoshynskyi-district"],
            "Solomianskyi": ["https://lun.ua/sale/kyiv/flats-solomianskyi-district"],
            "Shevchenkivskyi": ["https://lun.ua/sale/kyiv/flats-shevchenkivskyi-district"],
        }
    ),
    # WEST
    CityConfig(
        city="Lutsk",
        geo_region="West",
        center_urls=["https://lun.ua/sale/volyn/flats-tsentr"],
        residential_urls=[
            "https://lun.ua/sale/volyn/flats-teremno",
            "https://lun.ua/sale/volyn/flats-33-i-mikroraion",
            "https://lun.ua/sale/volyn/flats-55-i-mikroraion",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/volyn/flats-hnydava",
            "https://lun.ua/sale/volyn/flats-boholiuby",
            "https://lun.ua/sale/volyn/flats-tarasove",
        ],
    ),

    CityConfig(
        city="Lviv",
        geo_region="West",
        center_urls=["https://lun.ua/sale/lviv/flats-tsentr"],
        residential_urls=[
            "https://lun.ua/sale/lviv/flats-sykhiv",
            "https://lun.ua/sale/lviv/flats-zaliznychnyi-district",
            "https://lun.ua/sale/lviv/flats-frankivskyi-district",
            "https://lun.ua/sale/lviv/flats-lychakivskyi-district",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/lviv/flats-shevchenkivskyi-district",
            "https://lun.ua/sale/lviv/flats-sykhiv",
            "https://lun.ua/sale/lviv/flats-zboishcha"
        ],
    ),
    CityConfig(
        city="Ivano-Frankivsk",
        geo_region="West",
        center_urls=["https://lun.ua/sale/if/flats-tsentr",
                     "https://lun.ua/sale/if/flats-sofiivka"],
        residential_urls=[
            "https://lun.ua/sale/if/flats-kniahynyn",
            "https://lun.ua/sale/if/flats-naberezhna",
            "https://lun.ua/sale/if/flats-hirka",
            "https://lun.ua/sale/if/flats-budivelnykiv",
            "https://lun.ua/sale/if/flats-kant",
            "https://lun.ua/sale/if/flats-patriot"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/if/flats-kaskad",
            "https://lun.ua/sale/if/flats-braty",
            "https://lun.ua/sale/if/flats-pozytron",
            "https://lun.ua/sale/if/flats-opryshivtsi"
        ],
    ),
    CityConfig(
        city="Ternopil",
        geo_region="West",
        center_urls=["https://lun.ua/sale/ternopil/flats-tsentr",
                     "https://lun.ua/sale/ternopil/flats-staryi-park",
                     "https://lun.ua/sale/ternopil/flats-novyi-svit",
                     "https://lun.ua/sale/ternopil/flats-obolonia"],
        residential_urls=[
            "https://lun.ua/sale/ternopil/flats-druzhba",
            "https://lun.ua/sale/ternopil/flats-bam",
            "https://lun.ua/sale/ternopil/flats-lvivska-st",
            "https://lun.ua/sale/ternopil/flats-hlyboka-st"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/ternopil/flats-pivnichnyi",
            "https://lun.ua/sale/ternopil/flats-aliaska",
            "https://lun.ua/sale/ternopil/flats-promyslovyi",
            "https://lun.ua/sale/ternopil/flats-mykulynetska-st",
            "https://lun.ua/sale/ternopil/flats-kutkivtsi"
        ],
    ),
    CityConfig(
        city="Khmelnytskyi",
        geo_region="West",
        center_urls=["https://lun.ua/sale/khmelnytskyi/flats-tsentr",
                     "https://lun.ua/sale/khmelnytskyi/flats-nyzhnia-berehova-st"],
        residential_urls=[
            "https://lun.ua/sale/khmelnytskyi/flats-pivdenno-zakhidnyi",
            "https://lun.ua/sale/khmelnytskyi/flats-dubovo",
            "https://lun.ua/sale/khmelnytskyi/flats-hrechany",
            "https://lun.ua/sale/khmelnytskyi/flats-rakovo",
            "https://lun.ua/sale/khmelnytskyi/flats-bolhary"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/khmelnytskyi/flats-knyzhkivtsi",
            "https://lun.ua/sale/khmelnytskyi/flats-leznevo",
            "https://lun.ua/sale/khmelnytskyi/flats-ruzhychna",
            "https://lun.ua/sale/khmelnytskyi/flats-oleshyn",
            "https://lun.ua/sale/khmelnytskyi/flats-sharovechka-10025534"
        ],
    ),
    CityConfig(
        city="Uzhhorod",
        geo_region="West",
        center_urls=["https://lun.ua/sale/uz/flats-tsentr",
                     "https://lun.ua/sale/uz/flats-sobranetska-st"],
        residential_urls=[
            "https://lun.ua/sale/uz/flats-novyi",
            "https://lun.ua/sale/uz/flats-vokzal",
            "https://lun.ua/sale/uz/flats-shakhta",
            "https://lun.ua/sale/uz/flats-radvanka",
            "https://lun.ua/sale/uz/flats-natsionalnoi-hvardii-st",
            "https://lun.ua/sale/uz/flats-zahorska-st"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/uz/flats-shakhta",
        ],
    ),
    CityConfig(
        city="Chernivtsi",
        geo_region="West",
        center_urls=["https://lun.ua/sale/chernivtsi/flats-tsentr"],
        residential_urls=[
            "https://lun.ua/sale/chernivtsi/flats-pershyi-miroraion",
            "https://lun.ua/sale/chernivtsi/flats-kalinets",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/uz/flats-dravtsi",
            "https://lun.ua/sale/uz/flats-chervenytsia",
            "https://lun.ua/sale/uz/flats-minai",
            "https://lun.ua/sale/uz/flats-storozhnytsia",
            "https://lun.ua/sale/uz/flats-yenkivska-st"
        ],
    ),
    CityConfig(
        city="Rivne",
        geo_region="West",
        center_urls=["https://lun.ua/sale/rivne/flats-tsentr",
                     "https://lun.ua/sale/rivne/flats-dvorets",
                     "https://lun.ua/sale/rivne/flats-avtovokzal",
                     "https://lun.ua/sale/rivne/flats-hrabnyk"],
        residential_urls=[
            "https://lun.ua/sale/rivne/flats-mototrek",
            "https://lun.ua/sale/rivne/flats-studentska-st",
            "https://lun.ua/sale/rivne/flats-basiv-kut",
            "https://lun.ua/sale/rivne/flats-korolova-st"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/rivne/flats-pivnichnyi",
            "https://lun.ua/sale/rivne/flats-novyi-dvir",
            "https://lun.ua/sale/rivne/flats-mlynivska-st",
            "https://lun.ua/sale/rivne/flats-kolodenka",
            "https://lun.ua/sale/rivne/flats-barmaky"
        ],
    ),

    # NORTH!!!!!!
    CityConfig(
        city="Zhytomyr",
        geo_region="North",
        center_urls=["https://lun.ua/sale/zhytomyr/flats-tsentr"],
        residential_urls=[
            "https://lun.ua/sale/zhytomyr/flats-vokzal",
            "https://lun.ua/sale/zhytomyr/flats-chudnivska-st",
            "https://lun.ua/sale/zhytomyr/flats-seletska-st"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/zhytomyr/flats-oliivka-10016953",
            "https://lun.ua/sale/zhytomyr/flats-zarichany-10007890",
            "https://lun.ua/sale/zhytomyr/flats-hlybochytsia",
            "https://lun.ua/sale/zhytomyr/flats-huiva"
        ],
    ),
    CityConfig(
        city="Chernihiv",
        geo_region="North",
        center_urls=["https://lun.ua/sale/chernihiv/flats-tsentr",
                     "https://lun.ua/sale/chernihiv/flats-val",
                     "https://lun.ua/sale/chernihiv/flats-myru-ave",
                     "https://lun.ua/sale/chernihiv/flats-liskovytsia",
                     "https://lun.ua/sale/chernihiv/flats-miskyi-sad"],
        residential_urls=[
            "https://lun.ua/sale/chernihiv/flats-yalivshchyna",
            "https://lun.ua/sale/chernihiv/flats-bobrovytsia",
            "https://lun.ua/sale/chernihiv/flats-ivana-vyhovskoho-st",
            "https://lun.ua/sale/chernihiv/flats-berizky"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/chernihiv/flats-rivnopillia",
            "https://lun.ua/sale/chernihiv/flats-kozelets"
        ],
    ),
    CityConfig(
        city="Sumy",
        geo_region="North",
        center_urls=["https://lun.ua/sale/sumy/flats-tsentr",
                     "https://lun.ua/sale/sumy/flats-avtovokzal"],
        residential_urls=[
            "https://lun.ua/sale/sumy/flats-9-i-mikroraion",
            "https://lun.ua/sale/sumy/flats-11-i-mikroraion",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/sumy/flats-kurskyi",
            "https://lun.ua/sale/sumy/flats-basy",
            "https://lun.ua/sale/sumy/flats-bilopilskyi-shliakh-st",
            "https://lun.ua/sale/sumy/flats-sad",
            "https://lun.ua/sale/sumy/flats-kosivshchyna"
        ],
    ),

    # CENTER
    CityConfig(
        city="Vinnytsia",
        geo_region="Center",
        center_urls=["https://lun.ua/sale/vinnytsia/flats-tsentr",
                     "https://lun.ua/sale/vinnytsia/flats-zamostia",
                     "https://lun.ua/sale/vinnytsia/flats-nyzhnia-slovianka",
                     "https://lun.ua/sale/vinnytsia/flats-pyrohova-st"],
        residential_urls=[
            "https://lun.ua/sale/vinnytsia/flats-maslozhyr",
            "https://lun.ua/sale/vinnytsia/flats-viiskove-mistechko",
            "https://lun.ua/sale/vinnytsia/flats-vinnytsia-barske-hwy",
            "https://lun.ua/sale/vinnytsia/flats-buchmy-st"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/vinnytsia/flats-pyrohovo",
            "https://lun.ua/sale/vinnytsia/flats-hnivanske-hwy",
            "https://lun.ua/sale/vinnytsia/flats-ahronomichne",
            "https://lun.ua/sale/vinnytsia/flats-vinnytski-khutory"
        ],
    ),
    CityConfig(
        city="Poltava",
        geo_region="Center",
        center_urls=["https://lun.ua/sale/poltava/flats-tsentr",
                     "https://lun.ua/sale/poltava/flats-shevchenkivskyi-district"],
        residential_urls=[
            "https://lun.ua/sale/poltava/flats-kyivskyi-district",
            "https://lun.ua/sale/poltava/flats-podilskyi-district",
            "https://lun.ua/sale/poltava/flats-dublianshchyna"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/poltava/flats-rozsoshentsi",
            "https://lun.ua/sale/poltava/flats-suprunivka",
            "https://lun.ua/sale/poltava/flats-hozhuly",
            "https://lun.ua/sale/poltava/flats-kovalivka-poltavskyi-district"
        ],
    ),
    CityConfig(
        city="Kropyvnytskyi",
        geo_region="Center",
        center_urls=["https://lun.ua/sale/kr/flats-tsentr",
                     "https://lun.ua/sale/kr/flats-kovalivka",
                     "https://lun.ua/sale/kr/flats-bieliaieva",
                     "https://lun.ua/sale/kr/flats-krytyi-rynok"],
        residential_urls=[
            "https://lun.ua/sale/kr/flats-fortechnyi-district",
            "https://lun.ua/sale/kr/flats-podilskyi-district",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/kr/flats-nova-balashivka",
            "https://lun.ua/sale/kr/flats-dolynska"
        ],
    ),
    CityConfig(
        city="Cherkasy",
        geo_region="Center",
        center_urls=["https://lun.ua/sale/cherkasy/flats-tsentr",
                     "https://lun.ua/sale/cherkasy/flats-hoholia-st",
                     "https://lun.ua/sale/cherkasy/flats-nadpilna-st",
                     "https://lun.ua/sale/cherkasy/flats-pryportovyi"],
        residential_urls=[
            "https://lun.ua/sale/cherkasy/flats-700-richchia",
            "https://lun.ua/sale/cherkasy/flats-ivana-mazepy-st",
            "https://lun.ua/sale/cherkasy/flats-dniprovskyi"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/cherkasy/flats-pzr",
            "https://lun.ua/sale/cherkasy/flats-sumhaitska-st",
            "https://lun.ua/sale/cherkasy/flats-sosnivka",
            "https://lun.ua/sale/cherkasy/flats-kanivska-st"
        ],
    ),

    # EAST
    CityConfig(
        city="Kharkiv",
        geo_region="East",
        center_urls=["https://lun.ua/sale/kharkiv/flats-tsentr",
                     "https://lun.ua/sale/kharkiv/flats-moskalivka"],
        residential_urls=[
            "https://lun.ua/sale/kharkiv/flats-pavlovo-pole",
            "https://lun.ua/sale/kharkiv/flats-kholodnohirskyi-district",
            "https://lun.ua/sale/kharkiv/flats-osnovianskyi-district",
            "https://lun.ua/sale/kharkiv/flats-slobidskyi-district"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/kharkiv/flats-saltivka",
            "https://lun.ua/sale/kharkiv/flats-nemyshlianskyi-district",
            "https://lun.ua/sale/kharkiv/flats-industrialnyi-district",
            "https://lun.ua/sale/kharkiv/flats-novobavarskyi-district"
        ],
    ),
    CityConfig(
        city="Dnipro",
        geo_region="East",
        center_urls=["https://lun.ua/sale/dnipro/flats-tsentr",
                     "https://lun.ua/sale/dnipro/flats-tsentralnyi-district",
                     "https://lun.ua/sale/dnipro/flats-shevchenkivskyi-district",
                     "https://lun.ua/sale/dnipro/flats-sobornyi-district"
                     ],
        residential_urls=[
            "https://lun.ua/sale/dnipro/flats-12-i-kvartal",
            "https://lun.ua/sale/dnipro/flats-chechelivskyi-district",
            "https://lun.ua/sale/dnipro/flats-nyzhnodniprovskyi-district",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/dnipro/flats-novokodatskyi-district",
            "https://lun.ua/sale/dnipro/flats-industrialnyi-district",
            "https://lun.ua/sale/dnipro/flats-samarskyi-district",
            "https://lun.ua/sale/dnipro/flats-lomivka"
        ],
    ),
    CityConfig(
        city="Zaporizhzhia",
        geo_region="East",
        center_urls=["https://lun.ua/sale/zp/flats-voznesenivskyi-district",
                     "https://lun.ua/sale/zp/flats-oleksandrivskyi-district"],
        residential_urls=[
            "https://lun.ua/sale/zp/flats-zavodskyi-district",
            "https://lun.ua/sale/zp/flats-dniprovskyi-district",
            "https://lun.ua/sale/zp/flats-komunarskyi-district"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/zp/flats-khortytskyi-district",
            "https://lun.ua/sale/zp/flats-shevchenkivskyi-district",
            "https://lun.ua/sale/zp/flats-3-i-shevchenkivskyi",
            "https://lun.ua/sale/zp/flats-17-i-mikroraion",
            "https://lun.ua/sale/zp/flats-volodymyrivske"
        ],
    ),

    # SOUTH
    CityConfig(
        city="Odesa",
        geo_region="South",
        center_urls=["https://lun.ua/sale/odesa/flats-tsentr",
                     "https://lun.ua/sale/odesa/flats-prymorskyi-district",
                     "https://lun.ua/sale/odesa/flats-moldavanka",
                     "https://lun.ua/sale/odesa/flats-blyzhni-mlyny"],
        residential_urls=[
            "https://lun.ua/sale/odesa/flats-khadzhybeiskyi-district",
            "https://lun.ua/sale/odesa/flats-serednii-fontan",
            "https://lun.ua/sale/odesa/flats-arkadiia",
        ],
        outskirts_urls=[
            "https://lun.ua/sale/odesa/flats-kyivskyi-district",
            "https://lun.ua/sale/odesa/flats-peresypskyi-district",
            "https://lun.ua/sale/odesa/flats-tairova",
            "https://lun.ua/sale/odesa/flats-velykyi-fontan",
            "https://lun.ua/sale/odesa/flats-dacha-kovalevskoho",
            "https://lun.ua/sale/odesa/flats-dmytryivka"
        ],
    ),
    CityConfig(
        city="Mykolaiv",
        geo_region="South",
        center_urls=["https://lun.ua/sale/mykolaiv/flats-tsentr",
                     "https://lun.ua/sale/mykolaiv/flats-tsentralnyi-district",
                     "https://lun.ua/sale/mykolaiv/flats-zavodskyi-district",
                     "https://lun.ua/sale/mykolaiv/flats-slobidka"
                     ],
        residential_urls=[
            "https://lun.ua/sale/mykolaiv/flats-inhulskyi-district",
            "https://lun.ua/sale/mykolaiv/flats-soliani",
            "https://lun.ua/sale/mykolaiv/flats-novyi-vodopii"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/mykolaiv/flats-korabelnyi-district",
            "https://lun.ua/sale/mykolaiv/flats-kulbakino",
            "https://lun.ua/sale/mykolaiv/flats-varvarivka",
            "https://lun.ua/sale/mykolaiv/flats-matviivka",
            "https://lun.ua/sale/mykolaiv/flats-ternivka"
        ],
    ),
    CityConfig(
        city="Kherson",
        geo_region="South",
        center_urls=["https://lun.ua/sale/kherson/flats-tsentr",
                     "https://lun.ua/sale/kherson/flats-tsentralnyi-district"],
        residential_urls=[
            "https://lun.ua/sale/kherson/flats-tavriiskyi",
            "https://lun.ua/sale/kherson/flats-pivnichnyi",
            "https://lun.ua/sale/kherson/flats-dniprovskyi-district"
        ],
        outskirts_urls=[
            "https://lun.ua/sale/kherson/flats-korabelnyi-district",
        ],
    ),
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


async def get_apartment_urls_from_page(page: Page) -> list[str]:
    """
    On listing/category pages, apartment cards are rendered as <button> elements
    with client-side (JS) routing — they have NO <a href="/realty/..."> at all.
    (Only the "recommended" cards on individual realty *detail* pages use real
    <a href> anchors.) Every card, button or anchor, still carries its numeric
    listing id via data-event-options="...|page_id:1234567890|...", so we pull
    ids from that instead of relying on hrefs.
    """
    urls = []
    try:
        html = await page.content()
        ids = re.findall(r"page_id:(\d+)", html)
        for pid in dict.fromkeys(ids):  # de-dupe, preserve order
            urls.append(f"https://lun.ua/realty/{pid}")
    except Exception as e:
        print(f"  Error getting URLs: {e}")
    return urls


async def get_urls_from_listing(
    listing_url: str,
    page: Page,
    on_page_urls: Callable[[list[str]], Awaitable[None]] | None = None,
) -> list[str]:
    all_urls = []
    page_number = 1

    while True:
        paginated_url = f"{listing_url}?page={page_number}"
        print(f"  Scanning page: {paginated_url}")
        await page.goto(paginated_url, wait_until="domcontentloaded", timeout=15000)

        # Чекаємо картки
        try:
            await page.wait_for_selector(
                '[class*="RealtyCard_propertyGrid"]', timeout=10000
            )
            await page.wait_for_timeout(2000)
        except Exception:
            print(f"  No cards on page {page_number}, stopping")
            break

        # Перемикаємо $ ТІЛЬКИ якщо ще не в доларах
        try:
            cards_with_price = await page.query_selector_all(
                '[class*="RealtyCard_priceSqm"]'
            )
            if cards_with_price:
                first_price = await cards_with_price[0].inner_text()
                if "$" not in first_price:
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

        urls = await get_apartment_urls_from_page(page)
        print(f"  Apartment ids found on page: {len(urls)}")
        if not urls:
            print(f"  No URLs found on page {page_number}, stopping")
            break

        if on_page_urls is not None:
            await on_page_urls(urls)

        all_urls.extend(urls)
        print(f"  Found {len(urls)} apartments on page {page_number}, total: {len(all_urls)}")
        page_number += 1

    return all_urls


# ========================
# PARSING — DETAIL PAGE
# ========================

async def switch_to_usd_detail(page: Page) -> None:
    try:
        price_el = await page.query_selector('[class*="RealtyDetails_priceSqm"]')
        if price_el:
            price_text = await price_el.inner_text()
            if "$" in price_text:
                return
        usd_btn = await page.query_selector('button:has(span:text("$"))')
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


async def parse_detail_page(url: str, page: Page, retries: int = 3) -> Apartment | None:
    for attempt in range(retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            await page.wait_for_function(
                '''() => {
                    return document.querySelector(".error-content") ||
                           document.querySelector('[class*="RealtyDetails_priceSqm"]');
                }''',
                timeout=10000
            )

            # Видалене оголошення
            if await page.query_selector(".error-content"):
                print(f"  Deleted: {url}")
                return None

            await switch_to_usd_detail(page)

            apt = Apartment(url=url)

            # Ціна за м²
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

            items = await page.query_selector_all(".PropertyItem_item__b9xcp")
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

                if "кімн" in text:
                    try:
                        apt.num_of_rooms = int(text.split()[0])
                    except ValueError:
                        pass

                elif "м²" in text and "/" in text:
                    try:
                        clean = text.replace("м²", "").strip()
                        parts = [p.strip() for p in clean.split("/")]
                        apt.area = float(parts[0]) if parts[0] != "-" else None
                        apt.living_area = float(parts[1]) if parts[1] != "-" else None
                        apt.kitchen_area = float(parts[2]) if parts[2] != "-" else None
                    except (ValueError, IndexError):
                        pass

                elif "поверх" in text and "рік" not in text:
                    try:
                        floor_info = text.split()
                        apt.floor = int(floor_info[1])
                        apt.floors_in_house = int(floor_info[3])
                    except (ValueError, IndexError):
                        pass

                elif "рік будівництва" in text:
                    try:
                        apt.year_of_building = int(text.split()[0])
                    except ValueError:
                        pass

                elif "рем" in text:
                    apt.freshly_renovated = text == "з ремонтом"

            if apt.house_type is None or apt.heating is None or apt.wall_type is None:
                print(f"  Skipped (missing required fields): {url}")
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

LinkRow = tuple[str, str, str, str]  # (url, district, city, geo_region)
LINK_CSV_HEADER = ["url", "district", "city", "geo_region"]


def write_apartment_to_csv(apartment: Apartment, filename: str) -> None:
    file_exists = os.path.exists(filename)
    fieldnames = [f.name for f in fields(Apartment)]
    with open(filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(apartment.__dict__)


def append_links_to_csv(rows: list[LinkRow], filename: str) -> None:
    """Append newly found listing URLs to the links CSV right away (called
    after every scanned page), so the file grows incrementally instead of
    only being written once the whole city is scanned."""
    if not rows:
        return
    file_exists = os.path.exists(filename)
    with open(filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(LINK_CSV_HEADER)
        writer.writerows(rows)


def read_links_from_csv(filename: str) -> list[LinkRow]:
    """Read back the links CSV for the detail-parsing stage, de-duping by
    URL (the same listing can occasionally appear via more than one
    district/microdistrict page)."""
    rows: list[LinkRow] = []
    seen: set[str] = set()
    if not os.path.exists(filename):
        return rows
    with open(filename, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
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
    tasks: list[tuple[str, str]],  # (listing_url, district)
    worker_id: int,
    city: str,
    geo_region: str,
    links_filename: str,
    lock: asyncio.Lock,
    browser: Browser,
) -> int:
    """Scans a slice of listing/district pages with its own page, writing
    found URLs to links_filename as soon as each listing page is scanned."""
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
    urls: list[tuple[str, str, str, str]],  # (url, district, city, geo_region)
    worker_id: int,
    csv_filename: str,
    lock: asyncio.Lock,
    browser: Browser,
) -> None:
    page = await browser.new_page()
    try:
        for url, district, city, geo_region in urls:
            apt = await parse_detail_page(url, page)
            if apt is None:
                continue

            apt.district = district
            apt.city = city
            apt.geo_region = geo_region

            async with lock:
                write_apartment_to_csv(apt, csv_filename)

            print(f"[Worker {worker_id}] ✓ saved: {url}")
    finally:
        await page.close()


def chunk_list(items: list, num_chunks: int) -> list[list]:
    if not items or num_chunks <= 0:
        return [items] if items else []
    chunk_size = max(1, -(-len(items) // num_chunks))  # ceil division
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


async def process_city(
    city_config: CityConfig,
    num_scan_workers: int = 3,
    num_detail_workers: int = 5,
) -> None:
    csv_filename = f"{city_config.city}.csv"
    links_filename = f"{city_config.city}_links.csv"
    print(f"\n{'='*50}")
    print(f"Processing: {city_config.city}")
    print(f"{'='*50}")

    if os.path.exists(csv_filename):
        os.remove(csv_filename)
    if os.path.exists(links_filename):
        os.remove(links_filename)

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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

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
        print(f"Total unique apartments to parse: {len(all_detail_urls)}")

        detail_chunks = chunk_list(all_detail_urls, num_detail_workers)
        detail_lock = asyncio.Lock()
        await asyncio.gather(*[
            detail_worker(chunk, worker_id, csv_filename, detail_lock, browser)
            for worker_id, chunk in enumerate(detail_chunks)
        ])

        await browser.close()

    print(f"\n✓ {city_config.city} done — saved to {csv_filename}")


# ========================
# MAIN
# ========================
async def main() -> None:
    for city_config in CITIES:
        await process_city(city_config, num_scan_workers=3, num_detail_workers=5)


if __name__ == "__main__":
    asyncio.run(main())