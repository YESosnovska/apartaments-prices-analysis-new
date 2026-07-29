import asyncio
import csv
import json
import math
import os
import re
from dataclasses import dataclass, field, fields
from typing import Awaitable, Callable
from playwright.async_api import async_playwright, Page, Browser

# ========================
# DATA STRUCTURES
# ========================


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Відстань по великому колу (км) між двома точками (lat, lon)."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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


@dataclass
class Apartment:
    # Ідентифікація
    group_id: str | None = None
    has_duplicates: bool | None = None
    url: str | None = None

    # Локація
    city: str = ""
    district: str = ""
    residential_complex: str | None = None
    lat: float | None = None
    lon: float | None = None
    distance_to_center_km: float | None = None
    poi_name: str | None = None
    poi_distance_m: float | None = None
    geo_region: str = ""

    price: float | None = None

    num_of_rooms: int | None = None
    area: float | None = None
    living_area: float | None = None
    kitchen_area: float | None = None
    floor: int | None = None
    floors_in_house: int | None = None
    house_type: str | None = None
    heating: str | None = None
    wall_type: str | None = None
    year_of_building: int | None = None
    ceiling_height: float | None = None
    freshly_renovated: bool | None = None

    bedroom_count: int | None = None
    balcony_count: int | None = None
    toilets_count: int | None = None
    kitchen_type: str | None = None
    hot_water_type: str | None = None

    has_gas: bool | None = None
    autonomy_power: bool | None = None
    autonomy_heat: bool | None = None
    autonomy_water: bool | None = None
    autonomy_net: bool | None = None
    autonomy_lift: bool | None = None

    without_commission: int = 0
    is_exclusive: int = 0
    text: str = ""


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
    urls = []
    try:
        html = await page.content()
        ids = re.findall(r"page_id:(\d+)", html)
        for pid in dict.fromkeys(ids):
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

        try:
            await page.wait_for_selector(
                '[class*="RealtyCard_propertyGrid"]', timeout=10000
            )
            await page.wait_for_timeout(2000)
        except Exception:
            print(f"  No cards on page {page_number}, stopping")
            break

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
# PARSING — DETAIL PAGE (JSON-based)
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


def parse_renovation_fallback(text_lower: str) -> bool | None:
    if "євроремонт" in text_lower or "дизайнерськ" in text_lower or "з ремонтом" in text_lower:
        return True
    if "без ремонту" in text_lower or "потребує ремонту" in text_lower:
        return False
    return None


async def parse_detail_page(url: str, page: Page, retries: int = 3) -> Apartment | None:
    for attempt in range(retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_function(
                '''() => {
                    return document.querySelector(".error-content") ||
                           document.querySelector('[class*="RealtyDetails_priceSqm"]');
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

            apt = Apartment(url=url)

            apt.group_id = data.get("groupId")
            apt.has_duplicates = data.get("hasDuplicates")

            apt.price = data.get("priceSqm")

            apt.num_of_rooms = data.get("roomCount")
            apt.area = data.get("areaTotal")
            apt.living_area = data.get("areaLiving")
            apt.kitchen_area = data.get("areaKitchen")

            apt.floor = data.get("floor")
            apt.floors_in_house = data.get("floorCount")
            apt.house_type = data.get("houseTypeName")
            apt.wall_type = data.get("wallTypeName")
            apt.heating = data.get("heatingSystemName")
            apt.year_of_building = data.get("builtYear")
            apt.ceiling_height = data.get("ceilingHeight")

            apt.has_gas = data.get("hasGas")
            apt.autonomy_power = data.get("autonomyPower")
            apt.autonomy_heat = data.get("autonomyHeat")
            apt.autonomy_water = data.get("autonomyWater")
            apt.autonomy_net = data.get("autonomyNet")
            apt.autonomy_lift = data.get("autonomyLift")

            house_data = data.get("houseData") or {}
            apt.bedroom_count = house_data.get("bedroomCount")
            balcony_count = house_data.get("balconyCount")
            apt.balcony_count = balcony_count if balcony_count is not None and balcony_count >= 0 else None
            apt.toilets_count = house_data.get("toiletsCount")
            apt.kitchen_type = house_data.get("kitchenTypeName")
            apt.hot_water_type = house_data.get("hotWaterName")

            apt.without_commission = int(bool(data.get("withoutCommission")))
            apt.is_exclusive = int(bool(data.get("isExclusive")))

            text = resolve_text_reference(html, data.get("text"))
            apt.text = text
            text_lower = text.lower()

            without_renovation = data.get("withoutRenovation")
            apt.freshly_renovated = (
                not without_renovation if without_renovation is not None
                else parse_renovation_fallback(text_lower)
            )

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

            if (
                apt.house_type is None or apt.wall_type is None or apt.heating is None
                or apt.area is None or apt.num_of_rooms is None
                or apt.floor is None or apt.floors_in_house is None
            ):
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

            center = CITY_CENTERS.get(city)
            if center and center != (0.0, 0.0) and apt.lat is not None and apt.lon is not None:
                apt.distance_to_center_km = haversine(apt.lat, apt.lon, center[0], center[1])

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
        await process_city(city_config, num_scan_workers=8, num_detail_workers=12)


if __name__ == "__main__":
    asyncio.run(main())