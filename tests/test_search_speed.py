"""Search-time limits, vector-cache invalidation, deleted/changed registrations."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_search as vs
import search_vectors as vectors
import test_visual_search as fixtures
from test_visual_search import picture, png


class SearchSpeedTests(unittest.TestCase):
    setUp = fixtures.SearchTests.setUp
    add = fixtures.SearchTests.add

    def test_deleted_and_missing_files_are_excluded_with_warm_cache(self):
        rows = [self.add(str(i)+'.png',[picture(i)]) for i in range(3)]
        self.index.search(png(picture(0)))
        with self.db.connect() as c:
            c.execute('UPDATE files SET missing=1 WHERE id=?',(rows[0]['id'],))
            c.execute('DELETE FROM files WHERE id=?',(rows[1]['id'],))
        result = self.index.search(png(picture(0)))
        self.assertEqual([r['registration_uid'] for r in result.hits],[rows[2]['registration_uid']])
        self.assertEqual(result.pages,1)
        self.assertNotIn(rows[0]['registration_uid'],self.index.thumbnail_uids())

    def test_warm_search_does_not_unpack_vectors_again_and_annotations_reuse_cache(self):
        row = self.add('old.png',[picture(5)])
        self.index.search(png(picture(5)))
        with self.db.connect() as c: c.execute("UPDATE files SET memo='new memo'")
        real = vs.unpack
        def only_local(blob):
            result = real(blob)
            self.assertNotIn('vectors',result,'warmed vectors were decompressed again')
            return result
        with patch('visual_search.unpack',side_effect=only_local):
            report = self.index.search(png(picture(5)))
        self.assertEqual(report.hits[0]['memo'],'new memo')

    def test_scope_changes_do_not_reuse_filtered_results(self):
        rows = [self.add(str(i)+'.png',[picture(i)]) for i in range(2)]
        self.index.search(png(picture(0)),allowed_uids=[rows[0]['registration_uid']])
        report = self.index.search(png(picture(1)),allowed_uids=[rows[1]['registration_uid']])
        self.assertEqual(report.pages,1)
        self.assertEqual(report.hits[0]['registration_uid'],rows[1]['registration_uid'])
        self.assertFalse(self.index.search(png(picture(1)),allowed_uids=[]).hits)

    def test_in_place_vector_updates_invalidate_even_with_unchanged_count(self):
        self.add('one.png',[picture(6)])
        encoder = vs.StandardEncoder()
        self.index.search(png(picture(6)),encoder)
        q = encoder.encode(picture(6),multiple=False)
        with self.db.connect() as c:
            c.execute('UPDATE visual_features SET vectors=? WHERE engine=?',(vs.pack(vectors=-q),encoder.key))
        ranking,pages,errors = vectors.rank(self.db,encoder.key,q,lambda:False,lambda *a:None)
        self.assertEqual((pages,errors),(1,0))
        self.assertAlmostEqual(ranking[0][0],-1,places=5)

    def test_removed_and_reregistered_id_cannot_inherit_old_result(self):
        first = self.add('same.png',[picture(7)])
        self.index.search(png(picture(7)))
        self.db.remove_folder(self.root)
        self.db.add_root(self.root)
        second = self.add('same.png',[picture(8)])
        self.assertEqual(first['id'],second['id'])
        self.assertNotEqual(first['registration_uid'],second['registration_uid'])
        report = self.index.search(png(picture(8)))
        self.assertEqual(report.hits[0]['registration_uid'],second['registration_uid'])

    def test_deletion_during_geometry_is_rechecked_before_returning(self):
        row = self.add('gone.png',[picture(9)])
        def remove(*args):
            with self.db.connect() as c: c.execute('DELETE FROM files WHERE id=?',(row['id'],))
            return 1,None,20
        with patch('visual_search.match_geometry',side_effect=remove):
            self.assertFalse(self.index.search(png(picture(9))).hits)

    def test_snapshot_changed_during_load_is_not_published(self):
        row = self.add('one.png',[picture(4)])
        encoder = vs.StandardEncoder()
        self.index.build(encoder)
        called = []
        def change(stage,*args):
            if not called:
                called.append(True)
                with self.db.connect() as c: c.execute('UPDATE files SET missing=1')
        query = encoder.encode(picture(4),multiple=False)
        vectors.rank(self.db,encoder.key,query,lambda:False,change)
        self.assertIsNone(vectors._cached)
        ranking,pages,errors = vectors.rank(self.db,encoder.key,query,lambda:False,lambda *a:None)
        self.assertEqual(pages,0)
        self.assertFalse(ranking)

    def test_streaming_budget_and_vectorized_scores_match(self):
        rows = [self.add(str(i)+'.png',[picture(i)]) for i in range(4)]
        encoder = vs.StandardEncoder()
        self.index.build(encoder)
        query = encoder.encode(picture(2),multiple=False)
        expected = {}
        with self.db.connect() as c:
            for row in c.execute('SELECT registration_uid,vectors FROM visual_features'):
                expected[row[0]] = float((vs.unpack(row[1])['vectors']@query.T).max())
        with patch.object(vectors,'MAX_CACHE_BYTES',1):
            ranking,pages,errors = vectors.rank(self.db,encoder.key,query,lambda:False,lambda *a:None)
            self.assertIsNone(vectors._cached)
        for score,fid,page,uid,revision in ranking: self.assertAlmostEqual(score,expected[uid],places=5)

    def test_default_limits_geometry_and_detailed_is_explicit(self):
        row = self.add('one.png',[picture(3)])
        encoder = vs.StandardEncoder()
        self.index.build(encoder)
        with self.db.connect() as c:
            feature = c.execute('SELECT vectors FROM visual_features').fetchone()[0]
            local = c.execute('SELECT data FROM visual_local_features').fetchone()[0]
            # 350 files, all already indexed; independent records, no source IO.
            for i in range(2,352):
                c.execute("""INSERT INTO files(root_id,path,parent,name,kind,size,mtime_ns,changed_at,seen,
                             registration_uid,revision) SELECT root_id,?,parent,?,'image',size,mtime_ns,
                             changed_at,seen,?,revision FROM files WHERE id=?""",
                          ('/not-opened/'+str(i),str(i),str(i),row['id']))
                c.execute('INSERT INTO page_thumbs VALUES (?,?,?)',(i,1,png(picture(3))))
                c.execute('INSERT INTO visual_features VALUES (?,?,?,?,?,?,?,?)',
                          (i,1,encoder.key,str(i),row['revision'],feature,local,''))
        with patch('visual_search.match_geometry',return_value=(0,None,0)) as match:
            report = self.index.search(png(picture(3)))
            self.assertEqual((report.pages,report.compared,match.call_count),(351,300,300))
        with patch('visual_search.match_geometry',return_value=(0,None,0)) as match:
            report = self.index.search(png(picture(3)),detailed=True)
            self.assertEqual((report.compared,match.call_count),(351,351))

    def test_multiple_matching_pages_are_one_catalog_item(self):
        row = self.add('pages.tif',[picture(1),picture(2),picture(3)])
        report = self.index.search(png(picture(2)))
        self.assertEqual(len(report.hits),1)
        self.assertEqual(report.hits[0]['page_number'],2)

    def test_corrupt_vector_does_not_abort_search(self):
        self.add('broken.png',[picture(1)])
        self.index.build(vs.StandardEncoder())
        with self.db.connect() as c: c.execute('UPDATE visual_features SET vectors=?',(b'not npz',))
        report = self.index.search(png(picture(1)))
        self.assertEqual(report.errors,1)
        self.assertFalse(report.hits)

    def test_full_restore_keeps_search_features_and_accepts_own_triggers(self):
        from catalog_restore import prepare_restore
        from catalog import Catalog
        row = self.add('restored.png',[picture(13)])
        self.index.search(png(picture(13)))
        backup = Path(self.tmp.name)/'backup.sqlite3'
        self.db.backup(backup)
        target = prepare_restore(backup,Path(self.tmp.name)/'other-app',
                                 {row['root_id']:str(Path(self.tmp.name)/'unavailable-source')})
        result = vs.SearchIndex(Catalog(target)).search(png(picture(13)))
        self.assertEqual(result.indexed,0)
        self.assertEqual(result.hits[0]['registration_uid'],row['registration_uid'])
        self.assertGreaterEqual(result.hits[0]['inliers'],8)


if __name__=='__main__': unittest.main()
