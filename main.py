"""
main.py
=======
FastAPI Real-Time Multimodal Clinical Inference & Ingestion Service.

Endpoints:
  POST /patients/ingest      - Ingests patient demographics & modality assessments into 3NF schema,
                               atomically materializing the feature store vector.
  POST /predict/multimodal   - Executes real-time inference using either raw metrics or patient_id,
                               calculates per-modality clinical risk breakdown, and logs full audit trail.
  GET  /health               - Service health and model artifact readiness probe.
  GET  /audit/recent         - Retrieves recent model predictions from model_inference_audit.

Run:
    uvicorn main:app --reload --port 8000
"""

import io
import os
import tempfile
import uuid
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import joblib
import pandas as pd
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query, status, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from sql_predictor import (
    run_sql_query,
    execute_sql_dump,
    list_tables_in_db,
    detect_parkinsons_from_df,
)


DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost:5432/parkinsons")
MODEL_PATH = os.environ.get("MODEL_PATH", "parkinsons_multimodal_model.joblib")
MODEL_VERSION = "rf-v1.0-multimodal"

model_state: Dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: loads the serialized ML model artifact upon application start."""
    try:
        model_state["artifact"] = joblib.load(MODEL_PATH)
        print(f"[Lifespan] Loaded model bundle from '{MODEL_PATH}' successfully.")
    except Exception as e:
        model_state["artifact"] = None
        print(f"[Lifespan Warning] Could not load model bundle from '{MODEL_PATH}': {e}. "
              f"Run train_pipeline.py to create the model artifact.")
    yield
    model_state.clear()


app = FastAPI(
    title="Parkinson's Disease Multimodal Screening API",
    description=(
        "Production-grade Clinical Decision Support Service for early Parkinson's screening. "
        "Integrates high-frequency tablet kinematics, voice acoustics, gait telemetry, and non-motor indicators."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


def get_db_connection():
    """Returns an active PostgreSQL connection."""
    return psycopg2.connect(DATABASE_URL)


# --------------------------------------------------------------------------- #
# Pydantic Request & Response Schemas
# --------------------------------------------------------------------------- #

class ClinicalScreeningIn(BaseModel):
    upsit_smell_score: Optional[int] = Field(None, ge=0, le=40, description="University of Pennsylvania Smell Identification Test (0-40)")
    rbd_sleep_flag: bool = Field(False, description="REM Sleep Behavior Disorder screening flag")
    constipation_scale: Optional[int] = Field(None, ge=0, le=10, description="Autonomic constipation severity scale (0-10)")
    phq9_depression_score: Optional[int] = Field(None, ge=0, le=27, description="PHQ-9 Depression questionnaire score (0-27)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "upsit_smell_score": 16,
                "rbd_sleep_flag": True,
                "constipation_scale": 6,
                "phq9_depression_score": 10,
            }
        }
    }


class VoiceAssessmentIn(BaseModel):
    mean_f0_hz: Optional[float] = Field(None, gt=0, description="Mean fundamental vocal frequency in Hz")
    jitter_percent: Optional[float] = Field(None, ge=0, description="Local frequency perturbation ratio (%)")
    shimmer_local: Optional[float] = Field(None, ge=0, description="Local amplitude perturbation ratio (%)")
    hnr_db: Optional[float] = Field(None, description="Harmonics-to-Noise ratio in dB")
    audio_recording_url: Optional[str] = Field(None, description="Object storage S3/GCS audio file URL")

    model_config = {
        "json_schema_extra": {
            "example": {
                "mean_f0_hz": 142.5,
                "jitter_percent": 1.95,
                "shimmer_local": 6.80,
                "hnr_db": 13.2,
                "audio_recording_url": "s3://clinical-audio/patient-sample.wav",
            }
        }
    }


class GaitAssessmentIn(BaseModel):
    step_cadence_spm: Optional[float] = Field(None, gt=0, description="Walking cadence in steps per minute")
    stride_time_variance: Optional[float] = Field(None, ge=0, description="Stride time coefficient of variation")
    arm_swing_asymmetry_index: Optional[float] = Field(None, ge=0, description="Bilateral arm swing asymmetry index (0-1)")
    walk_duration_s: Optional[float] = Field(None, gt=0, description="Timed walk test duration in seconds")

    model_config = {
        "json_schema_extra": {
            "example": {
                "step_cadence_spm": 92.0,
                "stride_time_variance": 0.055,
                "arm_swing_asymmetry_index": 0.36,
                "walk_duration_s": 60.0,
            }
        }
    }


class PatientIngestRequest(BaseModel):
    mrn: str = Field(..., description="Unique Medical Record Number (e.g. MRN-1049281)")
    age: int = Field(..., ge=40, le=90, description="Patient age in years (40-90)")
    gender: str = Field(..., description="Biological sex ('male', 'female', 'other', 'undisclosed')")
    dominant_hand: str = Field(..., description="Dominant writing hand ('right', 'left', 'ambidextrous')")
    clinical_notes: Optional[str] = Field(None, description="Clinical provider observations")
    clinical_screening: Optional[ClinicalScreeningIn] = None
    voice_assessment: Optional[VoiceAssessmentIn] = None
    gait_assessment: Optional[GaitAssessmentIn] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "mrn": "MRN-5829104",
                "age": 68,
                "gender": "male",
                "dominant_hand": "right",
                "clinical_notes": "Referred following persistent right-hand resting tremor and loss of smell.",
                "clinical_screening": {
                    "upsit_smell_score": 15,
                    "rbd_sleep_flag": True,
                    "constipation_scale": 7,
                    "phq9_depression_score": 9,
                },
                "voice_assessment": {
                    "mean_f0_hz": 138.0,
                    "jitter_percent": 2.10,
                    "shimmer_local": 7.20,
                    "hnr_db": 11.5,
                },
                "gait_assessment": {
                    "step_cadence_spm": 88.0,
                    "stride_time_variance": 0.062,
                    "arm_swing_asymmetry_index": 0.42,
                    "walk_duration_s": 60.0,
                },
            }
        }
    }


class PatientIngestResponse(BaseModel):
    patient_id: str
    feature_vector_id: Optional[str]
    message: str


class MultimodalPredictRequest(BaseModel):
    """
    Multimodal prediction payload:
    Either supply a valid patient_id (to query the database feature store),
    OR pass raw biomarker metrics directly for real-time one-off clinical screening.
    """
    patient_id: Optional[str] = Field(None, description="UUID of existing patient in database")

    # 1. Tablet Kinematics
    avg_velocity: Optional[float] = Field(None, description="Stylus drawing velocity (px/ms)")
    avg_jerk: Optional[float] = Field(None, description="Stylus jerk rate of change")
    pressure_std: Optional[float] = Field(None, description="Stylus pressure standard deviation")
    in_air_time_ratio: Optional[float] = Field(None, description="Ratio of hovering time without surface contact")

    # 2. Voice Acoustics
    mean_f0_hz: Optional[float] = Field(None, description="Fundamental vocal pitch (Hz)")
    jitter_percent: Optional[float] = Field(None, description="Frequency perturbation (%)")
    shimmer_local: Optional[float] = Field(None, description="Amplitude perturbation (%)")
    hnr_db: Optional[float] = Field(None, description="Harmonics-to-Noise Ratio (dB)")

    # 3. Mobility & Gait
    step_cadence_spm: Optional[float] = Field(None, description="Gait cadence (steps/min)")
    stride_time_variance: Optional[float] = Field(None, description="Stride variability index")
    arm_swing_asymmetry_index: Optional[float] = Field(None, description="Arm swing asymmetry index")

    # 4. Non-Motor Clinical
    upsit_smell_score: Optional[float] = Field(None, description="UPSIT olfactory score (0-40)")
    rbd_sleep_flag: Optional[bool] = Field(None, description="REM sleep behavior disorder positive flag")

    model_config = {
        "json_schema_extra": {
            "example": {
                "avg_velocity": 1.45,
                "avg_jerk": 0.58,
                "pressure_std": 0.20,
                "in_air_time_ratio": 0.26,
                "mean_f0_hz": 139.0,
                "jitter_percent": 2.15,
                "shimmer_local": 7.10,
                "hnr_db": 12.0,
                "step_cadence_spm": 89.0,
                "stride_time_variance": 0.059,
                "arm_swing_asymmetry_index": 0.39,
                "upsit_smell_score": 16.0,
                "rbd_sleep_flag": True,
            }
        }
    }


class MultimodalPredictResponse(BaseModel):
    prediction_id: str
    patient_id: Optional[str]
    predicted_label: str
    probability_score: float
    modality_risk_breakdown: Dict[str, float]
    model_version: str
    clinical_interpretation: str


# --------------------------------------------------------------------------- #
# Endpoint Implementations
# --------------------------------------------------------------------------- #

@app.post("/patients/ingest", response_model=PatientIngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_patient(payload: PatientIngestRequest):
    """
    Transactional ingestion of a clinical patient record across normalized 3NF tables.
    Automatically calls the stored procedure 'refresh_feature_vector()' to update the feature store.
    """
    conn = get_db_connection()
    feature_vector_id = None
    patient_id = str(uuid.uuid4())

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO parkinsons, public;")

                # 1. Insert demographic root record
                cur.execute(
                    """
                    INSERT INTO patients (patient_id, mrn, age, gender, dominant_hand, clinical_notes)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (patient_id, payload.mrn, payload.age, payload.gender,
                     payload.dominant_hand, payload.clinical_notes),
                )

                # 2. Insert clinical screening (if provided)
                if payload.clinical_screening:
                    cs = payload.clinical_screening
                    cur.execute(
                        """
                        INSERT INTO clinical_screenings
                            (screening_id, patient_id, upsit_smell_score, rbd_sleep_flag,
                             constipation_scale, phq9_depression_score)
                        VALUES (%s, %s, %s, %s, %s, %s);
                        """,
                        (str(uuid.uuid4()), patient_id, cs.upsit_smell_score,
                         cs.rbd_sleep_flag, cs.constipation_scale, cs.phq9_depression_score),
                    )

                # 3. Insert voice assessment (if provided)
                if payload.voice_assessment:
                    va = payload.voice_assessment
                    cur.execute(
                        """
                        INSERT INTO voice_assessments
                            (voice_id, patient_id, mean_f0_hz, jitter_percent,
                             shimmer_local, hnr_db, audio_recording_url)
                        VALUES (%s, %s, %s, %s, %s, %s, %s);
                        """,
                        (str(uuid.uuid4()), patient_id, va.mean_f0_hz, va.jitter_percent,
                         va.shimmer_local, va.hnr_db, va.audio_recording_url),
                    )

                # 4. Insert gait assessment (if provided)
                if payload.gait_assessment:
                    ga = payload.gait_assessment
                    cur.execute(
                        """
                        INSERT INTO gait_assessments
                            (gait_id, patient_id, step_cadence_spm, stride_time_variance,
                             arm_swing_asymmetry_index, walk_duration_s)
                        VALUES (%s, %s, %s, %s, %s, %s);
                        """,
                        (str(uuid.uuid4()), patient_id, ga.step_cadence_spm,
                         ga.stride_time_variance, ga.arm_swing_asymmetry_index, ga.walk_duration_s),
                    )

                # 5. Invoke stored procedure to materialize consolidated feature vector
                cur.execute("SELECT refresh_feature_vector(%s);", (patient_id,))
                res = cur.fetchone()
                if res:
                    feature_vector_id = str(res[0])
    except psycopg2.IntegrityError as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Database integrity violation: {e.pgerror or e}")
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Internal database error: {e}")
    finally:
        conn.close()

    return PatientIngestResponse(
        patient_id=patient_id,
        feature_vector_id=feature_vector_id,
        message="Patient demographics and assessments ingested; multimodal feature vector materialized.",
    )


def fetch_feature_vector_by_patient_id(patient_id_str: str) -> Dict[str, Any]:
    """Retrieves the latest materialized multimodal feature vector from PostgreSQL."""
    try:
        uuid.UUID(str(patient_id_str))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid UUID format: '{patient_id_str}'. Please provide a valid patient UUID.",
        )

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET search_path TO parkinsons, public;")
            cur.execute(
                """
                SELECT * FROM multimodal_feature_store
                WHERE patient_id = %s
                ORDER BY generated_at DESC
                LIMIT 1;
                """,
                (patient_id_str,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"No feature store record found for patient_id: '{patient_id_str}'.",
                )
            return dict(row)
    finally:
        conn.close()


def log_inference_audit(
    patient_id: Optional[str],
    feature_vector_id: Optional[str],
    predicted_label: str,
    probability: float,
    modality_breakdown: Dict[str, float],
) -> str:
    """Logs the model inference execution into the model_inference_audit table."""
    prediction_id = str(uuid.uuid4())
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO parkinsons, public;")
                cur.execute(
                    """
                    INSERT INTO model_inference_audit (
                        prediction_id, patient_id, feature_vector_id, model_version,
                        predicted_label, probability_score, modality_risk_breakdown
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        prediction_id,
                        patient_id,
                        feature_vector_id,
                        MODEL_VERSION,
                        predicted_label,
                        float(probability),
                        psycopg2.extras.Json(modality_breakdown),
                    ),
                )
    except Exception as e:
        print(f"[Audit Warning] Failed to log inference audit: {e}")
    finally:
        conn.close()
    return prediction_id


@app.post("/predict/multimodal", response_model=MultimodalPredictResponse)
def predict_multimodal(payload: MultimodalPredictRequest):
    """
    Executes real-time multimodal inference:
    1. Loads feature vector from the database or constructs from raw input metrics.
    2. Runs inference using the fitted ML pipeline.
    3. Computes normalized per-modality risk contribution.
    4. Logs full audit record to PostgreSQL 'model_inference_audit'.
    """
    artifact = model_state.get("artifact")
    if not artifact:
        raise HTTPException(
            status_code=503,
            detail="Machine learning model not loaded. Run train_pipeline.py to create the model bundle.",
        )

    pipeline = artifact["pipeline"]
    feature_columns: List[str] = artifact["feature_columns"]
    modality_map: Dict[str, str] = artifact["modality_map"]

    feature_vector_id = None
    target_patient_id = None

    if payload.patient_id:
        target_patient_id = payload.patient_id
        db_row = fetch_feature_vector_by_patient_id(payload.patient_id)
        feature_vector_id = db_row.get("feature_vector_id")
        feature_values = {col: db_row.get(col) for col in feature_columns}
    else:
        feature_values = {col: getattr(payload, col, None) for col in feature_columns}
        if all(v is None for v in feature_values.values()):
            raise HTTPException(
                status_code=400,
                detail="Provide either a valid 'patient_id' or at least one raw assessment metric.",
            )

    # Construct feature matrix DataFrame matching pipeline expected column layout
    X = pd.DataFrame([feature_values])[feature_columns]

    try:
        probability = float(pipeline.predict_proba(X)[0, 1])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model inference failed: {e}")

    predicted_label = "parkinsons" if probability >= 0.50 else "healthy"

    # Compute modality-specific risk attribution based on feature importances
    rf_classifier = pipeline.named_steps["classifier"]
    importances = rf_classifier.feature_importances_
    modality_weights: Dict[str, float] = {}

    for feat, imp in zip(feature_columns, importances):
        mod = modality_map[feat]
        modality_weights[mod] = modality_weights.get(mod, 0.0) + float(imp)

    total_weight = sum(modality_weights.values()) or 1.0
    modality_breakdown = {
        k: round((v / total_weight) * probability, 4)
        for k, v in modality_weights.items()
    }

    # Clinical interpretation note
    if probability >= 0.75:
        interpretation = "High clinical risk. Multimodal indicators strongly suggest Parkinsonian pathology."
    elif probability >= 0.50:
        interpretation = "Moderate clinical risk. Early motor/non-motor indicators detected; specialist follow-up advised."
    elif probability >= 0.25:
        interpretation = "Low-to-moderate risk. Minor biomarker deviations; monitor longitudinally."
    else:
        interpretation = "Low clinical risk. Assessment features remain within healthy control ranges."

    # Persist prediction in audit table
    prediction_id = log_inference_audit(
        patient_id=target_patient_id,
        feature_vector_id=feature_vector_id,
        predicted_label=predicted_label,
        probability=probability,
        modality_breakdown=modality_breakdown,
    )

    return MultimodalPredictResponse(
        prediction_id=prediction_id,
        patient_id=target_patient_id,
        predicted_label=predicted_label,
        probability_score=round(probability, 4),
        modality_risk_breakdown=modality_breakdown,
        model_version=MODEL_VERSION,
        clinical_interpretation=interpretation,
    )


@app.get("/health")
def health_probe():
    """Health & Readiness probe checking database connectivity and model status."""
    db_connected = False
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        conn.close()
        db_connected = True
    except Exception:
        db_connected = False

    return {
        "status": "healthy" if db_connected else "degraded",
        "database_connected": db_connected,
        "model_loaded": model_state.get("artifact") is not None,
        "model_version": MODEL_VERSION,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/audit/recent")
def get_recent_audits(limit: int = Query(10, ge=1, le=100)):
    """Retrieves recent inference audit logs from model_inference_audit."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET search_path TO parkinsons, public;")
            cur.execute(
                """
                SELECT prediction_id, patient_id, model_version, predicted_label,
                       probability_score, modality_risk_breakdown, inference_timestamp
                FROM model_inference_audit
                ORDER BY inference_timestamp DESC
                LIMIT %s;
                """,
                (limit,),
            )
            records = cur.fetchall()
            return {"count": len(records), "audits": [dict(r) for r in records]}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Web Interface & Dynamic SQL Dataset Detection Endpoints
# --------------------------------------------------------------------------- #

class SqlQueryPayload(BaseModel):
    query: str
    db_target: Optional[str] = "sample_patients.db"
    custom_dsn: Optional[str] = None


class SqlScriptPayload(BaseModel):
    script: str
    query: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
def serve_web_interface():
    """Serves the interactive Parkinson's Disease SQL Detection Web Application."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>Error: templates/index.html not found</h2>", status_code=404)


@app.post("/api/sql/query")
def execute_sql_prediction(payload: SqlQueryPayload):
    """
    Executes a read-only SQL query against SQLite or PostgreSQL,
    intelligently maps columns, and performs Parkinson's disease detection.
    """
    artifact = model_state.get("artifact")
    if not artifact:
        raise HTTPException(
            status_code=503,
            detail="Machine learning model not loaded. Run train_pipeline.py to create the model bundle.",
        )

    db_target = payload.db_target or "sample_patients.db"
    if db_target == "custom" and payload.custom_dsn:
        target_path = payload.custom_dsn
    elif db_target == "postgres":
        target_path = DATABASE_URL
    else:
        target_path = "sample_patients.db"

    try:
        db_type = "postgres" if (target_path.startswith("postgresql://") or target_path.startswith("postgres://")) else "sqlite"
        df = run_sql_query(payload.query, dsn_or_path=target_path, db_type=db_type)
        results = detect_parkinsons_from_df(df, artifact)
        results["query_executed"] = payload.query
        results["target_database"] = target_path
        return results
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e), "success": False})


@app.post("/api/sql/upload")
async def upload_and_predict(file: UploadFile = File(...), file_type: str = Form("sql")):
    """
    Receives an uploaded dataset (.sql, .db, .sqlite, .csv, .xlsx),
    executes queries against it, and detects Parkinson's disease.
    """
    artifact = model_state.get("artifact")
    if not artifact:
        raise HTTPException(status_code=503, detail="ML model bundle not loaded.")

    content = await file.read()
    filename = file.filename or "uploaded_data"

    try:
        if file_type == "sql" or filename.endswith(".sql"):
            sql_text = content.decode("utf-8", errors="replace")
            conn = execute_sql_dump(sql_text)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            tables = [r[0] for r in cursor.fetchall()]
            if not tables:
                return JSONResponse(status_code=400, content={"error": "No tables created by the SQL script.", "success": False})
            primary_table = tables[0]
            df = pd.read_sql_query(f"SELECT * FROM [{primary_table}];", conn)
            conn.close()

        elif file_type == "db" or filename.endswith((".db", ".sqlite", ".sqlite3")):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            conn = sqlite3.connect(tmp_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            tables = [r[0] for r in cursor.fetchall()]
            if not tables:
                conn.close()
                os.unlink(tmp_path)
                return JSONResponse(status_code=400, content={"error": "No tables found in SQLite database.", "success": False})
            primary_table = tables[0]
            df = pd.read_sql_query(f"SELECT * FROM [{primary_table}];", conn)
            conn.close()
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        elif file_type == "csv" or filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))

        elif file_type == "xlsx" or filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))

        else:
            return JSONResponse(status_code=400, content={"error": f"Unsupported file type: {filename}", "success": False})

        results = detect_parkinsons_from_df(df, artifact)
        results["filename"] = filename
        results["rows_ingested"] = len(df)
        return results

    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"File processing failed: {e}", "success": False})


@app.post("/api/sql/execute-script")
def execute_sql_script_endpoint(payload: SqlScriptPayload):
    """Executes a multi-statement SQL script and detects Parkinson's on created tables."""
    artifact = model_state.get("artifact")
    if not artifact:
        raise HTTPException(status_code=503, detail="ML model bundle not loaded.")

    try:
        conn = execute_sql_dump(payload.script)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [r[0] for r in cursor.fetchall()]
        if not tables:
            return JSONResponse(status_code=400, content={"error": "Script did not create any tables.", "success": False})

        query = payload.query or f"SELECT * FROM [{tables[0]}];"
        df = pd.read_sql_query(query, conn)
        conn.close()

        results = detect_parkinsons_from_df(df, artifact)
        results["query_executed"] = query
        return results
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e), "success": False})


@app.get("/api/sql/tables")
def get_available_tables():
    """Lists available tables in the local SQLite database and PostgreSQL."""
    tables = list_tables_in_db("sample_patients.db", db_type="sqlite")
    return {"tables": tables}


@app.get("/api/sample/uci-sql")
def get_uci_sample_sql():
    """Returns sample UCI voice Parkinson's SQL script."""
    sql_path = os.path.join(os.path.dirname(__file__), "sample_uci_parkinsons.sql")
    if os.path.exists(sql_path):
        with open(sql_path, "r", encoding="utf-8") as f:
            return {"sql": f.read()}
    return {"error": "sample_uci_parkinsons.sql not found"}

