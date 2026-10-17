"""GitHub Releases updater; UI I/O occurs only on the Qt main thread."""
from __future__ import annotations
import json, os, re, sys, hashlib, tempfile, time, subprocess
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QHBoxLayout, QMessageBox, QTextEdit

VERSION = "2.6.1"
REPO = "2819lijiawei/MaxCompress-"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_PATTERN = re.compile(r"^MaxCompress_Setup_v(\d+\.\d+\.\d+)\.exe$", re.I)
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

def version_tuple(s):
    m=VERSION_PATTERN.fullmatch(s.strip())
    if not m: raise ValueError("无效版本号: " + str(s))
    return tuple(map(int,m.groups()))

def get_latest_release():
    req=Request(API_URL,headers={"Accept":"application/vnd.github+json", "User-Agent":"MaxCompress-Updater/"+VERSION, "X-GitHub-Api-Version":"2022-11-28"})
    with urlopen(req,timeout=15) as response:
        data=json.load(response)
    tag=data.get('tag_name','')
    if data.get('draft') or data.get('prerelease') or version_tuple(tag)<=version_tuple(VERSION):
        return None
    matches=[a for a in data.get('assets',[]) if ASSET_PATTERN.fullmatch(a.get('name',''))]
    matches=[a for a in matches if version_tuple(ASSET_PATTERN.fullmatch(a['name']).group(1))==version_tuple(tag)]
    if len(matches)!=1: raise RuntimeError("发现新版，但未找到与版本号一致的唯一 Windows 安装包，请检查 Release 附件命名。")
    asset=matches[0]
    url=asset.get('browser_download_url','')
    if not url.startswith('https://github.com/'+REPO+'/releases/download/'):
        raise RuntimeError('下载地址不符合预期的 GitHub 官方发布路径')
    return {"version":tag,"notes":str(data.get('body') or '该版本没有提供更新说明。')[:10000],"url":url,"name":asset['name'],"size":int(asset.get('size') or 0),"digest":asset.get('digest','')}

class CheckWorker(QThread):
    result=Signal(object)
    failed=Signal(str)
    def run(self):
        try: self.result.emit(get_latest_release())
        except Exception as e: self.failed.emit(str(e))

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

class UpdateDialog(QDialog):
    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.release=release;self.worker=None;self.installer=None
        self.setWindowTitle('MaxCompress 软件更新');self.resize(540,400)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(f"发现新版本：{release['version']}   ·   当前版本：v{VERSION}"))
        layout.addWidget(QLabel('更新内容：'))
        notes=QTextEdit(); notes.setReadOnly(True);notes.setPlainText(release['notes']);layout.addWidget(notes)
        self.status=QLabel('下载安装包前请保存其他工作。');layout.addWidget(self.status)
        self.bar=QProgressBar();self.bar.setRange(0,100);self.bar.setValue(0);layout.addWidget(self.bar)
        actions=QHBoxLayout();self.button=QPushButton('下载更新');self.button.clicked.connect(self.download)
        close=QPushButton('稍后更新');close.clicked.connect(self.reject)
        actions.addWidget(self.button);actions.addWidget(close);layout.addLayout(actions)
    def download(self):
        if self.worker and self.worker.isRunning():return
        self.button.setEnabled(False);self.status.setText('正在从 GitHub 下载…')
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
        self.button.setEnabled(True);self.status.setText('下载失败，现有版本未更改。')
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
            QMessageBox.information(self,'请稍后','正在压缩文件，请等待压缩完成后再更新。');return
        if QMessageBox.question(self,'确认安装','即将退出 MaxCompress 并启动新版安装程序，是否继续？',QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:return
        try:
            flags=(subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform=='win32' else 0
            subprocess.Popen([self.installer],creationflags=flags,close_fds=True)
        except Exception as exc:
            QMessageBox.warning(self,'无法启动安装程序',str(exc)+'\n旧版本未改变。');return
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
    def reject(self):
        if self.worker and self.worker.isRunning():
            self.worker.abort=True
            self.worker.wait(2500)
            if self.worker.isRunning():
                QMessageBox.information(self,'请等待','正在停止下载，请稍后再关闭窗口。');return
        super().reject()
