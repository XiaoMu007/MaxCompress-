
from __future__ import annotations
import sys, os, traceback, concurrent.futures, threading, datetime, collections, zipfile, time, platform, subprocess
from pathlib import Path
from dataclasses import dataclass

from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer, QObject, QRunnable, QThreadPool
from PySide6.QtGui import QFont, QIcon, QColor, QPixmap, QImage, QImageReader
from PySide6.QtWidgets import (
    QApplication,QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QPushButton,QLabel,QSizePolicy,
    QFileDialog,QTableWidget,QTableWidgetItem,QHeaderView,QProgressBar,QComboBox,
    QCheckBox,QLineEdit,QFrame,QMessageBox,QAbstractItemView,QButtonGroup,QStackedWidget
)
from compressors import *
from compressors import _quiet_subprocess_options
from updater import VERSION, CheckWorker, UpdateDialog

def asset_path(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / name


@dataclass
class Item:
    src: Path
    kind: str
    out: Path|None = None
    status: str = "等待压缩"

class Worker(QThread):
    progress = Signal(int,int)
    stage = Signal(int,str)
    encoder_changed = Signal(int,str)
    started_one = Signal(int)
    finished_one = Signal(int,str,str)
    failed_one = Signal(int,str)
    all_done = Signal()
    archived = Signal(str)
    archive_failed = Signal(str)

    def __init__(self, items, out_dir, level, concurrency, encoder_mode="自动（GPU优先）", output_kind="图片", output_format="保持原格式"):
        super().__init__()
        self.items=list(items)
        self.out_dir=Path(out_dir)
        self.level=level
        self.concurrency=concurrency
        self.encoder_mode=encoder_mode
        self.output_kind=output_kind
        self.output_format=output_format
        self.pause_gate=threading.Event()
        self.pause_gate.set()
        self.name_locks={}
        self.name_lock_guard=threading.Lock()
        self.failures=[]
        self.failure_log_path=self.out_dir / "压缩失败日志.txt"

    def set_paused(self, paused: bool):
        # Cooperative pause: stops on the next callback / safe phase boundary.
        if paused:
            self.pause_gate.clear()
        else:
            self.pause_gate.set()

    def process(self, i, item):
        self.pause_gate.wait()
        self.started_one.emit(i)
        def cb(v, idx=i):
            self.pause_gate.wait()
            self.progress.emit(idx, v)
        # Same-named inputs must not race for the same output path.
        key=(item.src.stem.lower(),item.kind)
        with self.name_lock_guard:
            lock=self.name_locks.setdefault(key,threading.Lock())
        with lock:
            self.pause_gate.wait()
            fmt=self.output_format if item.kind==self.output_kind else "保持原格式"
            if item.kind=="图片": out=compress_image(item.src,self.out_dir,self.level,cb,output_format=fmt)
            elif item.kind=="视频": out=compress_video(item.src,self.out_dir,self.level,cb,self.concurrency,self.encoder_mode,status_callback=lambda encoder,idx=i:self.encoder_changed.emit(idx,encoder),output_format=fmt)
            elif item.kind=="PDF": out=compress_pdf(item.src,self.out_dir,self.level,cb, status_callback=lambda text,idx=i:self.stage.emit(idx,text))
            elif item.kind=="Word": out=compress_word(item.src,self.out_dir,self.level,cb)
            elif item.kind=="PPT": out=compress_ppt(item.src,self.out_dir,self.level,cb)
            else: raise RuntimeError("不支持的文件类型")
        return out

    def _write_failure_log(self):
        """Write only on failure; keep errors isolated from the compression tasks."""
        if not self.failures:
            return
        lines=["MaxCompress 压缩失败排查日志", "="*65,
               "批次时间："+datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "结果目录："+str(self.out_dir),
               "操作系统："+platform.platform(),
               "压缩模式："+str(self.level),
               "视频编码："+self.encoder_mode,
               "并行任务数："+str(self.concurrency),
               "失败数量："+str(len(self.failures)),
               "说明：该文件包含本机文件路径，公开分享前可遮盖个人目录名。", ""]
        for n,(idx,description,detail) in enumerate(self.failures,1):
            lines += [f"[{n}] 任务序号：{idx+1}","失败对象："+description,
                      "错误详情：", detail,"-"*65,""]
        try:
            self.failure_log_path.write_text("\n".join(lines),encoding="utf-8-sig")
        except Exception as exc:
            self.archive_failed.emit(f"无法写入失败日志 {self.failure_log_path}: {exc}")

    def run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            jobs={pool.submit(self.process,i,item):i for i,item in enumerate(self.items)}
            for job in concurrent.futures.as_completed(jobs):
                idx=jobs[job]
                try:
                    out=job.result()
                    self.finished_one.emit(idx,str(out),"输出完成")
                except Exception as exc:
                    self.failures.append((idx,str(self.items[idx].src),
                                          "".join(traceback.format_exception(type(exc),exc,exc.__traceback__))))
                    self.failed_one.emit(idx,str(exc))
        self.pause_gate.wait()
        self._write_failure_log()
        # ZIP output after all compression futures finish. Store beside the batch folder.
        # Already-compressed media are stored without redundant recompression for speed.
        files = sorted(x for x in self.out_dir.rglob("*") if x.is_file())
        if files:
            archive = self.out_dir.with_suffix(".zip")
            try:
                with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
                    for f in files:
                        self.pause_gate.wait()
                        zf.write(f, arcname=str(Path(self.out_dir.name) / f.relative_to(self.out_dir)))
                self.archived.emit(str(archive))
            except Exception as exc:
                try: archive.unlink(missing_ok=True)
                except OSError: pass
                self.failures.append((-2,str(archive),
                                     "".join(traceback.format_exception(type(exc),exc,exc.__traceback__))))
                self._write_failure_log()
                self.archive_failed.emit(str(exc))
        self.all_done.emit()

class ThumbnailSignals(QObject):
    ready = Signal(str, QImage)


class ThumbnailJob(QRunnable):
    """Read image preview / video first frame off the GUI thread."""
    def __init__(self, src: Path, kind: str, signals: ThumbnailSignals):
        super().__init__()
        self.src, self.kind, self.signals = src, kind, signals

    def run(self):
        try:
            if self.kind == "图片":
                reader = QImageReader(str(self.src))
                reader.setAutoTransform(True)
                original_size = reader.size()
                if original_size.isValid():
                    reader.setScaledSize(original_size.scaled(QSize(280, 160), Qt.KeepAspectRatio))
                img = reader.read()
            elif self.kind == "视频":
                ffmpeg = find_ffmpeg()
                if not ffmpeg:
                    return
                args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "00:00:00.5", "-i", str(self.src),
                        "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=240:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
                kwargs = _quiet_subprocess_options()
                data = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      timeout=15, check=False, **kwargs)
                if data.returncode != 0 or not data.stdout:
                    # A very short video may not have a frame at 0.5 seconds.
                    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(self.src),
                            "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=240:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
                    data = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          timeout=15, check=False, **kwargs)
                img = QImage.fromData(data.stdout, "JPG") if data.returncode == 0 else QImage()
            else:
                return
            if not img.isNull():
                self.signals.ready.emit(str(self.src.resolve()), img)
        except Exception:
            # A missing preview must not stop the real compression job.
            pass


class TaskProgress(QWidget):
    """Render a graphical progress bar and a separate DPI-safe percent label."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("taskProgressContainer")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 8, 7, 8)
        layout.setSpacing(9)
        self.bar = QProgressBar(self)
        self.bar.setObjectName("taskProgress")
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setMinimumHeight(14)
        self.bar.setMaximumHeight(18)
        self.bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.percent = QLabel("0%", self)
        self.percent.setObjectName("taskPercent")
        self.percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.percent.setMinimumWidth(46)
        self.percent.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.percent)
        self.setMinimumHeight(42)

    def setValue(self, value):
        v = max(0, min(100, int(value)))
        self.bar.setValue(v)
        self.percent.setText(f"{v}%")

class DropTable(QTableWidget):
    filesDropped = Signal(list)
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()
    def dropEvent(self,e):
        paths=[]
        for u in e.mimeData().urls():
            p=Path(u.toLocalFile())
            if p.is_dir():
                paths += [x for x in p.rglob("*") if x.is_file()]
            else: paths.append(p)
        self.filesDropped.emit(paths)
        e.acceptProposedAction()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MaxCompress - 全能压缩 v{VERSION}")
        self.resize(1320,790)
        self.items=[]
        self.thumb_pool=QThreadPool(self)
        self.thumb_pool.setMaxThreadCount(2)
        self.thumb_signals=ThumbnailSignals()
        self.thumb_signals.ready.connect(self._on_thumbnail_ready)
        self.active_kind="图片"
        self.worker=None
        self.last_batch_dir=None
        self.last_zip=None
        self.zip_error=None
        self._ui()
        icon=asset_path("app.ico")
        if icon.exists(): self.setWindowIcon(QIcon(str(icon)))
        self.checker = None
        self.update_dialog = None
        self._startup_update_check_scheduled = False
        self._startup_update_announcement_shown = False

    def showEvent(self, event):
        super().showEvent(event)
        # Only start the automatic request after the main window is visible.
        if not self._startup_update_check_scheduled:
            self._startup_update_check_scheduled = True
            QTimer.singleShot(1800, lambda: self.check_updates(manual=False))

    def _ui(self):
        root=QWidget(); self.setCentralWidget(root)
        outer=QHBoxLayout(root); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)

        side=QFrame(); side.setObjectName("side"); side.setFixedWidth(230)
        sv=QVBoxLayout(side); sv.setContentsMargins(18,24,18,20); sv.setSpacing(8)
        brandrow=QHBoxLayout()
        brandicon=QLabel(); brandicon.setFixedSize(36,36)
        if asset_path("app.png").exists(): brandicon.setPixmap(QPixmap(str(asset_path("app.png"))).scaled(36,36,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        brandrow.addWidget(brandicon)
        title=QLabel("MaxCompress"); title.setObjectName("brand"); brandrow.addWidget(title); brandrow.addStretch()
        sv.addLayout(brandrow)
        sv.addSpacing(18)
        self.group=QButtonGroup(self)
        for kind,filename in [("图片","image.png"),("视频","video.png"),("PDF","pdf.png"),("Word","word.png"),("PPT","ppt.png")]:
            b=QPushButton(f"  {kind}压缩"); b.setCheckable(True); b.setObjectName("nav")
            if asset_path(filename).exists(): b.setIcon(QIcon(str(asset_path(filename)))); b.setIconSize(QSize(27,27))
            if kind=="图片": b.setChecked(True)
            b.clicked.connect(lambda checked,k=kind:self.switch_kind(k))
            self.group.addButton(b); sv.addWidget(b)
        sv.addWidget(QLabel("文件压缩 / 文件解压缩"))
        sv.addStretch()
        sv.addWidget(QLabel("支持拖拽文件/文件夹\n极限模式优先减小体积"))

        main=QWidget(); mv=QVBoxLayout(main); mv.setContentsMargins(28,22,28,22); mv.setSpacing(14)

        top=QHBoxLayout()
        self.page_title=QLabel("图片压缩"); self.page_title.setObjectName("pageTitle"); top.addWidget(self.page_title)
        top.addStretch()
        self.update_btn=QPushButton("↻ 检查更新"); self.update_btn.clicked.connect(lambda: self.check_updates(manual=True)); top.addWidget(self.update_btn)
        clear=QPushButton("清空列表"); clear.clicked.connect(self.clear_items); top.addWidget(clear)
        mv.addLayout(top)

        bar=QHBoxLayout()
        addf=QPushButton("＋ 添加文件"); addf.setObjectName("primarySmall"); addf.clicked.connect(self.add_files)
        addd=QPushButton("▣ 添加文件夹"); addd.clicked.connect(self.add_folder)
        bar.addWidget(addf); bar.addWidget(addd); bar.addStretch()
        mv.addLayout(bar)

        self.table=DropTable(); self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["编号","文件预览 / 文件名称","原文件大小","压缩后大小","压缩率","状态","进度","操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(92)
        self.table.verticalHeader().setMinimumSectionSize(88)
        self.table.setIconSize(QSize(100, 60))
        self.table.setAlternatingRowColors(True)
        self.table.setMouseTracking(True)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.filesDropped.connect(self.add_paths)
        h=self.table.horizontalHeader()
        h.setMinimumSectionSize(65)
        for c in range(8):
            h.setSectionResizeMode(c, QHeaderView.Interactive)
        for c,width in enumerate((75,460,135,145,100,180,230,112)):
            self.table.setColumnWidth(c,width)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.horizontalScrollBar().setSingleStep(45)
        self.table.verticalScrollBar().setSingleStep(35)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setToolTip("可通过列表底部横向滚动条查看所有列，或拖动表头调整宽度")
        self.content_stack=QStackedWidget()
        empty=QFrame(); empty.setObjectName("emptyPanel")
        el=QVBoxLayout(empty); el.setAlignment(Qt.AlignCenter); el.setSpacing(18)
        symbol=QLabel(); self.empty_symbol=symbol; symbol.setObjectName("emptySymbol"); symbol.setAlignment(Qt.AlignCenter)
        symbol.setFixedHeight(235); self.set_empty_image("图片")
        self.empty_title=QLabel("图片压缩"); self.empty_title.setObjectName("emptyTitle"); self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_desc=QLabel("点击添加文件或拖拽文件到窗口中\n支持批量压缩，轻度、标准及极限压缩模式")
        self.empty_desc.setObjectName("emptyDescription"); self.empty_desc.setAlignment(Qt.AlignCenter)
        empty_add=QPushButton("＋  添加文件"); empty_add.setObjectName("primaryLarge"); empty_add.setFixedWidth(210); empty_add.clicked.connect(self.add_files)
        el.addWidget(symbol); el.addWidget(self.empty_title); el.addWidget(self.empty_desc)
        el.addWidget(empty_add,alignment=Qt.AlignHCenter)
        self.content_stack.addWidget(empty)
        self.content_stack.addWidget(self.table)
        self.content_stack.setCurrentIndex(0)
        mv.addWidget(self.content_stack,1)

        bottom=QFrame(); bottom.setObjectName("panel")
        bv=QVBoxLayout(bottom); bv.setContentsMargins(18,14,18,14)
        line1=QHBoxLayout(); line1.setSpacing(14)
        line1.addWidget(QLabel("压缩模式："))
        self.level=QComboBox(); self.level.addItems(["轻度压缩（高清）","标准压缩（推荐）","极限压缩（最小体积）"]); self.level.setCurrentIndex(2); self.level.setObjectName("elegantCombo"); self.level.setMinimumWidth(255); self.level.setMinimumHeight(42)
        line1.addWidget(self.level)
        line1.addSpacing(8)
        line1.addWidget(QLabel("输出格式："))
        self.output_format=QComboBox(); self.output_format.setObjectName("elegantCombo")
        self.output_format.setMinimumWidth(155); self.output_format.setMinimumHeight(42)
        line1.addWidget(self.output_format)
        self.refresh_output_formats()
        line1.addSpacing(8)
        line1.addWidget(QLabel("同时任务："))
        self.parallel=QComboBox(); self.parallel.addItems(["2 个", "3 个", "4 个", "6 个"])
        self.parallel.setCurrentIndex(1); self.parallel.setObjectName("elegantCombo"); self.parallel.setMinimumWidth(105); self.parallel.setMinimumHeight(42); self.parallel.setToolTip("视频推荐 2~3 个，太多会争抢 CPU")
        line1.addWidget(self.parallel)
        # Automatically choose an available GPU encoder; users need no GPU knowledge.
        line1.addStretch()
        self.auto_acceleration=QLabel("⚡ 智能加速 · 等待视频任务")
        self.auto_acceleration.setObjectName("accelerationHint")
        self.auto_acceleration.setToolTip("自动检测可用的 NVIDIA、Intel 或 AMD 编码器，并在不可用时回退到 CPU。")
        line1.addWidget(self.auto_acceleration)
        bv.addLayout(line1)

        # Give the output path its own full row, independent of other options.
        outrow=QHBoxLayout()
        outrow.setSpacing(12)
        output_label=QLabel("输出目录：")
        output_label.setObjectName("outputLabel")
        output_label.setMinimumWidth(78)
        outrow.addWidget(output_label)
        self.output=QLineEdit(str(Path.home()/"Desktop"/"MaxCompress_Output"))
        self.output.setObjectName("outputPath")
        self.output.setMinimumWidth(240)
        self.output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.output.setToolTip(self.output.text())
        self.output.textChanged.connect(self.output.setToolTip)
        outrow.addWidget(self.output,1)
        choose=QPushButton("▢ 选择目录"); choose.setObjectName("secondaryAction"); choose.clicked.connect(self.choose_output)
        choose.setMinimumWidth(96)
        outrow.addWidget(choose)
        self.open_output_btn=QPushButton("📂 打开输出目录"); self.open_output_btn.setObjectName("secondaryAction")
        self.open_output_btn.setMinimumWidth(145)
        self.open_output_btn.setToolTip("在文件资源管理器中打开当前输出目录")
        self.open_output_btn.clicked.connect(self.open_output_directory)
        outrow.addWidget(self.open_output_btn)
        bv.addLayout(outrow)

        line2=QHBoxLayout()
        self.keep=QCheckBox("保留原文件"); self.keep.setChecked(True)
        line2.addWidget(self.keep)
        self.folder_info=QLabel("每批自动生成结果文件夹 + ZIP 压缩包")
        line2.addWidget(self.folder_info)
        self.copy_output_btn=QPushButton("⧉ 复制输出路径")
        self.copy_output_btn.setObjectName("secondaryAction")
        self.copy_output_btn.setToolTip("将当前输出目录复制到剪贴板（稳定安装版）")
        self.copy_output_btn.clicked.connect(self.copy_output_path)
        line2.addStretch()
        line2.addWidget(self.copy_output_btn)
        self.start=QPushButton("▶  开始压缩"); self.start.setObjectName("primary"); self.start.setFixedSize(180,48); self.start.clicked.connect(self.start_work)
        line2.addWidget(self.start)
        self.pause_button=QPushButton("Ⅱ 暂停压缩")
        self.pause_button.setObjectName("secondaryAction")
        self.pause_button.setMinimumHeight(48)
        self.pause_button.setMinimumWidth(130)
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip("协作式暂停：视频在进度回调点响应，PDF 和图片会在安全处理点响应")
        self.pause_button.clicked.connect(self.toggle_pause)
        line2.addWidget(self.pause_button)
        bv.addLayout(line2)
        mv.addWidget(bottom)

        outer.addWidget(side); outer.addWidget(main,1)
        self.setStyleSheet("""
            QWidget{font-size:14px;color:#263753;background:#f7f9fc;}
            #side{background:#f5f7fa;border-right:1px solid #e8edf5;}
            #brand{font-size:21px;font-weight:700;color:#193a73;}
            QPushButton{background:white;border:1px solid #dbe6f1;border-radius:8px;padding:9px 16px;}
            QPushButton:hover{border-color:#60a5fa;background:#eff6ff;}
            QPushButton#nav{text-align:left;border:none;background:transparent;padding:12px 14px;}
            QPushButton#nav:checked{background:#eaf3ff;color:#2563eb;font-weight:700;}
            #pageTitle{font-size:22px;font-weight:700;color:#0f172a;}
            #primary,#primarySmall,#primaryLarge{background:#096bdd;color:white;border:none;font-weight:700;padding:12px 20px;}
            #primary:hover,#primarySmall:hover,#primaryLarge:hover{background:#005cc4;}
            QTableWidget{background:white;alternate-background-color:#f8fbff;border:1px solid #e4ecf5;border-radius:10px;gridline-color:#eef3f8;}
            QHeaderView::section{background:#f4f7fc;border:none;border-bottom:1px solid #e7edf4;padding:12px;color:#64748b;font-weight:600;}
            #panel{background:white;border:1px solid #e4ecf5;border-radius:12px;}
            #outputLabel{font-weight:700;color:#243b5b;font-size:14px;background:transparent;}
            #outputPath{font-size:14px;color:#182b48;background:#ffffff;selection-background-color:#cce5ff;padding:10px 12px;min-height:22px;}
            #accelerationHint{color:#50749c;font-size:12px;background:transparent;}
            #emptyPanel{background:white;border:1px solid #e8edf4;border-radius:12px;}
            #emptySymbol{font-size:120px;color:#159cf1;}
            #emptyTitle{font-size:32px;color:#101a30;font-weight:700;}
            #emptyDescription{font-size:16px;line-height:1.8;color:#65758b;}
            QComboBox,QLineEdit{background:white;border:1px solid #d9e3ee;border-radius:7px;padding:8px;}
            QProgressBar{border:none;background:#edf2f7;border-radius:5px;height:10px;text-align:center;}
            QProgressBar::chunk{background:#60a5fa;border-radius:5px;}

            /* v2.6.6: calmer controls, legible selection, compact row actions */
            QComboBox#elegantCombo{background:#fbfdff;color:#18385e;border:1px solid #cbdcf2;border-radius:11px;padding:9px 33px 9px 13px;font-weight:600;min-height:24px;}
            QComboBox#elegantCombo:hover{background:#f2f8ff;border:1px solid #7aafee;}
            QComboBox#elegantCombo:focus{border:2px solid #3185e8;}
            QComboBox#elegantCombo::drop-down{border:none;width:30px;}
            QComboBox#elegantCombo QAbstractItemView{background:white;color:#18385e;border:1px solid #cbdcf2;selection-background-color:#e2f0ff;selection-color:#114f9a;outline:none;padding:4px;}
            QPushButton#secondaryAction{min-height:26px;background:#fff;border:1px solid #cddff3;border-radius:10px;color:#215389;font-weight:600;}
            QPushButton#secondaryAction:hover{background:#edf6ff;border:1px solid #77b3f5;}
            QPushButton#dangerAction{color:#af3c4b;background:#fff;border:1px solid #dae5f2;border-radius:9px;padding:8px 10px;}
            QPushButton#dangerAction:hover{color:#c52037;background:#fff0f2;border:1px solid #f1aab5;}
            QTableWidget::item{padding:7px 6px;border-bottom:1px solid #edf3fb;}
            QTableWidget::item:selected{background:#e7f2ff;color:#183c67;}
            QTableWidget::item:hover{background:#f1f7ff;}
            QProgressBar#taskProgress{background:#edf2fa;border:none;border-radius:7px;min-height:14px;max-height:18px;}
            QLabel#taskPercent{font-size:13px;font-weight:700;color:#244b76;background:transparent;min-width:46px;}
            QWidget#taskProgressContainer{background:transparent;}
            QScrollBar:vertical{background:#eaf1fa;width:15px;margin:0;border-radius:7px;}
            QScrollBar::handle:vertical{background:#91b9e9;min-height:48px;border-radius:7px;}
            QScrollBar::handle:vertical:hover{background:#488bdf;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;border:none;}
            QScrollBar:horizontal{background:#eaf1fa;height:14px;border-radius:7px;}
            QScrollBar::handle:horizontal{background:#91b9e9;min-width:50px;border-radius:7px;}
            QScrollBar::handle:horizontal:hover{background:#488bdf;}
            QProgressBar#taskProgress::chunk{background:#4295ed;border-radius:7px;}
            QLineEdit#outputPath{border:1px solid #cbdcf2;border-radius:10px;background:#fbfdff;min-height:27px;}
            QLineEdit#outputPath:focus{border:2px solid #3185e8;}
        """)

    def _show_update_center(self):
        if self.update_dialog is None:
            self.update_dialog=UpdateDialog(None,self)
        self.update_dialog.show()
        self.update_dialog.raise_()
        self.update_dialog.activateWindow()
        return self.update_dialog

    def check_updates(self, manual=False):
        dialog=self._show_update_center() if manual else None
        if self.checker and self.checker.isRunning():
            return
        if dialog:
            dialog.set_checking()
        self.update_btn.setEnabled(False)
        self.checker=CheckWorker(manual=manual)
        self.checker.result.connect(lambda info: self._update_result(info,manual))
        self.checker.failed.connect(lambda err: self._update_error(err,manual))
        self.checker.finished.connect(lambda: self.update_btn.setEnabled(True))
        self.checker.start()

    def _update_error(self, error, manual):
        if self.update_dialog and self.update_dialog.isVisible():
            self.update_dialog.set_check_error(error)
        # 自动检查失败时静默处理，不影响压缩。

    def _update_result(self, release, manual):
        if not manual:
            # On startup, announce only valid newer official releases, once per launch.
            if (not release or not release.get('is_newer', False)
                    or self._startup_update_announcement_shown):
                return
            self._startup_update_announcement_shown = True
        dialog = self._show_update_center()
        dialog.set_release(release)
        if not manual:
            dialog.setWindowTitle(f"发现新版本 · MaxCompress v{release['version']}")

    def set_empty_image(self, kind):
        names={"图片":"empty_image.png","视频":"empty_video.png","PDF":"empty_pdf.png","Word":"empty_word.png","PPT":"empty_ppt.png"}
        path=asset_path(names.get(kind,"empty_image.png"))
        if path.exists(): self.empty_symbol.setPixmap(QPixmap(str(path)).scaled(290,220,Qt.KeepAspectRatio,Qt.SmoothTransformation))

    def refresh_output_formats(self):
        choices={"图片":["保持原格式","JPEG","PNG","WEBP"],
                 "视频":["保持原格式","MP4","MKV","MOV","WEBM"],
                 "PDF":["保持原格式"], "Word":["保持原格式"], "PPT":["保持原格式"]}
        if hasattr(self,"output_format"):
            self.output_format.clear()
            self.output_format.addItems(choices.get(self.active_kind,["保持原格式"]))
            self.output_format.setToolTip("保持原格式：输出与源文件相同的扩展名；仅当前分类应用指定格式")

    def switch_kind(self, kind):
        self.active_kind=kind;
        self.refresh_output_formats(); self.page_title.setText(kind+"压缩"); self.empty_title.setText(kind+"压缩"); self.set_empty_image(kind)

    def add_files(self):
        filters={
            "图片":"图片 (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)",
            "视频":"视频 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v)",
            "PDF":"PDF (*.pdf)","Word":"Word (*.docx)","PPT":"PPT (*.pptx)"
        }
        fs,_=QFileDialog.getOpenFileNames(self,"选择文件","",filters[self.active_kind])
        self.add_paths([Path(x) for x in fs])

    def add_folder(self):
        d=QFileDialog.getExistingDirectory(self,"选择文件夹")
        if d: self.add_paths([x for x in Path(d).rglob("*") if x.is_file()])

    def add_paths(self, paths):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self,"提示","当前批次正在压缩，请完成后再添加新文件。")
            return
        seen={str(i.src.resolve()) for i in self.items}
        for p in paths:
            p=Path(p)
            k=category_for(p)
            if not k or str(p.resolve()) in seen: continue
            item=Item(p,k); self.items.append(item); seen.add(str(p.resolve()))
            r=self.table.rowCount(); self.table.insertRow(r)
            vals=[str(r+1),p.name,human_size(p.stat().st_size),"—","—","等待压缩","","删除"]
            for c,v in enumerate(vals):
                if c==6:
                    prog=TaskProgress(self.table); self.table.setCellWidget(r,c,prog)
                elif c==7:
                    b=QPushButton("🗑 删除"); b.setObjectName("dangerAction"); b.clicked.connect(lambda _=False,row=r:self.remove_row(row)); self.table.setCellWidget(r,c,b)
                else:
                    cell=QTableWidgetItem(v)
                    if c == 0: cell.setTextAlignment(Qt.AlignCenter)
                    if c == 5: cell.setForeground(QColor("#3574b9"))
                    if c==1:
                        cell.setToolTip(str(p))
                        default_asset={"图片":"image.png","视频":"video.png","PDF":"pdf.png","Word":"word.png","PPT":"ppt.png"}.get(k,"app.png")
                        icon_file=asset_path(default_asset)
                        if icon_file.exists(): cell.setIcon(QIcon(str(icon_file)))
                    self.table.setItem(r,c,cell)
            self.table.setRowHeight(r,92)
            if k in ("图片","视频"):
                self.thumb_pool.start(ThumbnailJob(p,k,self.thumb_signals))
        self.refresh_empty_state()

    def _on_thumbnail_ready(self, resolved_path: str, image: QImage):
        if image.isNull(): return
        pix=QPixmap.fromImage(image).scaled(QSize(100, 60), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        for row,item in enumerate(self.items):
            if str(item.src.resolve()) == resolved_path:
                cell=self.table.item(row,1)
                if cell: cell.setIcon(QIcon(pix))
                break

    def refresh_empty_state(self):
        self.content_stack.setCurrentIndex(1 if self.items else 0)

    def remove_row(self,row):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self,"提示","压缩过程中不能删除任务。")
            return
        if 0<=row<len(self.items):
            dialog=QMessageBox(self)
            dialog.setWindowTitle("确认删除")
            dialog.setIcon(QMessageBox.Question)
            dialog.setText(f"确定从任务列表中删除「{self.items[row].src.name}」吗？")
            dialog.setInformativeText("此操作不会删除电脑上的原文件。")
            yes=dialog.addButton("是", QMessageBox.YesRole)
            no=dialog.addButton("否", QMessageBox.NoRole)
            dialog.setDefaultButton(no)
            dialog.exec()
            if dialog.clickedButton() is not yes:return
            self.items.pop(row); self.table.removeRow(row)
            self.rebuild_rows(); self.refresh_empty_state()

    def rebuild_rows(self):
        for r in range(self.table.rowCount()):
            self.table.item(r,0).setText(str(r+1))

    def clear_items(self):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self,"提示","请等待当前压缩任务完成。")
            return
        self.items.clear(); self.table.setRowCount(0); self.refresh_empty_state()

    def open_output_directory(self):
        folder = self.output.text().strip().strip('"')
        if not folder:
            QMessageBox.warning(self,"提示","请先选择输出目录。")
            return
        target=self.last_batch_dir if self.last_batch_dir and self.last_batch_dir.exists() else Path(folder).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target)) if sys.platform == "win32" else __import__("subprocess").Popen(["xdg-open", str(target)])
        except Exception as exc:
            QMessageBox.warning(self,"打开失败",f"无法打开输出目录：\n{exc}")

    def copy_output_path(self):
        """Copy the output directory without opening it or modifying files."""
        folder = self.output.text().strip().strip('"')
        if not folder:
            QMessageBox.information(self, "提示", "请先选择输出目录。")
            return
        QApplication.clipboard().setText(folder)
        QMessageBox.information(self, "复制成功", "输出目录已复制到剪贴板。\n" + folder)

    def choose_output(self):
        d=QFileDialog.getExistingDirectory(self,"选择输出目录")
        if d: self.output.setText(d)

    def start_work(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.items:
            QMessageBox.information(self,"提示","请先添加文件。"); return
        out=self.output.text().strip()
        if not out: QMessageBox.warning(self,"提示","请选择输出目录。"); return
        root=Path(out).expanduser()
        root.mkdir(parents=True,exist_ok=True)
        now=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        batch=root / ("压缩结果_" + now)
        number=1
        while batch.exists():
            batch=root / ("压缩结果_" + now + f"_{number}")
            number+=1
        batch.mkdir(parents=True,exist_ok=False)
        self.last_batch_dir=batch
        self.last_zip=None
        self.zip_error=None
        self.start.setEnabled(False); self.start.setText("压缩中…")
        self.pause_button.setEnabled(True); self.pause_button.setText("Ⅱ 暂停压缩")
        for r in range(self.table.rowCount()):
            self.table.item(r,5).setText("等待处理")
            bar=self.table.cellWidget(r,6)
            if bar:bar.setValue(0)
        self.parallel.setEnabled(False)
        self.worker=Worker(self.items,batch,self.level.currentIndex(),int(self.parallel.currentText().split()[0]),"自动（GPU优先）", self.active_kind, self.output_format.currentText())
        self.worker.started_one.connect(self.on_started_one)
        self.worker.progress.connect(self.on_progress)
        self.worker.stage.connect(self.on_stage)
        self.worker.encoder_changed.connect(self.on_encoder_changed)
        self.worker.finished_one.connect(self.on_done_one)
        self.worker.failed_one.connect(self.on_fail_one)
        self.worker.archived.connect(self.on_archived)
        self.worker.archive_failed.connect(self.on_archive_failed)
        self.worker.all_done.connect(self.on_all_done)
        self.auto_acceleration.setText("⚡ 智能加速 · 检测可用编码器…")
        self.worker.start()

    def toggle_pause(self):
        if self.worker is None or not self.worker.isRunning():
            return
        should_pause=self.worker.pause_gate.is_set()
        self.worker.set_paused(should_pause)
        if should_pause:
            self.pause_button.setText("▶ 继续压缩")
            self.start.setText("已请求暂停")
            self.auto_acceleration.setText("Ⅱ 暂停中 · 等待安全节点")
        else:
            self.pause_button.setText("Ⅱ 暂停压缩")
            self.start.setText("压缩中…")
            self.auto_acceleration.setText("⚡ 智能加速 · 已继续压缩")

    def on_encoder_changed(self,row,encoder):
        labels={"h264_nvenc":"NVIDIA 显卡 NVENC","h264_qsv":"Intel 显卡 Quick Sync",
                "h264_amf":"AMD 显卡 AMF","libx264":"CPU 软件编码"}
        label=labels.get(encoder,encoder)
        self.auto_acceleration.setText("⚡ 当前加速："+label)
        self.auto_acceleration.setToolTip(f"任务 {row+1}：{label}；多任务并行时展示最近更新的任务编码方式。")
        cell=self.table.item(row,5)
        if cell:cell.setText("压缩中 · "+label)

    def on_started_one(self,row):
        item=self.table.item(row,5)
        if item:
            item.setText("⟳ 压缩中"); item.setForeground(QColor("#1769d4"))

    def on_stage(self,row,stage):
        cell=self.table.item(row,5)
        if cell:
            cell.setText(stage)
            cell.setToolTip(stage)

    def on_progress(self,row,val):
        w=self.table.cellWidget(row,6)
        if w: w.setValue(val)

    def on_done_one(self,row,out,msg):
        p=Path(out); self.items[row].out=p
        src=self.items[row].src.stat().st_size
        dst=p.stat().st_size if p.exists() else 0
        self.table.item(row,3).setText(human_size(dst))
        rate=(1-dst/src)*100 if src else 0
        self.table.item(row,4).setText(f"{rate:.1f}%")
        self.table.item(row,5).setText("✓ 输出完成"); self.table.item(row,5).setForeground(QColor("#16835e"))
        w=self.table.cellWidget(row,6)
        if w: w.setValue(100)

    def on_fail_one(self,row,err):
        self.table.item(row,5).setText("✕ 压缩失败"); self.table.item(row,5).setForeground(QColor("#dc4754"))
        self.table.item(row,5).setToolTip(err)

    def on_archived(self, path):
        self.last_zip=Path(path)

    def on_archive_failed(self, error):
        self.zip_error=error

    def on_all_done(self):
        self.start.setEnabled(True); self.start.setText("开始压缩")
        self.pause_button.setEnabled(False); self.pause_button.setText("Ⅱ 暂停压缩")
        self.parallel.setEnabled(True)
        self.auto_acceleration.setText("⚡ 智能加速 · 等待视频任务")
        success=sum(1 for item in self.items if item.out and item.out.exists() and item.out.parent==self.last_batch_dir)
        failed=len(self.items)-success
        msg=f"本批压缩已结束。\n成功 {success} 个，失败 {failed} 个。\n结果文件夹：{self.last_batch_dir}"
        if self.last_zip: msg+=f"\nZIP 文件：{self.last_zip}"
        if failed or self.zip_error: msg+=f"\n失败日志：{self.last_batch_dir / '压缩失败日志.txt'}"
        if self.zip_error: msg+=f"\nZIP 打包失败：{self.zip_error}\n结果文件夹仍已保留。"
        msg+="\n\n立即打开结果所在目录吗？"
        answer=QMessageBox.question(self,"压缩完成",msg,QMessageBox.Yes|QMessageBox.No,QMessageBox.Yes)
        if answer==QMessageBox.Yes:
            try:
                folder=self.last_batch_dir.parent
                os.startfile(str(folder)) if sys.platform == "win32" else __import__("subprocess").Popen(["xdg-open",str(folder)])
            except Exception as exc: QMessageBox.warning(self,"打开失败",str(exc))

if __name__=="__main__":
    # Qt 6 handles per-monitor high DPI automatically. Set rounding before QApplication.
    if sys.platform == "win32":
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    try:
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except (AttributeError, RuntimeError):
        pass
    app=QApplication(sys.argv)
    preferred = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI") if sys.platform == "win32" else ("Noto Sans CJK SC", "Segoe UI")
    from PySide6.QtGui import QFontDatabase
    families = set(QFontDatabase.families())
    family = next((name for name in preferred if name in families), app.font().family())
    app.setFont(QFont(family, 10))
    app.setApplicationName("MaxCompress")
    w=MainWindow(); w.show()
    sys.exit(app.exec())
