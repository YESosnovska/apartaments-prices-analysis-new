import csv
import dataclasses
from dataclasses import dataclass
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
    num_of_rooms: int
    freshly_renovated: bool
    area: float
    living_area: float
    kitchen_area: float
    floor: int
    floors_in_house: int
    year_of_building: int
    price: float


APARTMENT_FIELDS = [field.name for field in dataclasses.fields(Apartment)]


def parse_single_apartment(apartment: WebElement) -> Apartment:
    try:
        props = apartment.find_elements(By.CLASS_NAME, "PropertyItem_text__IADK7")
        card = apartment.find_element(By.XPATH, "../..")

        num_of_rooms = 0
        renovated_status = "без ремонту"
        area_text = None
        floor = 0
        floors_in_house = 0
        year_of_building = 0
        price = 0.0

        for prop in props:
            text = prop.text.strip()

            if "кімн" in text:
                num_of_rooms = int(text.split()[0])

            elif "рем" in text:
                renovated_status = text

            elif "м²" in text and "/" in text:
                parts = text.replace(" м²", "").split("/")
                area = float(parts[0].strip())
                living_area = float(parts[1].strip().replace("-", "0"))
                kitchen_area = float(parts[2].strip())

            elif "поверх" in text:
                floor_info = text.split()
                floor = int(floor_info[1])
                floors_in_house = int(floor_info[3])

            elif "Рік будівництва" in text:
                year_of_building = int(text.split()[0])

        freshly_renovated = renovated_status == "з ремонтом"

        price_text = card.find_element(
            By.XPATH, './/*[contains(@class, "RealtyCard_priceSqm")]'
        ).text.strip()

        # Формат: "806 $/м²"
        price = float(
            price_text.split("$")[0].strip().replace(" ", "").replace("\xa0", "")
        )

        return Apartment(
            num_of_rooms=num_of_rooms,
            freshly_renovated=freshly_renovated,
            area=area,
            living_area=living_area,
            kitchen_area=kitchen_area,
            floor=floor,
            floors_in_house=floors_in_house,
            year_of_building=year_of_building,
            price=price
        )
    except Exception as e:
        print(f"Помилка парсингу: {e}")
        return Apartment(
            num_of_rooms=0,
            freshly_renovated=False,
            area=0, living_area=0, kitchen_area=0,
            floor=0, floors_in_house=0,
            year_of_building=0, price=0
        )


def get_page_apartments(page: WebDriver) -> list[Apartment]:
    apartments = page.find_elements(By.CLASS_NAME, "RealtyCard_propertyGrid__RZYDP")
    return [parse_single_apartment(apartment) for apartment in apartments]


def get_apartments(page_url: str) -> list[Apartment]:
    driver = get_driver()
    all_apartments = []
    page_number = 1

    while True:
        paginated_url = f"{page_url}?page={page_number}"
        driver.get(paginated_url)

        try:
            # Чекаємо кнопку "$" і клікаємо
            usd_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//button[.//span[contains(text(), "$")]]')
                )
            )
            usd_button.click()

            # Чекаємо поки ціни РЕАЛЬНО переключаться на $
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[contains(@class, "RealtyCard_priceSqm") and contains(text(), "$")]')
                )
            )

        except Exception:
            break  # Якщо кнопки немає — більше сторінок немає

        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.CLASS_NAME, "RealtyCard_propertyGrid__RZYDP")
                )
            )
        except Exception:
            break

        apartments_on_page = get_page_apartments(driver)

        if not apartments_on_page:
            break

        all_apartments.extend(apartments_on_page)
        page_number += 1

    driver.quit()
    return all_apartments


def write_apartment_to_csv(apartments: [Apartment], file_name: str) -> None:
    with open(file_name, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(APARTMENT_FIELDS)
        writer.writerows([dataclasses.astuple(apartment) for apartment in apartments])


def get_all_apartments() -> None:
    with webdriver.Chrome() as driver:
        set_driver(driver)
        write_apartment_to_csv(get_apartments("https://lun.ua/sale/volyn/flats"), "Lutsk")


if __name__ == "__main__":
    get_all_apartments()
