"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    fields = cnn.get_columns('ppp_soln')

    if fields['hash'].lower() != 'bigint':
        # check the database to modify the ppp_soln table hash column from integer to bigint
        print(' >> Converting hash column in ppp_soln to BIGINT. This operation might take a while...')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE ppp_soln
        ALTER COLUMN hash TYPE BIGINT;
        """)
        cnn.commit_transac()
