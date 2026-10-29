-- =============================================================================
-- PARKINSON'S DISEASE MULTIMODAL SCREENING DATABASE ARCHITECTURE
-- Engine: PostgreSQL 14+ / 16+ | Normal Form: Third Normal Form (3NF)
-- Academic DBMS Capstone Project & Clinical ML Screening Pipeline
-- =============================================================================
-- Architectural Rationale & Design Manifesto:
-- 1. Normalization & Decomposition (3NF):
--    - 1NF: All attributes are atomic (scalar metrics, strongly typed floats/integers).
--    - 2NF: No partial key dependencies; each entity has a surrogate or natural primary key.
--    - 3NF: No transitive dependencies; assessment details belong to modality-specific
--           tables rather than redundant denormalized patient rows.
-- 2. Hybrid Relational / Semi-Structured Modeling:
--    - High-frequency stroke streams and structured clinical records are strictly normalized.
--    - ML inference audit logs leverage JSONB for flexible, per-modality risk breakdowns.
-- 3. Referential Integrity & Clinical Safety:
--    - Strict FOREIGN KEY relationships with ON DELETE CASCADE to satisfy GDPR/HIPAA
--      right-to-erasure workflows without orphaned stream records.
--    - Domain CHECK constraints enforce physiologically valid clinical biomarker ranges.
-- 4. High-Throughput & Analytical Indexing:
--    - Composite B-Tree indexes on (patient_id, recorded_at DESC) optimize time-series queries.
--    - Point stream indexing on (session_id, timestamp_ms) allows streaming kinematics extraction.
-- =============================================================================

DROP SCHEMA IF EXISTS parkinsons CASCADE;
CREATE SCHEMA parkinsons;
SET search_path TO parkinsons, public;

-- =============================================================================
-- 1. PATIENTS (Root Demographic Entity)
-- =============================================================================
CREATE TABLE patients (
    patient_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mrn                 VARCHAR(32) UNIQUE NOT NULL,                       -- Medical Record Number
    age                 SMALLINT NOT NULL CHECK (age BETWEEN 40 AND 90),
    gender              VARCHAR(16) NOT NULL CHECK (gender IN ('male', 'female', 'other', 'undisclosed')),
    dominant_hand       VARCHAR(16) NOT NULL CHECK (dominant_hand IN ('left', 'right', 'ambidextrous')),
    diagnosis_label     VARCHAR(16) CHECK (diagnosis_label IN ('parkinsons', 'healthy', 'unknown')) DEFAULT 'unknown',
    clinical_notes      TEXT,
    enrolled_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_patients_diagnosis ON patients (diagnosis_label);
CREATE INDEX idx_patients_age       ON patients (age);
CREATE INDEX idx_patients_mrn       ON patients (mrn);

-- =============================================================================
-- 2. CLINICAL_SCREENINGS (Non-Motor Prodromal Biomarkers)
-- Captures non-motor prodromal signs often preceding motor onset by 5-10 years:
-- olfactory loss (UPSIT), REM sleep behavior disorder (RBD), autonomic constipation,
-- and mood screening (PHQ-9).
-- =============================================================================
CREATE TABLE clinical_screenings (
    screening_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id            UUID NOT NULL REFERENCES patients (patient_id) ON DELETE CASCADE,
    upsit_smell_score     SMALLINT CHECK (upsit_smell_score BETWEEN 0 AND 40),      -- 0-18: severe hyposmia, 34-40: normosmia
    rbd_sleep_flag        BOOLEAN NOT NULL DEFAULT FALSE,                           -- REM Sleep Behavior Disorder
    constipation_scale    SMALLINT CHECK (constipation_scale BETWEEN 0 AND 10),     -- Rome IV bowel difficulty score
    phq9_depression_score SMALLINT CHECK (phq9_depression_score BETWEEN 0 AND 27), -- Patient Health Questionnaire-9
    recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_screenings_patient_time ON clinical_screenings (patient_id, recorded_at DESC);

-- =============================================================================
-- 3. DRAWING_SESSIONS & STROKE_TELEMETRY (Digital Tablet Kinematics Stream)
-- Captures Archimedean spiral and handwriting telemetry for motor assessment.
-- Designed with a 1:N session-to-point architecture suitable for partitioning.
-- =============================================================================
CREATE TABLE drawing_sessions (
    session_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id            UUID NOT NULL REFERENCES patients (patient_id) ON DELETE CASCADE,
    task_type             VARCHAR(24) NOT NULL CHECK (task_type IN ('archimedean_spiral', 'handwriting_sentence', 'meander')),
    device_model          VARCHAR(64),
    sampling_hz           SMALLINT NOT NULL DEFAULT 120 CHECK (sampling_hz > 0),
    started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_ms           INTEGER CHECK (duration_ms > 0)
);

CREATE INDEX idx_drawing_sessions_patient ON drawing_sessions (patient_id, started_at DESC);

-- High-frequency telemetry stream (100–200 Hz digitizer samples)
CREATE TABLE stroke_telemetry (
    point_id              BIGSERIAL PRIMARY KEY,
    session_id            UUID NOT NULL REFERENCES drawing_sessions (session_id) ON DELETE CASCADE,
    x                     DOUBLE PRECISION NOT NULL,
    y                     DOUBLE PRECISION NOT NULL,
    pressure              REAL CHECK (pressure BETWEEN 0 AND 1),                    -- Normalized stylus pressure
    tilt_x                REAL,                                                     -- Stylus tilt angle (-90 to +90 deg)
    tilt_y                REAL,
    velocity              DOUBLE PRECISION,                                         -- Derived instantaneous velocity (px/ms)
    jerk                  DOUBLE PRECISION,                                         -- Derivative of acceleration (erraticness)
    in_air                BOOLEAN NOT NULL DEFAULT FALSE,                           -- Pen hovering without screen contact
    timestamp_ms          BIGINT NOT NULL CHECK (timestamp_ms >= 0)
);

-- Composite B-tree index enabling lightning-fast ordered telemetry extractions
CREATE INDEX idx_stroke_session_time ON stroke_telemetry (session_id, timestamp_ms ASC);

-- =============================================================================
-- 4. VOICE_ASSESSMENTS (Acoustic Phonatory Biomarkers)
-- Sustained vowel phonation (/a/) parameters analyzing hypokinetic dysarthria:
-- fundamental frequency perturbation (jitter), amplitude perturbation (shimmer),
-- and noise harmonics (HNR).
-- =============================================================================
CREATE TABLE voice_assessments (
    voice_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id            UUID NOT NULL REFERENCES patients (patient_id) ON DELETE CASCADE,
    mean_f0_hz            REAL CHECK (mean_f0_hz > 0),                              -- Fundamental vocal frequency
    jitter_percent        REAL CHECK (jitter_percent >= 0),                         -- Frequency perturbation ratio
    shimmer_local         REAL CHECK (shimmer_local >= 0),                          -- Amplitude perturbation ratio
    hnr_db                REAL,                                                     -- Harmonics-to-Noise Ratio (dB)
    audio_recording_url   TEXT,                                                     -- Secure object storage pointer
    recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_voice_patient_time ON voice_assessments (patient_id, recorded_at DESC);

-- =============================================================================
-- 5. GAIT_ASSESSMENTS (3-Axis IMU / Wearable Accelerometer Telemetry)
-- Mobility analysis quantifying bradykinesia, festination, and asymmetric arm swing.
-- =============================================================================
CREATE TABLE gait_assessments (
    gait_id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id                UUID NOT NULL REFERENCES patients (patient_id) ON DELETE CASCADE,
    step_cadence_spm          REAL CHECK (step_cadence_spm > 0),                    -- Steps per minute
    stride_time_variance      REAL CHECK (stride_time_variance >= 0),               -- Gait variability index
    arm_swing_asymmetry_index REAL CHECK (arm_swing_asymmetry_index >= 0),          -- Left vs. Right arm swing delta
    walk_duration_s           REAL CHECK (walk_duration_s > 0),                     -- Timed 10-meter walk test duration
    recorded_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_gait_patient_time ON gait_assessments (patient_id, recorded_at DESC);

-- =============================================================================
-- 6. MULTIMODAL_FEATURE_STORE (Pre-aggregated Model-Ready Vectors)
-- High-performance analytical table storing materialized multimodal feature snapshots.
-- Directly queried by the ML training and inference pipeline.
-- =============================================================================
CREATE TABLE multimodal_feature_store (
    feature_vector_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id                UUID NOT NULL REFERENCES patients (patient_id) ON DELETE CASCADE,
    -- Kinematic Aggregates (Tablet)
    avg_velocity              DOUBLE PRECISION,
    avg_jerk                  DOUBLE PRECISION,
    pressure_std              DOUBLE PRECISION,
    in_air_time_ratio         DOUBLE PRECISION,
    -- Acoustic Aggregates (Voice)
    mean_f0_hz                REAL,
    jitter_percent            REAL,
    shimmer_local             REAL,
    hnr_db                    REAL,
    -- Gait Aggregates (Mobility)
    step_cadence_spm          REAL,
    stride_time_variance      REAL,
    arm_swing_asymmetry_index REAL,
    -- Non-Motor Prodromal Aggregates
    upsit_smell_score         SMALLINT,
    rbd_sleep_flag            BOOLEAN,
    -- Supervised Training Ground Truth
    diagnosis_label           VARCHAR(16) CHECK (diagnosis_label IN ('parkinsons', 'healthy', 'unknown')),
    generated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (patient_id, generated_at)
);

CREATE INDEX idx_feature_store_patient ON multimodal_feature_store (patient_id);
CREATE INDEX idx_feature_store_label   ON multimodal_feature_store (diagnosis_label);
CREATE INDEX idx_feature_store_time    ON multimodal_feature_store (generated_at DESC);

-- =============================================================================
-- 7. MODEL_INFERENCE_AUDIT (MLOps Compliance & Traceability Log)
-- Implements full auditability for every prediction made by the FastAPI service.
-- Stored with semi-structured JSONB for explainable per-modality risk breakdowns.
-- =============================================================================
CREATE TABLE model_inference_audit (
    prediction_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id                UUID REFERENCES patients (patient_id) ON DELETE CASCADE, -- Nullable for anonymous one-off screenings
    feature_vector_id         UUID REFERENCES multimodal_feature_store (feature_vector_id) ON DELETE SET NULL,
    model_version             VARCHAR(32) NOT NULL,
    predicted_label           VARCHAR(16) NOT NULL CHECK (predicted_label IN ('parkinsons', 'healthy')),
    probability_score         REAL NOT NULL CHECK (probability_score BETWEEN 0 AND 1),
    modality_risk_breakdown   JSONB,                                                  -- e.g. {"tablet_kinematics": 0.72, "gait": 0.15, ...}
    inference_timestamp       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_patient_time  ON model_inference_audit (patient_id, inference_timestamp DESC);
CREATE INDEX idx_audit_model_version ON model_inference_audit (model_version);
CREATE INDEX idx_audit_risk_gin      ON model_inference_audit USING GIN (modality_risk_breakdown);

-- =============================================================================
-- 8. TRIGGER FUNCTION: Automatic Timestamp Maintenance
-- =============================================================================
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_patients_touch
BEFORE UPDATE ON patients
FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- =============================================================================
-- 9. ANALYTICAL SQL VIEW: v_patient_multimodal_matrix
-- Demonstrates advanced SQL window functions (ROW_NUMBER() OVER PARTITION BY)
-- to retrieve the most recent assessment per modality alongside aggregated
-- high-frequency tablet kinematic stream statistics.
-- =============================================================================
CREATE OR REPLACE VIEW v_patient_multimodal_matrix AS
WITH latest_voice AS (
    SELECT
        voice_id, patient_id, mean_f0_hz, jitter_percent, shimmer_local, hnr_db, recorded_at,
        ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY recorded_at DESC) AS rn
    FROM voice_assessments
),
latest_gait AS (
    SELECT
        gait_id, patient_id, step_cadence_spm, stride_time_variance, arm_swing_asymmetry_index, recorded_at,
        ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY recorded_at DESC) AS rn
    FROM gait_assessments
),
latest_screening AS (
    SELECT
        screening_id, patient_id, upsit_smell_score, rbd_sleep_flag, constipation_scale, phq9_depression_score, recorded_at,
        ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY recorded_at DESC) AS rn
    FROM clinical_screenings
),
drawing_agg AS (
    SELECT
        ds.patient_id,
        AVG(st.velocity)                               AS avg_velocity,
        AVG(st.jerk)                                   AS avg_jerk,
        STDDEV_POP(st.pressure)                         AS pressure_std,
        AVG(CASE WHEN st.in_air THEN 1.0 ELSE 0.0 END) AS in_air_time_ratio
    FROM drawing_sessions ds
    JOIN stroke_telemetry st ON st.session_id = ds.session_id
    GROUP BY ds.patient_id
)
SELECT
    p.patient_id,
    p.mrn,
    p.age,
    p.gender,
    p.dominant_hand,
    p.diagnosis_label,
    da.avg_velocity,
    da.avg_jerk,
    da.pressure_std,
    da.in_air_time_ratio,
    lv.mean_f0_hz,
    lv.jitter_percent,
    lv.shimmer_local,
    lv.hnr_db,
    lg.step_cadence_spm,
    lg.stride_time_variance,
    lg.arm_swing_asymmetry_index,
    ls.upsit_smell_score,
    ls.rbd_sleep_flag,
    ls.constipation_scale,
    ls.phq9_depression_score
FROM patients p
LEFT JOIN drawing_agg      da ON da.patient_id = p.patient_id
LEFT JOIN latest_voice     lv ON lv.patient_id = p.patient_id AND lv.rn = 1
LEFT JOIN latest_gait      lg ON lg.patient_id = p.patient_id AND lg.rn = 1
LEFT JOIN latest_screening ls ON ls.patient_id = p.patient_id AND ls.rn = 1;

-- =============================================================================
-- 10. STORED PROCEDURE: refresh_feature_vector(UUID)
-- Materializes and persists the analytical view's calculated vector into the
-- feature store for a target patient.
-- =============================================================================
CREATE OR REPLACE FUNCTION refresh_feature_vector(p_patient_id UUID)
RETURNS UUID AS $$
DECLARE
    v_feature_id UUID;
BEGIN
    INSERT INTO multimodal_feature_store (
        patient_id, avg_velocity, avg_jerk, pressure_std, in_air_time_ratio,
        mean_f0_hz, jitter_percent, shimmer_local, hnr_db,
        step_cadence_spm, stride_time_variance, arm_swing_asymmetry_index,
        upsit_smell_score, rbd_sleep_flag, diagnosis_label
    )
    SELECT
        patient_id, avg_velocity, avg_jerk, pressure_std, in_air_time_ratio,
        mean_f0_hz, jitter_percent, shimmer_local, hnr_db,
        step_cadence_spm, stride_time_variance, arm_swing_asymmetry_index,
        upsit_smell_score, rbd_sleep_flag, diagnosis_label
    FROM v_patient_multimodal_matrix
    WHERE patient_id = p_patient_id
    RETURNING feature_vector_id INTO v_feature_id;

    RETURN v_feature_id;
END;
$$ LANGUAGE plpgsql;
