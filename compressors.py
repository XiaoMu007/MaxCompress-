
from __future__ import annotations
from pathlib import Path
import os, io, zipfile, shutil, subprocess, tempfile, sys, sys
from typing import Callable
from PIL import Image

IMAGE_EXTS = {".jpg",".jpeg",".png",".webp",".bmp",".tif",".tiff"}
VIDEO_EXTS = {".mp4",".mov",".mkv",".avi",".webm",".m4v"}
PDF_EXTS = {".pdf"}
WORD_EXTS = {".docx"}
PPT_EXTS = {".pptx"}

def human_size(n:int)->str:
    units=["B","KB","MB","GB"]
    x=float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.0f}{u}" if u=="B" else f"{x:.1f}{u}"
        x/=1024
    return f"{n}B"

def ensure_output(src:Path, out_dir:Path, suffix="_compressed", ext=None)->Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    e = ext or src.suffix
    out = out_dir / f"{src.stem}{suffix}{e}"
    i=1
    while out.exists():
        out = out_dir / f"{src.stem}{suffix}_{i}{e}"
        i+=1
    return out

def _image_params(level:int):
    # level 0/1/2 = light/standard/extreme
    if level == 0:
        return dict(max_side=4000, quality=86)
    if level == 1:
        return dict(max_side=2560, quality=72)
    return dict(max_side=1600, quality=48)

def compress_image(src:Path, out_dir:Path, level:int, progress:Callable[[int],None]|None=None)->Path:
    p = _image_params(level)
    with Image.open(src) as im:
        im.load()
        if progress: progress(20)
        if im.mode not in ("RGB","L"):
            if "A" in im.mode:
                bg = Image.new("RGB", im.size, "white")
                bg.paste(im, mask=im.getchannel("A"))
                im = bg
            else:
                im = im.convert("RGB")
        w,h = im.size
        scale = min(1.0, p["max_side"]/max(w,h))
        if scale < 1:
            im = im.resize((max(1,int(w*scale)), max(1,int(h*scale))), Image.Resampling.LANCZOS)
        if progress: progress(55)
        out_ext = ".jpg"
        out = ensure_output(src, out_dir, ext=out_ext)
        im.save(out, "JPEG", quality=p["quality"], optimize=True, progressive=True, subsampling="4:2:0")
    if progress: progress(100)
    return out

def find_ffmpeg()->str|None:
    for base in [Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)), Path(sys.executable).resolve().parent, Path(__file__).resolve().parent]:
        local = base / "ffmpeg.exe"
        if local.exists(): return str(local)
    return shutil.which("ffmpeg")

def compress_video(src:Path, out_dir:Path, level:int, progress=None)->Path:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg。请安装 FFmpeg，或把 ffmpeg.exe 放到软件目录。")
    out = ensure_output(src, out_dir, ext=".mp4")
    if level == 0:
        crf, preset, scale = "25","medium",None
    elif level == 1:
        crf, preset, scale = "30","slow","trunc(min(iw\\,1920)/2)*2:-2"
    else:
        crf, preset, scale = "35","slow","trunc(min(iw\\,1280)/2)*2:-2"
    cmd=[ffmpeg,"-y","-i",str(src),"-map_metadata","-1","-c:v","libx264","-preset",preset,"-crf",crf]
    if scale:
        cmd += ["-vf",f"scale={scale}"]
    cmd += ["-c:a","aac","-b:a","96k" if level<2 else "64k","-movflags","+faststart",str(out)]
    # Suppress the console window that Windows creates for FFmpeg subprocesses.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    startup = None
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="ignore",
                          creationflags=flags, startupinfo=startup)
    if proc.returncode != 0:
        raise RuntimeError("视频压缩失败：\n" + proc.stderr[-1200:])
    if progress: progress(100)
    return out

def compress_pdf(src:Path, out_dir:Path, level:int, progress=None)->Path:
    import fitz
    out = ensure_output(src, out_dir)
    doc = fitz.open(src)
    new = fitz.open()
    # Light: preserve pages by garbage-cleaning if mostly vector; otherwise rasterize
    if level == 0:
        doc.save(out, garbage=4, deflate=True, clean=True, linear=True)
        if progress: progress(100)
        doc.close()
        return out
    dpi = 130 if level == 1 else 96
    jpg_q = 65 if level == 1 else 45
    total=max(1,len(doc))
    for i,page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        img = Image.frombytes("RGB",[pix.width,pix.height],pix.samples)
        buf=io.BytesIO()
        img.save(buf,"JPEG",quality=jpg_q,optimize=True,progressive=True)
        rect = page.rect
        np = new.new_page(width=rect.width,height=rect.height)
        np.insert_image(np.rect, stream=buf.getvalue())
        if progress: progress(int((i+1)/total*90))
    new.save(out, garbage=4, deflate=True, clean=True, linear=True)
    new.close(); doc.close()
    if progress: progress(100)
    return out

def _compress_office_zip(src:Path, out_dir:Path, level:int, media_prefix:str, progress=None)->Path:
    out = ensure_output(src, out_dir)
    p = _image_params(level)
    with zipfile.ZipFile(src,"r") as zin, zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as zout:
        infos=zin.infolist()
        total=max(1,len(infos))
        for idx,info in enumerate(infos):
            data=zin.read(info.filename)
            low=info.filename.lower()
            if low.startswith(media_prefix) and Path(low).suffix in IMAGE_EXTS:
                try:
                    im=Image.open(io.BytesIO(data)); im.load()
                    if im.mode not in ("RGB","L"):
                        if "A" in im.mode:
                            bg=Image.new("RGB", im.size, "white")
                            bg.paste(im, mask=im.getchannel("A"))
                            im=bg
                        else: im=im.convert("RGB")
                    w,h=im.size
                    scale=min(1.0,p["max_side"]/max(w,h))
                    if scale<1:
                        im=im.resize((max(1,int(w*scale)),max(1,int(h*scale))),Image.Resampling.LANCZOS)
                    buf=io.BytesIO()
                    # Always encode as JPEG only if original was JPEG; changing extension would break relationships.
                    ext=Path(low).suffix
                    if ext in {".jpg",".jpeg"}:
                        im.save(buf,"JPEG",quality=p["quality"],optimize=True,progressive=True)
                        data=buf.getvalue()
                    elif ext==".png":
                        # Quantize PNG aggressively but keep PNG container.
                        if level == 2:
                            imq=im.quantize(colors=128, method=Image.Quantize.FASTOCTREE)
                            imq.save(buf,"PNG",optimize=True)
                        else:
                            im.save(buf,"PNG",optimize=True,compress_level=9)
                        data=buf.getvalue()
                except Exception:
                    pass
            zout.writestr(info, data)
            if progress: progress(int((idx+1)/total*100))
    return out

def compress_word(src:Path, out_dir:Path, level:int, progress=None)->Path:
    return _compress_office_zip(src,out_dir,level,"word/media/",progress)

def compress_ppt(src:Path, out_dir:Path, level:int, progress=None)->Path:
    return _compress_office_zip(src,out_dir,level,"ppt/media/",progress)

def category_for(path:Path)->str|None:
    e=path.suffix.lower()
    if e in IMAGE_EXTS: return "图片"
    if e in VIDEO_EXTS: return "视频"
    if e in PDF_EXTS: return "PDF"
    if e in WORD_EXTS: return "Word"
    if e in PPT_EXTS: return "PPT"
    return None
