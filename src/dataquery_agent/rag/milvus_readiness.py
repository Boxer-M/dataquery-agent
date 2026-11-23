"""PostgreSQL-owned replica readiness; no writes from the retrieval role."""
from uuid import uuid4

from .milvus import MilvusUnavailable
from .store import ADMIN, CHUNKS, DOCS, KnowledgeStore

TABLE = 'agent_meta.milvus_readiness'


def index_ready(pg, scored, params):
    if pg.execute("SELECT to_regclass(%s) AS relation", (TABLE,)).fetchone()['relation'] is None:
        return False
    return pg.execute(scored + f"""SELECT NOT EXISTS (
        SELECT 1 FROM scored c LEFT JOIN {TABLE} r USING(document_id)
        GROUP BY c.document_id,c.document_version,c.index_version,c.embedding_model,c.embedding_version,
                 r.state,r.document_version,r.index_version,r.embedding_model,r.embedding_version,r.expected_ids
        HAVING r.state IS DISTINCT FROM 'ready'
            OR r.document_version IS DISTINCT FROM c.document_version
            OR r.index_version IS DISTINCT FROM c.index_version
            OR r.embedding_model IS DISTINCT FROM c.embedding_model
            OR r.embedding_version IS DISTINCT FROM c.embedding_version
            OR r.expected_ids IS DISTINCT FROM array_agg(c.chunk_id ORDER BY c.chunk_id)
        ) AS ready""", params).fetchone()['ready']


class Readiness:
    def __init__(self, rows, writer=None):
        self.writer = writer or KnowledgeStore.local(admin=True)
        if self.writer.role != ADMIN:
            raise ValueError('milvus_readiness_requires_admin')
        self.attempt = uuid4().hex
        self.targets = {}
        for row in rows:
            target = self.targets.setdefault(row['document_id'], dict(
                document_version=row['document_version'], index_version=row['index_version'],
                embedding_model=row['embedding_model'], embedding_version=row['embedding_version'], ids=[]))
            target['ids'].append(row['chunk_id'])
        for target in self.targets.values():
            target['ids'].sort()

    def lock_and_check(self, pg):
        for did, target in sorted(self.targets.items()):
            doc = pg.execute(f'SELECT active_version,active_index,deleted_at FROM {DOCS} '
                             'WHERE document_id=%s FOR UPDATE', (did,)).fetchone()
            if not doc or doc['deleted_at'] or (doc['active_version'],doc['active_index']) != (
                    target['document_version'],target['index_version']):
                raise MilvusUnavailable('milvus_sync_target_changed')
            ids = [row['chunk_id'] for row in pg.execute(f"""SELECT chunk_id FROM {CHUNKS}
                WHERE document_id=%s AND document_version=%s AND index_version=%s
                  AND embedding_model=%s AND embedding_version=%s ORDER BY chunk_id""",
                (did,target['document_version'],target['index_version'],target['embedding_model'],
                 target['embedding_version'])).fetchall()]
            if ids != target['ids']:
                raise MilvusUnavailable('milvus_sync_target_changed')

    def begin(self):
        with self.writer.transaction() as pg:
            self.lock_and_check(pg)
            for did, target in self.targets.items():
                pg.execute(f"""INSERT INTO {TABLE} (document_id,document_version,index_version,
                    embedding_model,embedding_version,expected_ids,attempt_id,state)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'syncing') ON CONFLICT(document_id) DO UPDATE SET
                    document_version=excluded.document_version,index_version=excluded.index_version,
                    embedding_model=excluded.embedding_model,embedding_version=excluded.embedding_version,
                    expected_ids=excluded.expected_ids,attempt_id=excluded.attempt_id,state='syncing',updated_at=now()""",
                    (did,target['document_version'],target['index_version'],target['embedding_model'],
                     target['embedding_version'],target['ids'],self.attempt))

    def finish(self):
        with self.writer.transaction() as pg:
            self.lock_and_check(pg)
            for did in self.targets:
                changed = pg.execute(f"""UPDATE {TABLE} SET state='ready',updated_at=now()
                    WHERE document_id=%s AND attempt_id=%s AND state='syncing' RETURNING document_id""",
                    (did,self.attempt)).fetchone()
                if not changed:
                    raise MilvusUnavailable('milvus_sync_superseded')

    def fail(self):
        with self.writer.transaction() as pg:
            pg.execute(f"UPDATE {TABLE} SET state='failed',updated_at=now() WHERE attempt_id=%s",
                       (self.attempt,))
