"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # For the Mask object: check that the new fields exist or create them
    if 'density' not in cnn.get_columns('earthquakes').keys():
        cnn.begin_transac()
        cnn.query("""
                    ALTER TABLE earthquakes
                    ADD COLUMN density INTEGER   DEFAULT NULL,
                    ADD COLUMN c_kml   TEXT      DEFAULT NULL,
                    ADD COLUMN cp_kml  TEXT      DEFAULT NULL;
                    """)
        cnn.commit_transac()

    # check that the index exists
    idx = cnn.query("SELECT * FROM pg_indexes WHERE tablename = 'earthquakes' "
                    "AND indexname = 'earthquake_id_key'")

    if not len(idx):
        cnn.begin_transac()
        cnn.query("""CREATE UNIQUE INDEX earthquake_id_key ON earthquakes (id);""")
        cnn.commit_transac()
