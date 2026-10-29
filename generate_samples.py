import sqlite3
import random
import numpy as np

def generate_samples():
    conn = sqlite3.connect("sample_patients.db")
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        patient_id TEXT PRIMARY KEY,
        mrn TEXT UNIQUE,
        patient_name TEXT,
        age INTEGER,
        gender TEXT,
        avg_velocity REAL,
        avg_jerk REAL,
        pressure_std REAL,
        in_air_time_ratio REAL,
        mean_f0_hz REAL,
        jitter_percent REAL,
        shimmer_local REAL,
        hnr_db REAL,
        step_cadence_spm REAL,
        stride_time_variance REAL,
        arm_swing_asymmetry_index REAL,
        upsit_smell_score REAL,
        rbd_sleep_flag INTEGER
    );
    """)

    profiles = {
        'healthy': {
            'velocity': (2.85, 0.35), 'jerk': (0.14, 0.04), 'pressure_std': (0.04, 0.015), 'in_air': (0.04, 0.02),
            'f0': (155.0, 18.0), 'jitter': (0.42, 0.12), 'shimmer': (2.40, 0.65), 'hnr': (23.5, 2.8),
            'cadence': (114.0, 5.5), 'stride_var': (0.014, 0.004), 'arm_asym': (0.045, 0.018), 'upsit': (35.0, 2.8)
        },
        'parkinsons': {
            'velocity': (1.55, 0.45), 'jerk': (0.58, 0.16), 'pressure_std': (0.19, 0.05), 'in_air': (0.24, 0.07),
            'f0': (142.0, 28.0), 'jitter': (1.92, 0.55), 'shimmer': (6.85, 1.75), 'hnr': (12.8, 3.2),
            'cadence': (91.0, 9.5), 'stride_var': (0.058, 0.018), 'arm_asym': (0.38, 0.11), 'upsit': (17.5, 5.2)
        }
    }

    names = ['John Doe', 'Jane Smith', 'Robert Johnson', 'Emily Davis', 'Michael Brown', 'Sarah Wilson', 'David Miller', 'Lisa Anderson', 'James Taylor', 'Mary Thomas']
    cursor.execute("DELETE FROM patients;")

    for i in range(1, 51):
        cohort = 'parkinsons' if i % 2 == 0 else 'healthy'
        p = profiles[cohort]
        p_id = f"PT-{1000+i}"
        mrn = f"MRN-{200000+i}"
        p_name = f"{random.choice(names)} {i}"
        age = random.randint(55, 82)
        gender = 'male' if i % 3 != 0 else 'female'
        
        vel = max(0.2, np.random.normal(*p['velocity']))
        jerk = max(0.01, np.random.normal(*p['jerk']))
        p_std = max(0.01, np.random.normal(*p['pressure_std']))
        in_air = max(0.01, min(0.9, np.random.normal(*p['in_air'])))
        f0 = max(80.0, np.random.normal(*p['f0']))
        jit = max(0.1, np.random.normal(*p['jitter']))
        shim = max(0.5, np.random.normal(*p['shimmer']))
        hnr = max(5.0, np.random.normal(*p['hnr']))
        cad = max(60.0, np.random.normal(*p['cadence']))
        s_var = max(0.005, np.random.normal(*p['stride_var']))
        arm = max(0.01, min(0.9, np.random.normal(*p['arm_asym'])))
        upsit = max(5.0, min(40.0, np.random.normal(*p['upsit'])))
        rbd = 1 if (cohort == 'parkinsons' and random.random() < 0.75) else (1 if random.random() < 0.05 else 0)
        
        cursor.execute("""
        INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (p_id, mrn, p_name, age, gender, round(vel, 3), round(jerk, 3), round(p_std, 3), round(in_air, 3),
              round(f0, 2), round(jit, 3), round(shim, 3), round(hnr, 2), round(cad, 1), round(s_var, 4),
              round(arm, 3), round(upsit, 1), rbd))

    conn.commit()

    with open("sample_patients.sql", "w", encoding="utf-8") as f:
        for line in conn.iterdump():
            f.write(f"{line}\n")
    conn.close()

    # Create UCI Voice sample SQL
    conn_uci = sqlite3.connect(":memory:")
    c = conn_uci.cursor()
    c.execute("""
    CREATE TABLE uci_voice_patients (
        subject_id TEXT PRIMARY KEY,
        name TEXT,
        "MDVP:Fo(Hz)" REAL,
        "MDVP:Jitter(%)" REAL,
        "MDVP:Shimmer" REAL,
        "HNR" REAL,
        "status" INTEGER
    );
    """)
    rows = [
        ('SUBJ_01', 'phon_R01_S01_1', 119.992, 1.84, 6.74, 12.03, 1),
        ('SUBJ_02', 'phon_R01_S01_2', 122.400, 2.18, 7.34, 11.08, 1),
        ('SUBJ_03', 'phon_R01_S02_1', 197.076, 0.38, 2.19, 24.77, 0),
        ('SUBJ_04', 'phon_R01_S02_2', 202.632, 0.42, 2.34, 25.12, 0),
        ('SUBJ_05', 'phon_R01_S03_1', 116.682, 2.30, 7.82, 10.84, 1),
        ('SUBJ_06', 'phon_R01_S04_1', 188.339, 0.35, 1.94, 26.14, 0),
        ('SUBJ_07', 'phon_R01_S05_1', 120.267, 1.93, 6.96, 12.44, 1),
        ('SUBJ_08', 'phon_R01_S06_1', 204.032, 0.39, 2.15, 25.49, 0),
    ]
    c.executemany('INSERT INTO uci_voice_patients VALUES (?, ?, ?, ?, ?, ?, ?)', rows)
    with open("sample_uci_parkinsons.sql", "w", encoding="utf-8") as f:
        for line in conn_uci.iterdump():
            f.write(f"{line}\n")
    conn_uci.close()
    print("Samples generated successfully!")

if __name__ == "__main__":
    generate_samples()

