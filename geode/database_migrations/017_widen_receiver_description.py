"""Auto-extracted migration block; see geode/dbConnection.py history for provenance."""

def migrate(cnn):
    # Widen ReceiverDescription in receivers from VARCHAR(22) to VARCHAR(256).
    # The original schema allocated only 22 chars — identical to ReceiverCode —
    # which is far too short for the IGS equipment descriptions in rcvr_ant.txt.
    rcv_desc = cnn.query_float("""
        SELECT character_maximum_length
        FROM information_schema.columns
        WHERE table_name  = 'receivers'
          AND column_name = 'ReceiverDescription';
    """, as_dict=True)

    if rcv_desc and rcv_desc[0]['character_maximum_length'] is not None \
            and rcv_desc[0]['character_maximum_length'] < 256:
        print(' >> Widening receivers."ReceiverDescription" from '
              f'VARCHAR({rcv_desc[0]["character_maximum_length"]}) to VARCHAR(256)')
        cnn.begin_transac()
        cnn.query("""
        ALTER TABLE receivers
            ALTER COLUMN "ReceiverDescription" TYPE VARCHAR(256);
        """)
        cnn.commit_transac()
