"""Bounded, disposable RAM cache of existing vectors; no source image access."""
from contextlib import contextmanager
import heapq
import threading
from zipfile import BadZipFile

import numpy as np

# One engine/database at a time, including approximate Python metadata overhead.
MAX_CACHE_BYTES = 512 * 1024 * 1024
BLOCK_PAGES = 512
_lock = threading.Lock()
_cached = None


@contextmanager
def cache_lock(stop):
    from visual_search import check_cancel
    while not _lock.acquire(timeout=.1): check_cancel(stop)
    try:
        check_cancel(stop)
        yield
    finally:
        _lock.release()


def generation(db, key):
    with db.connect() as c:
        row = c.execute('SELECT generation FROM visual_search_state WHERE engine=?', (key,)).fetchone()
        if row is None:
            c.execute('INSERT OR IGNORE INTO visual_search_state VALUES (?,0)', (key,))
            row = c.execute('SELECT generation FROM visual_search_state WHERE engine=?', (key,)).fetchone()
        return row[0]


def blocks(db, key, dimensions, stop):
    from visual_search import unpack, check_cancel
    after = (0, 0)
    # Fetch bounded BLOB batches and close the read cursor before CPU work.
    with db.connect() as c:
        while True:
            check_cancel(stop)
            rows = c.execute('''SELECT v.file_id,v.page_number,v.registration_uid,v.revision,v.vectors,v.error
                FROM visual_features v JOIN files f ON f.id=v.file_id
                  AND f.registration_uid=v.registration_uid AND f.revision=v.revision
                WHERE v.engine=? AND f.missing=0 AND (v.file_id,v.page_number)>(?,?)
                ORDER BY v.file_id,v.page_number LIMIT ?''', (key, *after, BLOCK_PAGES)).fetchall()
            if not rows: break
            after = (rows[-1]['file_id'], rows[-1]['page_number'])
            arrays, records, offsets, errors = [], [], [], []
            count = 0
            for row in rows:
                check_cancel(stop)
                try:
                    if row['error']: raise ValueError('recorded thumbnail error')
                    vectors = unpack(row['vectors'])['vectors']
                    if (vectors.ndim != 2 or vectors.shape[1] != dimensions or
                            not 1 <= len(vectors) <= 5 or not np.isfinite(vectors).all()):
                        raise ValueError('invalid cached vectors')
                    vectors = vectors.astype(np.float32, copy=False)
                except (ValueError, KeyError, TypeError, OSError, EOFError, BadZipFile):
                    errors.append(row['registration_uid'])
                    continue
                records.append((row['file_id'], row['page_number'], row['registration_uid'], row['revision']))
                arrays.append(vectors)
                offsets.append(count)
                count += len(vectors)
            matrix = np.concatenate(arrays) if arrays else np.empty((0, dimensions), np.float32)
            yield matrix, np.asarray(offsets, np.int32), records, errors


def rank(db, key, query, stop, progress, allowed=None, candidates=300):
    """Return exact global-vector top candidates; only geometric verification is capped.

    A changing DB is never published as a reusable snapshot. Final results are
    also validated against live records by SearchIndex and the catalog view.
    """
    from visual_search import check_cancel
    global _cached
    with cache_lock(stop):
        token = (db, key, generation(db, key), query.shape[1])
        cached = _cached is not None and _cached[0] == token
        source = _cached[1] if cached else blocks(db, key, query.shape[1], stop)
        if not cached: _cached = None
        retained, memory, keep = [], 0, not cached
        ranking, pages, errors = [], 0, 0
        for block in source:
            check_cancel(stop)
            matrix, offsets, records, bad = block
            errors += sum(allowed is None or uid in allowed for uid in bad)
            if len(records):
                # einsum avoids launching a many-thread BLAS pool for tiny dots.
                dots = np.einsum('ij,kj->ik', matrix, query, optimize=False).max(axis=1)
                scores = np.maximum.reduceat(dots, offsets)
                for score, record in zip(scores, records):
                    if allowed is None or record[2] in allowed:
                        pages += 1
                        item = (float(score), *record)
                        if candidates is None:
                            ranking.append(item)
                        elif len(ranking) < candidates:
                            heapq.heappush(ranking, item)
                        elif item > ranking[0]:
                            heapq.heapreplace(ranking, item)
            progress('rank', pages, 0)
            if keep:
                memory += matrix.nbytes + offsets.nbytes + len(records)*256 + len(bad)*128
                if memory <= MAX_CACHE_BYTES:
                    retained.append(block)
                else:
                    retained.clear()
                    keep = False  # Large catalogs still work using bounded streaming.
        check_cancel(stop)
        if keep and generation(db, key) == token[2]:
            _cached = token, retained
        return sorted(ranking, reverse=True), pages, errors
