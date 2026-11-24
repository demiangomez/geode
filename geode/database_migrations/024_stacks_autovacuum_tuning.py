"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # Lower the autovacuum analyze threshold on stacks, which is large
    # (tens of millions of rows) and grows via routine inserts. The
    # default scale_factor-based threshold let autoanalyze go long
    # enough without refreshing statistics that the planner picked a
    # bad join plan once stacks passed ~46M rows: PlotETM.py queries
    # joining rinex to stacks went from sub-second to many minutes
    # until a manual ANALYZE stacks fixed it. A lower threshold keeps
    # statistics fresh as the table keeps growing.

    stacks_tuned = cnn.query_float("""
        SELECT EXISTS (
            SELECT 1 FROM pg_class
            WHERE relname = 'stacks'
            AND reloptions::text LIKE '%autovacuum_analyze_scale_factor%');
        """, as_dict=True)

    if not stacks_tuned[0]['exists']:
        print(' >> Lowering autovacuum analyze threshold on stacks')
        cnn.query("""
            ALTER TABLE stacks SET (autovacuum_analyze_scale_factor = 0.02,
                                     autovacuum_analyze_threshold = 1000);
        """)
