"""PostgreSQL schema-equivalence evidence, independent of runtime ORM metadata."""
from sqlalchemy import text


def schema_snapshot(connection) -> dict:
    """Capture tables, defaults, constraints, indexes, RLS, functions and grants.

Only migration-version tables are excluded. Object IDs and row/sequence data
are excluded; identities, sequence definitions and security properties remain.
"""
    tables = connection.execute(text("""
        SELECT c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p')
          AND c.relname NOT LIKE 'alembic_version%'
        ORDER BY c.relname
    """)).all()
    result = {'tables': {}, 'functions': {}, 'sequences': {}}

    def rows(sql, oid):
        return [list(row) for row in connection.execute(text(sql), {'oid': oid}).all()]

    def grants(oid, kind):
        acl = 'proacl' if kind == 'f' else 'relacl'
        owner = 'proowner' if kind == 'f' else 'relowner'
        catalog = 'pg_proc' if kind == 'f' else 'pg_class'
        return rows(f"""
            SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' WHEN a.grantee=o.{owner}
                        THEN 'OWNER' ELSE pg_get_userbyid(a.grantee) END,
                   a.privilege_type, a.is_grantable
            FROM {catalog} o, LATERAL aclexplode(COALESCE(o.{acl}, acldefault('{kind}', o.{owner}))) a
            WHERE o.oid=:oid ORDER BY 1,2,3
        """, oid)

    for oid, name, enabled, forced in tables:
        result['tables'][name] = {
            'columns': rows("""SELECT a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
                pg_get_expr(d.adbin,d.adrelid), a.attidentity, a.attgenerated,
                CASE WHEN a.attcollation=0 THEN NULL ELSE a.attcollation::regcollation::text END
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=:oid AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", oid),
            'constraints': rows("""SELECT conname, contype, pg_get_constraintdef(oid,true), convalidated
                FROM pg_constraint WHERE conrelid=:oid ORDER BY conname""", oid),
            'indexes': rows("""SELECT c.relname, pg_get_indexdef(i.indexrelid)
                FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
                WHERE i.indrelid=:oid ORDER BY c.relname""", oid),
            'rls': [enabled, forced],
            'policies': rows("""SELECT polname, polcmd, polpermissive,
                ARRAY(SELECT CASE WHEN role=0 THEN 'PUBLIC' ELSE pg_get_userbyid(role) END
                      FROM unnest(polroles) role ORDER BY 1),
                pg_get_expr(polqual,polrelid), pg_get_expr(polwithcheck,polrelid)
                FROM pg_policy WHERE polrelid=:oid ORDER BY polname""", oid),
            'triggers': rows("""SELECT tgname, pg_get_triggerdef(oid), tgenabled
                FROM pg_trigger WHERE tgrelid=:oid AND NOT tgisinternal ORDER BY tgname""", oid),
            'grants': grants(oid, 'r'),
            'column_grants': rows("""SELECT col.attname,
                CASE WHEN a.grantee=0 THEN 'PUBLIC' WHEN a.grantee=c.relowner THEN 'OWNER'
                     ELSE pg_get_userbyid(a.grantee) END, a.privilege_type, a.is_grantable
                FROM pg_attribute col JOIN pg_class c ON c.oid=col.attrelid,
                LATERAL aclexplode(CASE WHEN cardinality(col.attacl)>0 THEN col.attacl ELSE NULL END) a
                WHERE col.attrelid=:oid AND col.attnum>0 AND NOT col.attisdropped ORDER BY 1,2,3,4""", oid),
        }
    functions = connection.execute(text("""SELECT p.oid, p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
        pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' ORDER BY 2""")).all()
    for oid, name, definition in functions:
        result['functions'][name] = {'definition': definition, 'grants': grants(oid, 'f')}
    sequences = connection.execute(text("""SELECT c.oid,c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S' ORDER BY 2""")).all()
    for oid, name in sequences:
        result['sequences'][name] = {
            'definition': rows("""SELECT format_type(seqtypid,NULL), seqstart, seqincrement, seqmax,
                seqmin, seqcache, seqcycle FROM pg_sequence WHERE seqrelid=:oid""", oid),
            'owned_by': rows("""SELECT t.relname,a.attname FROM pg_depend d
                JOIN pg_class t ON t.oid=d.refobjid JOIN pg_attribute a
                ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
                WHERE d.objid=:oid AND d.deptype IN ('a','i') ORDER BY 1,2""", oid),
            'grants': grants(oid, 's'),
        }
    return result
