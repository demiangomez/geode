"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # New field in table api_visitgnssdatafiles
    if 'rinexed' not in cnn.get_columns('api_visitgnssdatafiles').keys():
        print(' >> Adding rinexed field to api_visitgnssdatafiles')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE api_visitgnssdatafiles
        ADD COLUMN rinexed BOOLEAN DEFAULT FALSE;
        """)
        cnn.commit_transac()
