"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # modifications to ppp_soln to store big int values
    fields = cnn.get_columns('ppp_soln')

    if 'orbit' not in fields.keys():
        print(' >> Adding orbit field to ppp_soln')
        # New field in table ppp_soln present, no need to migrate.
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE ppp_soln
        ADD COLUMN orbit VARCHAR(100) DEFAULT '';
        """)
        cnn.commit_transac()
