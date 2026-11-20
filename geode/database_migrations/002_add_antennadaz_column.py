"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # New AntennaDAZ field in table stationinfo
    if 'AntennaDAZ' not in cnn.get_columns('stationinfo').keys():
        print(' >> Adding AntennaDAZ field to stationinfo')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE stationinfo 
        ADD COLUMN "AntennaDAZ" NUMERIC(4,1) DEFAULT 0.0
        CHECK ("AntennaDAZ" >= 0.0 AND "AntennaDAZ" <= 360.0);
        """)
        cnn.commit_transac()
