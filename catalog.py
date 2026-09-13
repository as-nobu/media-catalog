"""Metadata-only catalog. This module must never read source file contents."""
from __future__ import annotations
import os
import sqlite3
import time
import uuid
import json
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.gif', '.webp'}
VIDEO_EXT = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.m4v', '.webm', '.mpg', '.mpeg'}
POWERPOINT_EXT = {'.ppt', '.pptx', '.pptm'}


def kind_for(name):
    ext = Path(name).suffix.lower()
    return 'image' if ext in IMAGE_EXT else 'video' if ext in VIDEO_EXT else 'powerpoint' if ext in POWERPOINT_EXT else None


def norm(path):
    # No resolve()/realpath(): avoid following placeholder reparse points.
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def within(path, parent):
    try:
        return os.path.commonpath([norm(path), norm(parent)]) == norm(parent)
    except ValueError:
        return False


class Catalog:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS roots (
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                    last_scan REAL, error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL REFERENCES roots(id),
                    path TEXT UNIQUE NOT NULL, parent TEXT NOT NULL, name TEXT NOT NULL,
                    kind TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    changed_at REAL NOT NULL, seen TEXT NOT NULL,
                    missing INTEGER NOT NULL DEFAULT 0,
                    thumb_size INTEGER, thumb_mtime INTEGER,
                    revision INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', retry_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS thumbs (
                    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                    data BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS files_parent ON files(parent);
                CREATE INDEX IF NOT EXISTS files_root ON files(root_id);
                CREATE INDEX IF NOT EXISTS files_missing ON files(missing);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS folders (path TEXT PRIMARY KEY, root_id INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS exclusions (path TEXT PRIMARY KEY);
            ''')
            if 'timed_out' not in {r[1] for r in c.execute('PRAGMA table_info(files)')}:
                c.execute('ALTER TABLE files ADD COLUMN timed_out INTEGER NOT NULL DEFAULT 0')
                c.execute("UPDATE files SET timed_out=1 WHERE error LIKE '%タイムアウト%'")
            if 'registration_uid' not in {r[1] for r in c.execute('PRAGMA table_info(files)')}:
                c.execute('ALTER TABLE files ADD COLUMN registration_uid TEXT')
            c.execute('UPDATE files SET registration_uid=lower(hex(randomblob(16))) WHERE registration_uid IS NULL')
            c.execute('CREATE UNIQUE INDEX IF NOT EXISTS files_registration_uid ON files(registration_uid)')
            if 'page_count' not in {r[1] for r in c.execute('PRAGMA table_info(files)')}:
                c.execute('ALTER TABLE files ADD COLUMN page_count INTEGER NOT NULL DEFAULT 1')
            for name, declaration in [('metadata_json', "TEXT NOT NULL DEFAULT '{}'"),
                                      ('tags', "TEXT NOT NULL DEFAULT ''"),
                                      ('favorite', 'INTEGER NOT NULL DEFAULT 0'),
                                      ('memo', "TEXT NOT NULL DEFAULT ''")]:
                if name not in {r[1] for r in c.execute('PRAGMA table_info(files)')}:
                    c.execute(f'ALTER TABLE files ADD COLUMN {name} {declaration}')
            c.execute('''CREATE TABLE IF NOT EXISTS page_thumbs (
                file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL, data BLOB NOT NULL,
                PRIMARY KEY(file_id,page_number))''')
            if not c.execute("SELECT 1 FROM settings WHERE key='four_page_thumbnail_v1'").fetchone():
                # Keep old thumbnails visible until replacement succeeds; respect timeout holds.
                c.execute("""UPDATE files SET thumb_size=NULL,thumb_mtime=NULL
                    WHERE kind='powerpoint' OR substr(lower(name),-4)='.tif' OR substr(lower(name),-5)='.tiff'""")
                c.execute("INSERT INTO settings VALUES ('four_page_thumbnail_v1','1')")
            if not c.execute("SELECT 1 FROM settings WHERE key='pages_512_v1'").fetchone():
                c.execute('UPDATE files SET thumb_size=NULL,thumb_mtime=NULL')
                c.execute("INSERT INTO settings VALUES ('pages_512_v1','1')")
            if not c.execute("SELECT 1 FROM settings WHERE key='empty_ppt_v1'").fetchone():
                c.execute("""UPDATE files SET retry_at=0,timed_out=0,thumb_size=NULL,error=''
                    WHERE kind='powerpoint' AND (size=0 OR instr(error,'スライドがありません')>0)""")
                c.execute("INSERT INTO settings VALUES ('empty_ppt_v1','1')")

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def get_setting(self, key, default):
        with self.connect() as c:
            r = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return r[0] if r else default

    def set_setting(self, key, value):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(value)))

    def roots(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM roots ORDER BY path')]

    def add_root(self, path):
        path = norm(path)
        if not os.path.isdir(path):
            raise ValueError('アクセス可能なフォルダを指定してください。')
        storage = norm(Path(self.path).parent)
        if within(path,storage) or within(storage,path):
            raise ValueError('アプリの保存領域と重なるフォルダは登録できません。')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for r in list(c.execute('SELECT path FROM exclusions')):
                if within(r[0],path) or within(path,r[0]):
                    c.execute('DELETE FROM exclusions WHERE path=?',(r[0],))
            roots = list(c.execute('SELECT * FROM roots'))
            if any(within(path,r['path']) for r in roots):
                return '登録済み範囲です（除外されていた場合は再登録しました）。'
            root_id = c.execute('INSERT INTO roots(path) VALUES (?)',(path,)).lastrowid
            merged = 0
            for r in roots:
                if within(r['path'],path):
                    c.execute('UPDATE files SET root_id=? WHERE root_id=?',(root_id,r['id']))
                    c.execute('UPDATE folders SET root_id=? WHERE root_id=?',(root_id,r['id']))
                    c.execute('DELETE FROM roots WHERE id=?',(r['id'],))
                    merged += 1
            return f'登録しました。下位の登録{merged}件を統合しました。'

    def remove_folder(self,path):
        """Forget only catalog rows; subfolders of a root become exclusions."""
        path = norm(path)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            roots = list(c.execute('SELECT * FROM roots'))
            if not any(within(path,r['path']) for r in roots):
                return 0
            ids = [r['id'] for r in c.execute('SELECT id,path FROM files') if within(r['path'],path)]
            c.executemany('DELETE FROM files WHERE id=?',[(i,) for i in ids])
            for r in list(c.execute('SELECT path FROM folders')):
                if within(r[0],path):
                    c.execute('DELETE FROM folders WHERE path=?',(r[0],))
            exact = next((r for r in roots if r['path']==path),None)
            if exact:
                c.execute('DELETE FROM roots WHERE id=?',(exact['id'],))
            else:
                c.execute('INSERT OR IGNORE INTO exclusions VALUES (?)',(path,))
            return len(ids)

    def excluded(self):
        with self.connect() as c:
            return [r[0] for r in c.execute('SELECT path FROM exclusions')]

    def scan(self, root, stop=lambda: False, workers=4):
        """Commit upserts in batches. Only a complete root scan can mark missing.

        scandir + stat(follow_symlinks=False) reads directory metadata only.
        Symlinks/junctions are skipped; cloud reparse points are not blanket-skipped.
        """
        token = uuid.uuid4().hex
        pending, errors, stack = [], [], [root['path']]
        count = 0
        now = time.time()
        excluded = self.excluded()
        discovered = set()

        def flush():
            nonlocal count
            if not pending:
                return
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                if not c.execute('SELECT 1 FROM roots WHERE id=? AND path=?',(root['id'],root['path'])).fetchone():
                    pending.clear()
                    return
                omitted = [r[0] for r in c.execute('SELECT path FROM exclusions')]
                pending[:] = [r for r in pending if not any(within(r[1],p) for p in omitted)]
                c.executemany('''INSERT INTO files
                    (root_id,path,parent,name,kind,size,mtime_ns,changed_at,seen,registration_uid)
                    VALUES (?,?,?,?,?,?,?,?,?,lower(hex(randomblob(16))))
                    ON CONFLICT(path) DO UPDATE SET
                    changed_at=CASE WHEN files.size!=excluded.size OR files.mtime_ns!=excluded.mtime_ns
                        THEN excluded.changed_at ELSE files.changed_at END,
                    error=CASE WHEN files.size!=excluded.size OR files.mtime_ns!=excluded.mtime_ns
                        THEN '' ELSE files.error END,
                    retry_at=CASE WHEN files.size!=excluded.size OR files.mtime_ns!=excluded.mtime_ns
                        THEN 0 ELSE files.retry_at END,
                    timed_out=CASE WHEN files.size!=excluded.size OR files.mtime_ns!=excluded.mtime_ns
                        THEN 0 ELSE files.timed_out END,
                    size=excluded.size,mtime_ns=excluded.mtime_ns,seen=excluded.seen,missing=0''', pending)
            count += len(pending)
            pending.clear()

        def read_folder(folder):
            records, children, failures = [], [], []
            if any(within(folder,p) for p in excluded):
                return records, children, failures
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if stop():
                            break
                        try:
                            if entry.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(entry.path)):
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                children.append(entry.path)
                                continue
                            kind = kind_for(entry.name)
                            if not kind or entry.name.startswith('~$') or not entry.is_file(follow_symlinks=False):
                                continue
                            st = entry.stat(follow_symlinks=False)
                            path = norm(entry.path)
                            records.append((root['id'],path,norm(folder),entry.name,kind,st.st_size,st.st_mtime_ns,now,token))
                        except OSError as e:
                            failures.append(f'{entry.path}: {e}')
            except OSError as e:
                failures.append(f'{folder}: {e}')
            return records, children, failures

        # Directory workers only enumerate metadata. One coordinator writes DB.
        workers = max(1, min(16, workers))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='metadata-scan') as pool:
            active = set()
            while (stack or active) and not stop():
                while stack and len(active) < workers and not stop():
                    folder = stack.pop()
                    if any(within(folder,p) for p in excluded):
                        continue
                    discovered.add(norm(folder))
                    active.add(pool.submit(read_folder, folder))
                if not active:
                    break
                done, active = wait(active, timeout=.2, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        records, children, failures = future.result()
                    except Exception as exc:
                        errors.append(str(exc))
                        continue
                    stack.extend(children)
                    errors.extend(failures)
                    for offset in range(0, len(records), 300):
                        pending.extend(records[offset:offset+300])
                        flush()
        flush()
        if stop():
            errors.append('スキャン中断：削除判定は実施していません。')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM roots WHERE id=? AND path=?',(root['id'],root['path'])).fetchone():
                return count, errors
            omitted = [r[0] for r in c.execute('SELECT path FROM exclusions')]
            if not errors:
                c.execute('DELETE FROM folders WHERE root_id=?',(root['id'],))
            c.executemany('INSERT OR REPLACE INTO folders VALUES (?,?)',
                [(p,root['id']) for p in discovered if not any(within(p,e) for e in omitted)])
            if not errors:
                c.execute('UPDATE files SET missing=1 WHERE root_id=? AND seen!=?', (root['id'],token))
            c.execute('UPDATE roots SET last_scan=?,error=? WHERE id=?', (time.time(),'\n'.join(errors)[:8000],root['id']))
        return count, errors

    def folders(self):
        paths = {r['path'] for r in self.roots()}
        with self.connect() as c:
            paths.update(r[0] for r in c.execute('SELECT path FROM folders'))
            for r in c.execute('SELECT DISTINCT parent FROM files'):
                paths.add(r[0])
        return sorted(paths)

    def rows(self, folder=None, recursive=True, missing_only=False, search='', timeout_only=False, error_only=False):
        where, args = [], []
        if folder:
            folder = norm(folder)
            if recursive:
                prefix = folder.rstrip(os.sep) + os.sep
                # substr avoids LIKE wildcard problems in real paths.
                where.append('(parent=? OR substr(parent,1,?)=?)')
                args.extend([folder,len(prefix),prefix])
            else:
                where.append('parent=?')
                args.append(folder)
        if missing_only:
            where.append('missing=1')
        if timeout_only:
            where.append('timed_out=1')
        if error_only:
            where.append("(error!='' OR timed_out=1)")
        if search:
            where.append('(instr(lower(name),lower(?))>0 OR instr(lower(memo),lower(?))>0 OR instr(lower(tags),lower(?))>0)')
            args.extend([search,search,search])
        sql = 'SELECT * FROM files' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY name COLLATE NOCASE,path'
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql,args)]

    def thumbnail(self, file_id, registration_uid=None, revision=None):
        with self.connect() as c:
            if registration_uid is None:
                r = c.execute('SELECT data FROM thumbs WHERE file_id=?',(file_id,)).fetchone()
            else:
                r = c.execute('''SELECT t.data FROM thumbs t JOIN files f ON f.id=t.file_id
                    WHERE f.id=? AND f.registration_uid=? AND f.revision=?''',
                    (file_id,registration_uid,revision)).fetchone()
            return r[0] if r else None

    def next_job(self, exclude=(), skip_office=False):
        now = time.time()
        excluded = tuple(exclude)
        extra = (' AND id NOT IN ('+','.join('?' for _ in excluded)+')') if excluded else ''
        if skip_office:
            extra += " AND kind!='powerpoint'"
        with self.connect() as c:
            r = c.execute('''SELECT * FROM files WHERE missing=0 AND timed_out=0 AND retry_at<=? AND changed_at<=?
                AND (thumb_size IS NULL OR thumb_size!=size OR thumb_mtime!=mtime_ns)
                '''+extra+' ORDER BY changed_at,id LIMIT 1',(now,now-3,*excluded)).fetchone()
            return dict(r) if r else None

    def page_thumbnail(self,file_id,registration_uid,revision,page_number):
        with self.connect() as c:
            r = c.execute('''SELECT t.data FROM page_thumbs t JOIN files f ON f.id=t.file_id
                WHERE f.id=? AND f.registration_uid=? AND f.revision=? AND t.page_number=?''',
                (file_id,registration_uid,revision,page_number)).fetchone()
            return r[0] if r else None

    def file_record(self,registration_uid):
        with self.connect() as c:
            r = c.execute('SELECT * FROM files WHERE registration_uid=?',(registration_uid,)).fetchone()
            return dict(r) if r else None

    def save_memo(self, registration_uid, memo, previous):
        """Optimistic lock avoids overwriting another open editor or a new registration."""
        with self.connect() as c:
            return c.execute('UPDATE files SET memo=? WHERE registration_uid=? AND memo=?',
                             (memo,registration_uid,previous)).rowcount == 1

    def save_annotations(self, uid, memo, tags, favorite, previous):
        tags = ', '.join(dict.fromkeys(t.strip() for t in tags.replace('、',',').split(',') if t.strip()))
        with self.connect() as c:
            return c.execute('''UPDATE files SET memo=?,tags=?,favorite=?
                WHERE registration_uid=? AND memo=? AND tags=? AND favorite=?''',
                (memo,tags,int(favorite),uid,*previous)).rowcount == 1

    def backup(self, destination):
        # Create a new file only: never overwrite an original or a live database.
        destination = norm(destination)
        if destination == norm(self.path):
            raise ValueError('使用中のDBは保存先にできません。')
        with open(destination,'xb'):
            pass
        try:
            with self.connect() as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target,pages=256,sleep=.02)
                    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError('バックアップの整合性確認に失敗しました。')
                finally:
                    target.close()
        except Exception:
            # Keep failed output for inspection; never remove user files.
            raise

    def generation_counts(self):
        with self.connect() as c:
            return dict(c.execute('''SELECT count(*) AS total,
                coalesce(sum(thumb_size=size AND thumb_mtime=mtime_ns),0) AS complete,
                coalesce(sum(timed_out=1),0) AS timeout,
                coalesce(sum(error!='' AND timed_out=0),0) AS errors,
                coalesce(sum(thumb_size IS NULL OR thumb_size!=size OR thumb_mtime!=mtime_ns),0) AS pending
                FROM files WHERE missing=0''').fetchone())

    def save_thumb(self, job, data, pages=None, page_count=1, metadata=None):
        if page_count<0 or (page_count==0 and (job['kind']!='powerpoint' or data is not None)):
            raise ValueError('ページ数が不正です。')
        with self.connect() as c:
            result = c.execute('''UPDATE files SET thumb_size=?,thumb_mtime=?,page_count=?,revision=revision+1,error='',retry_at=0,timed_out=0
                WHERE id=? AND registration_uid=? AND path=? AND missing=0 AND size=? AND mtime_ns=?''',
                (job['size'],job['mtime_ns'],page_count,job['id'],job['registration_uid'],job['path'],job['size'],job['mtime_ns']))
            if result.rowcount:
                c.execute('UPDATE files SET metadata_json=? WHERE id=?',
                          (json.dumps(metadata or {},ensure_ascii=False),job['id']))
                if page_count:
                    c.execute('INSERT OR REPLACE INTO thumbs VALUES (?,?)',(job['id'],data))
                else:
                    c.execute('DELETE FROM thumbs WHERE file_id=?',(job['id'],))
                c.execute('DELETE FROM page_thumbs WHERE file_id=?',(job['id'],))
                actual = 0
                for actual,blob in enumerate(pages if pages is not None else [data],1):
                    c.execute('INSERT INTO page_thumbs VALUES (?,?,?)',(job['id'],actual,blob))
                if actual!=page_count:
                    raise ValueError('ページ数が一致しません。')
                return True
        return False

    def fail_job(self, job, error, timed_out=False):
        with self.connect() as c:
            c.execute('UPDATE files SET error=?,retry_at=?,timed_out=? WHERE id=? AND registration_uid=? AND path=? AND size=? AND mtime_ns=?',
                (str(error)[:2000],time.time()+600,int(timed_out),job['id'],job['registration_uid'],job['path'],job['size'],job['mtime_ns']))

    def regenerate(self, ids):
        with self.connect() as c:
            c.executemany("UPDATE files SET thumb_size=NULL,thumb_mtime=NULL,retry_at=0,error='',timed_out=0 WHERE id=? AND missing=0",[(i,) for i in ids])

    def confirm_removal(self, ids):
        """Remove catalog rows only; re-list parent successfully before trusting absence."""
        removed, restored, skipped = 0, 0, []
        with self.connect() as c:
            for file_id in ids:
                r = c.execute('SELECT * FROM files WHERE id=? AND missing=1',(file_id,)).fetchone()
                if not r:
                    continue
                root = c.execute('SELECT path FROM roots WHERE id=?',(r['root_id'],)).fetchone()[0]
                try:
                    # Require root enumeration even when the missing file's parent was deleted.
                    with os.scandir(root) as entries:
                        list(entries)
                    ancestor = r['parent']
                    while True:
                        try:
                            with os.scandir(ancestor) as entries:
                                names = {norm(e.path) for e in entries}
                            break
                        except FileNotFoundError:
                            if norm(ancestor) == norm(root) or not within(ancestor,root):
                                raise
                            ancestor = os.path.dirname(ancestor)
                    if norm(ancestor) == norm(r['parent']) and r['path'] in names:
                        c.execute('UPDATE files SET missing=0 WHERE id=?',(file_id,))
                        restored += 1
                        continue
                    c.execute('DELETE FROM files WHERE id=? AND missing=1',(file_id,))
                    removed += 1
                except OSError as e:
                    skipped.append(f"{r['path']}: {e}")
        return removed, restored, skipped
