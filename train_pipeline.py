"""
train_pipeline.py
=================
Multimodal Machine Learning Training & Evaluation Pipeline for Parkinson's Disease Screening.

1. Queries pre-aggregated multimodal feature vectors directly from PostgreSQL
   (parkinsons.multimodal_feature_store).
2. Performs stratified train/test split (80/20).
3. Constructs an end-to-end Scikit-Learn Pipeline encapsulating:
     - Median Imputation (handles missing clinical assessments gracefully)
     - Standard Scaling (normalizes heterogeneous units: Hz, dB, px/ms, scores)
     - Balanced Random Forest Classifier Ensemble
4. Evaluates test set performance: Precision, Recall, F1-Score, ROC-AUC, Confusion Matrix.
5. Decomposes Gini feature importances into clinical modality contributions.
6. Serializes the fitted model bundle to disk (parkinsons_multimodal_model.joblib).

Usage:
    python train_pipeline.py --dsn "postgresql://postgres@localhost:5432/parkinsons"
"""

import argparse
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
import psycopg2
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FEATURE_COLUMNS: List[str] = [
    # 1. Digital Tablet Kinematics
    "avg_velocity", "avg_jerk", "pressure_std", "in_air_time_ratio",
    # 2. Acoustic / Vocal Biomarkers
    "mean_f0_hz", "jitter_percent", "shimmer_local", "hnr_db",
    # 3. Gait & Mobility Telemetry
    "step_cadence_spm", "stride_time_variance", "arm_swing_asymmetry_index",
    # 4. Non-Motor Clinical Screening
    "upsit_smell_score", "rbd_sleep_flag",
]

LABEL_COLUMN: str = "diagnosis_label"

MODALITY_MAP: Dict[str, str] = {
    "avg_velocity": "tablet_kinematics",
    "avg_jerk": "tablet_kinematics",
    "pressure_std": "tablet_kinematics",
    "in_air_time_ratio": "tablet_kinematics",
    "mean_f0_hz": "voice",
    "jitter_percent": "voice",
    "shimmer_local": "voice",
    "hnr_db": "voice",
    "step_cadence_spm": "gait",
    "stride_time_variance": "gait",
    "arm_swing_asymmetry_index": "gait",
    "upsit_smell_score": "non_motor",
    "rbd_sleep_flag": "non_motor",
}


def load_dataset_from_db(dsn: str) -> pd.DataFrame:
    """Extracts labeled feature vectors from the multimodal feature store."""
    query = f"""
        SELECT {', '.join(FEATURE_COLUMNS)}, {LABEL_COLUMN}
        FROM parkinsons.multimodal_feature_store
        WHERE {LABEL_COLUMN} IN ('parkinsons', 'healthy')
        ORDER BY generated_at ASC;
    """
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            records = cur.fetchall()
            colnames = [desc[0] for desc in cur.description]
            df = pd.DataFrame(records, columns=colnames)
    return df


def build_pipeline() -> Pipeline:
    """Builds a unified preprocessing and classification pipeline."""
    preprocessor = ColumnTransformer(transformers=[
        ("num", Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]), FEATURE_COLUMNS),
    ])

    classifier = RandomForestClassifier(
        n_estimators=300,
        max_depth=8,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )

    return Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", classifier),
    ])


def compute_modality_importance(pipeline: Pipeline) -> Dict[str, float]:
    """Aggregates individual feature importances into clinical modality contributions."""
    rf = pipeline.named_steps["classifier"]
    importances = rf.feature_importances_
    modality_sums: Dict[str, float] = {}

    for col, imp in zip(FEATURE_COLUMNS, importances):
        mod = MODALITY_MAP[col]
        modality_sums[mod] = modality_sums.get(mod, 0.0) + float(imp)

    total = sum(modality_sums.values()) or 1.0
    return {k: round(v / total, 4) for k, v in sorted(modality_sums.items(), key=lambda x: -x[1])}


def train_and_evaluate(dsn: str, output_path: str, test_size: float = 0.2):
    print("=" * 70)
    print("PARKINSON'S MULTIMODAL ML TRAINING PIPELINE")
    print("=" * 70)

    print(f"\n[1/5] Extracting training vectors from PostgreSQL...")
    df = load_dataset_from_db(dsn)
    n_pd = (df[LABEL_COLUMN] == "parkinsons").sum()
    n_healthy = (df[LABEL_COLUMN] == "healthy").sum()
    print(f"      Total records: {len(df)} ({n_pd} Parkinson's / {n_healthy} Healthy Controls)")

    if len(df) == 0:
        raise ValueError("No labeled records found in multimodal_feature_store. Run seed_database.py first!")

    X = df[FEATURE_COLUMNS]
    # Binary target: 1 = Parkinson's, 0 = Healthy
    y = (df[LABEL_COLUMN] == "parkinsons").astype(int)

    print(f"\n[2/5] Splitting cohort (stratified {int((1-test_size)*100)}/{int(test_size*100)} train/test split)...")
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, stratify=y, random_state=42
    )
    print(f"      Training samples: {len(X_train)} | Test samples: {len(X_test)}")

    print(f"\n[3/5] Fitting ColumnTransformer + RandomForest ensemble...")
    pipeline = build_pipeline()
    pipeline.fit(X_train, y_train)

    print(f"\n[4/5] Evaluating model performance on unseen test cohort...")
    y_pred = pipeline.predict(X_test)
    y_proba = pipeline.predict_proba(X_test)[:, 1]

    prec = precision_score(y_test, y_pred, zero_division=0)
    rec = recall_score(y_test, y_pred, zero_division=0)
    f1 = f1_score(y_test, y_pred, zero_division=0)
    auc = roc_auc_score(y_test, y_proba)
    cm = confusion_matrix(y_test, y_pred)

    print("-" * 50)
    print(f"  Precision : {prec:.4f}")
    print(f"  Recall    : {rec:.4f}")
    print(f"  F1-Score  : {f1:.4f}")
    print(f"  ROC-AUC   : {auc:.4f}")
    print("-" * 50)
    print("\nConfusion Matrix (Rows: True, Cols: Predicted):")
    print(f"  Healthy:     TN={cm[0, 0]:3d} | FP={cm[0, 1]:3d}")
    print(f"  Parkinson's: FN={cm[1, 0]:3d} | TP={cm[1, 1]:3d}")

    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=["Healthy", "Parkinson's"]))

    print("=" * 70)
    print("CLINICAL MODALITY CONTRIBUTION (Normalized Feature Importance)")
    print("=" * 70)
    modality_breakdown = compute_modality_importance(pipeline)
    for mod, score in modality_breakdown.items():
        bar = "#" * int(score * 40)
        print(f"  {mod:20s} [{score:6.1%}] {bar}")

    print(f"\n[5/5] Serializing model bundle artifact to '{output_path}'...")
    bundle = {
        "pipeline": pipeline,
        "feature_columns": FEATURE_COLUMNS,
        "modality_map": MODALITY_MAP,
        "model_version": "rf-v1.0-multimodal",
        "metrics": {"precision": prec, "recall": rec, "f1": f1, "roc_auc": auc},
    }
    joblib.dump(bundle, output_path)
    print(f"      Model artifact successfully serialized ({output_path}).\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train Parkinson's multimodal screening model.")
    parser.add_argument("--dsn", default="postgresql://postgres@localhost:5432/parkinsons", help="PostgreSQL DSN")
    parser.add_argument("--output", default="parkinsons_multimodal_model.joblib", help="Output artifact path")
    parser.add_argument("--test-size", type=float, default=0.2, help="Fraction of test set")
    args = parser.parse_args()
    train_and_evaluate(args.dsn, args.output, args.test_size)
