
from __future__ import annotations
import sys, os, traceback
from pathlib import Path
from dataclasses import dataclass

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QFont, QIcon, QColor
from PySide6.QtWidgets import (
    QApplication,QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QPushButton,QLabel,
    QFileDialog,QTableWidget,QTableWidgetItem,QHeaderView,QProgressBar,QComboBox,
    QCheckBox,QLineEdit,QFrame,QMessageBox,QAbstractItemView,QButtonGroup,QStackedWidget
)
from compressors import *

@dataclass
class Item:
    src: Path
    kind: str
    out: Path|None = None
    status: str = "等待压缩"

class Worker(QThread):
    progress = Signal(int,int)
    finished_one = Signal(int,str,str)
    failed_one = Signal(int,str)
    all_done = Signal()

    def __init__(self, items, out_dir, level):
        super().__init__()
        self.items=items; self.out_dir=Path(out_dir); self.level=level

    def run(self):
        for i,item in enumerate(self.items):
            try:
                cb=lambda v, idx=i: self.progress.emit(idx,v)
                if item.kind=="图片": out=compress_image(item.src,self.out_dir,self.level,cb)
                elif item.kind=="视频": out=compress_video(item.src,self.out_dir,self.level,cb)
                elif item.kind=="PDF": out=compress_pdf(item.src,self.out_dir,self.level,cb)
                elif item.kind=="Word": out=compress_word(item.src,self.out_dir,self.level,cb)
                elif item.kind=="PPT": out=compress_ppt(item.src,self.out_dir,self.level,cb)
                else: raise RuntimeError("不支持的文件类型")
                self.finished_one.emit(i,str(out),"输出完成")
            except Exception as e:
                self.failed_one.emit(i,str(e))
        self.all_done.emit()

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
        self.setWindowTitle("MaxCompress - 全能压缩")
        self.resize(1320,790)
        self.items=[]
        self.active_kind="图片"
        self.worker=None
        self._ui()

    def _ui(self):
        root=QWidget(); self.setCentralWidget(root)
        outer=QHBoxLayout(root); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)

        side=QFrame(); side.setObjectName("side"); side.setFixedWidth(230)
        sv=QVBoxLayout(side); sv.setContentsMargins(18,24,18,20); sv.setSpacing(8)
        title=QLabel("▣  MaxCompress"); title.setObjectName("brand"); sv.addWidget(title)
        sv.addSpacing(18)
        self.group=QButtonGroup(self)
        for kind,emoji in [("图片","🖼"),("视频","🎞"),("PDF","📄"),("Word","📝"),("PPT","📊")]:
            b=QPushButton(f"{emoji}   {kind}压缩"); b.setCheckable(True); b.setObjectName("nav")
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
        clear=QPushButton("清空列表"); clear.clicked.connect(self.clear_items); top.addWidget(clear)
        mv.addLayout(top)

        bar=QHBoxLayout()
        addf=QPushButton("＋ 添加文件"); addf.setObjectName("primarySmall"); addf.clicked.connect(self.add_files)
        addd=QPushButton("▣ 添加文件夹"); addd.clicked.connect(self.add_folder)
        bar.addWidget(addf); bar.addWidget(addd); bar.addStretch()
        mv.addLayout(bar)

        self.table=DropTable(); self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["编号","文件名称","原文件大小","压缩后大小","压缩率","状态","进度","操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.filesDropped.connect(self.add_paths)
        h=self.table.horizontalHeader()
        h.setSectionResizeMode(0,QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1,QHeaderView.Stretch)
        for c in range(2,8): h.setSectionResizeMode(c,QHeaderView.ResizeToContents)
        self.content_stack=QStackedWidget()
        empty=QFrame(); empty.setObjectName("emptyPanel")
        el=QVBoxLayout(empty); el.setAlignment(Qt.AlignCenter); el.setSpacing(18)
        symbol=QLabel("▧"); symbol.setObjectName("emptySymbol"); symbol.setAlignment(Qt.AlignCenter)
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
        line1=QHBoxLayout()
        line1.addWidget(QLabel("压缩模式："))
        self.level=QComboBox(); self.level.addItems(["轻度压缩（高清）","标准压缩（推荐）","极限压缩（最小体积）"]); self.level.setCurrentIndex(2)
        line1.addWidget(self.level)
        line1.addSpacing(25)
        line1.addWidget(QLabel("输出目录："))
        self.output=QLineEdit(str(Path.home()/"Desktop"/"MaxCompress_Output"))
        line1.addWidget(self.output,1)
        choose=QPushButton("选择目录"); choose.clicked.connect(self.choose_output); line1.addWidget(choose)
        self.open_output_btn=QPushButton("📂 打开输出目录")
        self.open_output_btn.setToolTip("在文件资源管理器中打开当前输出目录")
        self.open_output_btn.clicked.connect(self.open_output_directory)
        line1.addWidget(self.open_output_btn)
        bv.addLayout(line1)
        line2=QHBoxLayout()
        self.keep=QCheckBox("保留原文件"); self.keep.setChecked(True)
        line2.addWidget(self.keep); line2.addStretch()
        self.start=QPushButton("开始压缩"); self.start.setObjectName("primary"); self.start.setFixedSize(180,48); self.start.clicked.connect(self.start_work)
        line2.addWidget(self.start)
        bv.addLayout(line2)
        mv.addWidget(bottom)

        outer.addWidget(side); outer.addWidget(main,1)
        self.setStyleSheet("""
            QWidget{font-family:"Microsoft YaHei UI";font-size:14px;color:#263753;background:#f7f9fc;}
            #side{background:#f5f7fa;border-right:1px solid #e8edf5;}
            #brand{font-size:21px;font-weight:700;color:#193a73;}
            QPushButton{background:white;border:1px solid #dbe6f1;border-radius:8px;padding:9px 16px;}
            QPushButton:hover{border-color:#60a5fa;background:#eff6ff;}
            QPushButton#nav{text-align:left;border:none;background:transparent;padding:12px 14px;}
            QPushButton#nav:checked{background:#eaf3ff;color:#2563eb;font-weight:700;}
            #pageTitle{font-size:22px;font-weight:700;color:#0f172a;}
            #primary,#primarySmall,#primaryLarge{background:#096bdd;color:white;border:none;font-weight:700;padding:12px 20px;}
            #primary:hover,#primarySmall:hover,#primaryLarge:hover{background:#005cc4;}
            QTableWidget{background:white;border:1px solid #e4ecf5;border-radius:10px;gridline-color:#eef3f8;}
            QHeaderView::section{background:#f7f9fc;border:none;border-bottom:1px solid #e7edf4;padding:10px;color:#64748b;}
            #panel{background:white;border:1px solid #e4ecf5;border-radius:12px;}
            #emptyPanel{background:white;border:1px solid #e8edf4;border-radius:12px;}
            #emptySymbol{font-size:120px;color:#159cf1;}
            #emptyTitle{font-size:32px;color:#101a30;font-weight:700;}
            #emptyDescription{font-size:16px;line-height:1.8;color:#65758b;}
            QComboBox,QLineEdit{background:white;border:1px solid #d9e3ee;border-radius:7px;padding:8px;}
            QProgressBar{border:none;background:#edf2f7;border-radius:5px;height:10px;text-align:center;}
            QProgressBar::chunk{background:#60a5fa;border-radius:5px;}
        """)

    def switch_kind(self, kind):
        self.active_kind=kind; self.page_title.setText(kind+"压缩"); self.empty_title.setText(kind+"压缩")

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
                    prog=QProgressBar(); prog.setRange(0,100); prog.setValue(0); self.table.setCellWidget(r,c,prog)
                elif c==7:
                    b=QPushButton("删除"); b.clicked.connect(lambda _=False,row=r:self.remove_row(row)); self.table.setCellWidget(r,c,b)
                else: self.table.setItem(r,c,QTableWidgetItem(v))
        self.refresh_empty_state()

    def refresh_empty_state(self):
        self.content_stack.setCurrentIndex(1 if self.items else 0)

    def remove_row(self,row):
        if 0<=row<len(self.items):
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
        target=Path(folder).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target)) if sys.platform == "win32" else __import__("subprocess").Popen(["xdg-open", str(target)])
        except Exception as exc:
            QMessageBox.warning(self,"打开失败",f"无法打开输出目录：\n{exc}")

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
        Path(out).mkdir(parents=True,exist_ok=True)
        self.start.setEnabled(False); self.start.setText("压缩中…")
        for r in range(self.table.rowCount()):
            self.table.item(r,5).setText("压缩中")
        self.worker=Worker(self.items,out,self.level.currentIndex())
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_one.connect(self.on_done_one)
        self.worker.failed_one.connect(self.on_fail_one)
        self.worker.all_done.connect(self.on_all_done)
        self.worker.start()

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
        self.table.item(row,5).setText("输出完成")
        w=self.table.cellWidget(row,6)
        if w: w.setValue(100)

    def on_fail_one(self,row,err):
        self.table.item(row,5).setText("失败")
        self.table.item(row,5).setToolTip(err)

    def on_all_done(self):
        self.start.setEnabled(True); self.start.setText("开始压缩")
        QMessageBox.information(self,"完成","压缩任务已处理完成。\n失败项可将鼠标停在“失败”状态上查看原因。")

if __name__=="__main__":
    app=QApplication(sys.argv)
    app.setApplicationName("MaxCompress")
    w=MainWindow(); w.show()
    sys.exit(app.exec())
