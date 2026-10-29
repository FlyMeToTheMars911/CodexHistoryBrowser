"""Local Codex history browser. Python 3.10+, Zstandard support for compressed rollouts."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import webbrowser

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger('history-browser')
PAGE_SIZES = (10, 20, 50, 100)
MESSAGE_TYPES = ('userMessage', 'agentMessage')
MAX_DETAIL_PAGE = 100
INDEX_INTERVAL_SECONDS = 30
MAX_QUERY_CHARS = 300
TRIGRAM_SIZE = 3
HISTORY_ITEM_COLUMNS = frozenset(('thread_id', 'item_id', 'turn_id', 'rollout_ordinal',
                                  'created_at_ms', 'item_type', 'item_json'))
PROJECTION_COLUMNS = frozenset(('thread_id', 'next_rollout_byte_offset'))
MISSING_HISTORY_MESSAGE = '此会话的本地正文不可用：结构化历史为空，原始会话文件也不存在。会话元数据仍可查询。'
ZSTANDARD_SUFFIX = '.zst'
ZSTANDARD_DEPENDENCY_MESSAGE = '读取 .jsonl.zst 历史需要 Zstandard 支持；请安装 requirements.txt 中的依赖，或使用已内置依赖的 EXE。'


@contextmanager
def read_db(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    try:
        yield connection
    finally:
        connection.close()


def table_columns(connection, table):
    # The identifier comes exclusively from our fixed table names, never request input.
    return {row['name'] for row in connection.execute('PRAGMA table_info("' + table + '")')}


def discover_db(home, prefix, fallback):
    candidates = []
    for path in home.glob(prefix + '_*.sqlite'):
        version = path.stem.removeprefix(prefix + '_')
        if version.isdecimal():
            candidates.append((int(version), path))
    return max(candidates, key=lambda item: item[0])[1] if candidates else home / fallback


def file_signature(path):
    try:
        stat = path.stat()
        return f'{stat.st_size}:{stat.st_mtime_ns}'
    except FileNotFoundError:
        return 'missing'
    except OSError as error:
        # iter_items will report an unreadable source for this thread without
        # preventing other threads from synchronizing.
        return 'unreadable:' + type(error).__name__


def resolve_rollout_path(row):
    path = Path(row.get('rollout_path') or '')
    try:
        if path.is_file():
            return path
        compressed = Path(str(path) + ZSTANDARD_SUFFIX)
        if compressed.is_file():
            return compressed
    except OSError:
        # Opening the source will report a per-thread permission failure.
        pass
    return path


@contextmanager
def open_rollout(path):
    if path.suffix != ZSTANDARD_SUFFIX:
        with path.open(encoding='utf-8') as file:
            yield file
        return
    try:
        from compression import zstd
    except ImportError:
        try:
            import zstandard
        except ImportError as error:
            raise ValueError(ZSTANDARD_DEPENDENCY_MESSAGE) from error
        try:
            with path.open('rb') as raw:
                with zstandard.ZstdDecompressor().stream_reader(raw, read_across_frames=True) as stream:
                    with io.TextIOWrapper(stream, encoding='utf-8') as file:
                        yield file
        except zstandard.ZstdError as error:
            raise ValueError(f'会话压缩文件无法解压：{path}') from error
    else:
        try:
            with zstd.open(path, mode='rt', encoding='utf-8') as file:
                yield file
        except zstd.ZstdError as error:
            raise ValueError(f'会话压缩文件无法解压：{path}') from error


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(filter(None, (text_content(item) for item in value)))
    if isinstance(value, dict):
        for key in ('text', 'content', 'output', 'message'):
            if key in value:
                return text_content(value[key])
    return ''


def item_text(item):
    kind = item.get('type', '')
    if kind in ('userMessage', 'UserMessage'):
        return text_content(item.get('content', []))
    if kind in ('agentMessage', 'AgentMessage'):
        return item.get('text') or text_content(item.get('content', []))
    fields = ('command', 'aggregatedOutput', 'aggregated_output', 'stdout', 'stderr',
              'text', 'summary', 'content', 'result', 'output', 'arguments', 'changes')
    parts = []
    for key in fields:
        value = item.get(key)
        if value:
            parts.append(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    return '\n'.join(parts) or json.dumps(item, ensure_ascii=False)


class HistoryStore:
    def __init__(self, codex_home, work_dir):
        self.home = Path(codex_home).expanduser().resolve()
        self.state_path = discover_db(self.home, 'state', 'state_5.sqlite')
        self.history_path = discover_db(self.home, 'thread_history', 'thread_history_1.sqlite')
        if not self.state_path.is_file():
            raise ValueError(f'未找到任务数据库：{self.state_path}')
        with read_db(self.state_path) as connection:
            if not {'id', 'created_at', 'updated_at'}.issubset(table_columns(connection, 'threads')):
                raise ValueError(f'任务数据库 threads 缺少必需字段，需适配当前 Codex 结构：{self.state_path.name}')
        # Only our own derived index is writable. Never alter Codex files.
        identity = hashlib.sha256(str(self.home).encode()).hexdigest()[:12]
        self.cache_path = Path(work_dir) / f'search-v2-{identity}.sqlite'
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache() as connection:
            connection.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
                    body, content='documents', content_rowid='rowid', tokenize='trigram',
                    detail='none', columnsize=0
                );
                CREATE TRIGGER IF NOT EXISTS docs_insert AFTER INSERT ON documents BEGIN
                    INSERT INTO search_fts(rowid,body) VALUES(new.rowid,new.body);
                END;
                CREATE TRIGGER IF NOT EXISTS docs_delete AFTER DELETE ON documents BEGIN
                    INSERT INTO search_fts(search_fts,rowid,body) VALUES('delete',old.rowid,old.body);
                END;
                CREATE TRIGGER IF NOT EXISTS docs_update AFTER UPDATE ON documents BEGIN
                    INSERT INTO search_fts(search_fts,rowid,body) VALUES('delete',old.rowid,old.body);
                    INSERT INTO search_fts(rowid,body) VALUES(new.rowid,new.body);
                END;
            ''')
        self.status_lock = threading.Lock()
        self.status = dict(running=False, total=0, indexed=0, last_sync=None, error=None,
                           unavailable=0, warnings=[])
        self.schema_warnings = []
        self.unavailable = {}
        self.inspect_history_schema()
        LOG.info('Source schema state=%s history=%s structured_history=%s projection=%s',
                 self.state_path.name, self.history_path.name, self.history_items_available,
                 self.projection_available)
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.sync_loop, name='content-index', daemon=True)

    @contextmanager
    def cache(self):
        connection = sqlite3.connect(self.cache_path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def set_status(self, **values):
        with self.status_lock:
            self.status.update(values)

    def get_status(self):
        with self.status_lock:
            return {**self.status, 'codex_home': str(self.home),
                    'schema': {'state': self.state_path.name, 'history': self.history_path.name,
                               'structured_history': self.history_items_available,
                               'projection': self.projection_available}}

    def inspect_history_schema(self):
        warnings = []
        self.history_items_available = False
        self.projection_available = False
        if self.history_path.is_file():
            with read_db(self.history_path) as connection:
                self.history_items_available = HISTORY_ITEM_COLUMNS.issubset(table_columns(connection, 'thread_items'))
                self.projection_available = PROJECTION_COLUMNS.issubset(table_columns(connection, 'thread_history_projection_state'))
            if not self.history_items_available:
                warnings.append('结构化历史表缺少必需字段，已改读原始会话文件。')
            elif not self.projection_available:
                warnings.append('历史投影表缺少必需字段，已改用文件变化检测增量更新。')
        else:
            warnings.append('未找到结构化历史数据库，已改读原始会话文件。')
        if warnings != self.schema_warnings:
            for warning in warnings:
                LOG.warning('History schema database=%s message=%s', self.history_path.name, warning)
        self.schema_warnings = warnings
        self.set_status(warnings=list(warnings))
        LOG.debug('Schema state=%s history=%s items=%s projection=%s', self.state_path.name,
                  self.history_path.name, self.history_items_available, self.projection_available)

    def threads(self):
        with read_db(self.state_path) as connection:
            rows = [dict(row) for row in connection.execute('SELECT * FROM threads')]
            projects = {}
            if {'id', 'name'}.issubset(table_columns(connection, 'projects')):
                projects = {row['id']: row['name'] for row in connection.execute('SELECT id,name FROM projects')}
        for row in rows:
            cwd = (row.get('cwd') or '').removeprefix('\\\\?\\').rstrip('\\/')
            row['archived'] = row.get('archived') or 0
            project_id = row.get('project_id')
            project_name = projects.get(project_id)
            # Actual project association takes precedence; old records fall back to cwd.
            row['project_key'] = 'project:' + project_id if project_name else 'cwd:' + cwd.casefold()
            row['project_name'] = project_name or cwd.replace('\\', '/').split('/')[-1] or '无项目'
            row['project_path'] = cwd
            row['title'] = row.get('name') or row.get('title') or '未命名会话'
        return rows

    def get_thread(self, thread_id):
        for row in self.threads():
            if row['id'] == thread_id:
                return row
        raise KeyError('会话不存在或已被移除')

    def iter_items(self, row, after=-1, limit=None):
        if self.history_items_available:
            with read_db(self.history_path) as connection:
                exists = connection.execute('SELECT 1 FROM thread_items WHERE thread_id=? LIMIT 1', (row['id'],)).fetchone()
                if exists:
                    sql = ('SELECT item_id,turn_id,rollout_ordinal,created_at_ms,item_type,item_json '
                           'FROM thread_items WHERE thread_id=? AND rollout_ordinal>? ORDER BY rollout_ordinal,item_id')
                    args = [row['id'], after]
                    if limit is not None:
                        sql += ' LIMIT ?'
                        args.append(limit)
                    for record in connection.execute(sql, args):
                        result = dict(record)
                        try:
                            result['data'] = json.loads(result.pop('item_json'))
                        except (json.JSONDecodeError, TypeError):
                            result['data'] = {'type': result['item_type'], 'text': record['item_json']}
                        yield result
                    return
        yield from self.rollout_items(row, after, limit)

    def rollout_items(self, row, after, limit):
        path = resolve_rollout_path(row)
        if not path.is_file():
            raise FileNotFoundError(MISSING_HISTORY_MESSAGE)
        # Prefer visible completed events if available; otherwise use response messages.
        has_events = False
        with open_rollout(path) as file:
            for line in file:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get('type') == 'event_msg' and event.get('payload', {}).get('type') == 'item_completed':
                    has_events = True
                    break
        emitted = 0
        turn_id = 'legacy'
        with open_rollout(path) as file:
            for line_number, line in enumerate(file):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # An active writer may not have completed the last line yet.
                payload = event.get('payload', {})
                ordinal = event.get('ordinal', line_number)
                if payload.get('turn_id'):
                    turn_id = payload['turn_id']
                if ordinal <= after:
                    continue
                data = None
                if has_events and event.get('type') == 'event_msg' and payload.get('type') == 'item_completed':
                    data = dict(payload.get('item', {}))
                    kind = data.get('type', 'unknown')
                    data['type'] = kind[:1].lower() + kind[1:]
                elif not has_events and event.get('type') == 'response_item':
                    if payload.get('type') == 'message' and payload.get('role') in ('user', 'assistant'):
                        data = dict(payload)
                        data['type'] = 'userMessage' if payload['role'] == 'user' else 'agentMessage'
                        if data['type'] == 'agentMessage':
                            data['text'] = text_content(data.get('content', []))
                    elif payload.get('type') in ('function_call', 'function_call_output', 'custom_tool_call', 'custom_tool_call_output'):
                        data = dict(payload)
                if data is not None:
                    timestamp = event.get('timestamp', '')
                    try:
                        created_at = int(datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp() * 1000)
                    except ValueError:
                        created_at = 0
                    yield dict(item_id=data.get('id', f'line-{line_number}'), turn_id=turn_id,
                               rollout_ordinal=ordinal, created_at_ms=created_at,
                               item_type=data.get('type', 'unknown'), data=data)
                    emitted += 1
                    if limit is not None and emitted >= limit:
                        return

    def sync_once(self):
        rows = self.threads()
        self.inspect_history_schema()
        projections = {}
        if self.projection_available:
            with read_db(self.history_path) as connection:
                projections = {r['thread_id']: str(r['next_rollout_byte_offset']) for r in connection.execute('SELECT * FROM thread_history_projection_state')}
        with self.cache() as connection:
            known = {r['id']: r['fingerprint'] for r in connection.execute('SELECT id,fingerprint FROM documents')}
        history_signature = file_signature(self.history_path) + ':' + file_signature(Path(str(self.history_path) + '-wal'))
        self.set_status(running=True, total=len(rows), indexed=0, error=None)
        failures = 0
        unavailable = {}
        for position, row in enumerate(rows, 1):
            if self.stop.is_set():
                return
            rollout = resolve_rollout_path(row)
            fingerprint = (f"v2:{self.state_path.name}:{self.history_path.name}:"
                           f"{row.get('updated_at_ms') or row['updated_at']}:"
                           f"{self.history_items_available}:{projections.get(row['id'], history_signature)}:"
                           f"{rollout}:{file_signature(rollout)}")
            if known.get(row['id']) != fingerprint:
                try:
                    body = '\n\n'.join(item_text(item['data']) for item in self.iter_items(row))
                    with self.cache() as connection:
                        connection.execute('INSERT INTO documents(id,fingerprint,body) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET fingerprint=excluded.fingerprint,body=excluded.body', (row['id'], fingerprint, body))
                except FileNotFoundError:
                    unavailable[row['id']] = fingerprint
                    with self.cache() as connection:
                        connection.execute('DELETE FROM documents WHERE id=?', (row['id'],))
                    if row['id'] not in self.unavailable:
                        LOG.warning('History unavailable thread=%s rollout=%s; metadata remains searchable', row['id'], rollout)
                except (OSError, sqlite3.Error, ValueError):
                    failures += 1
                    LOG.exception('Index failed thread=%s', row['id'])
            self.set_status(indexed=position)
        live = {row['id'] for row in rows}
        with self.cache() as connection:
            connection.executemany('DELETE FROM documents WHERE id=?', [(key,) for key in known.keys() - live])
        self.unavailable = unavailable
        warnings = list(self.schema_warnings)
        if unavailable:
            warnings.append(f'{len(unavailable)} 个会话的本地正文不可用，仍可查询标题和项目；文件恢复后会自动重新索引。')
        self.set_status(running=False, last_sync=int(time.time()), unavailable=len(unavailable), warnings=warnings,
                        error=f'{failures} 个会话索引失败，详见服务日志' if failures else None)
        LOG.info('Index synchronized threads=%s unavailable=%s failed=%s', len(rows), len(unavailable), failures)

    def sync_loop(self):
        while not self.stop.is_set():
            self.wake.clear()
            try:
                self.sync_once()
            except Exception:
                LOG.exception('Index synchronization failed')
                self.set_status(running=False, error='内容索引更新失败，请查看服务日志')
            self.wake.wait(INDEX_INTERVAL_SECONDS)

    def search_body(self, query):
        with self.cache() as connection:
            # Store document membership only, not every trigram position. Intersect
            # trigram candidates then check the literal substring to avoid false hits.
            if len(query) >= TRIGRAM_SIZE:
                grams = dict.fromkeys(query[i:i + TRIGRAM_SIZE] for i in range(len(query) - TRIGRAM_SIZE + 1))
                phrase = ' AND '.join('"' + gram.replace('"', '""') + '"' for gram in grams)
                candidates = 'SELECT d.id,d.body FROM search_fts f JOIN documents d ON d.rowid=f.rowid WHERE search_fts MATCH ?'
                args = [phrase]
            else:
                candidates = 'SELECT id,body FROM documents'
                args = []
            rows = connection.execute(
                'WITH candidates AS (' + candidates + ') '
                'SELECT id,substr(body,max(1,instr(lower(body),lower(?))-70),?) AS excerpt '
                'FROM candidates WHERE instr(lower(body),lower(?))>0',
                args + [query, len(query) + 230, query])
            return {row['id']: row['excerpt'] + '…' for row in rows}

    def search(self, params):
        page = integer(params.get('page', '1'), 1, 1000000)
        size = integer(params.get('page_size', '20'), 1, 100)
        if size not in PAGE_SIZES:
            raise ValueError('分页大小必须为 10、20、50 或 100')
        title = params.get('title', '').strip().casefold()
        content = params.get('content', '').strip()
        if len(content) > MAX_QUERY_CHARS:
            raise ValueError(f'内容关键词最多 {MAX_QUERY_CHARS} 字符')
        matches = self.search_body(content) if content else None
        date_field = params.get('time_field', 'updated_at')
        if date_field not in ('updated_at', 'created_at'):
            raise ValueError('时间字段不正确')
        start = date_bound(params.get('start', ''))
        end = date_bound(params.get('end', ''), end=True)
        if start is not None and end is not None and start >= end:
            raise ValueError('开始日期不能晚于结束日期')
        results = []
        for row in self.threads():
            if title and title not in row['title'].casefold():
                continue
            if params.get('project') and row['project_key'] != params['project']:
                continue
            if params.get('archived', '') in ('0', '1') and bool(row['archived']) != (params['archived'] == '1'):
                continue
            if params.get('include_agents', '0') != '1' and (row.get('thread_source') == 'subagent' or 'subagent' in (row.get('source') or '')):
                continue
            value = row.get(date_field) or 0
            if start is not None and value < start or end is not None and value >= end:
                continue
            if matches is not None and row['id'] not in matches:
                continue
            results.append({key: row.get(key) for key in ('id', 'title', 'project_name', 'project_path', 'project_key', 'created_at', 'updated_at', 'archived', 'model', 'thread_source', 'preview')})
            results[-1]['snippet'] = matches[row['id']] if matches is not None else (row.get('preview') or '')[:240]
        results.sort(key=lambda row: (row.get(date_field) or 0, row['id']), reverse=True)
        total = len(results)
        pages = max(1, (total + size - 1) // size)
        page = min(page, pages)
        return dict(items=results[(page - 1) * size:page * size], total=total, page=page, pages=pages, page_size=size, index=self.get_status())

    def projects(self):
        projects = {}
        for row in self.threads():
            key = row['project_key']
            if key not in projects:
                projects[key] = dict(key=key, name=row['project_name'], path=row['project_path'], count=0)
            projects[key]['count'] += 1
        return sorted(projects.values(), key=lambda item: (item['name'].casefold(), item['path']))


def integer(value, minimum, maximum):
    try:
        value = int(value)
    except (ValueError, TypeError):
        raise ValueError('分页参数必须为整数')
    if not minimum <= value <= maximum:
        raise ValueError('分页参数超出范围')
    return value


def date_bound(value, end=False):
    if not value:
        return None
    try:
        result = datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        raise ValueError('日期格式必须为 YYYY-MM-DD')
    if end:
        result += timedelta(days=1)
    return result.timestamp()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, message, *args):
        LOG.info('%s %s', self.client_address[0], message % args)

    def respond(self, status, value, mime='application/json; charset=utf-8'):
        body = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, (dict, list)) else value
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        host = self.headers.get('Host', '').split(':')[0].lower()
        if host not in ('127.0.0.1', 'localhost'):
            self.respond(403, {'error': '仅支持本机访问'})
            return
        route = urlparse(self.path)
        params = {key: values[-1] for key, values in parse_qs(route.query).items()}
        store = self.server.store
        try:
            if route.path == '/api/status':
                self.respond(200, store.get_status())
            elif route.path == '/api/projects':
                self.respond(200, store.projects())
            elif route.path == '/api/search':
                self.respond(200, store.search(params))
            elif route.path == '/api/thread':
                row = store.get_thread(params.get('id', ''))
                after = integer(params.get('after', '-1'), -1, 2**63 - 1)
                size = integer(params.get('limit', '60'), 1, MAX_DETAIL_PAGE)
                items = list(store.iter_items(row, after, size + 1))
                has_more = len(items) > size
                items = items[:size]
                self.respond(200, dict(thread={k: row.get(k) for k in ('id', 'title', 'cwd', 'project_name', 'created_at', 'updated_at', 'archived', 'model', 'rollout_path')}, items=items, has_more=has_more, next_cursor=items[-1]['rollout_ordinal'] if items else after))
            elif route.path in ('/', '/index.html', '/app.js', '/style.css'):
                name = 'index.html' if route.path == '/' else route.path[1:]
                mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}[Path(name).suffix]
                self.respond(200, (ROOT / 'static' / name).read_bytes(), mime + '; charset=utf-8')
            else:
                self.respond(404, {'error': '页面不存在'})
        except (ValueError, KeyError) as error:
            self.respond(400 if isinstance(error, ValueError) else 404, {'error': str(error).strip("'")})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except FileNotFoundError as error:
            LOG.warning('History request unavailable route=%s thread=%s', route.path, params.get('id', ''))
            self.respond(404, {'error': str(error)})
        except Exception:
            LOG.exception('Request failed route=%s', route.path)
            self.respond(500, {'error': '读取记录失败，请查看程序运行日志'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', default=os.environ.get('CODEX_HOME') or str(Path.home() / '.codex'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[logging.StreamHandler(), logging.FileHandler(work / 'server.log', encoding='utf-8')])
    try:
        store = HistoryStore(args.codex_home, work)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except (ValueError, OSError, sqlite3.Error) as error:
        LOG.error('Startup failed: %s', error)
        return 1
    server.store = store
    store.worker.start()
    url = f'http://127.0.0.1:{server.server_port}'
    LOG.info('Ready url=%s codex_home=%s index=%s', url, store.home, store.cache_path)
    print(f'\nCodex History Browser: {url}\nPress Ctrl+C to stop.\n', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        store.stop.set()
        store.wake.set()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
