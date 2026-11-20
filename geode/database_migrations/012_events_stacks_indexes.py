"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # Index events(EventDate) and stacks(name): both are queried/filtered
    # on these columns often enough (event log lookups, stack name lookups)
    # to be worth an index.
    # NOTE: CREATE INDEX CONCURRENTLY cannot run inside a transaction block,
    # so these are intentionally NOT wrapped in begin_transac()/commit_transac()
    # -- the connection already runs with autocommit=True. The pg_indexes
    # check plus the SQL-level IF NOT EXISTS both guard against re-creating
    # an index that already exists.

    idx = cnn.query("SELECT * FROM pg_indexes WHERE tablename = 'events' "
                    "AND indexname = 'events_event_date_index'")

    if not len(idx):
        print(' >> Creating index events_event_date_index on events("EventDate")')
        cnn.query("""CREATE INDEX CONCURRENTLY IF NOT EXISTS events_event_date_index
                     ON events ("EventDate");""")

    idx = cnn.query("SELECT * FROM pg_indexes WHERE tablename = 'stacks' "
                    "AND indexname = 'stacks_name_index'")

    if not len(idx):
        print(' >> Creating index stacks_name_index on stacks(name)')
        cnn.query("""CREATE INDEX CONCURRENTLY IF NOT EXISTS stacks_name_index
                     ON stacks (name);""")
