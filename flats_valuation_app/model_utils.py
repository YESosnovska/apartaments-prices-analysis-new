"""
Утиліти для завантаження навчених пайплайнів (.joblib з train_Kyiv_ML.ipynb /
train_regions_ML_flats.ipynb для квартир) і побудови вхідного рядка ознак
з даних форми.

РЕФАКТОРИНГ (важливо для розуміння коду нижче): артефакт тепер зберігає
ГОТОВИЙ sklearn.Pipeline (імпутація + масштабування + модель, або сама
HistGradientBoosting -- вона працює з NaN нативно), а не окремі model+scaler.
Це означає, що build_input_row має ПЕРЕДАВАТИ NaN там, де значення невідоме
(а не самому вигадувати заповнення) -- Pipeline сам імпутує саме так, як
навчався (медіана/мода, порахована на train-спліті). Єдині свідомі винятки,
де ми підставляємо конкретне значення тут, а не NaN, -- це:
  - poi_distance_m при відсутності точки інтересу (сентинель, не пропуск),
  - year_of_building, де відсутність року замінюється на консервативний
    дефолт 1990 (модель навчена саме на year_of_building, а не на
    "віці будинку" -- останній ставав би від'ємним для об'єктів, які ще
    тільки плануються до будівництва).

Кожен .joblib-артефакт має вигляд:
    {
        "model_name": "Ridge" | "RandomForest" | "HistGradientBoosting",
        "model": <sklearn.pipeline.Pipeline, вже фітований>,
        "feature_cols": [<list str>],
        # лише для регіонів (є target encoding по місту):
        "cat_col": "city_raw",
        "cat_prefix": "city_",
        "target_encoding_mapping": <dict str -> float>,
        "target_encoding_global_mean": <float>,
        "target_enc_col": "city_raw_target_enc",
        "quantile_models": {0.1: <HGB>, 0.5: <HGB>, 0.9: <HGB>},
        "quantiles": [0.1, 0.5, 0.9],
    }

Модель передбачає ЦІНУ ЗА М² (log1p-трансформована ціль). Загальна ціна
об'єкта = ціна_за_м2 * площа.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import joblib

# --- Довідники категорій (ті самі, що використовувались при навчанні) ------

KYIV_DISTRICTS = [
    ("Holosiivskyi", "Голосіївський"),
    ("Darnytskyi", "Дарницький"),
    ("Desnianskyi", "Деснянський"),
    ("Dniprovskyi", "Дніпровський"),
    ("Obolonskyi", "Оболонський"),
    ("Pecherskyi", "Печерський"),
    ("Podilskyi", "Подільський"),
    ("Solomianskyi", "Соломʼянський"),
    ("Sviatoshynskyi", "Святошинський"),
    ("Shevchenkivskyi", "Шевченківський"),
]

REGION_CITIES = [
    ("Lviv", "Львів"), ("Kharkiv", "Харків"), ("Dnipro", "Дніпро"),
    ("Zaporizhzhia", "Запоріжжя"), ("Odesa", "Одеса"), ("Poltava", "Полтава"),
    ("Mykolaiv", "Миколаїв"), ("Cherkasy", "Черкаси"), ("Kropyvnytskyi", "Кропивницький"),
    ("Sumy", "Суми"), ("Chernihiv", "Чернігів"), ("Zhytomyr", "Житомир"),
    ("Lutsk", "Луцьк"), ("Ivano-Frankivsk", "Івано-Франківськ"), ("Ternopil", "Тернопіль"),
    ("Khmelnytskyi", "Хмельницький"), ("Uzhhorod", "Ужгород"), ("Vinnytsia", "Вінниця"),
    ("Rivne", "Рівне"), ("Chernivtsi", "Чернівці"), ("Kherson", "Херсон"),
]

HOUSE_TYPE_LABELS_KYIV = {
    "apps": "Квартирного типу", "apps_lux": "Апартаменти люкс", "bps": "БПС (панельний)",
    "czech_project": "Чеський проект", "hostinka": "Гостинка", "khrushchevka": "Хрущовка",
    "pre_revolutionary": "Дореволюційний будинок", "radmin": "Адміністративна перебудова",
    "series_87": "Серія 87", "series_96": "Серія 96", "series_134": "Серія 134",
    "series_215": "Серія 215", "series_es": "Серія ЕС", "series_kp": "Серія КП",
    "series_ks": "Серія КС", "series_kt": "Серія КТ", "series_t": "Серія Т",
    "special_project": "Індивідуальний проект", "stalinka": "Сталінка",
}
HOUSE_TYPE_LABELS_REGIONS = {
    "austrian_lux": "Австрійський проект (люкс)", "belgian_project": "Бельгійський проект",
    "czech_project": "Чеський проект", "hostinka": "Гостинка", "khrushchevka": "Хрущовка",
    "malosimeyka": "Малосімейка", "polish_house": "Польський дім", "polish_lux": "Польський люкс",
    "pre_revolutionary": "Дореволюційний будинок", "radmin": "Адміністративна перебудова",
    "series_87": "Серія 87", "series_96": "Серія 96", "series_134": "Серія 134",
    "series_es": "Серія ЕС", "series_kp": "Серія КП",
    "special_project": "Індивідуальний проект", "stalinka": "Сталінка",
}
WALL_TYPE_LABELS = {
    "brick": "Цегляний", "block": "Блочний", "panel": "Панельний",
    "insulated_panel": "Утеплена панель", "monolithic_frame": "Монолітно-каркасний",
}
HEATING_LABELS = {
    "centralized": "Централізоване", "autonomous": "Автономне (в будинку)",
    "individual": "Індивідуальне (котел у квартирі)",
}
KITCHEN_TYPE_LABELS = {"separate": "Окрема кухня", "studio": "Кухня-студія"}
HOT_WATER_TYPE_LABELS = {
    "centralized": "Централізоване", "gas_column": "Газова колонка",
    "gas_boiler": "Газовий котел", "electric_boiler": "Електричний бойлер", "none": "Відсутнє",
}

TRISTATE_FIELDS = [
    ("has_gas", "Газ підведено"),
    ("autonomy_power", "Автономне електропостачання (генератор)"),
    ("autonomy_heat", "Автономне опалення при відключеннях"),
    ("autonomy_water", "Автономне водопостачання"),
    ("autonomy_net", "Автономний інтернет/зв'язок"),
    ("autonomy_lift", "Резервне живлення ліфта"),
    ("freshly_renovated", "Свіжий ремонт"),
]

# Для обласних центрів "district" у сирих даних -- це НЕ райони (як у Києві),
# а зона всередині міста: Center/Residential/Outskirts. "Center" -- референсна
# категорія (drop_first=True при навчанні), тому їй не відповідає жодна
# district_* dummy-колонка -- звідси REGION_ZONE_OPTIONS не містить прапорця
# для Center у build_input_row нижче (лишити все на 0 -- і є "Center").
REGION_ZONE_OPTIONS = [
    ("Center", "Центр"),
    ("Residential", "Житловий масив"),
    ("Outskirts", "Околиця"),
]

# Ці ознаки у preprocessing.ipynb витягуються з тексту оголошення регексом
# (add_text_features) -- у формі ж просто прямі чекбокси "Так/Ні", без
# копіювання тексту й розпізнавання ключових слів. Завжди 0/1, без стану
# "невідомо" -- так само, як і в тренувальних даних (regex на порожній
# текст дає 0, а не NaN).
#
# "has_generator_text" тут НЕМАЄ -- це той самий факт, що й "Автономне
# електропостачання (генератор)" у розділі "Автономність" (там -- зі
# структурованого поля сайту, тут мало б дублювати той самий regex-факт
# із тексту оголошення). Питати те саме двічі в різних розділах форми
# безглуздо для НОВОГО оголошення -- тож значення просто беремо з відповіді
# на "автономне електропостачання" (див. build_input_row нижче).
AMENITY_CHECKBOX_FIELDS = [
    ("has_shelter_text", "Є укриття/бомбосховище поруч"),
    ("has_parking_text", "Є паркінг/парковка"),
    ("has_terrace_text", "Є тераса"),
    ("has_designer_renovation_text", "Авторський/дизайнерський ремонт"),
]

DEFAULT_YEAR_OF_BUILDING = 1990  # якщо рік не вказано -- консервативний дефолт
POI_DISTANCE_SENTINEL = 99999.0  # якщо точки інтересу немає -- маркер, не медіана

SCOPES = {
    "kyiv": {
        "label": "Київ",
        "cat_field": "district",
        "cat_prefix": "district_",
        "cat_options": KYIV_DISTRICTS,
        "house_type_labels": HOUSE_TYPE_LABELS_KYIV,
        "artifact_file": "flats_kyiv_model_pipeline.joblib",
        "uses_target_encoding": False,
        "has_zone_field": False,
    },
    "regions": {
        "label": "Обласні центри",
        "cat_field": "city",
        "cat_prefix": "city_",
        "cat_options": REGION_CITIES,
        "house_type_labels": HOUSE_TYPE_LABELS_REGIONS,
        "artifact_file": "flats_ukraine_model_pipeline.joblib",
        "uses_target_encoding": True,
        "has_zone_field": True,
        "zone_options": REGION_ZONE_OPTIONS,
    },
}


_ARTIFACT_CACHE: dict[str, dict] = {}


def load_artifact(scope: str, models_dir: str) -> dict | None:
    if scope in _ARTIFACT_CACHE:
        return _ARTIFACT_CACHE[scope]
    import os
    path = os.path.join(models_dir, SCOPES[scope]["artifact_file"])
    if not os.path.exists(path):
        return None
    artifact = joblib.load(path)
    _ARTIFACT_CACHE[scope] = artifact
    return artifact


def build_input_row(form: dict, scope: str) -> pd.Series:
    """Перетворює дані форми на "сирий" рядок ознак. Числові поля, які
    користувач лишив порожніми, ідуть у X як NaN (а НЕ якесь вигадане
    значення) -- Pipeline усередині моделі сам їх імпутує так само, як
    імпутував під час навчання (медіана/мода, порахована на train-спліті).
    Виняток -- poi_distance_m (сентинель) і рік будівництва (дефолт 1990),
    де свідомо підставляємо конкретне значення, а не NaN."""
    cfg = SCOPES[scope]

    def num_required(name: str) -> float:
        val = form.get(name, "").strip()
        return float(val) if val else 0.0

    def num_optional(name: str) -> float:
        """NaN, якщо поле порожнє -- хай Pipeline імпутує сам."""
        val = form.get(name, "").strip()
        return float(val) if val else float("nan")

    row: dict[str, float] = {}

    row["area"] = num_required("area")
    row["num_of_rooms"] = num_required("num_of_rooms")
    row["floor"] = num_required("floor")
    row["floors_in_house"] = num_required("floors_in_house")
    row["distance_to_center_km"] = num_required("distance_to_center_km")

    # --- рік будівництва -- модель тепер навчена саме на year_of_building
    # (не building_age): деякі об'єкти лише плануються до будівництва
    # (year_of_building у майбутньому), і "вік будинку" в такому разі
    # виходив би від'ємним і безглуздим. year_of_building сам по собі
    # такої проблеми не має.
    year_raw = form.get("year_of_building", "").strip()
    year = float(year_raw) if year_raw else float(DEFAULT_YEAR_OF_BUILDING)
    row["year_of_building"] = year

    for col in ("ceiling_height", "living_area", "kitchen_area"):
        row[col] = num_optional(col)

    # --- жорстка валідація площ: якщо living_area + kitchen_area > area,
    # пропорційно зменшуємо обидві, щоб не подавати моделі фізично
    # неможливий об'єкт (і не впасти нижче на цьому) ---
    living, kitchen, area = row["living_area"], row["kitchen_area"], row["area"]
    if not np.isnan(living) and not np.isnan(kitchen) and area > 0:
        total = living + kitchen
        if total > area:
            scale = area / total
            row["living_area"] = living * scale
            row["kitchen_area"] = kitchen * scale

    # --- поверх перший/останній (завжди обчислювані -- floor/floors_in_house
    # обов'язкові поля форми) ---
    row["is_first_floor"] = 1.0 if row["floor"] == 1 else 0.0
    row["is_last_floor"] = 1.0 if row["floor"] == row["floors_in_house"] else 0.0

    for col, default in [("bedroom_count", 0), ("toilets_count", 0), ("balcony_count", 0)]:
        raw = form.get(col, "").strip()
        row[f"{col}_missing"] = 0 if raw else 1
        row[col] = float(raw) if raw else float("nan")  # Pipeline імпутує МОДОЮ

    row["in_residential_complex"] = 1 if form.get("in_residential_complex") == "on" else 0
    row["has_poi"] = 1 if form.get("has_poi") == "on" else 0

    # ЖК (житловий комплекс) як формат забудови нетиповий для будинків до
    # 1998 -- та сама бізнес-логіка, що й на формі (JS блокує чекбокс), але
    # продубльована тут на сервері, про всяк випадок, якщо форму викликали
    # напряму без JS
    MIN_YEAR_FOR_COMPLEX = 1998
    if row["in_residential_complex"] == 1 and year < MIN_YEAR_FOR_COMPLEX:
        row["in_residential_complex"] = 0

    # poi_distance_m -- сентинель 99999, ЯКЩО точки інтересу взагалі немає
    # (це не пропуск даних, а свідомий факт "нема, і крапка")
    poi_raw = form.get("poi_distance_m", "").strip()
    if row["has_poi"] == 0:
        row["poi_distance_m"] = POI_DISTANCE_SENTINEL
    else:
        row["poi_distance_m"] = float(poi_raw) if poi_raw else float("nan")
        row["poi_distance_m"] = max(0.0, min(1000.0, row["poi_distance_m"])) if poi_raw else row["poi_distance_m"]

    for field, _ in TRISTATE_FIELDS:
        val = form.get(field, "")
        row[f"{field}_unknown"] = 1 if val == "" else 0
        row[field] = 1.0 if val == "yes" else (0.0 if val == "no" else float("nan"))

    for prefix, choice_field in [
        ("house_type_", "house_type"), ("wall_type_", "wall_type"),
        ("heating_", "heating"), ("kitchen_type_", "kitchen_type"),
        ("hot_water_type_", "hot_water_type"),
    ]:
        chosen = form.get(choice_field, "")
        if chosen:
            row[f"{prefix}{chosen}"] = 1

    for field, _ in AMENITY_CHECKBOX_FIELDS:
        row[field] = 1 if form.get(field) == "on" else 0

    # "has_generator_text" не питаємо окремим чекбоксом (дублювало б
    # "Автономне електропостачання (генератор)" вище) -- беремо звідти:
    # "невідомо" тут трактуємо як 0, так само, як regex на порожній текст
    # оголошення дає 0 (немає підстав вважати, що генератор буде згаданий).
    row["has_generator_text"] = 1.0 if row.get("autonomy_power") == 1.0 else 0.0

    if cfg["uses_target_encoding"]:
        row["__city_raw__"] = form.get(cfg["cat_field"], "")
    else:
        chosen_district = form.get(cfg["cat_field"], "")
        if chosen_district:
            row[f"{cfg['cat_prefix']}{chosen_district}"] = 1

    # зона всередині міста (лише для областей): Center -- референсна
    # категорія (усі district_* прапорці лишаються 0), Residential/Outskirts
    # -- відповідна dummy-колонка = 1
    if cfg.get("has_zone_field"):
        zone = form.get("district_zone", "")
        if zone and zone != "Center":
            row[f"district_{zone}"] = 1

    # серверна підстраховка: заборона від'ємних значень скрізь, окрім floor
    # (може бути підземним рівнем)
    for non_negative_col in ("area", "num_of_rooms", "floors_in_house", "distance_to_center_km",
                              "ceiling_height", "living_area", "kitchen_area",
                              "bedroom_count", "toilets_count", "balcony_count"):
        if non_negative_col in row and not np.isnan(row[non_negative_col]):
            row[non_negative_col] = max(0.0, row[non_negative_col])
    row["floor"] = max(-5.0, row["floor"])
    if row["floor"] > row["floors_in_house"]:
        row["floor"] = row["floors_in_house"]

    return pd.Series(row)


def predict_price(scope: str, artifact: dict, raw_row: pd.Series) -> dict:
    """Повертає точковий прогноз і, якщо є квантильні моделі, персональний
    діапазон. Pipeline у artifact["model"] сам імпутує/масштабує вхід --
    тут більше нічого вручну не робимо (на відміну від версії до
    рефакторингу, де доводилось окремо викликати scaler.transform())."""
    cfg = SCOPES[scope]
    feature_cols = artifact["feature_cols"]

    x = pd.Series(np.nan, index=feature_cols, dtype="float64")
    for col in feature_cols:
        if col in raw_row.index:
            x[col] = raw_row[col]

    if cfg["uses_target_encoding"]:
        enc_col = artifact["target_enc_col"]
        mapping = artifact["target_encoding_mapping"]
        city = raw_row.get("__city_raw__", "")
        value = mapping.get(city, artifact["target_encoding_global_mean"])
        if enc_col in x.index:
            x[enc_col] = value

    X_new = x.to_frame().T.astype(float)
    area = float(raw_row.get("area", 0.0))

    pred_log = artifact["model"].predict(X_new)[0]
    price_per_sqm = float(np.expm1(pred_log))

    result = {
        "price_per_sqm": price_per_sqm,
        "total_price": price_per_sqm * area,
    }

    quantile_models = artifact.get("quantile_models")
    if quantile_models:
        quantiles = sorted(quantile_models.keys())
        q_low, q_high = quantiles[0], quantiles[-1]
        # CQR-поправка (Conformalized Quantile Regression): "сирі" квантильні
        # моделі систематично недокривають номінальний рівень (напр. 80%
        # заявлено, ~70% фактично) -- q_hat, порахований у train-ноутбуці на
        # окремій калібрувальній вибірці, розширює інтервал рівно настільки,
        # щоб фактичне покриття на test збігалось із заявленим.
        q_hat = artifact.get("quantile_q_hat", 0.0)
        price_low = float(np.expm1(quantile_models[q_low].predict(X_new)[0] - q_hat))
        price_high = float(np.expm1(quantile_models[q_high].predict(X_new)[0] + q_hat))
        result.update({
            "price_per_sqm_low": price_low,
            "price_per_sqm_high": price_high,
            "total_price_low": price_low * area,
            "total_price_high": price_high * area,
            "interval_label": f"{int(q_low * 100)}–{int(q_high * 100)} перцентиль (калібрований, CQR)",
        })

    return result
