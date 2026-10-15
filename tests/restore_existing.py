"""Recreate the user-provided metadata in a DISPOSABLE test database only.

This fixture includes no production rows or credentials. Never run in Supabase.
"""
import json
from pathlib import Path
from psycopg import sql

def restore(connection):
    data=json.loads((Path(__file__).parent/'fixtures/existing_schema.json').read_text())
    tables=data['tables']
    for table in tables:
        columns=[]
        for col in table['columns']:
            definition=sql.Identifier(col['name'])+sql.SQL(' '+col['type'])
            if col['identity']:
                definition+=sql.SQL(' GENERATED '+('ALWAYS' if col['identity']=='a' else 'BY DEFAULT')+' AS IDENTITY')
            elif col['default'] is not None:
                definition+=sql.SQL(' DEFAULT '+col['default'])
            if col['not_null']:definition+=sql.SQL(' NOT NULL')
            columns.append(definition)
        connection.execute(sql.SQL('CREATE TABLE public.{} ({})').format(sql.Identifier(table['name']),sql.SQL(',').join(columns)))
    for kind in ['p','u','c','f']:
        for table in tables:
            for con in table['constraints']:
                if con['type']==kind:
                    connection.execute(sql.SQL('ALTER TABLE public.{} ADD CONSTRAINT {} ').format(sql.Identifier(table['name']),sql.Identifier(con['name']))+sql.SQL(con['definition']))
    for table in tables:
        for index in table['indexes']:
            if any(index['name']==c['name'] for c in table['constraints']):continue
            connection.execute(index['definition'])
        if table['rls_enabled']:
            connection.execute(sql.SQL('ALTER TABLE public.{} ENABLE ROW LEVEL SECURITY').format(sql.Identifier(table['name'])))
        for policy in table['policies']:
            command=sql.SQL('CREATE POLICY {} ON public.{} AS '+policy['permissive']+' FOR '+policy['command']+' TO {}').format(
                sql.Identifier(policy['name']),sql.Identifier(table['name']),sql.SQL(',').join(sql.Identifier(r) for r in policy['roles']))
            if policy['using']:command+=sql.SQL(' USING ('+policy['using']+')')
            if policy['with_check']:command+=sql.SQL(' WITH CHECK ('+policy['with_check']+')')
            connection.execute(command)
    connection.execute('GRANT ALL ON ALL TABLES IN SCHEMA public TO anon,authenticated,service_role')
    connection.execute('GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role')
