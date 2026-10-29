import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop import DesktopApi, DesktopRuntime, default_work_dir


class DesktopTest(unittest.TestCase):
    def test_service_uses_random_local_port_and_stops_on_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'codex'
            home.mkdir()
            connection = sqlite3.connect(home / 'state_5.sqlite')
            connection.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,cwd TEXT, '
                               'created_at INTEGER,updated_at INTEGER,archived INTEGER,rollout_path TEXT)')
            connection.close()
            runtime = DesktopRuntime(home, Path(temporary) / 'cache')
            runtime.start()
            try:
                self.assertEqual(runtime.server.server_address[0], '127.0.0.1')
                self.assertGreater(runtime.server.server_port, 0)
                with urlopen(runtime.url + '/api/search', timeout=3) as response:
                    self.assertEqual(json.load(response)['total'], 0)
                with urlopen(runtime.url, timeout=3) as response:
                    self.assertIn(b'<!doctype html>', response.read().lower())
            finally:
                runtime.close()
                runtime.close()
            self.assertFalse(runtime.thread.is_alive())
            self.assertFalse(runtime.store.worker.is_alive())

    def test_cache_is_persistent_and_independent_of_extraction_directory(self):
        with patch.dict('os.environ', {'LOCALAPPDATA': 'D:/User Cache'}):
            self.assertEqual(default_work_dir(), Path('D:/User Cache/CodexHistoryBrowser'))

    def test_markdown_export_and_cancel(self):
        class Dialog:
            SAVE = 1

        class Webview:
            FileDialog = Dialog

        class Window:
            def __init__(self, result):
                self.result = result

            def create_file_dialog(self, *args, **kwargs):
                return self.result

            def get_current_url(self):
                return 'http://127.0.0.1:43210/'

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / '历史导出.md'
            api = DesktopApi(Webview, 'http://127.0.0.1:43210')
            api._window = Window((str(target),))
            self.assertTrue(api.export_markdown('history.md', '\ufeff# 完整会话')['saved'])
            self.assertEqual(target.read_text(encoding='utf-8'), '\ufeff# 完整会话')
            api._window = Window(None)
            self.assertEqual(api.export_markdown('history.md', 'body'), {'saved': False})
            api._window.get_current_url = lambda: 'https://example.com/'
            with self.assertRaises(ValueError):
                api.export_markdown('history.md', 'body')


if __name__ == '__main__':
    unittest.main()
