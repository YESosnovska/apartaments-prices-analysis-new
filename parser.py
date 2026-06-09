import csv
import dataclasses
from dataclasses import dataclass, field
from selenium import webdriver
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.remote.webelement import WebElement

_driver: WebDriver | None = None


def get_driver() -> WebDriver:
    return _driver


def set_driver(new_driver: WebDriver) -> None:
    global _driver
    _driver = new_driver


@dataclass
class Apartment:
    num_of_rooms: int | None
    freshly_renovated: bool | None
    area: float | None
    living_area: float | None
    kitchen_area: float | None
    floor: int | None
    floors_in_house: int | None
    year_of_building: int | None
    price: float | None
    url: str | None = None
    district: str = ""
    city: str = ""
    geo_region: str = ""


APARTMENT_FIELDS = [field.name for field in dataclasses.fields(Apartment)]


def parse_single_apartment(apartment: WebElement) -> Apartment | None:
    try:
        card = apartment.find_element(By.XPATH, "../..")
        try:
            link_button = card.find_element(By.CLASS_NAME, "RealtyCard_link__yWMYZ")
            event_options = link_button.get_attribute("data-event-options")
            parsed = dict(part.split(":") for part in event_options.split("|") if ":" in part)
            page_id = parsed.get("page_id")
            url = f"https://lun.ua/uk/realty/{page_id}" if page_id else None
        except Exception as e:
            print(f"URL error: {e}")
            url = None
        props = apartment.find_elements(By.CLASS_NAME, "PropertyItem_text__IADK7")

        num_of_rooms = None
        freshly_renovated = None
        area = None
        living_area = None
        kitchen_area = None
        floor = None
        floors_in_house = None
        year_of_building = None
        price = None

        for prop in props:
            text = prop.text.strip()

            if "кімн" in text:
                try:
                    num_of_rooms = int(text.split()[0])
                except ValueError:
                    pass

            elif "рем" in text:
                freshly_renovated = text == "з ремонтом"

            elif "м²" in text and "/" in text:
                try:
                    clean = text.replace("м²", "").strip()
                    parts = [p.strip() for p in clean.split("/")]
                    area = float(parts[0]) if parts[0] != "-" else None
                    living_area = float(parts[1]) if parts[1] != "-" else None
                    kitchen_area = float(parts[2]) if parts[2] != "-" else None
                except (ValueError, IndexError):
                    pass

            elif "поверх" in text:
                try:
                    floor_info = text.split()
                    floor = int(floor_info[1])
                    floors_in_house = int(floor_info[3])
                except (ValueError, IndexError):
                    pass

            elif "рік будівництва" in text:
                try:
                    year_of_building = int(text.split()[0])
                except ValueError:
                    pass

        try:
            price_text = card.find_element(
                By.XPATH, './/*[contains(@class, "RealtyCard_priceSqm")]'
            ).text.strip()
            price = float(
                price_text.split("$")[0].strip().replace(" ", "").replace("\xa0", "")
            )
        except Exception:
            pass

        all_fields = [num_of_rooms, freshly_renovated, area, living_area,
                      kitchen_area, floor, floors_in_house, year_of_building, price]

        if sum(1 for f in all_fields if f is None) > 5:
            return None
        return Apartment(
            num_of_rooms=num_of_rooms,
            freshly_renovated=freshly_renovated,
            area=area,
            living_area=living_area,
            kitchen_area=kitchen_area,
            floor=floor,
            floors_in_house=floors_in_house,
            year_of_building=year_of_building,
            price=price,
            url=url
        )

    except Exception as e:
        print(f"Parsing error: {e}")
        return None


def get_page_apartments(driver) -> list[Apartment]:
    apartments = driver.find_elements(By.CLASS_NAME, "RealtyCard_propertyGrid__RZYDP")
    result = [parse_single_apartment(apt) for apt in apartments]
    return [apt for apt in result if apt is not None]


def get_apartments(page_url: str) -> list[Apartment]:
    driver = get_driver()
    all_apartments = []
    page_number = 1

    while True:
        paginated_url = f"{page_url}?page={page_number}"
        print(f"Parsing page: {paginated_url}")
        driver.get(paginated_url)

        # Клікаємо "$" тільки якщо кнопка є
        try:
            usd_button = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//button[.//span[contains(text(), "$")]]')
                )
            )
            usd_button.click()
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[contains(@class, "RealtyCard_priceSqm") and contains(text(), "$")]')
                )
            )
        except Exception:
            pass  # Кнопки немає — валюта вже в $, продовжуємо

        # Чекаємо картки
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.CLASS_NAME, "RealtyCard_propertyGrid__RZYDP")
                )
            )
        except Exception:
            break  # Карток немає — кінець сторінок

        apartments_on_page = get_page_apartments(driver)

        if not apartments_on_page:
            break

        all_apartments.extend(apartments_on_page)
        page_number += 1

    return all_apartments

def write_apartment_to_csv(apartments: [Apartment], file_name: str) -> None:
    with open(file_name, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(APARTMENT_FIELDS)
        writer.writerows([dataclasses.astuple(apartment) for apartment in apartments])


# ========================
# DATA STRUCTURES
# ========================

@dataclass
class CityConfig:
    city: str
    geo_region: str
    center_urls: list[str] = field(default_factory=list)
    residential_urls: list[str] = field(default_factory=list)
    outskirts_urls: list[str] = field(default_factory=list)
    district_urls: dict[str, list[str]] = field(default_factory=dict)  # для Києва


# ========================
# CONFIGURATION
# ========================

CITIES: list[CityConfig] = [
    # KYIV — окремо по районах
    # CityConfig(
    #     city="Kyiv",
    #     geo_region="Center",
    #     district_urls={
    #         "Holosiivskyi": ["https://lun.ua/sale/kyiv/flats-holosiivskyi-district"],
    #         "Darnytskyi": ["https://lun.ua/sale/kyiv/flats-darnytskyi-district"],
    #         "Desnianskyi": ["https://lun.ua/sale/kyiv/flats-desnianskyi-district"],
    #         "Dniprovskyi": ["https://lun.ua/sale/kyiv/flats-dniprovskyi-district"],
    #         "Obolonskyi": ["https://lun.ua/sale/kyiv/flats-obolonskyi-district"],
    #         "Pecherskyi": ["https://lun.ua/sale/kyiv/flats-pecherskyi-district"],
    #         "Podilskyi": ["https://lun.ua/sale/kyiv/flats-podilskyi-district"],
    #         "Sviatoshynskyi": ["https://lun.ua/sale/kyiv/flats-sviatoshynskyi-district"],
    #         "Solomianskyi": ["https://lun.ua/sale/kyiv/flats-solomianskyi-district"],
    #         "Shevchenkivskyi": ["https://lun.ua/sale/kyiv/flats-shevchenkivskyi-district"],
    #     }
    # ),
    # # WEST
    # CityConfig(
    #     city="Lutsk",
    #     geo_region="West",
    #     center_urls=["https://lun.ua/sale/volyn/flats-tsentr"],
    #     residential_urls=[
    #         "https://lun.ua/sale/volyn/flats-teremno",
    #         "https://lun.ua/sale/volyn/flats-33-i-mikroraion",
    #         "https://lun.ua/sale/volyn/flats-55-i-mikroraion",
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/volyn/flats-hnydava",
    #         "https://lun.ua/sale/volyn/flats-boholiuby",
    #         "https://lun.ua/sale/volyn/flats-tarasove",
    #     ],
    # ),
    #
    # CityConfig(
    #     city="Lviv",
    #     geo_region="West",
    #     center_urls=["https://lun.ua/sale/lviv/flats-tsentr"],
    #     residential_urls=[
    #         "https://lun.ua/sale/lviv/flats-sykhiv",
    #         "https://lun.ua/sale/lviv/flats-zaliznychnyi-district",
    #         "https://lun.ua/sale/lviv/flats-frankivskyi-district",
    #         "https://lun.ua/sale/lviv/flats-lychakivskyi-district",
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/lviv/flats-shevchenkivskyi-district",
    #         "https://lun.ua/sale/lviv/flats-sykhiv",
    #         "https://lun.ua/sale/lviv/flats-zboishcha"
    #     ],
    # ),
    # CityConfig(
    #     city="Ivano-Frankivsk",
    #     geo_region="West",
    #     center_urls=["https://lun.ua/sale/if/flats-tsentr",
    #                  "https://lun.ua/sale/if/flats-sofiivka"],
    #     residential_urls=[
    #         "https://lun.ua/sale/if/flats-kniahynyn",
    #         "https://lun.ua/sale/if/flats-naberezhna",
    #         "https://lun.ua/sale/if/flats-hirka",
    #         "https://lun.ua/sale/if/flats-budivelnykiv",
    #         "https://lun.ua/sale/if/flats-kant",
    #         "https://lun.ua/sale/if/flats-patriot"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/if/flats-kaskad",
    #         "https://lun.ua/sale/if/flats-braty",
    #         "https://lun.ua/sale/if/flats-pozytron",
    #         "https://lun.ua/sale/if/flats-opryshivtsi"
    #     ],
    # ),
    # CityConfig(
    #     city="Ternopil",
    #     geo_region="West",
    #     center_urls=["https://lun.ua/sale/ternopil/flats-tsentr",
    #                  "https://lun.ua/sale/ternopil/flats-staryi-park",
    #                  "https://lun.ua/sale/ternopil/flats-novyi-svit",
    #                  "https://lun.ua/sale/ternopil/flats-obolonia"],
    #     residential_urls=[
    #         "https://lun.ua/sale/ternopil/flats-druzhba",
    #         "https://lun.ua/sale/ternopil/flats-bam",
    #         "https://lun.ua/sale/ternopil/flats-lvivska-st",
    #         "https://lun.ua/sale/ternopil/flats-hlyboka-st"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/ternopil/flats-pivnichnyi",
    #         "https://lun.ua/sale/ternopil/flats-aliaska",
    #         "https://lun.ua/sale/ternopil/flats-promyslovyi",
    #         "https://lun.ua/sale/ternopil/flats-mykulynetska-st",
    #         "https://lun.ua/sale/ternopil/flats-kutkivtsi"
    #     ],
    # ),
    # CityConfig(
    #     city="Khmelnytskyi",
    #     geo_region="West",
    #     center_urls=["https://lun.ua/sale/khmelnytskyi/flats-tsentr",
    #                  "https://lun.ua/sale/khmelnytskyi/flats-nyzhnia-berehova-st"],
    #     residential_urls=[
    #         "https://lun.ua/sale/khmelnytskyi/flats-pivdenno-zakhidnyi",
    #         "https://lun.ua/sale/khmelnytskyi/flats-dubovo",
    #         "https://lun.ua/sale/khmelnytskyi/flats-hrechany",
    #         "https://lun.ua/sale/khmelnytskyi/flats-rakovo",
    #         "https://lun.ua/sale/khmelnytskyi/flats-bolhary"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/khmelnytskyi/flats-knyzhkivtsi",
    #         "https://lun.ua/sale/khmelnytskyi/flats-leznevo",
    #         "https://lun.ua/sale/khmelnytskyi/flats-ruzhychna",
    #         "https://lun.ua/sale/khmelnytskyi/flats-oleshyn",
    #         "https://lun.ua/sale/khmelnytskyi/flats-sharovechka-10025534"
    #     ],
    # ),
    # CityConfig(
    #     city="Uzhhorod",
    #     geo_region="West",
    #     center_urls=["https://lun.ua/sale/uz/flats-tsentr",
    #                  "https://lun.ua/sale/uz/flats-sobranetska-st"],
    #     residential_urls=[
    #         "https://lun.ua/sale/uz/flats-novyi",
    #         "https://lun.ua/sale/uz/flats-vokzal",
    #         "https://lun.ua/sale/uz/flats-shakhta",
    #         "https://lun.ua/sale/uz/flats-radvanka",
    #         "https://lun.ua/sale/uz/flats-natsionalnoi-hvardii-st",
    #         "https://lun.ua/sale/uz/flats-zahorska-st"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/uz/flats-shakhta",
    #     ],
    # ),
    # CityConfig(
    #     city="Chernivtsi",
    #     geo_region="West",
    #     center_urls=["https://lun.ua/sale/chernivtsi/flats-tsentr"],
    #     residential_urls=[
    #         "https://lun.ua/sale/chernivtsi/flats-pershyi-miroraion",
    #         "https://lun.ua/sale/chernivtsi/flats-kalinets",
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/uz/flats-dravtsi",
    #         "https://lun.ua/sale/uz/flats-chervenytsia",
    #         "https://lun.ua/sale/uz/flats-minai",
    #         "https://lun.ua/sale/uz/flats-storozhnytsia",
    #         "https://lun.ua/sale/uz/flats-yenkivska-st"
    #     ],
    # ),
    # CityConfig(
    #     city="Rivne",
    #     geo_region="West",
    #     center_urls=["https://lun.ua/sale/rivne/flats-tsentr",
    #                  "https://lun.ua/sale/rivne/flats-dvorets",
    #                  "https://lun.ua/sale/rivne/flats-avtovokzal",
    #                  "https://lun.ua/sale/rivne/flats-hrabnyk"],
    #     residential_urls=[
    #         "https://lun.ua/sale/rivne/flats-mototrek",
    #         "https://lun.ua/sale/rivne/flats-studentska-st",
    #         "https://lun.ua/sale/rivne/flats-basiv-kut",
    #         "https://lun.ua/sale/rivne/flats-korolova-st"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/rivne/flats-pivnichnyi",
    #         "https://lun.ua/sale/rivne/flats-novyi-dvir",
    #         "https://lun.ua/sale/rivne/flats-mlynivska-st",
    #         "https://lun.ua/sale/rivne/flats-kolodenka",
    #         "https://lun.ua/sale/rivne/flats-barmaky"
    #     ],
    # ),
    #
    # # NORTH!!!!!!
    # CityConfig(
    #     city="Zhytomyr",
    #     geo_region="North",
    #     center_urls=["https://lun.ua/sale/zhytomyr/flats-tsentr"],
    #     residential_urls=[
    #         "https://lun.ua/sale/zhytomyr/flats-vokzal",
    #         "https://lun.ua/sale/zhytomyr/flats-chudnivska-st",
    #         "https://lun.ua/sale/zhytomyr/flats-seletska-st"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/zhytomyr/flats-oliivka-10016953",
    #         "https://lun.ua/sale/zhytomyr/flats-zarichany-10007890",
    #         "https://lun.ua/sale/zhytomyr/flats-hlybochytsia",
    #         "https://lun.ua/sale/zhytomyr/flats-huiva"
    #     ],
    # ),
    # CityConfig(
    #     city="Chernihiv",
    #     geo_region="North",
    #     center_urls=["https://lun.ua/sale/chernihiv/flats-tsentr",
    #                  "https://lun.ua/sale/chernihiv/flats-val",
    #                  "https://lun.ua/sale/chernihiv/flats-myru-ave",
    #                  "https://lun.ua/sale/chernihiv/flats-liskovytsia",
    #                  "https://lun.ua/sale/chernihiv/flats-miskyi-sad"],
    #     residential_urls=[
    #         "https://lun.ua/sale/chernihiv/flats-yalivshchyna",
    #         "https://lun.ua/sale/chernihiv/flats-bobrovytsia",
    #         "https://lun.ua/sale/chernihiv/flats-ivana-vyhovskoho-st",
    #         "https://lun.ua/sale/chernihiv/flats-berizky"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/chernihiv/flats-rivnopillia",
    #         "https://lun.ua/sale/chernihiv/flats-kozelets"
    #     ],
    # ),
    # CityConfig(
    #     city="Sumy",
    #     geo_region="North",
    #     center_urls=["https://lun.ua/sale/sumy/flats-tsentr",
    #                  "https://lun.ua/sale/sumy/flats-avtovokzal"],
    #     residential_urls=[
    #         "https://lun.ua/sale/sumy/flats-9-i-mikroraion",
    #         "https://lun.ua/sale/sumy/flats-11-i-mikroraion",
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/sumy/flats-kurskyi",
    #         "https://lun.ua/sale/sumy/flats-basy",
    #         "https://lun.ua/sale/sumy/flats-bilopilskyi-shliakh-st",
    #         "https://lun.ua/sale/sumy/flats-sad",
    #         "https://lun.ua/sale/sumy/flats-kosivshchyna"
    #     ],
    # ),
    #
    # # CENTER
    # CityConfig(
    #     city="Vinnytsia",
    #     geo_region="Center",
    #     center_urls=["https://lun.ua/sale/vinnytsia/flats-tsentr",
    #                  "https://lun.ua/sale/vinnytsia/flats-zamostia",
    #                  "https://lun.ua/sale/vinnytsia/flats-nyzhnia-slovianka",
    #                  "https://lun.ua/sale/vinnytsia/flats-pyrohova-st"],
    #     residential_urls=[
    #         "https://lun.ua/sale/vinnytsia/flats-maslozhyr",
    #         "https://lun.ua/sale/vinnytsia/flats-viiskove-mistechko",
    #         "https://lun.ua/sale/vinnytsia/flats-vinnytsia-barske-hwy",
    #         "https://lun.ua/sale/vinnytsia/flats-buchmy-st"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/vinnytsia/flats-pyrohovo",
    #         "https://lun.ua/sale/vinnytsia/flats-hnivanske-hwy",
    #         "https://lun.ua/sale/vinnytsia/flats-ahronomichne",
    #         "https://lun.ua/sale/vinnytsia/flats-vinnytski-khutory"
    #     ],
    # ),
    # CityConfig(
    #     city="Poltava",
    #     geo_region="Center",
    #     center_urls=["https://lun.ua/sale/poltava/flats-tsentr",
    #                  "https://lun.ua/sale/poltava/flats-shevchenkivskyi-district"],
    #     residential_urls=[
    #         "https://lun.ua/sale/poltava/flats-kyivskyi-district",
    #         "https://lun.ua/sale/poltava/flats-podilskyi-district",
    #         "https://lun.ua/sale/poltava/flats-dublianshchyna"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/poltava/flats-rozsoshentsi",
    #         "https://lun.ua/sale/poltava/flats-suprunivka",
    #         "https://lun.ua/sale/poltava/flats-hozhuly",
    #         "https://lun.ua/sale/poltava/flats-kovalivka-poltavskyi-district"
    #     ],
    # ),
    # CityConfig(
    #     city="Kropyvnytskyi",
    #     geo_region="Center",
    #     center_urls=["https://lun.ua/sale/kr/flats-tsentr",
    #                  "https://lun.ua/sale/kr/flats-kovalivka",
    #                  "https://lun.ua/sale/kr/flats-bieliaieva",
    #                  "https://lun.ua/sale/kr/flats-krytyi-rynok"],
    #     residential_urls=[
    #         "https://lun.ua/sale/kr/flats-fortechnyi-district",
    #         "https://lun.ua/sale/kr/flats-podilskyi-district",
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/kr/flats-nova-balashivka",
    #         "https://lun.ua/sale/kr/flats-dolynska"
    #     ],
    # ),
    # CityConfig(
    #     city="Cherkasy",
    #     geo_region="Center",
    #     center_urls=["https://lun.ua/sale/cherkasy/flats-tsentr",
    #                  "https://lun.ua/sale/cherkasy/flats-hoholia-st",
    #                  "https://lun.ua/sale/cherkasy/flats-nadpilna-st",
    #                  "https://lun.ua/sale/cherkasy/flats-pryportovyi"],
    #     residential_urls=[
    #         "https://lun.ua/sale/cherkasy/flats-700-richchia",
    #         "https://lun.ua/sale/cherkasy/flats-ivana-mazepy-st",
    #         "https://lun.ua/sale/cherkasy/flats-dniprovskyi"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/cherkasy/flats-pzr",
    #         "https://lun.ua/sale/cherkasy/flats-sumhaitska-st",
    #         "https://lun.ua/sale/cherkasy/flats-sosnivka",
    #         "https://lun.ua/sale/cherkasy/flats-kanivska-st"
    #     ],
    # ),
    #
    # # EAST
    # CityConfig(
    #     city="Kharkiv",
    #     geo_region="East",
    #     center_urls=["https://lun.ua/sale/kharkiv/flats-tsentr",
    #                  "https://lun.ua/sale/kharkiv/flats-moskalivka"],
    #     residential_urls=[
    #         "https://lun.ua/sale/kharkiv/flats-pavlovo-pole",
    #         "https://lun.ua/sale/kharkiv/flats-kholodnohirskyi-district",
    #         "https://lun.ua/sale/kharkiv/flats-osnovianskyi-district",
    #         "https://lun.ua/sale/kharkiv/flats-slobidskyi-district"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/kharkiv/flats-saltivka",
    #         "https://lun.ua/sale/kharkiv/flats-nemyshlianskyi-district",
    #         "https://lun.ua/sale/kharkiv/flats-industrialnyi-district",
    #         "https://lun.ua/sale/kharkiv/flats-novobavarskyi-district"
    #     ],
    # ),
    # CityConfig(
    #     city="Dnipro",
    #     geo_region="East",
    #     center_urls=["https://lun.ua/sale/dnipro/flats-tsentr",
    #                  "https://lun.ua/sale/dnipro/flats-tsentralnyi-district",
    #                  "https://lun.ua/sale/dnipro/flats-shevchenkivskyi-district",
    #                  "https://lun.ua/sale/dnipro/flats-sobornyi-district"
    #                  ],
    #     residential_urls=[
    #         "https://lun.ua/sale/dnipro/flats-12-i-kvartal",
    #         "https://lun.ua/sale/dnipro/flats-chechelivskyi-district",
    #         "https://lun.ua/sale/dnipro/flats-nyzhnodniprovskyi-district",
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/dnipro/flats-novokodatskyi-district",
    #         "https://lun.ua/sale/dnipro/flats-industrialnyi-district",
    #         "https://lun.ua/sale/dnipro/flats-samarskyi-district",
    #         "https://lun.ua/sale/dnipro/flats-lomivka"
    #     ],
    # ),
    # CityConfig(
    #     city="Zaporizhzhia",
    #     geo_region="East",
    #     center_urls=["https://lun.ua/sale/zp/flats-voznesenivskyi-district",
    #                  "https://lun.ua/sale/zp/flats-oleksandrivskyi-district"],
    #     residential_urls=[
    #         "https://lun.ua/sale/zp/flats-zavodskyi-district",
    #         "https://lun.ua/sale/zp/flats-dniprovskyi-district",
    #         "https://lun.ua/sale/zp/flats-komunarskyi-district"
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/zp/flats-khortytskyi-district",
    #         "https://lun.ua/sale/zp/flats-shevchenkivskyi-district",
    #         "https://lun.ua/sale/zp/flats-3-i-shevchenkivskyi",
    #         "https://lun.ua/sale/zp/flats-17-i-mikroraion",
    #         "https://lun.ua/sale/zp/flats-volodymyrivske"
    #     ],
    # ),
    #
    # # SOUTH
    # CityConfig(
    #     city="Odesa",
    #     geo_region="South",
    #     center_urls=["https://lun.ua/sale/odesa/flats-tsentr",
    #                  "https://lun.ua/sale/odesa/flats-prymorskyi-district",
    #                  "https://lun.ua/sale/odesa/flats-moldavanka",
    #                  "https://lun.ua/sale/odesa/flats-blyzhni-mlyny"],
    #     residential_urls=[
    #         "https://lun.ua/sale/odesa/flats-khadzhybeiskyi-district",
    #         "https://lun.ua/sale/odesa/flats-serednii-fontan",
    #         "https://lun.ua/sale/odesa/flats-arkadiia",
    #     ],
    #     outskirts_urls=[
    #         "https://lun.ua/sale/odesa/flats-kyivskyi-district",
    #         "https://lun.ua/sale/odesa/flats-peresypskyi-district",
    #         "https://lun.ua/sale/odesa/flats-tairova",
    #         "https://lun.ua/sale/odesa/flats-velykyi-fontan",
    #         "https://lun.ua/sale/odesa/flats-dacha-kovalevskoho",
    #         "https://lun.ua/sale/odesa/flats-dmytryivka"
    #     ],
    # ),
    CityConfig(
        city="Mykolaiv", #!!!!!!!!!!!!
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
# RUNNER
# ========================

def get_all_apartments() -> None:
    with webdriver.Chrome() as driver:
        set_driver(driver)

        for city_config in CITIES:
            all_apartments = []

            # Київ — по районах
            if city_config.district_urls:
                for district, urls in city_config.district_urls.items():
                    for url in urls:
                        apartments = get_apartments(url)
                        for apt in apartments:
                            apt.district = district
                            apt.city = city_config.city
                            apt.geo_region = city_config.geo_region
                        all_apartments.extend(apartments)

            # Всі інші міста
            else:
                url_to_district = {
                    **{url: "Center" for url in city_config.center_urls},
                    **{url: "Residential" for url in city_config.residential_urls},
                    **{url: "Outskirts" for url in city_config.outskirts_urls},
                }
                for url, district in url_to_district.items():
                    apartments = get_apartments(url)
                    for apt in apartments:
                        apt.district = district
                        apt.city = city_config.city
                        apt.geo_region = city_config.geo_region
                    all_apartments.extend(apartments)

            write_apartment_to_csv(all_apartments, f"{city_config.city}.csv")
            print(f"✓ {city_config.city} — {len(all_apartments)} apartments saved")


if __name__ == "__main__":
    get_all_apartments()
