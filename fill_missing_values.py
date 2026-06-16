import pandas as pd
from sklearn.impute import KNNImputer
from sklearn.preprocessing import MinMaxScaler

zero_cols = ["num_of_rooms", "area", "living_area", "kitchen_area", "floor", "floors_in_house", "year_of_building"]

features_for_knn = ["price", "num_of_rooms", "area", "living_area", "kitchen_area", "floor", "floors_in_house",
                    "year_of_building"]


def impute_city_group(group: pd.DataFrame) -> pd.DataFrame:
    if not group[zero_cols].isna().any().any():
        return group

    knn_data = group[features_for_knn].copy()

    scaler = MinMaxScaler()
    knn_data_scaled = pd.DataFrame(
        scaler.fit_transform(knn_data),
        columns=features_for_knn,
        index=group.index
    )

    imputer = KNNImputer(n_neighbors=5, weights='distance')
    knn_data_imputed_scaled = pd.DataFrame(
        imputer.fit_transform(knn_data_scaled),
        columns=features_for_knn,
        index=group.index
    )

    knn_data_final = pd.DataFrame(
        scaler.inverse_transform(knn_data_imputed_scaled),
        columns=features_for_knn,
        index=group.index
    )

    group[zero_cols] = knn_data_final[zero_cols]
    group["num_of_rooms"] = group["num_of_rooms"].round().astype(int)
    group["year_of_building"] = group["year_of_building"].round().astype(int)
    group["area"] = group["year_of_building"].round().astype(int)
    return group


if __name__ == "__main__":
    files = [
        "Cherkasy.csv", "Chernihiv.csv", "Chernivtsi.csv", "Dnipro.csv", "Ivano-Frankivsk.csv",
        "Kharkiv.csv", "Kherson.csv", "Khmelnytskyi.csv", "Kropyvnytskyi.csv", "Kyiv.csv",
        "Lutsk.csv", "Lviv.csv", "Mykolaiv.csv", "Odesa.csv", "Poltava.csv", "Rivne.csv",
        "Sumy.csv", "Ternopil.csv", "Uzhhorod.csv", "Vinnytsia.csv", "Zaporizhzhia.csv",
        "Zhytomyr.csv"
    ]
    for filename in files:
        df = pd.read_csv(filename)
        df = impute_city_group(df)
        df.to_csv(filename, index=False)
        print(f"✓ {filename}")
