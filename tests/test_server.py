import json
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


if __name__=='__main__':unittest.main()
