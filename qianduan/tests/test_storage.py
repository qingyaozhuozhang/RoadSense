import tempfile
import unittest
from server.storage import RecordStore

class StorageTests(unittest.TestCase):
    def test_add_preserves_records(self):
        with tempfile.TemporaryDirectory() as d:
            store=RecordStore(d)
            self.assertEqual(store.all(),[])
            self.assertEqual(store.add({'level':'严重','time':'2026-10-21 12:00:00'}),1)
            self.assertEqual(store.add({'level':'轻微','time':'2026-10-21 12:00:01'}),2)
            self.assertEqual(len(RecordStore(d).all()),2)
if __name__=='__main__': unittest.main()
