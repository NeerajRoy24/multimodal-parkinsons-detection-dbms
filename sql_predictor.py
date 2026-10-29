"""
sql_predictor.py
================
Enterprise SQL Ingestion, Normalization, and Multi-Modal Inference Engine
for Parkinson's Disease Screening.

Supports:
1. Direct SQL execution against PostgreSQL or SQLite.
2. Arbitrary SQL script execution (CREATE TABLE + INSERT statements).
3. SQLite database file inspection (.db, .sqlite).
4. CSV/Excel file loading into temporary queryable SQL tables.
5. Intelligent fuzzy column matching & clinical biomarker normalization.
6. Scikit-learn Random Forest batch inference with modality risk attribution.
"""

import os
import re
import sqlite3
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

# Standard 13 feature columns expected by parkinsons_multimodal_model.joblib
EXPECTED_FEATURES: List[str] = [
    "avg_velocity",
    "avg_jerk",
    "pressure_std",
    "in_air_time_ratio",
    "mean_f0_hz",
    "jitter_percent",
    "shimmer_local",
    "hnr_db",
    "step_cadence_spm",
    "stride_time_variance",
    "arm_swing_asymmetry_index",
    "upsit_smell_score",
    "rbd_sleep_flag",
]

# Modality category mappings
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

MODALITY_LABELS: Dict[str, str] = {
    "tablet_kinematics": "Motor / Tablet Kinematics",
    "voice": "Voice Acoustics (Dysarthria)",
    "gait": "Gait & Locomotion",
    "non_motor": "Non-Motor Prodromal",
}

# Fuzzy aliases for intelligent dataset ingestion
COLUMN_ALIASES: Dict[str, List[str]] = {
    "mean_f0_hz": [
        "meanf0hz", "meanf0", "f0", "fo", "mdvpfohz", "mdvpf0hz", "pitch", "f0hz",
        "fundamentalpitch", "fundamentalfrequency", "vocalpitch"
    ],
    "jitter_percent": [
        "jitterpercent", "jitter", "mdvpjitter", "mdvpjitterpercent", "jitterabs",
        "jitterddp", "jitterppq", "jitterlocal", "jitterpct"
    ],
    "shimmer_local": [
        "shimmerlocal", "shimmer", "mdvpshimmer", "shimmerpercent", "mdvpshimmerdb",
        "shimmerapq", "shimmerdda", "shimmerlocaldb"
    ],
    "hnr_db": [
        "hnrdb", "hnr", "harmonicstonoise", "hnrratio", "noiseharmonics"
    ],
    "avg_velocity": [
        "avgvelocity", "velocity", "meanvelocity", "drawingvelocity", "speed",
        "penspeed", "stroklevelocity", "strokespeed"
    ],
    "avg_jerk": [
        "avgjerk", "jerk", "meanjerk", "tremorjerk", "penjerk", "accelerationjerk"
    ],
    "pressure_std": [
        "pressurestd", "pressure", "penpressurestd", "pressurevariance",
        "penpressure", "gripforce", "pressuredev"
    ],
    "in_air_time_ratio": [
        "inairtimeratio", "inair", "inairratio", "hoverratio", "airtime",
        "hesitationratio", "inairpercent"
    ],
    "step_cadence_spm": [
        "stepcadencespm", "cadence", "stepcadence", "stepsperminute",
        "cadencespm", "walkingcadence"
    ],
    "stride_time_variance": [
        "stridetimevariance", "stridevariance", "stridecv", "stridetimevar",
        "stridevariability", "gaitvariance"
    ],
    "arm_swing_asymmetry_index": [
        "armswingasymmetryindex", "armswingasymmetry", "armswing", "asymmetryindex",
        "armasymmetry", "bilateralasymmetry", "asymmetry"
    ],
    "upsit_smell_score": [
        "upsitsmellscore", "upsit", "smellscore", "olfactoryscore",
        "smellidentification", "upsittotal"
    ],
    "rbd_sleep_flag": [
        "rbdsleepflag", "rbd", "sleepflag", "rbdflag", "remsleepbehavior",
        "remsleepdisorder", "sleepdisorder"
    ],
}

ID_ALIASES = [
    "patientid", "mrn", "id", "subjectid", "subject", "name",
    "patientname", "recordid", "patientcode", "sampleid"
]

DEMO_ALIASES = {
    "age": ["age", "patientage", "years"],
    "gender": ["gender", "sex"],
    "notes": ["clinicalnotes", "notes", "diagnosis", "status"],
}


def clean_identifier(name: str) -> str:
    """Removes non-alphanumeric characters and converts to lowercase."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def map_dataset_columns(df_columns: List[str]) -> Tuple[Dict[str, str], Optional[str], Dict[str, str]]:
    """
    Intelligently maps raw SQL dataset columns to standard model features,
    patient identifier, and demographic columns.
    """
    clean_to_raw = {clean_identifier(c): c for c in df_columns}
    
    # 1. Feature mapping
    feature_mapping: Dict[str, str] = {}
    for target_feature, alias_list in COLUMN_ALIASES.items():
        matched_raw = None
        for alias in alias_list:
            if alias in clean_to_raw:
                matched_raw = clean_to_raw[alias]
                break
        if not matched_raw:
            # Fallback substring matching
            for clean_col, raw_col in clean_to_raw.items():
                if any(alias in clean_col for alias in alias_list if len(alias) >= 4):
                    matched_raw = raw_col
                    break
        if matched_raw:
            feature_mapping[target_feature] = matched_raw

    # 2. Patient ID mapping
    id_col = None
    for id_alias in ID_ALIASES:
        if id_alias in clean_to_raw:
            id_col = clean_to_raw[id_alias]
            break
    if not id_col:
        for clean_col, raw_col in clean_to_raw.items():
            if "patient" in clean_col or "subject" in clean_col or clean_col == "id":
                id_col = raw_col
                break

    # 3. Demographics mapping
    demo_mapping: Dict[str, str] = {}
    for demo_key, demo_aliases in DEMO_ALIASES.items():
        for alias in demo_aliases:
            if alias in clean_to_raw:
                demo_mapping[demo_key] = clean_to_raw[alias]
                break

    return feature_mapping, id_col, demo_mapping


def run_sql_query(query: str, dsn_or_path: str = "sample_patients.db", db_type: str = "sqlite") -> pd.DataFrame:
    """Executes a SQL query against PostgreSQL or SQLite."""
    query = query.strip()
    if not query.lower().startswith("select") and not query.lower().startswith("with"):
        raise ValueError("Only read-only SELECT or WITH SQL queries are permitted in this endpoint.")

    if db_type == "postgres" or (dsn_or_path.startswith("postgresql://") or dsn_or_path.startswith("postgres://")):
        with psycopg2.connect(dsn_or_path) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                records = cur.fetchall()
                cols = [desc[0] for desc in cur.description]
                return pd.DataFrame(records, columns=cols)
    else:
        # SQLite
        if not os.path.exists(dsn_or_path) and dsn_or_path != ":memory:":
            raise FileNotFoundError(f"Database file '{dsn_or_path}' does not exist.")
        conn = sqlite3.connect(dsn_or_path)
        try:
            df = pd.read_sql_query(query, conn)
            return df
        finally:
            conn.close()


def execute_sql_dump(sql_text: str) -> sqlite3.Connection:
    """
    Executes arbitrary multi-statement SQL text (DDL + DML) in an in-memory
    or temporary SQLite database and returns the active connection.
    """
    conn = sqlite3.connect(":memory:")
    # Clean SQL comments and dialect differences if any
    cleaned_sql = re.sub(r"/\*.*?\*/", "", sql_text, flags=re.DOTALL)
    conn.executescript(cleaned_sql)
    return conn


def list_tables_in_db(dsn_or_path: str = "sample_patients.db", db_type: str = "sqlite") -> List[Dict[str, Any]]:
    """Returns list of tables and column metadata from the database."""
    tables_meta = []
    if db_type == "postgres" or dsn_or_path.startswith("postgresql://"):
        try:
            with psycopg2.connect(dsn_or_path) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT table_name, table_schema
                        FROM information_schema.tables
                        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                        ORDER BY table_name;
                    """)
                    rows = cur.fetchall()
                    for t_name, t_schema in rows:
                        cur.execute(f"SELECT COUNT(*) FROM {t_schema}.{t_name};")
                        cnt = cur.fetchone()[0]
                        tables_meta.append({"table_name": f"{t_schema}.{t_name}", "rows": cnt})
        except Exception as e:
            tables_meta.append({"error": str(e)})
    else:
        if os.path.exists(dsn_or_path):
            conn = sqlite3.connect(dsn_or_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
                tables = [r[0] for r in cursor.fetchall()]
                for t in tables:
                    try:
                        cursor.execute(f"SELECT COUNT(*) FROM [{t}];")
                        cnt = cursor.fetchone()[0]
                    except Exception:
                        cnt = 0
                    tables_meta.append({"table_name": t, "rows": cnt})
            finally:
                conn.close()
    return tables_meta


def detect_parkinsons_from_df(df: pd.DataFrame, model_bundle: Dict[str, Any]) -> Dict[str, Any]:
    """
    Core detection engine:
    1. Analyzes schema and maps columns to clinical features.
    2. Constructs matrix X and aligns with model's expected layout.
    3. Executes Random Forest inference and probability calibration.
    4. Decomposes per-modality risk contributions.
    5. Returns patient-level diagnoses and cohort-level analytics.
    """
    if df.empty:
        return {
            "success": False,
            "error": "Dataset is empty. 0 rows returned from SQL query.",
            "total_patients": 0,
            "records": [],
            "summary": {},
        }

    pipeline = model_bundle["pipeline"]
    feature_cols: List[str] = model_bundle["feature_columns"]
    modality_map: Dict[str, str] = model_bundle.get("modality_map", MODALITY_MAP)

    feature_mapping, id_col, demo_mapping = map_dataset_columns(list(df.columns))

    # Construct feature matrix X
    X = pd.DataFrame(index=df.index)
    for feat in feature_cols:
        if feat in feature_mapping:
            raw_col = feature_mapping[feat]
            # Convert boolean or strings to numeric if necessary
            if feat == "rbd_sleep_flag":
                X[feat] = df[raw_col].apply(lambda v: 1.0 if v in (True, 1, "1", "true", "True", "yes", "Y") else 0.0)
            else:
                X[feat] = pd.to_numeric(df[raw_col], errors="coerce")
        else:
            X[feat] = np.nan

    # Predict probabilities
    try:
        probabilities = pipeline.predict_proba(X)[:, 1]
    except Exception as e:
        raise RuntimeError(f"Model prediction pipeline failed: {e}")

    # Extract feature importances for modality attribution
    rf = pipeline.named_steps.get("classifier")
    importances = rf.feature_importances_ if rf and hasattr(rf, "feature_importances_") else np.ones(len(feature_cols))
    
    modality_weights: Dict[str, float] = {}
    available_feature_weight = 0.0
    for feat, imp in zip(feature_cols, importances):
        mod = modality_map.get(feat, "other")
        modality_weights[mod] = modality_weights.get(mod, 0.0) + float(imp)
        if feat in feature_mapping:
            available_feature_weight += float(imp)

    total_w = sum(modality_weights.values()) or 1.0
    # Calibration factor for datasets with only a subset of modalities (e.g. voice-only or gait-only)
    is_subset_modality = available_feature_weight < 0.85 and available_feature_weight > 0.05
    scale_factor = (1.0 / available_feature_weight) if is_subset_modality else 1.0

    # Build patient result records
    records = []
    positive_count = 0
    negative_count = 0

    for idx, row in df.iterrows():
        raw_prob = float(probabilities[idx])
        # Calibrated probability accounts for partial modality datasets
        calibrated_prob = min(1.0, max(0.0, raw_prob * scale_factor)) if is_subset_modality else raw_prob
        
        # Diagnosis classification threshold at 50%
        is_positive = calibrated_prob >= 0.50
        predicted_label = "Positive" if is_positive else "Negative"
        if is_positive:
            positive_count += 1
        else:
            negative_count += 1

        # Patient Identifier
        patient_id = str(row[id_col]) if id_col and pd.notna(row[id_col]) else f"PT-{1000 + int(idx) + 1}"
        
        # Demographics
        age_val = row.get(demo_mapping.get("age")) if "age" in demo_mapping else None
        gender_val = row.get(demo_mapping.get("gender")) if "gender" in demo_mapping else None
        name_val = row.get(demo_mapping.get("patient_name")) or row.get("name") or row.get("patient_name") or None

        # Modality Risk Breakdown
        modality_breakdown = {}
        for mod, weight in modality_weights.items():
            modality_breakdown[mod] = round((weight / total_w) * calibrated_prob * 100, 1)

        # Primary risk driver
        dominant_modality = max(modality_breakdown.items(), key=lambda x: x[1])[0]

        # Clinical interpretation
        if calibrated_prob >= 0.75:
            interpretation = "High risk of Parkinsonian pathology. Significant biomarker deviations detected across active clinical modalities; neurologist evaluation recommended."
            severity_badge = "High"
        elif calibrated_prob >= 0.50:
            interpretation = "Moderate risk. Prodromal motor or phonatory abnormalities detected; close monitoring and specialist consultation advised."
            severity_badge = "Moderate"
        elif calibrated_prob >= 0.25:
            interpretation = "Mild/Borderline risk. Sub-threshold biomarker variance observed; periodic longitudinal follow-up suggested."
            severity_badge = "Low"
        else:
            interpretation = "Healthy control profile. Assessed biomarker indicators remain within physiological normal limits."
            severity_badge = "Normal"

        records.append({
            "index": int(idx) + 1,
            "patient_id": patient_id,
            "name": str(name_val) if pd.notna(name_val) else f"Patient {idx+1}",
            "age": int(age_val) if pd.notna(age_val) and str(age_val).isdigit() else age_val,
            "gender": str(gender_val) if pd.notna(gender_val) else "—",
            "prediction": predicted_label,
            "is_positive": bool(is_positive),
            "probability": round(calibrated_prob, 4),
            "raw_probability": round(raw_prob, 4),
            "confidence_pct": round(calibrated_prob * 100, 1) if is_positive else round((1.0 - calibrated_prob) * 100, 1),
            "severity_badge": severity_badge,
            "dominant_modality": MODALITY_LABELS.get(dominant_modality, dominant_modality),
            "modality_breakdown": modality_breakdown,
            "clinical_interpretation": interpretation,
            "raw_features": {
                k: round(float(v), 4) if pd.notna(v) and isinstance(v, (int, float, np.number)) else (str(v) if pd.notna(v) else None)
                for k, v in row.items()
            },
        })

    total_patients = len(records)
    avg_probability = float(np.mean([r["probability"] for r in records])) if total_patients > 0 else 0.0

    # Risk distribution bins
    probs_list = [r["probability"] for r in records]
    risk_bins = {
        "0-20% (Normal)": int(sum(1 for p in probs_list if p < 0.20)),
        "20-40% (Low)": int(sum(1 for p in probs_list if 0.20 <= p < 0.40)),
        "40-60% (Borderline)": int(sum(1 for p in probs_list if 0.40 <= p < 0.60)),
        "60-80% (Moderate)": int(sum(1 for p in probs_list if 0.60 <= p < 0.80)),
        "80-100% (High Risk)": int(sum(1 for p in probs_list if p >= 0.80)),
    }

    # Available mapped features summary
    mapped_features_list = [
        {"standard_feature": std, "source_column": src, "modality": MODALITY_LABELS.get(modality_map.get(std, ""), "")}
        for std, src in feature_mapping.items()
    ]

    return {
        "success": True,
        "total_patients": total_patients,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "positive_rate_pct": round((positive_count / total_patients) * 100, 1) if total_patients > 0 else 0,
        "avg_probability": round(avg_probability, 4),
        "mapped_features": mapped_features_list,
        "unmapped_features_count": len(feature_cols) - len(feature_mapping),
        "risk_distribution": risk_bins,
        "records": records,
    }
