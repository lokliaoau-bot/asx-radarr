"""SQLite audit store. Preserve source records; bound analysis reads in SQL."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import stamp

SCHEMA_VERSION = 3
BAR_TABLE = '''CREATE TABLE {name} (
 id TEXT PRIMARY KEY,
 instrument_id TEXT NOT NULL REFERENCES instruments(id),
 batch_id TEXT NOT NULL REFERENCES batches(id),
 session_date TEXT NOT NULL, source TEXT NOT NULL, basis TEXT NOT NULL,
 close_definition TEXT NOT NULL, available_at TEXT NOT NULL,
 revision INTEGER NOT NULL, fingerprint TEXT NOT NULL,
 content_fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(instrument_id,session_date,source,basis,close_definition,revision)
)'''
SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES('schema_version','3');
CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,mode TEXT NOT NULL,created_at TEXT NOT NULL,raw_csv TEXT NOT NULL,summary_json TEXT NOT NULL,UNIQUE(sha256,mode));
CREATE TABLE IF NOT EXISTS instruments(id TEXT PRIMARY KEY,symbol_id TEXT NOT NULL,mode TEXT NOT NULL,market TEXT NOT NULL,name TEXT NOT NULL,sector TEXT NOT NULL,currency TEXT NOT NULL,UNIQUE(symbol_id,mode));
CREATE TABLE IF NOT EXISTS provider_payloads(id TEXT PRIMARY KEY,provider TEXT NOT NULL,resource TEXT NOT NULL,retrieved_at TEXT NOT NULL,sha256 TEXT NOT NULL,payload_json TEXT NOT NULL,UNIQUE(provider,resource,sha256));
CREATE TABLE IF NOT EXISTS calendar_overrides(id TEXT PRIMARY KEY,market TEXT NOT NULL,session_date TEXT NOT NULL,available_at TEXT NOT NULL,observed_at TEXT NOT NULL,sha256 TEXT NOT NULL UNIQUE,raw_json TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS calendar_asof ON calendar_overrides(market,session_date,available_at,observed_at);
'''


def content_fingerprint(payload):
    """Only an explicit observation clock can be deduplicated independently.

The full original payload hash stays separate. Every raw file/batch is retained;
    unchanged observation rows keep their first clocks. Publication/declared
    timestamps remain revision-sensitive. Quality, names,
prices, volumes, session bounds and all other fields remain revision-sensitive.
"""
    material = dict(payload,availability_kind=payload.get('availability_kind','declared'))
    # Absent/blank new metadata has the same meaning as in older payloads;
    # introducing empty fields alone must not rewrite a first-receipt version.
    for optional in ('adjustment_id','volume_basis'):
        if not material.get(optional):material.pop(optional,None)
    if material['availability_kind']=='observation':material.pop('available_at',None)
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sqlite3.sqlite_version_info < (3, 25, 0):
            raise ValueError('SQLite 3.25+ is required for point-in-time window queries')
        with self.connection() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='meta' AND type='table'").fetchone():
                version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                if version and version['value'] not in {'1', '2', '3'}:
                    raise ValueError('unsupported database schema version; refusing downgrade')
            db.executescript(SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='bars' AND type='table'").fetchone()
            if not exists:
                db.execute(BAR_TABLE.format(name='bars'))
            elif 'close_definition' not in {r['name'] for r in db.execute('PRAGMA table_info(bars)')}:
                self._migrate_bars(db)
            db.execute('CREATE INDEX IF NOT EXISTS bar_asof ON bars(instrument_id,available_at,session_date)')
            db.execute('CREATE INDEX IF NOT EXISTS bar_series_recent ON bars(instrument_id,source,basis,close_definition,session_date DESC,available_at DESC)')
            db.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))

    @staticmethod
    def _migrate_bars(db):
        """Atomic v1/v2 migration, preserving IDs, full hashes and revisions."""
        db.execute(BAR_TABLE.format(name='bars_v3'))
        rows = db.execute('SELECT * FROM bars ORDER BY rowid')
        for row in rows:
            try:
                payload = json.loads(row['payload_json'])
                definition=payload['close_definition']
            except (ValueError,KeyError,TypeError):
                raise ValueError('legacy price payload needs review; schema migration is rolled back') from None
            db.execute('INSERT INTO bars_v3 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                row['id'], row['instrument_id'], row['batch_id'], row['session_date'],
                row['source'], row['basis'], definition, row['available_at'],
                row['revision'], row['fingerprint'], content_fingerprint(payload), row['payload_json'],
            ))
        db.execute('DROP TABLE bars')
        db.execute('ALTER TABLE bars_v3 RENAME TO bars')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def commit_preview(self, p, now, allow_rejections=False):
        if p.issues and not allow_rejections:
            raise ValueError('review rejected rows; use --allow-rejections only after reviewing the preview')
        if not p.rows:
            raise ValueError('no accepted rows')
        with self.connection() as db:
            # Serialize before the idempotency check, not only before INSERT.
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id FROM batches WHERE sha256=? AND mode=?', (p.sha256, p.mode)).fetchone()
            if existing:
                return {'batch_id': existing['id'], 'idempotent': True, 'inserted': 0}
            bid = str(uuid.uuid4())
            db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',
                       (bid, p.sha256, p.mode, stamp(now), p.raw, json.dumps(p.summary(), ensure_ascii=False)))
            count = 0
            instruments = {}
            for row in p.rows:
                iid = instruments.get(row.symbol_id)
                if iid is None:
                    item = db.execute('SELECT id FROM instruments WHERE symbol_id=? AND mode=?', (row.symbol_id, p.mode)).fetchone()
                    iid = item['id'] if item else str(uuid.uuid4())
                    if item is None:
                        db.execute('INSERT INTO instruments VALUES(?,?,?,?,?,?,?)',
                                   (iid, row.symbol_id, p.mode, row.market, row.name, row.sector, row.currency))
                    instruments[row.symbol_id] = iid
                data = row.asdict()
                payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
                fp = hashlib.sha256(payload.encode()).hexdigest()
                content_fp = content_fingerprint(data)
                key = (iid, row.session_date, row.source, row.basis, row.close_definition)
                latest = db.execute('''SELECT revision,content_fingerprint FROM bars
                    WHERE instrument_id=? AND session_date=? AND source=? AND basis=? AND close_definition=?
                    ORDER BY revision DESC LIMIT 1''', key).fetchone()
                if latest and latest['content_fingerprint'] == content_fp:
                    continue
                rev = latest['revision'] + 1 if latest else 1
                db.execute('INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                    str(uuid.uuid4()), iid, bid, row.session_date, row.source, row.basis,
                    row.close_definition, row.available_at, rev, fp, content_fp, payload,
                ))
                count += 1
            return {'batch_id': bid, 'idempotent': False, 'inserted': count}

    def analysis_inputs(self, mode, cutoff, history_limit=None,markets=None):
        """Capture all analysis inputs in one committed database snapshot.

        The read transaction ends before calendar checks and indicator math.
        We do not enable WAL or change the user's journal configuration.
        """
        with self.connection() as db:
            db.execute('BEGIN')
            items = self._histories(db, mode, cutoff, history_limit,markets)
            overrides = self._calendar_overrides(db, cutoff,markets) if mode == 'imported' else {}
        return items, overrides

    def histories(self, mode, cutoff, history_limit=None):
        with self.connection() as db:
            return self._histories(db, mode, cutoff, history_limit)

    @staticmethod
    def _histories(db, mode, cutoff, history_limit=None,markets=None):
        if history_limit is not None and (isinstance(history_limit, bool) or not isinstance(history_limit, int) or history_limit < 1):
            raise ValueError('history_limit must be a positive integer or None')
        # Both clocks are filtered BEFORE revision selection and truncation.
        # Distinct session definitions have independent version histories.
        scope,values=Store._market_scope(markets,'i.market')
        rows = db.execute(f'''
            WITH visible AS (
                SELECT b.id,b.instrument_id,b.session_date,b.source,b.basis,b.close_definition,
                       b.available_at,b.revision,b.rowid AS ingestion_order,p.created_at AS observed_at
                FROM bars b JOIN instruments i ON i.id=b.instrument_id
                JOIN batches p ON p.id=b.batch_id
                WHERE i.mode=? AND b.available_at<=? AND p.created_at<=? {scope}
            ), versioned AS (
                SELECT *,ROW_NUMBER() OVER (
                    PARTITION BY instrument_id,session_date,source,basis,close_definition
                    ORDER BY revision DESC,observed_at DESC,ingestion_order DESC
                ) AS version_rank FROM visible
            ), current AS (
                SELECT * FROM versioned WHERE version_rank=1
            ), bounded AS (
                SELECT *,ROW_NUMBER() OVER (
                    PARTITION BY instrument_id,source,basis,close_definition
                    ORDER BY session_date DESC
                ) AS history_rank FROM current
            )
            SELECT b.*,t.observed_at,i.symbol_id,i.market,i.name,i.sector,i.currency
            FROM bounded t JOIN bars b ON b.id=t.id JOIN instruments i ON i.id=b.instrument_id
            WHERE (? IS NULL OR t.history_rank<=?)
        ''', (mode, stamp(cutoff), stamp(cutoff),*values, history_limit, history_limit))
        result = {}
        for r in rows:
            item = result.setdefault(r['instrument_id'], {
                'id': r['instrument_id'], 'symbol_id': r['symbol_id'], 'market': r['market'],
                'name': r['name'], 'sector': r['sector'], 'currency': r['currency'], 'bars': [],
            })
            payload = json.loads(r['payload_json'])
            payload.update(revision=r['revision'], batch_id=r['batch_id'], observed_at=r['observed_at'],
                           payload_sha256=r['fingerprint'])
            item['bars'].append(payload)
        for item in result.values():
            item['bars'].sort(key=lambda b:(b['source'],b['basis'],b['close_definition'],b['session_date']))
        return result

    def status(self,markets=None):
        active=tuple(markets) if markets is not None else ('HK','US','AU')
        if not active or any(m not in ('HK','US','AU') for m in active):
            raise ValueError('unsupported status market scope')
        marks=','.join('?' for _ in active)
        with self.connection() as db:
            db.execute('BEGIN')
            return {
                'batches': db.execute('SELECT COUNT(*) FROM batches').fetchone()[0],
                'datasets': {mode: db.execute(f'SELECT COUNT(*) FROM instruments WHERE mode=? AND market IN ({marks})', (mode,*active)).fetchone()[0] for mode in ('demo', 'imported')},
                'markets': {m: db.execute('SELECT COUNT(*) FROM instruments WHERE market=?', (m,)).fetchone()[0] for m in active},
                'calendar_override_revisions': db.execute('SELECT COUNT(*) FROM calendar_overrides').fetchone()[0],
                'schema_version': SCHEMA_VERSION,
            }

    def save_payload(self, provider, resource, payload, now):
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        sha = hashlib.sha256(raw.encode()).hexdigest()
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO provider_payloads VALUES(?,?,?,?,?,?)',
                       (str(uuid.uuid4()), provider, resource, stamp(now), sha, raw))
        return sha

    def save_calendar_override(self, raw, now):
        from .calendars import parse_override
        payload = parse_override(raw, now)
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        sha = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id FROM calendar_overrides WHERE sha256=?', (sha,)).fetchone()
            if existing:
                return {'id': existing['id'], 'sha256': sha, 'idempotent': True}
            iid = str(uuid.uuid4())
            db.execute('INSERT INTO calendar_overrides VALUES(?,?,?,?,?,?,?,?)',
                       (iid, payload['market'], payload['session_date'], payload['available_at'], stamp(now), sha, raw, canonical))
        return {'id': iid, 'sha256': sha, 'idempotent': False}

    def calendar_overrides(self, cutoff,markets=None):
        with self.connection() as db:
            return self._calendar_overrides(db, cutoff,markets)

    @staticmethod
    def _calendar_overrides(db, cutoff,markets=None):
        scope,values=Store._market_scope(markets,'market')
        rows = db.execute(f'''SELECT * FROM calendar_overrides WHERE available_at<=? AND observed_at<=? {scope}
                             ORDER BY observed_at,rowid''', (stamp(cutoff), stamp(cutoff),*values))
        result = {}
        for row in rows:
            result[(row['market'], row['session_date'])] = {**json.loads(row['payload_json']), 'sha256': row['sha256']}
        return result

    @staticmethod
    def _market_scope(markets,column):
        if markets is None:return '',()
        values=tuple(markets)
        if not values or any(m not in ('AU','HK','US') for m in values):
            raise ValueError('unsupported read market scope')
        return 'AND '+column+' IN ('+','.join('?' for _ in values)+')',values
