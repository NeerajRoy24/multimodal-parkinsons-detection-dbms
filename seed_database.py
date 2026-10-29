"""
seed_database.py
================
Synthetic Multimodal Clinical Data Generator for Parkinson's Disease Screening.

Generates statistically grounded synthetic cohorts across 4 clinical modalities:
  1. Digital Tablet Kinematics: Archimedean spiral telemetry (velocity, jerk, pressure, pen hesitation).
  2. Acoustic Biomarkers: Sustained phonation (/a/) metrics (F0, jitter, shimmer, HNR).
  3. Gait Telemetry: 3-axis accelerometer features (cadence, stride variance, arm swing asymmetry).
  4. Non-Motor Clinical Screening: Prodromal signs (UPSIT smell score, RBD sleep flag, constipation, PHQ-9).

Uses high-throughput batch inserts (psycopg2.extras.execute_values) and materializes
the multimodal feature store via the database stored procedure.

Usage:
    python seed_database.py --dsn "postgresql://postgres@localhost:5432/parkinsons" --n-patients 300
"""

import argparse
import random
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import numpy as np
import psycopg2
from faker import Faker
from psycopg2.extras import execute_values

fake = Faker()
RNG = np.random.default_rng(seed=42)

# --------------------------------------------------------------------------- #
# Clinically-Grounded Synthetic Profiles
# (mean, std) derived from Parkinson's disease multimodal biomarker literature:
# - Movement Disorder Society (MDS) clinical consensus
# - Archimedean spiral kinematic dynamics (San Luciano et al.)
# - Acoustic dysarthria metrics (Little et al., Praat phonation standards)
# - Quantitative mobility / wearable sensor metrics (Horak et al.)
# --------------------------------------------------------------------------- #
PROFILES: Dict[str, Dict[str, Tuple[float, float]]] = {
    "healthy": {
        # Tablet Kinematics: fast, smooth, uniform pressure, minimal hesitation
        "velocity": (2.85, 0.35),          # px/ms
        "jerk": (0.14, 0.04),              # low erraticness (smooth acceleration)
        "pressure_std": (0.04, 0.015),     # stable grip and pen pressure
        "in_air_ratio": (0.04, 0.02),      # low hesitation / hovering time
        # Voice: clear harmonic structure, low perturbation
        "f0": (155.0, 18.0),               # Hz (fundamental vocal pitch)
        "jitter": (0.42, 0.12),            # % (frequency perturbation < 1.04% norm)
        "shimmer": (2.40, 0.65),           # % (amplitude perturbation < 3.81% norm)
        "hnr": (23.5, 2.8),                # dB (harmonics-to-noise ratio > 20 dB norm)
        # Gait: stable cadence, low stride variability, symmetric arm swing
        "cadence": (114.0, 5.5),           # steps/min (normal adult cadence 100-120)
        "stride_var": (0.014, 0.004),      # stride time coefficient of variation
        "arm_asym": (0.045, 0.018),        # bilateral arm swing asymmetry ratio
        # Non-motor: intact olfaction (normosmia), normal sleep and bowel function
        "upsit": (35.0, 2.8),              # UPSIT 34-40 = normosmia
    },
    "parkinsons": {
        # Tablet Kinematics: bradykinesia, micrographic tremor, cogwheel rigidity
        "velocity": (1.55, 0.45),          # reduced velocity (bradykinesia)
        "jerk": (0.58, 0.16),              # elevated jerk (tremor, ataxia)
        "pressure_std": (0.19, 0.05),      # erratic stylus pressure fluctuations
        "in_air_ratio": (0.24, 0.07),      # freezing, hesitation hovering
        # Voice: hypokinetic dysarthria, harsh vocal tremor, breathiness
        "f0": (142.0, 28.0),               # variable pitch / vocal fatigue
        "jitter": (1.92, 0.55),            # elevated micro-perturbation (> 1.5%)
        "shimmer": (6.85, 1.75),           # elevated shimmer (> 5.0%)
        "hnr": (12.8, 3.2),                # reduced harmonic energy (hoarse voice)
        # Gait: shuffling gait, festination, asymmetric arm swing (early hallmark)
        "cadence": (91.0, 9.5),            # reduced cadence (steps/min)
        "stride_var": (0.058, 0.018),      # high stride-to-stride temporal variability
        "arm_asym": (0.38, 0.11),          # marked unilateral arm swing reduction
        # Non-motor: hyposmia (90%+ of early PD cohorts), frequent RBD, constipation
        "upsit": (17.5, 5.2),              # severe hyposmia (< 19)
    },
}


def clip(val, lo: float, hi: float):
    """Clips scalars or multi-dimensional NumPy arrays within [lo, hi]."""
    clipped = np.clip(val, lo, hi)
    return float(clipped) if np.ndim(clipped) == 0 else clipped


def gen_patient_row(diagnosis: str) -> dict:
    age = int(RNG.integers(45, 86))  # Clinical target cohort: ages 45-85
    gender = random.choice(["male", "female", "other"])
    hand = random.choices(["right", "left", "ambidextrous"], weights=[0.85, 0.12, 0.03])[0]
    return {
        "patient_id": str(uuid.uuid4()),
        "mrn": fake.unique.bothify(text="MRN-#######"),
        "age": age,
        "gender": gender,
        "dominant_hand": hand,
        "diagnosis_label": diagnosis,
        "clinical_notes": fake.sentence(nb_words=12),
        "enrolled_at": fake.date_time_between(start_date="-2y", end_date="now"),
    }


def gen_screening_row(patient_id: str, diagnosis: str) -> dict:
    p = PROFILES[diagnosis]
    is_pd = diagnosis == "parkinsons"
    return {
        "screening_id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "upsit_smell_score": int(clip(RNG.normal(*p["upsit"]), 0, 40)),
        "rbd_sleep_flag": bool(RNG.random() < (0.38 if is_pd else 0.04)),
        "constipation_scale": int(clip(RNG.normal(6.5 if is_pd else 1.8, 1.8), 0, 10)),
        "phq9_depression_score": int(clip(RNG.normal(9.5 if is_pd else 3.2, 3.5), 0, 27)),
        "recorded_at": fake.date_time_between(start_date="-1y", end_date="now"),
    }


def gen_drawing_session_and_stream(patient_id: str, diagnosis: str, n_points: int = 300) -> Tuple[dict, list]:
    p = PROFILES[diagnosis]
    session_id = str(uuid.uuid4())
    session_row = {
        "session_id": session_id,
        "patient_id": patient_id,
        "task_type": "archimedean_spiral",
        "device_model": random.choice(["Wacom Cintiq Pro 16", "Apple iPad Pro (Apple Pencil 2)", "Samsung Galaxy Tab S9"]),
        "sampling_hz": 120,
        "started_at": fake.date_time_between(start_date="-1y", end_date="now"),
        "duration_ms": n_points * 8,
    }

    # Archimedean spiral trajectory simulation: r = a + b*theta
    theta = np.linspace(0, 8 * np.pi, n_points)
    noise_scale = p["jerk"][0]
    a, b = 6.0, 3.2
    r = a + b * theta + RNG.normal(0, noise_scale * 12.0, n_points)
    x = r * np.cos(theta) + 400.0
    y = r * np.sin(theta) + 400.0

    velocities = np.abs(RNG.normal(p["velocity"][0], p["velocity"][1], n_points))
    jerks = np.abs(RNG.normal(p["jerk"][0], p["jerk"][1], n_points))
    pressures = clip(RNG.normal(0.52, p["pressure_std"][0], n_points), 0.0, 1.0)
    in_air_flags = RNG.random(n_points) < p["in_air_ratio"][0]

    stream_rows = []
    duration = session_row["duration_ms"]
    for i in range(n_points):
        stream_rows.append((
            session_id,
            float(x[i]),
            float(y[i]),
            float(pressures[i] if np.ndim(pressures) else pressures),
            float(RNG.normal(0, 4.0)),  # tilt_x
            float(RNG.normal(0, 4.0)),  # tilt_y
            float(velocities[i]),
            float(jerks[i]),
            bool(in_air_flags[i]),
            int(i * (duration / n_points)),
        ))
    return session_row, stream_rows


def gen_voice_row(patient_id: str, diagnosis: str) -> dict:
    p = PROFILES[diagnosis]
    return {
        "voice_id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "mean_f0_hz": clip(RNG.normal(*p["f0"]), 65.0, 290.0),
        "jitter_percent": clip(RNG.normal(*p["jitter"]), 0.05, 12.0),
        "shimmer_local": clip(RNG.normal(*p["shimmer"]), 0.5, 22.0),
        "hnr_db": clip(RNG.normal(*p["hnr"]), 2.0, 36.0),
        "audio_recording_url": f"s3://parkinsons-clinical-audio/{uuid.uuid4()}.wav",
        "recorded_at": fake.date_time_between(start_date="-1y", end_date="now"),
    }


def gen_gait_row(patient_id: str, diagnosis: str) -> dict:
    p = PROFILES[diagnosis]
    return {
        "gait_id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "step_cadence_spm": clip(RNG.normal(*p["cadence"]), 45.0, 155.0),
        "stride_time_variance": clip(RNG.normal(*p["stride_var"]), 0.002, 0.20),
        "arm_swing_asymmetry_index": clip(RNG.normal(*p["arm_asym"]), 0.01, 1.0),
        "walk_duration_s": clip(RNG.normal(60.0, 8.0), 25.0, 120.0),
        "recorded_at": fake.date_time_between(start_date="-1y", end_date="now"),
    }


def seed_database(dsn: str, n_patients: int = 300, pd_ratio: float = 0.45):
    print(f"[Seed] Connecting to PostgreSQL DSN: {dsn}")
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET search_path TO parkinsons, public;")

    patients, screenings, sessions, streams, voices, gaits = [], [], [], [], [], []

    print(f"[Seed] Generating synthetic cohort of {n_patients} patients (target PD ratio: {pd_ratio:.0%})...")
    for _ in range(n_patients):
        diagnosis = "parkinsons" if RNG.random() < pd_ratio else "healthy"
        pat = gen_patient_row(diagnosis)
        patients.append(pat)
        pid = pat["patient_id"]

        screenings.append(gen_screening_row(pid, diagnosis))
        s_row, st_rows = gen_drawing_session_and_stream(pid, diagnosis)
        sessions.append(s_row)
        streams.extend(st_rows)
        voices.append(gen_voice_row(pid, diagnosis))
        gaits.append(gen_gait_row(pid, diagnosis))

    print(f"[Seed] Batch inserting {len(patients)} patient demographics...")
    execute_values(cur, """
        INSERT INTO patients (patient_id, mrn, age, gender, dominant_hand,
                               diagnosis_label, clinical_notes, enrolled_at)
        VALUES %s
    """, [(p["patient_id"], p["mrn"], p["age"], p["gender"], p["dominant_hand"],
           p["diagnosis_label"], p["clinical_notes"], p["enrolled_at"]) for p in patients])

    print(f"[Seed] Batch inserting {len(screenings)} non-motor clinical screenings...")
    execute_values(cur, """
        INSERT INTO clinical_screenings (screening_id, patient_id, upsit_smell_score,
                                          rbd_sleep_flag, constipation_scale,
                                          phq9_depression_score, recorded_at)
        VALUES %s
    """, [(s["screening_id"], s["patient_id"], s["upsit_smell_score"], s["rbd_sleep_flag"],
           s["constipation_scale"], s["phq9_depression_score"], s["recorded_at"]) for s in screenings])

    print(f"[Seed] Batch inserting {len(sessions)} drawing sessions...")
    execute_values(cur, """
        INSERT INTO drawing_sessions (session_id, patient_id, task_type, device_model,
                                       sampling_hz, started_at, duration_ms)
        VALUES %s
    """, [(s["session_id"], s["patient_id"], s["task_type"], s["device_model"],
           s["sampling_hz"], s["started_at"], s["duration_ms"]) for s in sessions])

    print(f"[Seed] Batch inserting {len(streams):,} high-frequency stroke telemetry samples...")
    execute_values(cur, """
        INSERT INTO stroke_telemetry (session_id, x, y, pressure, tilt_x, tilt_y,
                                       velocity, jerk, in_air, timestamp_ms)
        VALUES %s
    """, streams, page_size=2500)

    print(f"[Seed] Batch inserting {len(voices)} acoustic voice assessments...")
    execute_values(cur, """
        INSERT INTO voice_assessments (voice_id, patient_id, mean_f0_hz, jitter_percent,
                                        shimmer_local, hnr_db, audio_recording_url, recorded_at)
        VALUES %s
    """, [(v["voice_id"], v["patient_id"], v["mean_f0_hz"], v["jitter_percent"],
           v["shimmer_local"], v["hnr_db"], v["audio_recording_url"], v["recorded_at"]) for v in voices])

    print(f"[Seed] Batch inserting {len(gaits)} IMU gait mobility assessments...")
    execute_values(cur, """
        INSERT INTO gait_assessments (gait_id, patient_id, step_cadence_spm,
                                       stride_time_variance, arm_swing_asymmetry_index,
                                       walk_duration_s, recorded_at)
        VALUES %s
    """, [(g["gait_id"], g["patient_id"], g["step_cadence_spm"], g["stride_time_variance"],
           g["arm_swing_asymmetry_index"], g["walk_duration_s"], g["recorded_at"]) for g in gaits])

    conn.commit()

    print("[Seed] Invoking stored procedure 'refresh_feature_vector()' across cohort...")
    for p in patients:
        cur.execute("SELECT refresh_feature_vector(%s);", (p["patient_id"],))
    conn.commit()

    cur.close()
    conn.close()

    pd_count = sum(1 for p in patients if p["diagnosis_label"] == "parkinsons")
    healthy_count = len(patients) - pd_count
    print(f"\n[Seed Complete] Successfully seeded {n_patients} patients ({pd_count} Parkinson's / {healthy_count} Healthy)")
    print(f"                Stream samples loaded: {len(streams):,} points.")
    print(f"                Multimodal Feature Store materialized and ready for ML training.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed synthetic Parkinson's multimodal screening dataset.")
    parser.add_argument("--dsn", default="postgresql://postgres@localhost:5432/parkinsons", help="PostgreSQL DSN")
    parser.add_argument("--n-patients", type=int, default=300, help="Total number of patients to generate")
    parser.add_argument("--pd-ratio", type=float, default=0.45, help="Proportion of Parkinson's cases")
    args = parser.parse_args()
    seed_database(args.dsn, args.n_patients, args.pd_ratio)
