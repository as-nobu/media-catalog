"""Synthetic search-only timing. Example: python tests/benchmark_search.py --files 65000"""
import sys,time,tempfile,json,statistics,io,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from PIL import Image
import numpy as np,cv2
import visual_search as vs
import search_vectors as cache
from catalog import Catalog
from test_visual_search import picture,png
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--files',type=int,default=1000)
args=parser.parse_args()
if not 1 <= args.files <= 1000000: parser.error('--files must be between 1 and 1000000')
encoder=vs.StandardEncoder();fixtures=[]
for i in range(16):
    image=picture(i)
    buf=io.BytesIO();image.save(buf,format='JPEG',quality=80)
    image=vs.decode(buf.getvalue())
    fixtures.append((buf.getvalue(),vs.pack(vectors=encoder.encode(image)),vs.pack(**vs.local_features(image))))
print('fixture bytes',list(map(len,fixtures[0])),flush=True)
result={'files':args.files,'templates':16,'opencv':cv2.__version__,'numpy':np.__version__,'cv_threads':cv2.getNumThreads(),'samples':[]}
with tempfile.TemporaryDirectory(prefix='media-catalog-search-bench-') as td:
    db=Catalog(Path(td)/'catalog.sqlite3')
    start=time.perf_counter()
    with db.connect() as c:
        c.execute("INSERT INTO roots(id,path) VALUES (1,'/no-source-files')")
        for i in range(1,args.files+1):
            blob,vec,local=fixtures[i%len(fixtures)]
            c.execute("INSERT INTO files(id,root_id,path,parent,name,kind,size,mtime_ns,changed_at,seen,registration_uid,revision) VALUES (?,1,?,'/no-source-files',?,'image',1,1,0,'benchmark',?,1)",(i,f'/no-source-files/{i}.jpg',f'{i}.jpg',str(i)))
            c.execute('INSERT INTO page_thumbs VALUES (?,?,?)',(i,1,blob))
            c.execute('INSERT INTO visual_features VALUES (?,?,?,?,?,?,?,?)',(i,1,encoder.key,str(i),1,vec,local,''))
            if i%5000==0: print('prepared',i,round(time.perf_counter()-start,1),flush=True)
    print('db GiB',Path(db.path).stat().st_size/1024**3,flush=True)
    idx=vs.SearchIndex(db)
    for trial,seed in enumerate((3,7,11,15)):
        start=time.perf_counter(); stages=[];laststage=None
        def progress(stage,done,total):
            global laststage
            if stage!=laststage:
                stages.append((stage,time.perf_counter()-start));laststage=stage
        report=idx.search(png(picture(seed).crop((60,40,420,320))),progress=progress)
        elapsed=time.perf_counter()-start
        sample=dict(trial=trial,seconds=elapsed,indexed=report.indexed,pages=report.pages,compared=report.compared,hits=len(report.hits),stages=stages)
        result['samples'].append(sample); print(sample,flush=True)
    result['cache_bytes']=sum(b[0].nbytes+b[1].nbytes+len(b[2])*256+len(b[3])*128 for b in cache._cached[1]) if cache._cached else 0
    result['warm_median_seconds']=statistics.median(s['seconds'] for s in result['samples'][1:])
print(json.dumps(result,indent=2))
