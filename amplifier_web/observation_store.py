"""Durable quiet checks and one-shot outbox; no provider or app state here."""
import copy
from contextlib import contextmanager
import json
import os
import sqlite3
import uuid

from amplifier_scheduling.store import fingerprint


class ObservationStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        os.chmod(path, 0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS observation_objects (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id));
          CREATE TABLE IF NOT EXISTS observation_commands (id TEXT PRIMARY KEY, intent TEXT, result TEXT);
          CREATE TABLE IF NOT EXISTS observation_terminals (watch TEXT PRIMARY KEY, semantic_key TEXT, input_id TEXT UNIQUE);
          CREATE TABLE IF NOT EXISTS observation_lease (id INTEGER PRIMARY KEY, owner TEXT, until REAL);
          CREATE INDEX IF NOT EXISTS observation_session ON observation_objects
            (kind, json_extract(body,'$.sessionId'), id);
          CREATE INDEX IF NOT EXISTS observation_watch ON observation_objects
            (kind, json_extract(body,'$.watchId'), json_extract(body,'$.phase'), id);
          CREATE INDEX IF NOT EXISTS observation_phase ON observation_objects
            (kind, json_extract(body,'$.phase'), id);
          CREATE INDEX IF NOT EXISTS observation_status ON observation_objects
            (kind, json_extract(body,'$.status'), id);
          CREATE INDEX IF NOT EXISTS observation_request ON observation_objects
            (kind, json_extract(body,'$.sessionId'), json_extract(body,'$.requestId'), id);
        ''')
        self.owner = str(uuid.uuid4())

    def close(self): self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def put(self, kind, value):
        self.db.execute('INSERT OR REPLACE INTO observation_objects VALUES (?,?,?)', (kind, value['id'], json.dumps(value, allow_nan=False)))
        return copy.deepcopy(value)

    def get(self, kind, identity, sid=None):
        row = self.db.execute('SELECT body FROM observation_objects WHERE kind=? AND id=?', (kind, identity)).fetchone()
        if not row: raise ValueError('Unknown observation record')
        value = json.loads(row[0])
        if sid is not None and value.get('sessionId') != sid: raise ValueError('Observation belongs to another conversation')
        return value

    @staticmethod
    def _selection(kind, sid=None, *, watch_id=None, phases=None, status=None, request_id=None, exclude_phase=None):
        conditions, args = ['kind=?'], [kind]
        for field, value in (('sessionId', sid), ('watchId', watch_id), ('status', status), ('requestId', request_id)):
            if value is not None:
                conditions.append(f"json_extract(body,'$.{field}')=?")
                args.append(value)
        if phases is not None:
            conditions.append("json_extract(body,'$.phase') IN (" + ','.join('?' for _ in phases) + ')')
            args.extend(phases)
        if exclude_phase is not None:
            conditions.append("json_extract(body,'$.phase') IS NOT ?")
            args.append(exclude_phase)
        # Without statistics SQLite may choose another kind-prefixed index and
        # scan unrelated bodies for the remaining predicates. Select the exact
        # owner/state index so fresh and restored stores have the same bound.
        index = ('observation_watch' if watch_id is not None else
                 'observation_request' if request_id is not None else
                 'observation_status' if status is not None else
                 'observation_phase' if phases is not None else
                 'observation_session' if sid is not None else
                 'sqlite_autoindex_observation_objects_1')
        return 'observation_objects INDEXED BY ' + index + ' WHERE ' + ' AND '.join(conditions), args

    def rows(self, kind, sid=None, **filters):
        selection, args = self._selection(kind, sid, **filters)
        # Keep the original (kind,id) primary-key iteration order even when an
        # expression index selects a smaller set before decoding its bodies.
        return [json.loads(row[0]) for row in self.db.execute(
            'SELECT body FROM ' + selection + ' ORDER BY id', args)]

    def exists(self, kind, **filters):
        selection, args = self._selection(kind, **filters)
        return self.db.execute('SELECT 1 FROM ' + selection + ' LIMIT 1', args).fetchone() is not None

    def command(self, identity, intent):
        row = self.db.execute('SELECT intent,result FROM observation_commands WHERE id=?', (identity,)).fetchone()
        if row:
            if row[0] != fingerprint(intent): raise ValueError('Observation request identity reused with different intent')
            return json.loads(row[1])

    def save_command(self, identity, intent, result):
        self.db.execute('INSERT INTO observation_commands VALUES (?,?,?)', (identity, fingerprint(intent), json.dumps(result)))

    def acquire(self, now):
        with self.transaction():
            row = self.db.execute('SELECT owner,until FROM observation_lease WHERE id=1').fetchone()
            if row and row[0] != self.owner and row[1] > now: return False
            if row and row[0] != self.owner:
                for item in self.rows('run', phases=('reading',)):
                    # Only a qualified read may be repeated after owner loss.
                    item.update(phase='abandoned_read', updatedAt=now)
                    self.put('run', item)
                for item in self.rows('outbox', phases=('submitting',)):
                    item.update(phase='unknown', detail='Admission may have happened; no replay.', updatedAt=now)
                    if item.get('presentationPhase') == 'claimed': item['presentationPhase'] = 'unknown'
                    self.put('outbox', item)
            self.db.execute('INSERT OR REPLACE INTO observation_lease VALUES (1,?,?)', (self.owner, now + 60))
            return True

    def owns(self, now):
        row = self.db.execute('SELECT owner,until FROM observation_lease WHERE id=1').fetchone()
        return bool(row and row[0] == self.owner and row[1] > now)

    def claim(self, identity, now):
        with self.transaction():
            watch = self.get('watch', identity)
            if not self.owns(now) or watch['status'] != 'active' or watch['nextDue'] > now: return None
            if self.exists('run', watch_id=identity, phases=('reading',)): return None
            abandoned = next((r for r in self.rows('run', watch_id=identity, phases=('abandoned_read',)) if r['watchRevision'] == watch['revision']), None)
            if abandoned:
                abandoned.update(owner=self.owner, phase='reading', updatedAt=now)
                watch.update(nextDue=now + watch['intervalSeconds'])
                self.put('watch', watch)
                return self.put('run', abandoned)
            occurrence = 'observation:' + identity + ':' + str(watch['sequence'])
            run = {'id': occurrence, 'watchId': identity, 'sessionId': watch['sessionId'], 'watchRevision': watch['revision'],
                'owner': self.owner, 'phase': 'reading', 'createdAt': now, 'updatedAt': now}
            watch.update(sequence=watch['sequence'] + 1, nextDue=now + watch['intervalSeconds'])
            self.put('watch', watch)
            return self.put('run', run)

    def review(self, identity, reason, now):
        with self.transaction():
            watch = self.get('watch', identity)
            if watch['status'] not in {'cancelled', 'paused', 'needs_review'}:
                watch.update(status='needs_review', revision=watch['revision'] + 1, reason=reason, updatedAt=now)
                self.put('watch', watch)
            self.suppress(identity, now)

    def suppress(self, identity, now):
        for row in self.rows('outbox', watch_id=identity, phases=('pending', 'waiting_worker')):
            row.update(phase='suppressed', updatedAt=now)
            self.put('outbox', row)

    def terminal(self, watch, outcome, now):
        """Called inside the same transaction as result acceptance/expiry."""
        existing = self.db.execute('SELECT input_id FROM observation_terminals WHERE watch=?', (watch['id'],)).fetchone()
        if existing: return
        identity = 'observation:' + watch['id'] + ':result'
        self.db.execute('INSERT INTO observation_terminals VALUES (?,?,?)', (watch['id'], outcome.get('semanticKey'), identity))
        watch.update(status='ended', revision=watch['revision'] + 1, updatedAt=now, terminal=outcome['status'])
        self.put('watch', watch)
        self.put('outbox', {'id': identity, 'inputId': identity, 'watchId': watch['id'], 'sessionId': watch['sessionId'],
            'phase': 'pending', 'outcome': outcome, 'createdAt': now, 'updatedAt': now})

    def expire(self, identity, now):
        with self.transaction():
            watch = self.get('watch', identity)
            if watch['status'] != 'active' or now < watch['expiresAt']: return
            self.terminal(watch, {'status': 'expired', 'summary': 'Watching ended. This does not establish product completion or failure and did not cancel the watched work.',
                'lastVerified': watch.get('lastResult'), 'evidence': []}, now)

    def commit(self, run, outcome, now):
        with self.transaction():
            current = self.get('run', run['id'])
            watch = self.get('watch', run['watchId'])
            if current['phase'] != 'reading': return False
            if not self.owns(now) or current['owner'] != self.owner or watch['status'] != 'active' or watch['revision'] != run['watchRevision']:
                current.update(phase='discarded', updatedAt=now)
                self.put('run', current)
                return False
            if now >= watch['expiresAt']:
                self.terminal(watch, {'status': 'expired', 'summary': 'Watching ended before this read completed. Product outcome is not inferred.', 'lastVerified': watch.get('lastResult'), 'evidence': []}, now)
                current.update(phase='discarded', updatedAt=now)
                self.put('run', current)
                return False
            current.update(phase=outcome['status'], result=outcome, updatedAt=now)
            self.put('run', current)
            watch.update(lastResult=outcome, cursor=outcome.get('cursor'), updatedAt=now)
            self.put('watch', watch)
            if outcome['status'] != 'pending': self.terminal(watch, outcome, now)
            # Audit pruning never removes terminal tombstones or outbox identities.
            rows = sorted(self.rows('run', watch_id=watch['id'], exclude_phase='reading'), key=lambda r:r['createdAt'], reverse=True)
            for old in rows[100:]: self.db.execute("DELETE FROM observation_objects WHERE kind='run' AND id=?", (old['id'],))
            return True

    def outbox_phase(self, identity, before, phase, now, **fields):
        with self.transaction():
            item = self.get('outbox', identity)
            if item['phase'] not in before: return None
            item.update(phase=phase, updatedAt=now, **fields)
            return self.put('outbox', item)
