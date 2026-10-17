
from __future__ import annotations
from pathlib import Path
import os, io, zipfile, shutil, subprocess, tempfile, sys, re, time
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
        im.save(out, "JPEG", quality=p["quality"], optimize=False, progressive=False, subsampling="4:2:0")
    if progress: progress(100)
    return out

def find_ffmpeg()->str|None:
    for base in [Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)), Path(sys.executable).resolve().parent, Path(__file__).resolve().parent]:
        local = base / "ffmpeg.exe"
        if local.exists(): return str(local)
    return shutil.which("ffmpeg")

def _quiet_subprocess_options():
    if sys.platform != "win32":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": info}


def _video_duration_seconds(ffmpeg: str, src: Path) -> float:
    """Obtain duration using installed FFmpeg (no separately bundled ffprobe required)."""
    p = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src)],
                       capture_output=True, encoding="utf-8", errors="replace",
                       timeout=30, **_quiet_subprocess_options())
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
    if not m:
        return 0.0
    return int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))


_ENCODER_CACHE = {}
_ENCODER_LOCK = __import__("threading").Lock()


def probe_hw_encoders(ffmpeg: str) -> dict:
    """Attempt a real one-frame encode (listing encoders alone is insufficient)."""
    with _ENCODER_LOCK:
        if ffmpeg in _ENCODER_CACHE:
            return dict(_ENCODER_CACHE[ffmpeg])
        available = {}
        for label, encoder in (("NVIDIA NVENC", "h264_nvenc"),
                               ("Intel Quick Sync", "h264_qsv"),
                               ("AMD AMF", "h264_amf")):
            args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "lavfi", "-i", "color=c=black:s=128x128:r=25",
                    "-frames:v", "1", "-an", "-c:v", encoder, "-f", "null", "-"]
            try:
                result = subprocess.run(args, capture_output=True, timeout=12,
                                        **_quiet_subprocess_options())
                if result.returncode == 0:
                    available[label] = encoder
            except (OSError, subprocess.TimeoutExpired):
                pass
        _ENCODER_CACHE[ffmpeg] = dict(available)
        return available


def available_video_encoders() -> dict:
    ffmpeg=find_ffmpeg()
    return probe_hw_encoders(ffmpeg) if ffmpeg else {}


def _run_video_encoder(ffmpeg, src, out, level, progress, concurrency, encoder):
    duration=0.0
    try: duration=_video_duration_seconds(ffmpeg,src)
    except (OSError,subprocess.TimeoutExpired): pass
    scale = None if level==0 else (r"scale=trunc(min(iw\,1920)/2)*2:-2" if level==1 else r"scale=trunc(min(iw\,1280)/2)*2:-2")
    # FFmpeg uses different quality scales for CPU and GPU encoders.
    cmd=[ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-nostats",
         "-progress","pipe:1","-y","-i",str(src),"-map_metadata","-1", "-c:v",encoder]
    if encoder == "libx264":
        threads=max(1,min(8,(os.cpu_count() or 4)//max(1,concurrency)))
        cmd += ["-preset", "superfast" if level==0 else "veryfast", "-threads",str(threads),
                "-crf",str((25,30,35)[level])]
    elif encoder == "h264_nvenc":
        cmd += ["-preset","p1" if level==0 else "p2", "-rc","vbr",
                "-cq",str((25,30,35)[level]), "-b:v","0"]
    elif encoder == "h264_qsv":
        cmd += ["-preset","veryfast", "-global_quality",str((25,30,35)[level])]
    elif encoder == "h264_amf":
        cmd += ["-quality","speed", "-rc","cqp", "-qp_i",str((25,30,35)[level]),
                "-qp_p",str((25,30,35)[level])]
    if scale: cmd += ["-vf",scale]
    cmd += ["-c:a","aac","-b:a","96k" if level<2 else "64k",
            "-movflags","+faststart",str(out)]
    errors=[]
    with subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,encoding="utf-8",errors="replace",bufsize=1,
                          **_quiet_subprocess_options()) as proc:
        last=0
        for line in proc.stdout:
            line=line.strip()
            if line.startswith("out_time=") and duration>0:
                try:
                    h,m,sec=line[9:].split(":")
                    pct=max(1,min(99,int((int(h)*3600+int(m)*60+float(sec))/duration*100)))
                    if pct>last:
                        last=pct
                        if progress:progress(pct)
                except (ValueError, OverflowError): pass
        errors.append(proc.stderr.read())
        code=proc.wait()
    if code!=0:
        raise RuntimeError(f"{encoder} 编码失败："+"".join(errors)[-6000:])
    if not out.is_file() or out.stat().st_size==0:
        raise RuntimeError(f"{encoder} 输出为空")


def compress_video(src:Path, out_dir:Path, level:int, progress=None, concurrency:int=2,
                   encoder_mode:str="自动（GPU优先）", status_callback=None)->Path:
    ffmpeg=find_ffmpeg()
    if not ffmpeg: raise RuntimeError("未找到 FFmpeg，请重新打包或放入 ffmpeg.exe。")
    out=ensure_output(src,out_dir,ext=".mp4")
    encoders=available_video_encoders() if encoder_mode!="CPU（兼容）" else {}
    if encoder_mode=="自动（GPU优先）":
        selected=next((encoders[name] for name in ("NVIDIA NVENC","Intel Quick Sync","AMD AMF") if name in encoders),"libx264")
    elif encoder_mode=="CPU（兼容）":
        selected="libx264"
    else:
        selected=encoders.get(encoder_mode,"libx264")
    if progress:progress(1)
    try:
        try:
            if status_callback: status_callback(selected)
            _run_video_encoder(ffmpeg,src,out,level,progress,concurrency,selected)
        except Exception as gpu_error:
            if selected=="libx264": raise
            # GPU drivers, resources, or input formats can fail after a successful probe.
            out.unlink(missing_ok=True)
            if progress:progress(1)
            try:
                if status_callback: status_callback("libx264")
                _run_video_encoder(ffmpeg,src,out,level,progress,concurrency,"libx264")
            except Exception as cpu_error:
                raise RuntimeError(f"GPU 出错: {gpu_error}; CPU 回退也失败: {cpu_error}") from cpu_error
        if progress:progress(100)
        return out
    except BaseException:
        out.unlink(missing_ok=True)
        raise

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
    with zipfile.ZipFile(src,"r") as zin, zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=3) as zout:
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
