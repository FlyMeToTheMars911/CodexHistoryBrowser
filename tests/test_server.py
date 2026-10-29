import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import HistoryStore


@contextmanager
def writable_db(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'codex'
        self.home.mkdir()
        self.base = int(datetime(2026, 9, 8, 12).timestamp())
        with writable_db(self.home / 'state_5.sqlite') as c:
            c.executescript('''CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,cwd TEXT,
                project_id TEXT,rollout_path TEXT,created_at INTEGER,updated_at INTEGER,
                archived INTEGER,thread_source TEXT,source TEXT);
                CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT);''')
            c.execute('INSERT INTO projects VALUES(?,?)', ('p1', '项目甲'))
            c.executemany('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?)', [
                ('a', '中文 SQL 查询', 'E:/one', 'p1', str(self.home/'a.jsonl'), self.base, self.base, 0, 'user', 'vscode'),
                ('b', 'Archived conversation', 'E:/two', None, str(self.home/'b.jsonl'), self.base+86400, self.base+86400, 1, 'user', 'vscode'),
                ('c', '子代理', 'E:/one', 'p1', str(self.home/'c.jsonl'), self.base+10, self.base+10, 0, 'subagent', '{"subagent":{}}'),
            ])
        with writable_db(self.home/'thread_history_1.sqlite') as c:
            c.executescript('''CREATE TABLE thread_items(thread_id TEXT,turn_id TEXT,item_id TEXT,
                rollout_ordinal INTEGER,created_at_ms INTEGER,item_type TEXT,item_json TEXT);
                CREATE INDEX items_thread ON thread_items(thread_id,rollout_ordinal);
                CREATE TABLE thread_history_projection_state(thread_id TEXT,next_rollout_byte_offset INTEGER);''')
            for index in range(65):
                kind = 'userMessage' if index == 0 else 'agentMessage'
                value = '中文检索 回访 100% literal_ [x] "quoted" MixedCase <script>alert(1)</script>' if index == 0 else f'完整回复 {index}'
                data = {'type':kind, 'content':[{'type':'text','text':value}]} if index == 0 else {'type':kind,'text':value,'phase':'final'}
                c.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?,?)', ('a','turn1',f'i{index}',index,self.base*1000,kind,json.dumps(data,ensure_ascii=False)))
            for thread in ('b','c'):
                c.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?,?)', (thread,'t','item',0,self.base*1000,'agentMessage',json.dumps({'type':'agentMessage','text':'归档 独有关键词' if thread=='b' else '代理内容'})))
            c.executemany('INSERT INTO thread_history_projection_state VALUES(?,?)',[('a',100),('b',100),('c',100)])
        self.store=HistoryStore(self.home,Path(self.temp.name)/'work')
        self.store.sync_once()

    def tearDown(self):
        self.temp.cleanup()

    def search(self, **kwargs):
        return self.store.search(kwargs)

    def test_combined_filters_and_default_agent_exclusion(self):
        self.assertEqual(self.search()['total'],2)
        self.assertEqual(self.search(include_agents='1')['total'],3)
        self.assertEqual(self.search(project='project:p1',title='sql',content='中文检索',archived='0')['total'],1)
        self.assertEqual(self.search(project='project:p1',archived='1')['total'],0)

    def test_short_chinese_fts_and_literal_symbols(self):
        for text in ['回访','中文检索','100%','literal_','[x]','"quoted"','mixedcase']:
            with self.subTest(text=text):
                self.assertEqual(self.search(content=text)['items'][0]['id'],'a')
        self.assertEqual(self.search(content='does-not-exist')['total'],0)

    def test_date_inclusive_bounds_and_validation(self):
        self.assertEqual(self.search(start='2026-09-08',end='2026-09-08')['total'],1)
        self.assertEqual(self.search(start='2026-09-09',end='2026-09-09',archived='1')['total'],1)
        for params in [dict(start='2026-09-10',end='2026-09-08'),dict(start='bad'),dict(page='-1'),dict(page_size='15'),dict(time_field='DROP TABLE')]:
            with self.assertRaises(ValueError):self.search(**params)

    def test_detail_pagination_preserves_full_content(self):
        row=self.store.get_thread('a')
        first=list(self.store.iter_items(row,limit=60))
        second=list(self.store.iter_items(row,after=first[-1]['rollout_ordinal'],limit=60))
        self.assertEqual((len(first),len(second)),(60,5))
        self.assertEqual(second[-1]['data']['text'],'完整回复 64')
        self.assertEqual(len({x['item_id'] for x in first+second}),65)

    def test_incremental_reindex_and_removal(self):
        with writable_db(self.home/'thread_history_1.sqlite') as c:
            c.execute('UPDATE thread_items SET item_json=? WHERE thread_id=? AND item_id=?',(json.dumps({'type':'agentMessage','text':'增量新内容'}),'b','item'))
            c.execute('UPDATE thread_history_projection_state SET next_rollout_byte_offset=200 WHERE thread_id=?',('b',))
        self.store.sync_once()
        self.assertEqual(self.search(content='增量新内容')['total'],1)
        self.assertEqual(self.search(content='独有关键词')['total'],0)
        with writable_db(self.home/'state_5.sqlite') as c:c.execute('DELETE FROM threads WHERE id=?',('b',))
        self.store.sync_once()
        self.assertEqual(self.search(content='增量新内容')['total'],0)

    def test_source_readonly_and_clamped_page(self):
        from server import read_db
        with read_db(self.home/'state_5.sqlite') as c:
            with self.assertRaises(sqlite3.OperationalError):c.execute('DELETE FROM threads')
        self.assertEqual(self.search(page='999')['page'],1)
        with self.assertRaises(KeyError):self.store.get_thread('missing')

    def test_display_name_takes_precedence_over_raw_first_prompt(self):
        with writable_db(self.home/'state_5.sqlite') as c:
            c.execute('ALTER TABLE threads ADD COLUMN name TEXT')
            c.execute('UPDATE threads SET name=? WHERE id=?',('人工命名的会话','a'))
        self.assertEqual(self.search(title='人工命名')['items'][0]['title'],'人工命名的会话')

    def test_jsonl_fallback_without_duplicate_representations(self):
        with writable_db(self.home/'thread_history_1.sqlite') as c:c.execute('DELETE FROM thread_items WHERE thread_id=?',('b',))
        records=[
            {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'重复表示'}]}},
            {'type':'event_msg','payload':{'type':'item_completed','item':{'type':'UserMessage','content':[{'type':'text','text':'唯一正文'}]}}},
        ]
        (self.home/'b.jsonl').write_text('\n'.join(json.dumps(record) for record in records)+'\n{"incomplete":',encoding='utf-8')
        items=list(self.store.iter_items(self.store.get_thread('b')))
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['data']['content'][0]['text'],'唯一正文')

    def write_rollout(self, thread, text):
        record = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                  'content': [{'type': 'output_text', 'text': text}]}}
        (self.home / f'{thread}.jsonl').write_text(json.dumps(record) + '\n', encoding='utf-8')

    def test_missing_body_preserves_metadata_deduplicates_warning_and_recovers(self):
        with writable_db(self.home / 'thread_history_1.sqlite') as c:
            c.execute('DELETE FROM thread_items WHERE thread_id=?', ('b',))
            c.execute('UPDATE thread_history_projection_state SET next_rollout_byte_offset=201 WHERE thread_id=?', ('b',))
        with self.assertLogs('history-browser', level='WARNING') as logged:
            self.store.sync_once()
        self.assertEqual(len(logged.records), 1)
        self.assertEqual(self.store.get_status()['unavailable'], 1)
        self.assertIsNone(self.store.get_status()['error'])
        self.assertEqual(self.search()['total'], 2)
        self.assertEqual(self.search(content='独有关键词')['total'], 0)
        with self.assertRaises(FileNotFoundError):
            list(self.store.iter_items(self.store.get_thread('b')))
        with self.assertNoLogs('history-browser', level='WARNING'):
            self.store.sync_once()
        self.write_rollout('b', '恢复后的正文')
        self.store.sync_once()
        self.assertEqual(self.store.get_status()['unavailable'], 0)
        self.assertEqual(self.search(content='恢复后的正文')['total'], 1)

    def test_history_schema_change_falls_back_to_rollout(self):
        self.write_rollout('b', '原始文件兼容正文')
        with writable_db(self.home / 'thread_history_1.sqlite') as c:
            c.execute('ALTER TABLE thread_items RENAME COLUMN item_json TO payload_json')
        self.store.sync_once()
        self.assertFalse(self.store.get_status()['schema']['structured_history'])
        self.assertIn('结构化历史表', self.store.get_status()['warnings'][0])
        self.assertEqual(self.search(content='原始文件兼容正文')['total'], 1)
        self.assertEqual(list(self.store.iter_items(self.store.get_thread('b')))[0]['data']['text'], '原始文件兼容正文')

    def test_missing_structured_history_recovers_without_projection_change(self):
        with writable_db(self.home / 'thread_history_1.sqlite') as c:
            c.execute('DELETE FROM thread_items WHERE thread_id=?', ('b',))
            c.execute('UPDATE thread_history_projection_state SET next_rollout_byte_offset=201 WHERE thread_id=?', ('b',))
        self.store.sync_once()
        self.assertEqual(self.store.get_status()['unavailable'], 1)
        with writable_db(self.home / 'thread_history_1.sqlite') as c:
            c.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?,?)',
                      ('b', 't', 'restored', 0, self.base * 1000, 'agentMessage',
                       json.dumps({'type': 'agentMessage', 'text': '结构化恢复正文'})))
        self.store.sync_once()
        self.assertEqual(self.store.get_status()['unavailable'], 0)
        self.assertEqual(self.search(content='结构化恢复正文')['total'], 1)

    def test_unreadable_rollout_signature_does_not_abort_synchronization(self):
        from unittest.mock import patch
        from server import file_signature
        with patch.object(Path, 'stat', side_effect=PermissionError('denied')):
            self.assertEqual(file_signature(self.home / 'b.jsonl'), 'unreadable:PermissionError')
        original = self.store.iter_items

        def inaccessible(row, *args, **kwargs):
            if row['id'] == 'b':
                raise PermissionError('denied')
            yield from original(row, *args, **kwargs)

        with writable_db(self.home / 'thread_history_1.sqlite') as c:
            c.execute('UPDATE thread_history_projection_state SET next_rollout_byte_offset=201')
        with patch.object(self.store, 'iter_items', side_effect=inaccessible):
            with self.assertLogs('history-browser', level='ERROR'):
                self.store.sync_once()
        self.assertEqual(self.store.get_status()['indexed'], 3)
        self.assertIn('1 个会话索引失败', self.store.get_status()['error'])

    def test_missing_projection_detects_body_change_without_metadata_change(self):
        with writable_db(self.home / 'thread_history_1.sqlite') as c:
            c.execute('DROP TABLE thread_history_projection_state')
        self.store.sync_once()
        self.assertFalse(self.store.get_status()['schema']['projection'])
        with writable_db(self.home / 'thread_history_1.sqlite') as c:
            c.execute('UPDATE thread_items SET item_json=? WHERE thread_id=?',
                      (json.dumps({'type': 'agentMessage', 'text': '无投影的新正文'}), 'b'))
        self.store.sync_once()
        self.assertEqual(self.search(content='无投影的新正文')['total'], 1)

    def test_new_database_version_is_discovered_and_required_schema_is_checked(self):
        (self.home / 'state_5.sqlite').rename(self.home / 'state_6.sqlite')
        (self.home / 'thread_history_1.sqlite').rename(self.home / 'thread_history_2.sqlite')
        newer = HistoryStore(self.home, Path(self.temp.name) / 'new-work')
        self.assertEqual(newer.state_path.name, 'state_6.sqlite')
        self.assertEqual(newer.history_path.name, 'thread_history_2.sqlite')
        self.assertEqual(newer.search({})['total'], 2)
        with writable_db(self.home / 'state_7.sqlite') as c:
            c.execute('CREATE TABLE threads (id TEXT)')
        with self.assertRaisesRegex(ValueError, '缺少必需字段'):
            HistoryStore(self.home, Path(self.temp.name) / 'bad-work')

    def write_compressed_rollout(self, thread, records):
        body = ('\n'.join(json.dumps(record, ensure_ascii=False) for record in records) + '\n').encode('utf-8')
        try:
            from compression import zstd
            compressed = zstd.compress(body)
        except ImportError:
            try:
                import zstandard
            except ImportError:
                self.skipTest('压缩历史验证需要 Python 3.14+ compression.zstd 或 zstandard 包')
            compressed = zstandard.ZstdCompressor().compress(body)
        path = self.home / f'{thread}.jsonl.zst'
        path.write_bytes(compressed)
        return path

    def use_rollout_body(self, thread):
        with writable_db(self.home / 'thread_history_1.sqlite') as connection:
            connection.execute('DELETE FROM thread_items WHERE thread_id=?', (thread,))

    def response_record(self, text):
        return {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': text}]}}

    def test_migrated_compressed_rollout_preserves_pagination_and_search(self):
        records = [self.response_record(f'压缩正文记录 {index}') for index in range(65)]
        self.write_compressed_rollout('b', records)
        self.use_rollout_body('b')
        row = self.store.get_thread('b')
        self.assertEqual(Path(row['rollout_path']).suffix, '.jsonl')
        self.assertFalse(Path(row['rollout_path']).exists())
        first = list(self.store.iter_items(row, limit=60))
        second = list(self.store.iter_items(row, after=first[-1]['rollout_ordinal'], limit=60))
        self.assertEqual((len(first), len(second)), (60, 5))
        self.assertEqual(second[-1]['data']['text'], '压缩正文记录 64')
        self.assertEqual(len({item['item_id'] for item in first + second}), 65)
        self.store.sync_once()
        self.assertEqual(self.store.get_status()['unavailable'], 0)
        self.assertIsNone(self.store.get_status()['error'])
        self.assertEqual(self.search(content='压缩正文记录 64')['total'], 1)
        self.assertEqual(self.search(content='独有关键词')['total'], 0)

    def test_compressed_rollout_changes_reindex_without_metadata_or_projection_change(self):
        records = [self.response_record('压缩原始正文')]
        path = self.write_compressed_rollout('b', records)
        self.use_rollout_body('b')
        self.store.sync_once()
        previous = path.stat()
        updated_at = self.store.get_thread('b')['updated_at']
        records.append(self.response_record('压缩增量新增正文'))
        self.write_compressed_rollout('b', records)
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 2_000_000_000))
        self.store.sync_once()
        self.assertEqual(self.store.get_thread('b')['updated_at'], updated_at)
        with writable_db(self.home / 'thread_history_1.sqlite') as connection:
            self.assertEqual(connection.execute(
                'SELECT next_rollout_byte_offset FROM thread_history_projection_state WHERE thread_id=?',
                ('b',)).fetchone()[0], 100)
        self.assertEqual(self.search(content='压缩原始正文')['total'], 1)
        self.assertEqual(self.search(content='压缩增量新增正文')['total'], 1)

    def test_plain_rollout_takes_precedence_over_compressed_copy(self):
        path = self.write_compressed_rollout('b', [self.response_record('压缩旧副本正文')])
        self.write_rollout('b', '未压缩优先正文')
        self.use_rollout_body('b')
        current = path.stat()
        os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 2_000_000_000))
        items = list(self.store.iter_items(self.store.get_thread('b')))
        self.assertEqual([item['data']['text'] for item in items], ['未压缩优先正文'])
        self.store.sync_once()
        self.assertEqual(self.search(content='未压缩优先正文')['total'], 1)
        self.assertEqual(self.search(content='压缩旧副本正文')['total'], 0)

    def test_explicit_compressed_rollout_uses_completed_events_without_duplicates(self):
        path = self.write_compressed_rollout('b', [
            self.response_record('压缩重复表示'),
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
                'type': 'AgentMessage', 'text': '压缩唯一正文'}}},
        ])
        self.use_rollout_body('b')
        with writable_db(self.home / 'state_5.sqlite') as connection:
            connection.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(path), 'b'))
        items = list(self.store.iter_items(self.store.get_thread('b')))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['data']['text'], '压缩唯一正文')
        self.store.sync_once()
        self.assertEqual(self.search(content='压缩唯一正文')['total'], 1)
        self.assertEqual(self.search(content='压缩重复表示')['total'], 0)

    def test_compressed_rollout_reports_missing_decompression_dependency(self):
        import builtins
        from unittest.mock import patch
        (self.home / 'b.jsonl.zst').write_bytes(b'compressed fixture')
        self.use_rollout_body('b')
        original_import = builtins.__import__

        def without_decompressor(name, *args, **kwargs):
            unavailable_modules = ('compression', 'zstandard')
            if name in unavailable_modules:
                raise ImportError('decompressor unavailable for test')
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=without_decompressor):
            with self.assertRaisesRegex(ValueError, '(?i)zstandard'):
                list(self.store.iter_items(self.store.get_thread('b')))

    def test_corrupt_compressed_rollout_does_not_abort_other_threads(self):
        path = self.write_compressed_rollout('b', [self.response_record('待损坏正文')])
        path.write_bytes(b'not a valid zstandard frame')
        self.use_rollout_body('b')
        with self.assertRaisesRegex(ValueError, '无法解压'):
            list(self.store.iter_items(self.store.get_thread('b')))
        with self.assertLogs('history-browser', level='ERROR'):
            self.store.sync_once()
        self.assertEqual(self.store.get_status()['indexed'], 3)
        self.assertIn('1 个会话索引失败', self.store.get_status()['error'])
        self.assertEqual(self.search(content='中文检索')['total'], 1)


if __name__=='__main__':unittest.main()
