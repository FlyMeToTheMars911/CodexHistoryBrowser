"""Windows desktop entry point for the local Codex history browser."""
from __future__ import annotations

import argparse
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from server import Handler, HistoryStore, LOG


APP_NAME = 'Codex History Browser'
APP_DIRECTORY = 'CodexHistoryBrowser'
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 860
WINDOW_MINIMUM = (900, 620)
LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3
THREAD_JOIN_SECONDS = 3
WINDOWS_PLATFORM = 'win32'
WEBVIEW_RENDERER = 'edgechromium'
MARKDOWN_SUFFIX = '.md'


def default_work_dir():
    """Return a persistent, writable directory outside the executable.

    Params: None.
    Returns: Path for the current user's desktop cache and logs.
    """
    local = os.environ.get('LOCALAPPDATA')
    root = Path(local) if local else Path.home() / 'AppData' / 'Local'
    return root / APP_DIRECTORY


def configure_logging(work_dir):
    """Configure bounded desktop file logging.

    Params: work_dir -- persistent directory for application data.
    Returns: Installed rotating log handler.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(work_dir / 'desktop.log', maxBytes=LOG_BYTES,
                                 backupCount=LOG_BACKUPS, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    return handler


def show_startup_error(message):
    """Display a startup failure when there is no console window.

    Params: message -- error and log location shown to the user.
    Returns: None.
    """
    if sys.platform == WINDOWS_PLATFORM:
        error_icon = 0x10
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, error_icon)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


class DesktopRuntime:
    """Own the HTTP server and index worker created by this window.

    Author: Codex
    date: 2026-10-29
    """

    def __init__(self, codex_home, work_dir):
        """Prepare a private local service.

        Params: codex_home -- source history; work_dir -- writable derived data.
        Returns: None.
        """
        self.store = HistoryStore(codex_home, work_dir)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = self.store
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name='desktop-http', daemon=True)
        self.started = False
        self.closed = False

    @property
    def url(self):
        """Return the randomly allocated local service address.

        Params: None.
        Returns: HTTP URL bound to the IPv4 loopback interface.
        """
        return f'http://127.0.0.1:{self.server.server_port}'

    def start(self):
        """Start this window's query and indexing threads.

        Params: None.
        Returns: None.
        """
        try:
            self.store.worker.start()
            self.thread.start()
            self.started = True
        except Exception:
            self.close()
            raise
        LOG.info('Desktop service ready url=%s codex_home=%s index=%s',
                 self.url, self.store.home, self.store.cache_path)

    def close(self):
        """Stop owned resources without touching any other running service.

        Params: None.
        Returns: None; repeated calls are safe.
        """
        if self.closed:
            return
        self.closed = True
        self.store.stop.set()
        self.store.wake.set()
        if self.started:
            self.server.shutdown()
            self.thread.join(THREAD_JOIN_SECONDS)
        self.server.server_close()
        if self.store.worker.ident is not None:
            self.store.worker.join(THREAD_JOIN_SECONDS)
        LOG.info('Desktop service stopped index_worker_alive=%s', self.store.worker.is_alive())


class DesktopApi:
    """Expose the native save dialog required by Markdown export.

    Author: Codex
    date: 2026-10-29
    """

    def __init__(self, webview_module, origin):
        """Bind native export to the trusted local application origin.

        Params: webview_module -- desktop engine; origin -- local server URL.
        Returns: None.
        """
        self._webview = webview_module
        self._window = None
        location = urlsplit(origin)
        self._origin = (location.scheme, location.netloc)

    def export_markdown(self, filename, text):
        """Save Markdown through the user's native file dialog.

        Params: filename -- suggested name; text -- complete UTF-8 Markdown.
        Returns: Mapping containing saved and, on success, the chosen path.
        """
        location = urlsplit(self._window.get_current_url() or '')
        if (location.scheme, location.netloc) != self._origin:
            raise ValueError('仅允许从本地历史查询窗口导出')
        if not isinstance(filename, str) or not isinstance(text, str):
            raise ValueError('导出文件名和正文必须为文本')
        name = Path(filename.replace('\\', '/')).name
        name = Path(name).stem + MARKDOWN_SUFFIX
        paths = self._window.create_file_dialog(
            self._webview.FileDialog.SAVE, save_filename=name,
            file_types=('Markdown (*.md)',))
        if not paths:
            return {'saved': False}
        destination = Path(paths if isinstance(paths, str) else paths[0])
        destination.write_text(text, encoding='utf-8')
        LOG.info('Markdown export saved path=%s bytes=%s', destination, destination.stat().st_size)
        return {'saved': True, 'path': str(destination)}


def main(argv=None):
    """Run the desktop window and clean up its service after closing.

    Params: argv -- optional command line arguments, otherwise sys.argv.
    Returns: Process exit code, zero on a normal window close.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', default=os.environ.get('CODEX_HOME') or str(Path.home() / '.codex'))
    parser.add_argument('--work-dir', type=Path, default=default_work_dir(),
                        help='Persistent desktop logs and derived search index directory')
    args = parser.parse_args(argv)
    runtime = None
    log_handler = None
    try:
        log_handler = configure_logging(args.work_dir)
        LOG.info('Desktop startup frozen=%s', bool(getattr(sys, 'frozen', False)))
        import webview

        runtime = DesktopRuntime(args.codex_home, args.work_dir)
        runtime.start()
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        webview.settings['ALLOW_FILE_URLS'] = False
        api = DesktopApi(webview, runtime.url)
        api._window = webview.create_window(
            APP_NAME, runtime.url, js_api=api, width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
            min_size=WINDOW_MINIMUM, text_select=True, zoomable=True,
            background_color='#f5f7fb')
        webview.start(gui=WEBVIEW_RENDERER, private_mode=True,
                      storage_path=str(args.work_dir / 'webview'))
        return 0
    except Exception as error:
        LOG.exception('Desktop startup or window failed')
        message = (f'无法打开历史查询工具：{error}\n\n'
                   f'日志：{args.work_dir / "desktop.log"}\n\n'
                   '请确认 Codex 本地数据目录存在，且已安装 Microsoft Edge WebView2 Runtime。'
                   '\n源码运行还需要安装 requirements-desktop.txt 中的依赖。')
        show_startup_error(message)
        return 1
    finally:
        if runtime is not None:
            runtime.close()
        if log_handler is not None:
            logging.getLogger().removeHandler(log_handler)
            log_handler.close()


if __name__ == '__main__':
    raise SystemExit(main())
