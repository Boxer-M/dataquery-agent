"""Owner-scoped PostgreSQL storage with short claims and atomic completion."""
from contextlib import contextmanager
from hashlib import sha256
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROLE = "sqlshed_agent_meta"
LEASE_SECONDS = 180
RETENTION_DAYS = 30
STATE_COLUMNS = ("conversation_id, state_version, active_query_context, pending_clarification, "
                 "last_message_id, last_query_id, created_at, updated_at")


class ConversationError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def owner_hash(token):
    if not isinstance(token, str) or len(token) < 32:
        raise ConversationError("owner_required")
    return sha256(token.encode()).hexdigest()


def identifier(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        raise ConversationError("invalid_identifier") from None


class ConversationStore:
    def __init__(self, dsn):
        if not dsn:
            raise ConversationError("management_not_configured")
        config = conninfo_to_dict(dsn)
        if config.get("user") != ROLE or config.get("dbname") != "sqlshed":
            raise ConversationError("unsafe_management_role")
        self.dsn = dsn

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row) as pg:
            pg.execute("SET LOCAL search_path = pg_catalog")
            pg.execute("SET LOCAL statement_timeout = '5s'")
            pg.execute("SET LOCAL lock_timeout = '2s'")
            row = pg.execute("SELECT current_user AS role, session_user AS login, current_database() AS database").fetchone()
            if row != {"role": ROLE, "login": ROLE, "database": "sqlshed"}:
                raise ConversationError("unsafe_management_role")
            yield pg

    def create(self, owner):
        with self.transaction() as pg:
            return pg.execute(
                f"INSERT INTO agent_meta.conversations(conversation_id,owner_hash) VALUES (%s,%s) RETURNING {STATE_COLUMNS}",
                (uuid4(), owner_hash(owner)),
            ).fetchone()

    def get(self, owner, conversation_id):
        with self.transaction() as pg:
            row = pg.execute(
                f"SELECT {STATE_COLUMNS}, CASE WHEN in_flight IS NOT NULL AND lease_until > now() "
                "THEN 'busy' WHEN pending_clarification IS NOT NULL "
                "AND (pending_clarification->>'expires_at')::timestamptz > now() "
                "THEN 'awaiting_clarification' ELSE 'active' END AS conversation_status "
                "FROM agent_meta.conversations WHERE conversation_id=%s AND owner_hash=%s",
                (identifier(conversation_id), owner_hash(owner)),
            ).fetchone()
        if row is None:
            raise ConversationError("conversation_not_found")
        return row

    def claim(self, owner, conversation_id, message_id, message):
        cid, mid, owner_key = identifier(conversation_id), identifier(message_id), owner_hash(owner)
        digest = sha256(message.encode()).hexdigest()
        with self.transaction() as pg:
            state = pg.execute(
                f"SELECT {STATE_COLUMNS}, in_flight, lease_until > now() AS leased "
                "FROM agent_meta.conversations WHERE conversation_id=%s AND owner_hash=%s FOR UPDATE",
                (cid, owner_key),
            ).fetchone()
            if state is None:
                raise ConversationError("conversation_not_found")
            if state["in_flight"] and not state["leased"]:
                pg.execute("UPDATE agent_meta.messages SET status='abandoned', response=%s "
                           "WHERE conversation_id=%s AND claim_id=%s AND status='running'",
                           (Jsonb({"status":"error","code":"message_abandoned"}), cid, state["in_flight"]))
                pg.execute("UPDATE agent_meta.conversations SET in_flight=NULL,lease_until=NULL WHERE conversation_id=%s",
                           (cid,))
            prior = pg.execute("SELECT digest,status,response FROM agent_meta.messages "
                               "WHERE conversation_id=%s AND message_id=%s", (cid, mid)).fetchone()
            if prior:
                if prior["digest"] != digest:
                    raise ConversationError("message_id_reused")
                if prior["status"] == "running":
                    raise ConversationError("conversation_busy")
                return {"replay": prior["response"]}
            if state["leased"]:
                raise ConversationError("conversation_busy")
            claim_id = uuid4()
            started = pg.execute(
                "INSERT INTO agent_meta.messages(conversation_id,message_id,claim_id,digest,status) "
                "VALUES (%s,%s,%s,%s,'running') RETURNING created_at",
                (cid, mid, claim_id, digest),
            ).fetchone()["created_at"]
            pg.execute("UPDATE agent_meta.conversations SET in_flight=%s, lease_until=now() + %s * interval '1 second' "
                       "WHERE conversation_id=%s", (claim_id, LEASE_SECONDS, cid))
            return {"state": state, "conversation_id": cid, "message_id": mid, "claim_id": claim_id,
                    "started_at": started, "owner_hash": owner_key}

    def finish(self, claim, context, pending, record, response):
        cid, mid = claim["conversation_id"], claim["message_id"]
        with self.transaction() as pg:
            changed = pg.execute(
                "UPDATE agent_meta.conversations SET active_query_context=%s,pending_clarification=%s,"
                "last_message_id=%s,last_query_id=%s,state_version=state_version+1,updated_at=now(),"
                "in_flight=NULL,lease_until=NULL WHERE conversation_id=%s AND owner_hash=%s AND in_flight=%s "
                "AND state_version=%s AND lease_until>now()",
                (Jsonb(context) if context is not None else None, Jsonb(pending) if pending is not None else None,
                 mid, record["query_id"], cid, claim["owner_hash"], claim["claim_id"], claim["state"]["state_version"]),
            )
            if changed.rowcount != 1:
                raise ConversationError("state_conflict")
            updated = pg.execute(
                "UPDATE agent_meta.messages SET status='completed',response=%s "
                "WHERE conversation_id=%s AND message_id=%s AND claim_id=%s AND status='running'",
                (Jsonb(response), cid, mid, claim["claim_id"]),
            )
            if updated.rowcount != 1:
                raise ConversationError("state_conflict")
            pg.execute("INSERT INTO agent_meta.query_history(query_id,conversation_id,message_id,trace_id,record,started_at) "
                       "VALUES (%s,%s,%s,%s,%s,%s)",
                       (record["query_id"], cid, mid, record["trace_id"], Jsonb(record), claim["started_at"]))

    def abort(self, claim):
        with self.transaction() as pg:
            changed = pg.execute("UPDATE agent_meta.conversations SET in_flight=NULL,lease_until=NULL "
                                 "WHERE conversation_id=%s AND owner_hash=%s AND in_flight=%s",
                                 (claim["conversation_id"], claim["owner_hash"], claim["claim_id"]))
            if changed.rowcount:
                pg.execute("UPDATE agent_meta.messages SET status='failed',response=%s "
                           "WHERE conversation_id=%s AND message_id=%s AND claim_id=%s AND status='running'",
                           (Jsonb({"status":"error","code":"history_write_failed"}), claim["conversation_id"],
                            claim["message_id"], claim["claim_id"]))

    def history(self, owner, query_id):
        with self.transaction() as pg:
            row = pg.execute("""
                SELECT h.record,h.started_at,h.completed_at
                FROM agent_meta.query_history h JOIN agent_meta.conversations c USING(conversation_id)
                WHERE h.query_id=%s AND c.owner_hash=%s
            """, (identifier(query_id), owner_hash(owner))).fetchone()
        if row is None:
            raise ConversationError("query_not_found")
        return dict(row["record"], started_at=row["started_at"], completed_at=row["completed_at"])

    def prune(self, owner):
        with self.transaction() as pg:
            key = owner_hash(owner)
            old_messages = pg.execute("""
                DELETE FROM agent_meta.messages m USING agent_meta.conversations c
                WHERE m.conversation_id=c.conversation_id AND c.owner_hash=%s
                  AND m.status <> 'running' AND m.created_at < now() - %s * interval '1 day'
                  AND (c.in_flight IS NULL OR c.lease_until <= now())
            """, (key, RETENTION_DAYS)).rowcount
            old_conversations = pg.execute("""
                DELETE FROM agent_meta.conversations WHERE owner_hash=%s
                  AND updated_at < now() - %s * interval '1 day'
                  AND (in_flight IS NULL OR lease_until <= now())
            """, (key, RETENTION_DAYS)).rowcount
            return {"removed_messages": old_messages, "removed_conversations": old_conversations}
