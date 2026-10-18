"""GitHub Releases updater; UI I/O occurs only on the Qt main thread."""
from __future__ import annotations
import json, os, re, sys, hashlib, tempfile, time, subprocess
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone
from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QHBoxLayout, QMessageBox, QTextEdit, QFrame, QScrollArea, QWidget, QSizePolicy

VERSION = "2.6.12"
REPO = "XiaoMu007/MaxCompress-"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_PATTERN = re.compile(r"^MaxCompress_Setup_v(\d+\.\d+\.\d+)\.exe$", re.I)
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

def version_tuple(s):
    m=VERSION_PATTERN.fullmatch(s.strip())
    if not m: raise ValueError("无效版本号: " + str(s))
    return tuple(map(int,m.groups()))

def verify_release_download_url(url, release_page_url, tag, asset_name):
    """Verify the installer is a GitHub Release asset from the release we queried.

    GitHub can canonicalize/redirect old repository names. Compare the current
    release page and asset path rather than a hard-coded owner/repository prefix.
    """
    download = urlsplit(url)
    page = urlsplit(release_page_url)
    if (download.scheme.lower() != "https" or download.hostname != "github.com"
            or page.scheme.lower() != "https" or page.hostname != "github.com"
            or download.username is not None or download.password is not None
            or page.username is not None or page.password is not None
            or download.port not in (None, 443) or page.port not in (None, 443)
            or download.query or download.fragment):
        raise RuntimeError("安装包地址不是有效的 GitHub Releases 官方 HTTPS 链接")
    dparts = [unquote(p) for p in download.path.split('/') if p]
    pparts = [unquote(p) for p in page.path.split('/') if p]
    if (len(dparts) != 6 or len(pparts) != 5
            or [p.lower() for p in dparts[2:4]] != ['releases', 'download']
            or [p.lower() for p in pparts[2:4]] != ['releases', 'tag']
            or [p.lower() for p in dparts[:2]] != [p.lower() for p in pparts[:2]]
            or dparts[4] != tag or pparts[4] != tag
            or dparts[5] != asset_name
            or any('/' in item or '\\' in item or item in ('.','..') for item in dparts)):
        raise RuntimeError("安装包链接与 GitHub 版本信息不匹配，请核对 Tag 和 EXE 附件名称")
    return url

CACHE_SECONDS_AUTO = 30 * 60
CACHE_SECONDS_MANUAL = 5 * 60
CACHE_KEEP_SECONDS = 7 * 24 * 3600

def _cache_path():
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'MaxCompress'
    return base / 'github_release_cache.json'

def _read_cache():
    try:
        data = json.loads(_cache_path().read_text(encoding='utf-8'))
        if data.get('repo') == REPO and isinstance(data.get('release'), dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return None

def _save_cache(release):
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'repo': REPO, 'checked_at': time.time(), 'release': release}, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)
    except OSError:
        pass  # 缓存写入失败不应影响检查更新

def _parse_release(data):
    tag = str(data.get('tag_name') or '')
    if data.get('draft') or data.get('prerelease'):
        return None
    # 兼容老版本两段式标签，避免旧 Release 阻断更新检测
    try:
        current = version_tuple(tag)
    except ValueError:
        short = re.fullmatch(r'v?(\d+)\.(\d+)', tag, re.I)
        if not short:
            raise RuntimeError('最新 Release 标签无法识别：' + tag + '。请将 Tag 设置为 v2.6.4 这类格式。')
        current = (int(short.group(1)), int(short.group(2)), 0)
    # 即使已经是最新版，也保留其公告、发布时间与版本号供更新中心展示。
    # 仅新版本才要求必须存在匹配的 EXE 下载附件。
    release_info = {
        'version': tag,
        'notes': str(data.get('body') or '该版本暂未填写更新说明。')[:20000],
        'published_at': str(data.get('published_at') or ''),
        'release_page': str(data.get('html_url') or ''),
        'is_newer': current > version_tuple(VERSION),
    }
    if not release_info['is_newer']:
        return release_info
    matches = [a for a in data.get('assets', []) if ASSET_PATTERN.fullmatch(a.get('name',''))]
    matches = [a for a in matches if version_tuple(ASSET_PATTERN.fullmatch(a['name']).group(1)) == current]
    if len(matches) != 1:
        raise RuntimeError('发现新版，但没有找到版本号匹配的唯一 EXE 安装包，请检查 Release 附件。')
    asset = matches[0]
    url = asset.get('browser_download_url', '')
    verify_release_download_url(url, data.get('html_url', ''), tag, asset['name'])
    release_info.update({
        'url': url, 'name': asset['name'], 'size': int(asset.get('size') or 0),
        'digest': asset.get('digest', '')
    })
    return release_info

def _ratelimit_message(exc):
    reset = exc.headers.get('X-RateLimit-Reset')
    if reset:
        try:
            wait = max(1, int((int(reset) - time.time() + 59) / 60))
            return f'GitHub API 请求次数暂时达到上限，请约 {wait} 分钟后重试。'
        except (ValueError, TypeError):
            pass
    retry = exc.headers.get('Retry-After')
    if retry:
        return f'GitHub 暂时限制更新检查，请等待约 {retry} 秒后重试。'
    return 'GitHub API 暂时限制了请求，请稍后重试，或前往 GitHub Releases 手动下载。'

def get_latest_release(manual=False):
    cache = _read_cache()
    now = time.time()
    interval = CACHE_SECONDS_MANUAL if manual else CACHE_SECONDS_AUTO
    if cache and 0 <= now - cache.get('checked_at', 0) < interval:
        return _parse_release(cache['release'])
    req = Request(API_URL, headers={
        'Accept':'application/vnd.github+json', 'User-Agent':'MaxCompress-Updater/'+VERSION,
        'X-GitHub-Api-Version':'2022-11-28'})
    try:
        with urlopen(req, timeout=15) as response:
            data = json.load(response)
    except HTTPError as exc:
        if exc.code in (403, 429):
            # 不冒险使用不确定的安装包地址；缓存内容须来自之前校验过的 GitHub Release。
            if cache and 0 <= now - cache.get('checked_at', 0) < CACHE_KEEP_SECONDS:
                return _parse_release(cache['release'])
            raise RuntimeError(_ratelimit_message(exc)) from exc
        raise RuntimeError(f'GitHub 更新请求失败（HTTP {exc.code}），请稍后重试。') from exc
    _save_cache(data)
    return _parse_release(data)

class CheckWorker(QThread):
    result = Signal(object)
    failed = Signal(str)
    def __init__(self, manual=False):
        super().__init__()
        self.manual = manual
    def run(self):
        try:
            self.result.emit(get_latest_release(manual=self.manual))
        except Exception as exc:
            self.failed.emit(str(exc))

class DownloadWorker(QThread):
    progress=Signal(int,float,int,int)
    done=Signal(str)
    failed=Signal(str)
    def __init__(self, release):
        super().__init__();self.release=release;self.abort=False
    def run(self):
        tmp=None
        try:
            folder=Path(tempfile.gettempdir())/'MaxCompress-updates'
            folder.mkdir(parents=True,exist_ok=True)
            tmp=folder/(self.release['name']+'.part')
            dest=folder/self.release['name']
            # Always download to a fresh .part file; never trust incomplete previous downloads.
            req=Request(self.release['url'],headers={'User-Agent':'MaxCompress-Updater/'+VERSION})
            h=hashlib.sha256(); count=0; start=time.monotonic()
            with urlopen(req,timeout=40) as res, open(tmp,'wb') as out:
                total=int(res.headers.get('Content-Length') or self.release['size'] or 0)
                while True:
                    if self.abort: raise InterruptedError('下载已取消')
                    block=res.read(256*1024)
                    if not block:break
                    out.write(block);h.update(block);count+=len(block)
                    seconds=max(.01,time.monotonic()-start)
                    self.progress.emit(min(100,int(count/total*100)) if total else 0,count/seconds/1048576,count,total)
            if self.release['size'] and count!=self.release['size']:
                raise RuntimeError('下载文件大小与 GitHub 记录不一致')
            if count<65536: raise RuntimeError('安装文件异常：大小不足')
            with open(tmp,'rb') as f:
                if f.read(2)!=b'MZ': raise RuntimeError('下载文件不是有效的 Windows EXE')
            digest=self.release.get('digest') or ''
            if digest.startswith('sha256:') and h.hexdigest().lower()!=digest[7:].lower():
                raise RuntimeError('SHA256 校验失败，文件可能损坏')
            os.replace(tmp,dest)
            self.done.emit(str(dest))
        except Exception as exc:
            if tmp is not None:
                try: tmp.unlink(missing_ok=True)
                except OSError:pass
            self.failed.emit(str(exc))

def launch_after_exit(installer, pid):
    """Launch traceable independent installer helper after main process exits."""
    exe = Path(installer).resolve()
    if not exe.is_file() or exe.suffix.lower() != '.exe':
        raise FileNotFoundError('已下载的安装包不存在，请重新下载。')
    if sys.platform != 'win32':
        raise RuntimeError('仅支持 Windows')
    if exe.stat().st_size < 65536:
        raise RuntimeError('安装包文件大小异常，请重新下载。')
    workdir = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'MaxCompress' / 'updates'
    workdir.mkdir(parents=True, exist_ok=True)
    logfile = workdir / 'installer_launch.log'
    helper = workdir / 'launch_installer.ps1'
    script = r'''param([int]$ParentPid, [string]$Installer, [string]$LogPath)
$ErrorActionPreference = 'Stop'
# Background helper: no console window is created
function Write-Log([string]$message) {
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $message"
  try { Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 } catch {}
  Write-Host $line
}
function Show-Failure([string]$message) {
  Write-Log $message
  try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($message + "`n`n安装包：" + $Installer + "`n日志：" + $LogPath, 'MaxCompress 安装失败', 'OK', 'Warning') | Out-Null
  } catch {}
  # Error details remain in the log and a native Windows message box is shown.
}
try {
  Write-Log ('准备安装：' + $Installer)
  Write-Log ('等待旧版进程退出，PID=' + $ParentPid)
  $limit = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $limit) {
    if (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 400
  }
  if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
    Show-Failure '旧版程序尚未退出。请关闭 MaxCompress 后，手动打开已下载的安装包。'
    exit 2
  }
  if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    Show-Failure '安装包不存在，请重新下载。'
    exit 3
  }
  $file = Get-Item -LiteralPath $Installer
  if ($file.Length -lt 65536) { throw '安装包文件大小异常，请重新下载。' }
  Write-Log '旧版已退出。即将打开安装向导，请检查任务栏或 Windows 权限提示。'
  # Start normally: the Inno Setup EXE decides whether administrator privileges are needed.
  $proc = Start-Process -FilePath $Installer -WorkingDirectory $file.DirectoryName -PassThru -ErrorAction Stop
  if ($null -eq $proc) { throw 'Windows 未返回安装进程' }
  Write-Log ('安装向导已启动，PID=' + $proc.Id)
  Start-Sleep -Seconds 4
  exit 0
} catch {
  Show-Failure ('安装向导启动失败：' + $_.Exception.Message)
  exit 1
}
'''

    # UTF-8 BOM makes Chinese text safe for Windows PowerShell 5.1.
    helper.write_text(script, encoding='utf-8-sig')
    with logfile.open('a', encoding='utf-8') as log:
        log.write('\n-- MaxCompress v' + VERSION + ' 启动安装任务 ' + datetime.now().isoformat(timespec='seconds') + ' --\n')
    # Run helper without a PowerShell console. The PowerShell script still
    # displays a native Windows error dialog and writes installer_launch.log.
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    proc = subprocess.Popen(
        ['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
         '-File', str(helper), '-ParentPid', str(int(pid)),
         '-Installer', str(exe), '-LogPath', str(logfile)],
        creationflags=flags, startupinfo=startupinfo, close_fds=True,
        stdin=subprocess.DEVNULL)
    # Detect immediate launch errors before we close the old application.
    import time as _time
    _time.sleep(0.5)
    if proc.poll() is not None:
        raise RuntimeError('更新启动器过早退出，主程序已保留。请查看：' + str(logfile))
    return str(logfile)

class UpdateDialog(QDialog):
    """User-facing update center, also useful when no new release exists."""
    def __init__(self, release=None, parent=None):
        super().__init__(parent)
        self.release = release
        self.worker = None
        self.installer = None
        self.setWindowTitle('更新与公告')
        self.setMinimumSize(650, 530)
        self.resize(800, 640)
        self.setStyleSheet("""
            QDialog {background: #f1f6fd; color: #132b49; font-size: 14px;}
            QFrame#panel {background: #ffffff; border: 1px solid #d8e5f3; border-radius: 14px;}
            QLabel#sectionTitle {font-size: 20px; font-weight: 700; color: #132b49;}
            QLabel#muted {color: #627b99;}
            QPushButton {padding: 10px 17px; background: #f9fbff; border: 1px solid #cddcf0;
                         border-radius: 8px; color: #24446c;}
            QPushButton:hover:enabled {background: #e9f3ff;}
            QPushButton#downloadBtn {background: #1672e8; color: white; font-weight: 600;}
            QPushButton#downloadBtn:disabled {background: #e8edf4; color: #9baec3;}
            QProgressBar {background: #eaf0f7; border: 1px solid #d9e4f0; border-radius: 7px;
                          text-align: center; min-height: 18px;}
            QProgressBar::chunk {background: #3291f7; border-radius: 7px;}
            QTextEdit {background: #f7f9fc; border: none; border-radius: 6px;
                       padding: 14px; color: #243d5c;}
        """)
        shell=QVBoxLayout(self);shell.setContentsMargins(26,23,26,23);shell.setSpacing(16)
        heading=QLabel('更新与公告');heading.setStyleSheet('font-size:24px;font-weight:700;color:#17375f;')
        shell.addWidget(heading)

        panel=QFrame();panel.setObjectName('panel');outer=QVBoxLayout(panel)
        outer.setContentsMargins(26,22,26,22);outer.setSpacing(13)
        title=QLabel('⇩  版本与下载');title.setObjectName('sectionTitle');outer.addWidget(title)
        desc=QLabel('从 GitHub Releases 获取正式版本，下载时显示实际进度。')
        desc.setObjectName('muted');desc.setWordWrap(True);outer.addWidget(desc)
        self.version_label=QLabel(f'当前版本 v{VERSION} · 正在检查最新版本…')
        outer.addWidget(self.version_label)
        self.bar=QProgressBar();self.bar.setRange(0,100);self.bar.setValue(0);outer.addWidget(self.bar)
        self.status=QLabel('正在检查更新…');self.status.setObjectName('muted');outer.addWidget(self.status)
        actions=QHBoxLayout();actions.setSpacing(10)
        self.check_button=QPushButton('⟳ 检查更新')
        self.check_button.clicked.connect(self.recheck)
        actions.addWidget(self.check_button)
        self.button=QPushButton('下载更新');self.button.setObjectName('downloadBtn')
        self.button.setEnabled(False);self.button.clicked.connect(self.download);actions.addWidget(self.button)
        self.close_button=QPushButton('关闭');self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button);actions.addStretch(1)
        outer.addLayout(actions);shell.addWidget(panel)

        notes_panel=QFrame();notes_panel.setObjectName('panel');nl=QVBoxLayout(notes_panel)
        nl.setContentsMargins(26,22,26,22);nl.setSpacing(10)
        t=QLabel('▤  更新说明与公告');t.setObjectName('sectionTitle');nl.addWidget(t)
        muted=QLabel('显示最新正式版本的更新内容。');muted.setObjectName('muted');nl.addWidget(muted)
        self.notes=QTextEdit();self.notes.setReadOnly(True)
        self.notes.setPlaceholderText('更新说明将在检查完成后显示')
        nl.addWidget(self.notes)
        shell.addWidget(notes_panel,1)
        if release is not None:
            self.set_release(release)

    def recheck(self):
        parent=self.parent()
        if parent and hasattr(parent,'check_updates'):
            self.set_checking()
            parent.check_updates(manual=True)

    def set_checking(self):
        self.check_button.setEnabled(False)
        self.version_label.setText(f'当前版本 v{VERSION} · 正在检查…')
        self.status.setText('正在连接 GitHub…')
        if not self.installer:
            self.button.setEnabled(False)

    def set_release(self, release):
        self.check_button.setEnabled(True)
        self.release=release
        if release is None:
            self.version_label.setText(f'当前版本 v{VERSION} · 暂无可展示的正式版本')
            self.status.setText('GitHub 暂无有效的正式版公告。')
            self.notes.setPlainText('尚未获取到正式版本说明。')
            self.button.setEnabled(False)
            return
        self.version_label.setText(f'当前版本 v{VERSION} · 最新正式版 {release["version"]}')
        self.notes.setPlainText(release.get('notes') or '该版本暂未填写更新说明。')
        if release.get('is_newer', False):
            self.status.setText('发现新版本，可下载安装。')
            self.button.setEnabled(bool(release.get('url')))
        else:
            self.status.setText('当前已是最新正式版本，下方是本版本的发布说明。')
            self.button.setEnabled(False)

    def set_check_error(self, error):
        self.check_button.setEnabled(True)
        self.status.setText('检查失败，可稍后重试；压缩功能不受影响。')
        self.version_label.setText(f'当前版本 v{VERSION} · 暂时无法获取最新版本')
        self.notes.setPlainText('更新检查失败：\n'+str(error))
        self.button.setEnabled(False)

    def download(self):
        if self.worker and self.worker.isRunning():return
        if not self.release:
            return
        if not self.release.get('is_newer') or not self.release.get('url'):
            return
        self.button.setEnabled(False);self.check_button.setEnabled(False);self.status.setText('正在从 GitHub 下载…')
        self.worker=DownloadWorker(self.release)
        self.worker.progress.connect(self.show_progress)
        self.worker.done.connect(self.downloaded)
        self.worker.failed.connect(self.failed)
        self.worker.start()
    def show_progress(self,percent,speed,count,total):
        self.bar.setRange(0,100) if total else self.bar.setRange(0,0)
        if total:self.bar.setValue(percent)
        self.status.setText(f"已下载 {count/1048576:.1f} MB / {total/1048576:.1f} MB，速度 {speed:.2f} MB/s" if total else f"已下载 {count/1048576:.1f} MB，速度 {speed:.2f} MB/s")
    def failed(self,error):
        self.button.setEnabled(True);self.check_button.setEnabled(True);self.status.setText('下载失败，现有版本未更改。')
        QMessageBox.warning(self,'更新失败',error+'\n\n现有版本仍可正常使用。')
    def downloaded(self,path):
        self.installer=path;self.bar.setRange(0,100);self.bar.setValue(100)
        self.status.setText('下载并验证完成。')
        self.button.setText('安装并退出');self.button.setEnabled(True)
        try:self.button.clicked.disconnect()
        except RuntimeError:pass
        self.button.clicked.connect(self.install)
    def install(self):
        parent=self.parent()
        if parent and getattr(parent,'worker',None) is not None and parent.worker.isRunning():
            QMessageBox.information(self,'请稍后','正在压缩文件，请等待压缩任务完成后再更新。')
            return
        box=QMessageBox(self)
        box.setWindowTitle('确认安装')
        box.setText('即将退出 MaxCompress，等待程序完全关闭后启动新版安装程序。')
        box.setInformativeText('请先保存工作；安装时若检测到其他 MaxCompress 窗口，请按提示关闭。')
        yes=box.addButton('是，立即安装',QMessageBox.AcceptRole)
        no=box.addButton('否',QMessageBox.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        if box.clickedButton() is not yes:return
        from PySide6.QtWidgets import QApplication
        if sys.platform!='win32':
            QMessageBox.warning(self,'平台不支持','自动安装仅支持 Windows。')
            return
        try:
            launch_after_exit(self.installer, os.getpid())
        except Exception as exc:
            QMessageBox.warning(self,'无法准备安装程序',str(exc)+'\n旧版本未改变。')
            return
        self.status.setText('正在退出旧版，稍后将启动安装器；失败时会显示错误和日志位置。')
        self.button.setEnabled(False)
        # Do not let the old Qt process continue running while installer replaces the executable.
        QApplication.quit()
        QTimer.singleShot(6000, lambda: os._exit(0))
    def reject(self):
        if self.worker and self.worker.isRunning():
            self.worker.abort=True
            self.worker.wait(2500)
            if self.worker.isRunning():
                QMessageBox.information(self,'请等待','正在停止下载，请稍后再关闭窗口。');return
        super().reject()
