import os

from flask import Flask, redirect, render_template, request, url_for

import model_utils as mu

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")

app = Flask(__name__)


@app.route("/")
def index():
    return render_template(
        "valuation.html", result=None, error=None, form_values={},
        **_valuation_context(scope="kyiv"),
    )


@app.route("/valuation", methods=["GET", "POST"])
def valuation():
    scope = request.values.get("scope", "kyiv")
    if scope not in mu.SCOPES:
        scope = "kyiv"

    result = None
    error = None
    form_values = {}

    if request.method == "POST":
        form_values = request.form.to_dict()
        artifact = mu.load_artifact(scope, MODELS_DIR)
        if artifact is None:
            error = (
                f"Файл моделі \"{mu.SCOPES[scope]['artifact_file']}\" не знайдено в папці models/. "
                "Покладіть туди .joblib, збережений у train_Kyiv_ML.ipynb / train_regions_ML_flats.ipynb."
            )
        else:
            try:
                raw_row = mu.build_input_row(request.form, scope)
                result = mu.predict_price(scope, artifact, raw_row)
            except ValueError:
                # Некоректний формат числа (напр. кома замість крапки, літери
                # в числовому полі) -- зрозуміле повідомлення користувачу,
                # а не сирий Python traceback.
                error = "Перевірте правильність числових форматів у формі (наприклад, використовуйте крапку, а не кому, для дробових чисел)."
            except Exception as exc:  # noqa: BLE001 -- показуємо аналітику причину напряму
                error = f"Не вдалось порахувати оцінку: {exc}"

    return render_template(
        "valuation.html",
        result=result, error=error, form_values=form_values,
        **_valuation_context(scope=scope),
    )


@app.route("/dashboards")
def dashboards_redirect():
    return redirect(url_for("dashboards_kyiv"))


@app.route("/dashboards/kyiv")
def dashboards_kyiv():
    return render_template("dashboards_kyiv.html")


@app.route("/dashboards/regions")
def dashboards_regions_redirect():
    return redirect(url_for("dashboards_regions_comparison"))


@app.route("/dashboards/regions/comparison")
def dashboards_regions_comparison():
    return render_template("dashboards_regions_comparison.html")


@app.route("/dashboards/regions/price-formation")
def dashboards_regions_price_formation():
    return render_template("dashboards_regions_price_formation.html")


def _valuation_context(scope: str) -> dict:
    cfg = mu.SCOPES[scope]
    model_ready = mu.load_artifact(scope, MODELS_DIR) is not None
    return {
        "scope": scope,
        "scopes": mu.SCOPES,
        "cfg": cfg,
        "model_ready": model_ready,
        "tristate_fields": mu.TRISTATE_FIELDS,
        "amenity_checkbox_fields": mu.AMENITY_CHECKBOX_FIELDS,
        "wall_type_labels": mu.WALL_TYPE_LABELS,
        "heating_labels": mu.HEATING_LABELS,
        "kitchen_type_labels": mu.KITCHEN_TYPE_LABELS,
        "hot_water_type_labels": mu.HOT_WATER_TYPE_LABELS,
    }


if __name__ == "__main__":
    app.run(debug=True, port=5000)
