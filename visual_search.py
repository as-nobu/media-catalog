"""Image retrieval from SQLite thumbnails only. Never open a catalog source path.

Only the optional, explicitly configured model directory is read from disk.
The standard engine requires OpenCV, NumPy and Pillow; AI imports are lazy.
"""
from __future__ import annotations

import hashlib
import heapq
import io
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

FEATURE_VERSION = 'visual-v1'
LOCAL_VERSION = 'sift-v1'
WRITE_BATCH = 24
MAX_FEATURES = 600
MAX_PREVIEW_PIXELS = 16_000_000
_INDEX_LOCK = threading.RLock()


class SearchCancelled(Exception):
    pass


def check_cancel(stop):
    if stop():
        raise SearchCancelled()


@contextmanager
def index_lock(stop):
    """Serialize cache builders, including interactive searches, without blocking Qt."""
    while not _INDEX_LOCK.acquire(timeout=.1):
        check_cancel(stop)
    try:
        check_cancel(stop)
        yield
    finally:
        _INDEX_LOCK.release()


def opencv():
    try:
        import cv2
        if not hasattr(cv2, 'SIFT_create'):
            raise ImportError('SIFT unavailable')
        return cv2
    except ImportError as exc:
        raise RuntimeError('OpenCVを読み込めません。IMAGE_SEARCH.ja.mdの導入手順を確認してください。') from exc


def decode(blob):
    with Image.open(io.BytesIO(blob)) as source:
        if source.width * source.height > MAX_PREVIEW_PIXELS:
            raise ValueError('保存済み画像が検索用の画素数上限を超えています。')
        image = ImageOps.exif_transpose(source)
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        rgba = image.convert('RGBA')
        rgb = Image.new('RGB', rgba.size, 'white')
        rgb.paste(rgba, mask=rgba.getchannel('A'))
        return rgb


def regions(image):
    """Whole image plus four overlapping regions; never discard outer edges."""
    w, h = image.size
    yield image
    if min(w, h) >= 48:
        cw, ch = round(w * .65), round(h * .65)
        for x, y in ((0, 0), (w-cw, 0), (0, h-ch), (w-cw, h-ch)):
            yield image.crop((x, y, x+cw, y+ch))


def unit(a):
    return a / np.maximum(np.linalg.norm(a, axis=-1, keepdims=True), 1e-8)


def basic_vector(image):
    cv = opencv()
    rgb = np.asarray(image.resize((64, 64), Image.Resampling.BILINEAR))
    gray = cv.cvtColor(rgb, cv.COLOR_RGB2GRAY).astype(np.float32) / 255
    dct = cv.dct(gray)[:8, :8].flatten()
    dct[0] = 0
    hsv = cv.cvtColor(rgb, cv.COLOR_RGB2HSV)
    hist = cv.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
    gx, gy = cv.Sobel(gray, cv.CV_32F, 1, 0), cv.Sobel(gray, cv.CV_32F, 0, 1)
    mag, angle = cv.cartToPolar(gx, gy)
    bins = (angle * 8 / (2*np.pi)).astype(int) % 8
    hog = np.concatenate([np.bincount(bins[y:y+16, x:x+16].ravel(),
                         weights=mag[y:y+16, x:x+16].ravel(), minlength=8)
                         for y in range(0, 64, 16) for x in range(0, 64, 16)])
    return unit(np.concatenate((unit(dct)*.6, unit(hist)*.45, unit(hog)*.55)).astype(np.float32))


def local_features(image):
    cv = opencv()
    gray = cv.cvtColor(np.asarray(image), cv.COLOR_RGB2GRAY)
    keys, desc = cv.SIFT_create(nfeatures=MAX_FEATURES, contrastThreshold=.02).detectAndCompute(gray, None)
    points = np.asarray([k.pt for k in keys], dtype=np.float32).reshape(-1, 2)
    # SIFT histograms have integer-valued bins; keep compact bytes on disk.
    desc = np.clip(desc, 0, 255).astype(np.uint8) if desc is not None else np.empty((0, 128), np.uint8)
    return dict(points=points, descriptors=desc, shape=np.asarray(image.size, np.int32))


def pack(**arrays):
    output = io.BytesIO()
    np.savez_compressed(output, **arrays)
    return output.getvalue()


def unpack(blob):
    with np.load(io.BytesIO(blob), allow_pickle=False) as data:
        return {k: data[k] for k in data.files}


class StandardEncoder:
    key = FEATURE_VERSION + ':standard'

    def __init__(self):
        opencv()

    def encode(self, image, multiple=True):
        return np.stack([basic_vector(p) for p in (regions(image) if multiple else [image])])


def dino_model_key(directory, stop=lambda: False):
    """Identify cached features without loading Torch or running inference."""
    check_cancel(stop)
    folder = Path(directory).expanduser()
    required = ('config.json', 'preprocessor_config.json', 'model.safetensors')
    if not directory or not folder.is_dir() or not all((folder/p).is_file() for p in required):
        raise RuntimeError('DINOv2モデルがありません。IMAGE_SEARCH.ja.mdのAI設定手順を確認してください。')
    digest = hashlib.sha256()
    for filename in required:
        digest.update(filename.encode())
        with (folder/filename).open('rb') as stream:
            while block := stream.read(1024*1024):
                check_cancel(stop)
                digest.update(block)
    config = json.loads((folder/'config.json').read_text(encoding='utf-8'))
    if config.get('model_type') != 'dinov2':
        raise RuntimeError('DINOv2形式のモデルフォルダを指定してください。')
    return FEATURE_VERSION + ':dinov2:' + digest.hexdigest()


class DinoEncoder:
    """Load a local safetensors model; no hub requests or downloaded Python code."""
    def __init__(self, directory, stop=lambda: False):
        self.key = dino_model_key(directory, stop)
        folder = Path(directory).expanduser()
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModel
        except ImportError as exc:
            raise RuntimeError('AI検索の追加パッケージがありません。IMAGE_SEARCH.ja.mdを確認してください。') from exc
        self.torch = torch
        # CPU by default, modest thread count, no CUDA installation required.
        torch.set_num_threads(min(4, torch.get_num_threads()))
        self.processor = AutoImageProcessor.from_pretrained(str(folder), local_files_only=True,
                                                           trust_remote_code=False, use_fast=False)
        check_cancel(stop)
        self.model = AutoModel.from_pretrained(str(folder), local_files_only=True,
                                              trust_remote_code=False, use_safetensors=True).eval()
        self.stop = stop

    def encode(self, image, multiple=True):
        # Same five regions, resize, FP32 weights and CLS vectors as visual-v1.
        # Batch only one page (at most five inputs), bounding peak memory and
        # cancellation latency without changing the cache/model fingerprint.
        check_cancel(self.stop)
        parts = [part.resize((224, 224)) for part in (regions(image) if multiple else [image])]
        inputs = self.processor(images=parts, return_tensors='pt',
                                do_resize=False, do_center_crop=False)
        with self.torch.inference_mode():
            vectors = self.model(**inputs).last_hidden_state[:, 0].float().cpu().numpy()
        check_cancel(self.stop)
        return unit(vectors).astype(np.float32)


# Page thumbnails are authoritative. Legacy DBs with only thumbs get page 1.
def page_query(with_data):
    return '''SELECT f.id AS file_id,f.registration_uid,f.revision,p.page_number''' + (',p.data' if with_data else '') + '''
           FROM files f JOIN page_thumbs p ON p.file_id=f.id
           UNION ALL
           SELECT f.id,f.registration_uid,f.revision,1''' + (',t.data' if with_data else '') + '''
           FROM files f JOIN thumbs t ON t.file_id=f.id
           WHERE NOT EXISTS (SELECT 1 FROM page_thumbs p WHERE p.file_id=f.id)'''


PAGES = page_query(True)
PAGE_KEYS = page_query(False)


@dataclass
class SearchReport:
    hits: list
    pages: int
    errors: int
    without_thumbnail: int
    indexed: int
    compared: int


class SearchIndex:
    def __init__(self, db):
        self.db = db

    def thumbnail_uids(self):
        """Files with saved previews, without reading any image bytes."""
        with self.db.connect() as c:
            return {row[0] for row in c.execute('WITH pages AS ('+PAGE_KEYS+') '
                                                'SELECT DISTINCT registration_uid FROM pages')}

    @staticmethod
    def _scope(uid=None, uids=None):
        if uid is not None and uids is not None:
            raise ValueError('Specify uid or uids, not both')
        if uid is not None:
            return ' AND p.registration_uid=?', (uid,)
        if uids is not None:
            ids = tuple(dict.fromkeys(uids))
            return (' AND p.registration_uid IN (' + ','.join('?' for _ in ids) + ')', ids) if ids else (' AND 0', ())
        return '', ()

    def pending(self, key, uid=None, *, uids=None):
        """Metadata-only backlog, counted in saved pages (not source files).

        A corrupt thumbnail's recorded error counts as an attempted page, so it
        does not cause the same unproductive confirmation at every startup.
        """
        scope, args = self._scope(uid, uids)
        with self.db.connect() as c:
            rows = c.execute('''WITH pages AS ('''+PAGE_KEYS+''')
                SELECT p.registration_uid,count(*) AS n FROM pages p
                LEFT JOIN visual_features v ON v.file_id=p.file_id
                  AND v.page_number=p.page_number AND v.engine=?
                  AND v.registration_uid=p.registration_uid AND v.revision=p.revision
                WHERE v.file_id IS NULL ''' + scope +
                ' GROUP BY p.registration_uid', (key, *args)).fetchall()
        return {r['registration_uid']: r['n'] for r in rows}

    def build(self, encoder, stop=lambda: False, progress=lambda *a: None, rebuild=False, uid=None,
              *, uids=None, file_progress=lambda uid: None):
        with index_lock(stop):
            return self._build(encoder, stop, progress, rebuild, uid, uids, file_progress)

    def _build(self, encoder, stop, progress, rebuild, uid, uids, file_progress):
        key = encoder.key
        uids = tuple(uids) if uids is not None else None
        scope, args = self._scope(uid, uids)
        if rebuild:
            with self.db.connect() as c:
                c.execute('DELETE FROM visual_features WHERE engine=?' +
                          scope.replace('p.registration_uid', 'registration_uid'), (key, *args))
        remaining = self.pending(key, uid, uids=uids)
        total = sum(remaining.values())
        # Already cached files need no thumbnail reads and still count as done.
        for cached_uid in ((uids if uids is not None else (uid,)) if uid is not None or uids is not None else ()):
            if cached_uid not in remaining:
                file_progress(cached_uid)
        if not total:
            return 0
        done = new = 0
        after = (0, 0)
        buffered = []
        last_save = time.monotonic()

        def flush():
            nonlocal buffered, new, last_save
            if not buffered:
                return
            batch, buffered = buffered, []
            saved_uids = []
            # No write transaction is held while decoding or running inference.
            with self.db.connect() as c:
                for row, vectors, local, error in batch:
                    identity = (row['file_id'], row['registration_uid'], row['revision'])
                    if local is not None:
                        c.execute('''INSERT OR REPLACE INTO visual_local_features
                            (file_id,page_number,version,registration_uid,revision,data)
                            SELECT id,?,?,registration_uid,revision,? FROM files
                            WHERE id=? AND registration_uid=? AND revision=?''',
                            (row['page_number'], LOCAL_VERSION, local, *identity))
                    saved = c.execute('''INSERT OR REPLACE INTO visual_features
                        (file_id,page_number,engine,registration_uid,revision,vectors,local_data,error)
                        SELECT id,?,?,registration_uid,revision,?,NULL,? FROM files
                        WHERE id=? AND registration_uid=? AND revision=?''',
                        (row['page_number'], key, vectors, error, *identity))
                    new += saved.rowcount
                    if saved.rowcount:
                        saved_uids.append(row['registration_uid'])
            # Report files only after their last page has actually committed.
            for saved_uid in saved_uids:
                if remaining.get(saved_uid, 0) > 0:
                    remaining[saved_uid] -= 1
                    if remaining[saved_uid] == 0:
                        file_progress(saved_uid)
            last_save = time.monotonic()

        try:
            while True:
                check_cancel(stop)
                with self.db.connect() as c:
                    rows = c.execute('''WITH pages AS ('''+PAGES+''')
                        SELECT p.*,l.data AS shared_local,
                          CASE WHEN l.data IS NULL THEN
                            (SELECT old.local_data FROM visual_features old
                             WHERE old.file_id=p.file_id AND old.page_number=p.page_number
                               AND old.registration_uid=p.registration_uid AND old.revision=p.revision
                               AND old.engine LIKE ? AND old.local_data IS NOT NULL LIMIT 1)
                          END AS legacy_local
                        FROM pages p LEFT JOIN visual_features v
                          ON v.file_id=p.file_id AND v.page_number=p.page_number AND v.engine=?
                          AND v.registration_uid=p.registration_uid AND v.revision=p.revision
                        LEFT JOIN visual_local_features l
                          ON l.file_id=p.file_id AND l.page_number=p.page_number AND l.version=?
                          AND l.registration_uid=p.registration_uid AND l.revision=p.revision
                        WHERE v.file_id IS NULL AND (p.file_id,p.page_number)>(?,?)''' + scope + '''
                        ORDER BY p.file_id,p.page_number LIMIT ?''',
                        (FEATURE_VERSION+':%', key, LOCAL_VERSION, *after, *args, WRITE_BATCH)).fetchall()
                if not rows:
                    break
                for row in rows:
                    check_cancel(stop)
                    after = (row['file_id'], row['page_number'])
                    # Decode failures belong to this cached page. Model/DB failures
                    # must abort, not poison every page's cache as an image error.
                    try:
                        image = decode(row['data'])
                    except Exception as exc:
                        vectors, local, error = None, None, str(exc)[:500]
                    else:
                        vectors = pack(vectors=encoder.encode(image))
                        # Legacy inline SIFT remains untouched. Copy it lazily
                        # only when this page needs a missing engine; otherwise
                        # new pages share one SIFT record across both engines.
                        local = None if row['shared_local'] is not None else row['legacy_local']
                        if row['shared_local'] is None and local is None:
                            local = pack(**local_features(image))
                        error = ''
                    check_cancel(stop)
                    buffered.append((row, vectors, local, error))
                    done += 1
                    if len(buffered) >= WRITE_BATCH or time.monotonic() - last_save >= 1.0:
                        flush()
                    progress('index', done, total)
                flush()
        finally:
            # Cancellation/model errors keep completed pages, including a short
            # final batch. The in-flight page is retried on the next run.
            flush()
        return new

    def search(self, query_blob, encoder=None, stop=lambda: False, progress=lambda *a: None,
               allowed_uids=None, detailed=True, limit=100, candidates=300, rebuild=False):
        encoder = encoder or StandardEncoder()
        query = decode(query_blob)
        if min(query.size) < 8:
            raise ValueError('検索範囲をもう少し大きく選択してください。')
        qvectors = encoder.encode(query, multiple=False)
        qlocal = local_features(query)
        indexed = self.build(encoder, stop, progress, rebuild)
        check_cancel(stop)
        allowed = None if allowed_uids is None else set(allowed_uids)
        # Keep just IDs/scores in memory; vector and descriptor reads are bounded.
        ranking = []
        errors = pages = 0
        after = (0, 0)
        while True:
            check_cancel(stop)
            with self.db.connect() as c:
                rows = c.execute('''SELECT v.file_id,v.page_number,v.registration_uid,v.revision,v.vectors,v.error
                    FROM visual_features v JOIN files f ON f.id=v.file_id
                    AND f.registration_uid=v.registration_uid AND f.revision=v.revision
                    WHERE engine=? AND (v.file_id,v.page_number)>(?,?)
                    ORDER BY v.file_id,v.page_number LIMIT 128''', (encoder.key, *after)).fetchall()
            if not rows:
                break
            for row in rows:
                check_cancel(stop)
                after = (row['file_id'], row['page_number'])
                if allowed is not None and row['registration_uid'] not in allowed:
                    continue
                if row['error']:
                    errors += 1
                    continue
                try:
                    vectors = unpack(row['vectors'])['vectors']
                    score = float(np.max(vectors @ qvectors.T))
                    if not np.isfinite(score):
                        raise ValueError('invalid cached vector')
                except Exception:
                    errors += 1
                    continue
                pages += 1
                ranking.append((score, row['file_id'], row['page_number'], row['registration_uid'], row['revision']))
            progress('rank', pages, 0)
        ranking = sorted(ranking, reverse=True) if detailed else heapq.nlargest(candidates, ranking)
        best = []
        for n, (similarity, fid, page, uid, revision) in enumerate(ranking, 1):
            check_cancel(stop)
            with self.db.connect() as c:
                row = c.execute('''SELECT coalesce(v.local_data,l.data) AS local_data
                    FROM visual_features v JOIN files f ON f.id=v.file_id
                    AND f.registration_uid=v.registration_uid AND f.revision=v.revision
                    LEFT JOIN visual_local_features l ON l.file_id=v.file_id AND l.page_number=v.page_number
                    AND l.registration_uid=v.registration_uid AND l.revision=v.revision AND l.version=?
                    WHERE v.file_id=? AND v.page_number=? AND v.engine=? AND v.registration_uid=? AND v.revision=?''',
                    (LOCAL_VERSION,fid,page,encoder.key,uid,revision)).fetchone()
            if row is None:
                continue
            try:
                geometry, polygon, inliers = match_geometry(qlocal, unpack(row['local_data']))
            except (ValueError, KeyError, TypeError, opencv().error):
                geometry, polygon, inliers = 0., None, 0
            # Geometrically verified copies first, then visual neighbours. This
            # score is only a ranking value, never a probability or identity proof.
            score = (2. + geometry) if geometry else max(-1., min(1., similarity))
            item = dict(file_id=fid, registration_uid=uid, revision=revision, page_number=page,
                        score=score, similarity=similarity, inliers=inliers, polygon=polygon)
            entry = (score, similarity, fid, page, item)
            if len(best) < limit:
                heapq.heappush(best, entry)
            elif entry[:4] > best[0][:4]:
                heapq.heapreplace(best, entry)
            progress('match', n, len(ranking))
        hits = []
        for *_, hit in sorted(best, reverse=True):
            check_cancel(stop)
            row = self.db.file_record(hit['registration_uid'])
            if row is not None and row['revision'] == hit['revision']:
                hits.append(dict(row, **{k:v for k,v in hit.items() if k not in row}))
        with self.db.connect() as c:
            uids = [r[0] for r in c.execute('''SELECT registration_uid FROM files f
                WHERE NOT EXISTS (SELECT 1 FROM thumbs t WHERE t.file_id=f.id)
                AND NOT EXISTS (SELECT 1 FROM page_thumbs p WHERE p.file_id=f.id)''')]
        absent = sum(allowed is None or uid in allowed for uid in uids)
        return SearchReport(hits, pages, errors, absent, indexed, len(ranking))


def match_geometry(query, candidate):
    cv = opencv()
    q, t = query['descriptors'], candidate['descriptors']
    if len(q) < 8 or len(t) < 8:
        return 0., None, 0
    pairs = cv.BFMatcher(cv.NORM_L2).knnMatch(q.astype(np.float32), t.astype(np.float32), k=2)
    good = [pair[0] for pair in pairs if len(pair)==2 and pair[0].distance < .72*pair[1].distance]
    # Do not count repeated matches to the same target point as evidence.
    good = list({m.trainIdx:m for m in sorted(good, key=lambda m:m.distance, reverse=True)}.values())
    if len(good) < 8:
        return 0., None, 0
    source = query['points'][[m.queryIdx for m in good]]
    target = candidate['points'][[m.trainIdx for m in good]]
    matrix, mask = cv.findHomography(source, target, cv.RANSAC, 3., maxIters=1000, confidence=.995)
    if matrix is None or mask is None:
        return 0., None, 0
    valid = mask.ravel().astype(bool)
    count = int(valid.sum())
    w, h = query['shape']
    coverage = cv.contourArea(cv.convexHull(source[valid])) / max(1, int(w)*int(h)) if count>=3 else 0
    if count < 8 or count/len(good) < .4 or coverage < .08:
        return 0., None, 0
    corners = np.float32([[0,0],[w,0],[w,h],[0,h]]).reshape(-1,1,2)
    polygon = cv.perspectiveTransform(corners, matrix).reshape(-1,2)
    tw, th = candidate['shape']
    area = abs(cv.contourArea(polygon))
    if not np.isfinite(polygon).all() or not cv.isContourConvex(polygon) or not 16 <= area <= 4*int(tw)*int(th):
        return 0., None, 0
    strength = .5*min(1., count/40) + .3*count/len(good) + .2*min(1., coverage/.5)
    return strength, polygon.tolist(), count
