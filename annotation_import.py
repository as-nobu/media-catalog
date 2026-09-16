"""Read-only import planning and atomic annotation-only application."""
import ntpath
import posixpath
import sqlite3
import uuid
from pathlib import Path


def path_key(path):
    path = path.replace('\\','/')
    windows = bool(ntpath.splitdrive(path)[0]) or path.startswith('//')
    return (ntpath.normpath(path).replace('\\','/').casefold() if windows else posixpath.normpath(path))


def read_source(path):
    # Do not instantiate Catalog: that would migrate the source database.
    conn = sqlite3.connect(Path(path).absolute().as_uri()+'?mode=ro',uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        if conn.execute("SELECT type FROM sqlite_master WHERE name='files'").fetchone()!=('table',):
            raise ValueError('取り込み元DBの形式が不正です。')
        columns={r[1] for r in conn.execute('PRAGMA table_info(files)')}
        if not {'path','memo','tags'}<=columns:
            raise ValueError('取り込み元DBの形式が不正です。')
        rows=[]
        for path,memo,tags in conn.execute('SELECT path,memo,tags FROM files'):
            if not all(isinstance(v,str) for v in (path,memo,tags)):
                raise ValueError('取り込み元DBの形式が不正です。')
            if memo.strip() or tags.strip():
                rows.append(dict(path=path,memo=memo,tags=tags))
        roots=[]
        if conn.execute("SELECT type FROM sqlite_master WHERE name='roots'").fetchone()==('table',):
            roots=[r[0] for r in conn.execute('SELECT path FROM roots') if isinstance(r[0],str)]
        return rows,roots
    finally:
        conn.close()


def merge_tags(current,incoming):
    return ', '.join(dict.fromkeys(t.strip() for t in (current+','+incoming).replace('、',',').split(',') if t.strip()))


def plan_import(db,incoming,source_root='',target_root=''):
    if bool(source_root)!=bool(target_root):
        raise ValueError('対応元と対応先のフォルダを両方指定してください。')
    with db.connect() as c:
        current=[dict(r) for r in c.execute('SELECT registration_uid,path,memo,tags FROM files')]
    index={}
    for row in current:
        index.setdefault(path_key(row['path']),[]).append(row)
    candidates=[]; skipped=0
    for source in incoming:
        key=path_key(source['path'])
        if source_root:
            prefix=path_key(source_root).rstrip('/')+'/'
            if not key.startswith(prefix):
                skipped+=1; continue
            key=path_key(target_root).rstrip('/')+'/'+key[len(prefix):]
        matches=index.get(key,[])
        if len(matches)!=1:
            skipped+=1; continue
        row=matches[0]
        candidates.append(dict(row,incoming_memo=source['memo'],incoming_tags=source['tags']))
    counts={}
    for row in candidates:
        uid=row['registration_uid']; counts[uid]=counts.get(uid,0)+1
    result=[]
    for row in candidates:
        if counts[row['registration_uid']]!=1:
            skipped+=1; continue
        row['merged_tags']=merge_tags(row['tags'],row['incoming_tags'])
        row['conflict']=bool(row['memo'].strip() and row['incoming_memo'].strip() and row['memo']!=row['incoming_memo'])
        row['choice']='keep'
        result.append(row)
    return result,skipped


def merged_memo(row):
    if not row['incoming_memo'].strip(): return row['memo']
    if not row['memo'].strip(): return row['incoming_memo']
    if row['memo']==row['incoming_memo']: return row['memo']
    if row['choice']=='replace': return row['incoming_memo']
    if row['choice']=='both':
        # Re-importing the same note should not append another identical copy.
        suffix='\n\n---\n'+row['incoming_memo']
        return row['memo'] if row['memo'].endswith(suffix) else row['memo']+suffix
    return row['memo']


def apply_import(db,plan):
    changes=[(row,merged_memo(row)) for row in plan
             if merged_memo(row)!=row['memo'] or row['merged_tags']!=row['tags']]
    if not changes: return 0,None
    folder=Path(db.path).parent/'backups'; folder.mkdir(exist_ok=True)
    backup=folder/f'before-import-{uuid.uuid4().hex}.sqlite3'
    db.backup(backup)
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for row,memo in changes:
            updated=c.execute('''UPDATE files SET memo=?,tags=?
                WHERE registration_uid=? AND path=? AND memo=? AND tags=?''',
                (memo,row['merged_tags'],row['registration_uid'],row['path'],row['memo'],row['tags']))
            if updated.rowcount!=1:
                raise ValueError('確認後にデータが変更されました。取り込みをやり直してください。')
    return len(changes),str(backup)
