BEGIN TRANSACTION;
CREATE TABLE uci_voice_patients (
        subject_id TEXT PRIMARY KEY,
        name TEXT,
        "MDVP:Fo(Hz)" REAL,
        "MDVP:Jitter(%)" REAL,
        "MDVP:Shimmer" REAL,
        "HNR" REAL,
        "status" INTEGER
    );
INSERT INTO "uci_voice_patients" VALUES('SUBJ_01','phon_R01_S01_1',119.992,1.84,6.74,12.03,1);
INSERT INTO "uci_voice_patients" VALUES('SUBJ_02','phon_R01_S01_2',122.4,2.18,7.34,11.08,1);
INSERT INTO "uci_voice_patients" VALUES('SUBJ_03','phon_R01_S02_1',197.076,0.38,2.19,24.77,0);
INSERT INTO "uci_voice_patients" VALUES('SUBJ_04','phon_R01_S02_2',202.632,0.42,2.34,25.12,0);
INSERT INTO "uci_voice_patients" VALUES('SUBJ_05','phon_R01_S03_1',116.682,2.3,7.82,10.84,1);
INSERT INTO "uci_voice_patients" VALUES('SUBJ_06','phon_R01_S04_1',188.339,0.35,1.94,26.14,0);
INSERT INTO "uci_voice_patients" VALUES('SUBJ_07','phon_R01_S05_1',120.267,1.93,6.96,12.44,1);
INSERT INTO "uci_voice_patients" VALUES('SUBJ_08','phon_R01_S06_1',204.032,0.39,2.15,25.49,0);
COMMIT;
