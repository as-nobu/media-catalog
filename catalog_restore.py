"""Prepare independent catalog copies; never replace an open database."""
import json
import os
import ntpath
import posixpath
import sqlite3
import uuid
from pathlib import Path
from catalog import Catalog,norm,within
from annotation_import import path_key


def open_backup(path):
    c=sqlite3.connect(Path(path).absolute().as_uri()+'?mode=ro',uri=True)
    try:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA trusted_schema=OFF')
        c.execute('BEGIN')
        objects=list(c.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        if any(kind in ('trigger','view') or 'VIRTUAL TABLE' in (sql or '').upper() for kind,name,sql in objects):
            raise ValueError('復元できないDB形式です。')
        for table,columns in {'roots':{'id','path'},'files':{'id','root_id','path','parent','name','kind','size','mtime_ns','memo','tags','favorite'},'thumbs':{'file_id','data'},'settings':{'key','value'}}.items():
            if not columns<={r[1] for r in c.execute(f'PRAGMA table_info({table})')}:
                raise ValueError('復元できないDB形式です。')
        return c
    except Exception:
        c.close();raise


def inspect_backup(path):
    c=open_backup(path)
    try:
        return list(c.execute('SELECT id,path FROM roots ORDER BY path')),c.execute('SELECT count(*) FROM files').fetchone()[0]
    finally:c.close()


def remap_copy(db,mapping,storage):
    roots=db.roots()
    if set(mapping)!={r['id'] for r in roots}:raise ValueError('登録フォルダが変わりました。再確認してください。')
    if any(not os.path.isabs(path) for path in mapping.values()):raise ValueError('変更先は絶対パスで指定してください。')
    destinations={rid:norm(path) for rid,path in mapping.items()}
    for rid,path in destinations.items():
        if within(path,storage) or within(storage,path):raise ValueError('アプリの保存領域と重なるフォルダは登録できません。')
        for other_id,other in destinations.items():
            if rid!=other_id and (within(path,other) or within(other,path)):
                raise ValueError('変更先のフォルダが重複しています。')
    def translate(path,rid=None):
        matches=[r for r in roots if (rid is None or r['id']==rid) and (path_key(path)==path_key(r['path']) or path_key(path).startswith(path_key(r['path']).rstrip('/')+'/'))]
        if not matches and rid is None:
            # Old exclusions can remain after their registered root is removed.
            return path
        if len(matches)!=1:raise ValueError('DB内のパスと登録フォルダが一致しません。')
        root=matches[0]
        module=ntpath if ntpath.splitdrive(root['path'])[0] else posixpath
        rel=module.relpath(path,root['path']).replace('\\','/')
        return norm(os.path.join(destinations[root['id']],*rel.split('/')))
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        files=[(r['id'],translate(r['path'],r['root_id'])) for r in c.execute('SELECT id,path,root_id FROM files')]
        folders=[(r['path'],translate(r['path'],r['root_id'])) for r in c.execute('SELECT path,root_id FROM folders')]
        exclusions=[(r['path'],translate(r['path'])) for r in c.execute('SELECT path FROM exclusions')]
        for values in (files,folders,exclusions):
            if len({path_key(v[1]) for v in values})!=len(values):raise ValueError('変更先のファイルパスが重複しています。')
        prefix='__restore_'+uuid.uuid4().hex+'/'
        for rid,path in destinations.items():c.execute('UPDATE roots SET path=? WHERE id=?',(prefix+str(rid),rid))
        for fid,path in files:c.execute('UPDATE files SET path=? WHERE id=?',(prefix+str(fid),fid))
        for table,values in [('folders',folders),('exclusions',exclusions)]:
            for i,(old,new) in enumerate(values):c.execute(f'UPDATE {table} SET path=? WHERE path=?',(prefix+str(i),old))
            for i,(old,new) in enumerate(values):c.execute(f'UPDATE {table} SET path=? WHERE path=?',(new,prefix+str(i)))
        for rid,path in destinations.items():c.execute("UPDATE roots SET path=?,last_scan=NULL,error='' WHERE id=?",(path,rid))
        for fid,path in files:c.execute('UPDATE files SET path=?,parent=?,name=? WHERE id=?',(path,os.path.dirname(path),os.path.basename(path),fid))
        c.execute("INSERT OR REPLACE INTO settings VALUES ('generate','0')")
        c.execute("INSERT OR REPLACE INTO settings VALUES ('scan_enabled','0')")


def prepare_restore(source,data_home,mapping):
    home=Path(data_home)
    folder=home/'catalogs'/uuid.uuid4().hex;folder.mkdir(parents=True)
    target=folder/'catalog.sqlite3'
    src=open_backup(source)
    try:
        dst=sqlite3.connect(target)
        try:
            src.backup(dst,pages=256)
            if dst.execute('PRAGMA integrity_check').fetchone()!=('ok',) or dst.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('DBの整合性確認に失敗しました。')
        finally:dst.close()
    finally:src.close()
    db=Catalog(target)
    remap_copy(db,mapping,home)
    with db.connect() as c:
        if c.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('DBの整合性確認に失敗しました。')
    return str(target)


def select_catalog(data_home,path):
    home=Path(data_home)
    relative=Path(path).relative_to(home)
    if not Path(path).is_file():raise ValueError('復元DBが見つかりません。')
    temporary=home/('catalog-selection-'+uuid.uuid4().hex+'.json')
    temporary.write_text(json.dumps({'path':str(relative)},ensure_ascii=False),encoding='utf-8')
    os.replace(temporary,home/'active-catalog.json')


def selected_catalog(data_home):
    home=Path(data_home); selector=home/'active-catalog.json'
    if not selector.exists():return home/'catalog.sqlite3'
    value=json.loads(selector.read_text(encoding='utf-8'))['path']
    path=home/value
    if not within(path,home) or not path.is_file():raise ValueError('復元DBが見つかりません。')
    return path
