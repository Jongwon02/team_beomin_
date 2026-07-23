"""전국 기상 관측소를 기후 특성 기준으로 클러스터링한다."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "Malgun Gothic"  # 한글 라벨 깨짐 방지 (Windows 기본 폰트)
plt.rcParams["axes.unicode_minus"] = False

import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

DATA_DIR = Path(__file__).resolve().parent.parent
RAW_CSV = DATA_DIR / "raw" / "climate_clustering_final_v3.csv"
PROCESSED_DIR = DATA_DIR / "processed"
ELBOW_PLOT_PATH = PROCESSED_DIR / "elbow_plot.png"
OUTPUT_JSON_PATH = PROCESSED_DIR / "region_cluster_map.json"

FEATURES = [
    "avg_temp",
    "jan_min_temp",
    "summer_avg_temp",
    "annual_precip",
    "sunshine_hours",
    "elevation",
]
K_RANGE = range(3, 9)  # 엘보우 기법 탐색 범위 (3~8)
K = 6  # 엘보우 결과를 보고 나중에 바꿀 수 있도록 변수로 분리
RANDOM_STATE = 42

# v3(서울 station_id=108 복구, 89개소) 재실행 결과(summarize_clusters 출력)를 보고 붙인 이름.
# KMeans의 cluster_id는 실행마다 임의로 배정되므로, 데이터나 K/RANDOM_STATE가 바뀌면
# 이름이 엉뚱한 클러스터를 가리키게 될 수 있다 - 재실행할 때마다 반드시 실제 평균 특성표를
# 보고 이 매핑이 여전히 맞는지 확인할 것.
CLUSTER_NAMES = {
    0: "중산간내륙형",    # 철원·정선군·영월·인제·홍천 등, 고도 179m의 강원·산간 내륙
    1: "중남부저지대형",  # 서울 포함 29개소, 고도 54m의 중남부 내륙·해안 저지대
    2: "고랭지형",        # 대관령·태백, 고도 743m·겨울 최저기온 -10.9도로 가장 서늘
    3: "중부내륙형",      # 인천·대구·포항·구미 등, 강수량 최저(1149mm)·일조량 최다인 내륙
    4: "남부해안형",      # 부산·여수·거제 등, 강수량 최다(1586mm)인 남해안
    5: "도서형",          # 울릉도·흑산도·제주·완도·서귀포 등, 겨울 온화(2.0도)하나 일조량 최저
}


def load_data(csv_path=RAW_CSV):
    return pd.read_csv(csv_path)


def scale_features(df, features=FEATURES):
    scaler = StandardScaler()
    scaled = scaler.fit_transform(df[features])
    return scaled, scaler


def run_elbow_method(scaled_features, k_range=K_RANGE, save_path=ELBOW_PLOT_PATH):
    inertias = []
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=RANDOM_STATE, n_init=10)
        km.fit(scaled_features)
        inertias.append(km.inertia_)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(8, 5))
    plt.plot(list(k_range), inertias, marker="o")
    plt.xlabel("k (클러스터 개수)")
    plt.ylabel("Inertia")
    plt.title("Elbow Method for Optimal k")
    plt.xticks(list(k_range))
    plt.grid(True, alpha=0.3)
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()

    print(f"[엘보우 기법] inertia 그래프 저장 완료: {save_path}")
    for k, inertia in zip(k_range, inertias):
        print(f"  k={k}: inertia={inertia:.2f}")

    return inertias


def run_kmeans(scaled_features, k=K, random_state=RANDOM_STATE):
    km = KMeans(n_clusters=k, random_state=random_state, n_init=10)
    labels = km.fit_predict(scaled_features)
    return km, labels


def summarize_clusters(df, features=FEATURES, cluster_col="cluster_id", cluster_names=CLUSTER_NAMES):
    summary = df.groupby(cluster_col)[features].mean().round(2)
    summary["station_count"] = df.groupby(cluster_col).size()
    summary.insert(0, "cluster_name", summary.index.map(cluster_names))
    return summary


def save_results(df, output_path=OUTPUT_JSON_PATH, cluster_names=CLUSTER_NAMES):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df = df[["station_id", "station_name", "lat", "lon", "cluster_id"]].copy()
    out_df["cluster_name"] = out_df["cluster_id"].map(cluster_names)
    records = out_df.to_dict(orient="records")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장 완료: {output_path} (총 {len(records)}개 관측소)")


def main():
    df = load_data()
    scaled_features, _ = scale_features(df)

    print("=" * 60)
    print("1단계: 엘보우 기법으로 최적 k 탐색 (k=3~8)")
    print("=" * 60)
    run_elbow_method(scaled_features)

    print("\n" + "=" * 60)
    print(f"2단계: k={K}로 KMeans 클러스터링 실행")
    print("=" * 60)
    _, labels = run_kmeans(scaled_features, k=K)
    df["cluster_id"] = labels

    print("\n[클러스터별 관측소 수]")
    print(df["cluster_id"].value_counts().sort_index())

    print("\n" + "=" * 60)
    print("3단계: 클러스터별 평균 특성 (5개 변수)")
    print("=" * 60)
    summary = summarize_clusters(df)
    print(summary)

    save_results(df)


if __name__ == "__main__":
    main()
