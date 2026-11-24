"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # check precision of lat lon height and auto_[x|y|z] in stations table
    stn_types = cnn.query_float("""
    SELECT 
        column_name,
        data_type,
        numeric_precision,
        numeric_scale
    FROM information_schema.columns 
        WHERE table_name = 'stations' 
        AND column_name = 'auto_x';
    """, as_dict=True)
    if stn_types[0]['numeric_precision'] is None:
        print(' >> Converting lat lon height and auto_[x|y|z] types')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE stations ALTER COLUMN auto_x TYPE NUMERIC(16,5) USING ROUND(auto_x::numeric, 5);
        ALTER TABLE stations ALTER COLUMN auto_y TYPE NUMERIC(16,5) USING ROUND(auto_y::numeric, 5);
        ALTER TABLE stations ALTER COLUMN auto_z TYPE NUMERIC(16,5) USING ROUND(auto_z::numeric, 5);
        ALTER TABLE stations ALTER COLUMN lat    TYPE NUMERIC(12,9) USING ROUND(lat::numeric, 9);
        ALTER TABLE stations ALTER COLUMN lon    TYPE NUMERIC(12,9) USING ROUND(lon::numeric, 9);
        ALTER TABLE stations ALTER COLUMN height TYPE NUMERIC(10,5) USING ROUND(height::numeric, 5);
        """)
        cnn.commit_transac()
