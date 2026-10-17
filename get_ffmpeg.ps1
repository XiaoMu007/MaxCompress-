param([switch]$Force)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $root 'ffmpeg.exe'
$log = Join-Path $root 'ffmpeg_download.log'
$cache = Join-Path $root '.ffmpeg_cache'
New-Item -ItemType Directory -Force -Path $cache | Out-Null
function Log([string]$s) { Write-Host $s; Add-Content -LiteralPath $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $s" -Encoding UTF8 }
function ValidExe([string]$p) {
 if (!(Test-Path -LiteralPath $p -PathType Leaf)) { return $false }
 try {
   $pr = Start-Process -FilePath $p -ArgumentList '-hide_banner -version' -PassThru -Wait -NoNewWindow -RedirectStandardOutput (Join-Path $cache 'version.txt') -RedirectStandardError (Join-Path $cache 'version.err')
   if ($pr.ExitCode -ne 0) {return $false}
   return ((Get-Content -LiteralPath (Join-Path $cache 'version.txt') -TotalCount 1) -match '^ffmpeg version')
 } catch { return $false }
}
function FetchResume([string]$url,[string]$part) {
 for ($try=1;$try -le 4;$try++) {
   $already = if (Test-Path -LiteralPath $part) {(Get-Item -LiteralPath $part).Length} else {0L}
   $resp=$null; $stream=$null; $output=$null
   try {
     $req=[System.Net.HttpWebRequest]::Create($url)
     $req.UserAgent='MaxCompressBuilder/2.0'
     $req.Timeout=25000; $req.ReadWriteTimeout=45000
     $req.AllowAutoRedirect=$true; $req.MaximumAutomaticRedirections=8
     if ($already -gt 0) { $req.AddRange($already) }
     $resp=$req.GetResponse()
     $status=[int]$resp.StatusCode
     if ($status -eq 206) {
       $range=$resp.Headers['Content-Range']
       if ($range -notmatch ('^bytes ' + $already + '-\d+/(\d+)$')) {throw 'Server returned an inconsistent Content-Range'}
       $total=[long]$Matches[1]
       $mode=[System.IO.FileMode]::Append
       Log ("Resuming from {0:N1} MB (HTTP 206)" -f ($already/1MB))
     } elseif ($status -eq 200) {
       if ($already -gt 0) {Log 'Mirror does not support range; restarting THIS mirror file.'}
       $already=0L
       $total=if ($resp.ContentLength -ge 0) {[long]$resp.ContentLength} else {-1L}
       $mode=[System.IO.FileMode]::Create
     } else {throw "Unexpected HTTP status $status"}
     if ($total -le 0) {throw 'Unknown Content-Length: cannot safely resume/verify'}
     $stream=$resp.GetResponseStream()
     $output=[System.IO.File]::Open($part,$mode,[System.IO.FileAccess]::Write,[System.IO.FileShare]::None)
     $bytes=New-Object byte[] 262144
     $sw=[Diagnostics.Stopwatch]::StartNew(); $last=[DateTime]::MinValue
     while (($n=$stream.Read($bytes,0,$bytes.Length)) -gt 0) {
       $output.Write($bytes,0,$n)
       if (((Get-Date)-$last).TotalSeconds -ge 1) {
         $current=$already+$output.Position
         $speed=(($current-$already)/1MB)/[Math]::Max(.1,$sw.Elapsed.TotalSeconds)
         Write-Host ("`r    {0:N1}%   {1:N1}/{2:N1} MB   {3:N2} MB/s        " -f (100*$current/$total),($current/1MB),($total/1MB),$speed) -NoNewline
         $last=Get-Date
       }
     }
     Write-Host ''
     $output.Dispose();$output=$null; $stream.Dispose();$stream=$null;$resp.Dispose();$resp=$null
     $actual=(Get-Item -LiteralPath $part).Length
     if ($actual -ne $total) {throw "Incomplete: $actual / $total bytes"}
     if ($actual -lt 5MB) {throw 'Archive unexpectedly small'}
     return
   } catch {
     Log ("Attempt $try/4 error: $($_.Exception.Message)")
     if ($try -eq 4) {throw}
     Start-Sleep -Seconds ([Math]::Min(2*$try,8))
   } finally {
     if ($output) {$output.Dispose()};if ($stream) {$stream.Dispose()};if ($resp) {$resp.Dispose()}
   }
 }
}
function ExtractBinary([string]$zipfile,[string]$dest) {
 # PowerShell 5.1 needs System.IO.Compression.FileSystem for ZipFile.
 Add-Type -AssemblyName System.IO.Compression.FileSystem
 $archive=[System.IO.Compression.ZipFile]::OpenRead($zipfile)
 try {
   $entry=$archive.Entries | Where-Object {$_.FullName -match '(^|/)bin/ffmpeg\.exe$'} | Select-Object -First 1
   if (!$entry) { throw 'Archive lacks bin/ffmpeg.exe' }
   if ($entry.Length -lt 1MB) {throw 'FFmpeg binary too small'}
   $a=$entry.Open();$b=[IO.File]::Create($dest)
   try {$a.CopyTo($b)} finally {$b.Dispose();$a.Dispose()}
 } finally {$archive.Dispose()}
}
try {
 [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
 if (!$Force -and (ValidExe $target)) {Log 'Found valid local ffmpeg.exe. Download skipped.';exit 0}
 # Check ffmpeg on PATH, and copy it alongside the application to embed into the EXE.
 if (!$Force) {
   $cmd=Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
   if ($cmd -and $cmd.Source -and (ValidExe $cmd.Source)) {
     Copy-Item -LiteralPath $cmd.Source -Destination $target -Force
     Log "Reused existing FFmpeg from PATH: $($cmd.Source)";exit 0
   }
 }
 # Separate .part file per URL: never concatenate binary bytes from different mirrors.
 $sources=@(
   @{Name='Gyan essentials (smaller official archive)';Url='https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip';File='gyan_essentials.part'},
   @{Name='GitHub relay 1';Url='https://ghfast.top/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip';File='btbn_relay1.part'},
   @{Name='GitHub relay 2';Url='https://ghproxy.net/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip';File='btbn_relay2.part'},
   @{Name='GitHub upstream';Url='https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip';File='btbn_upstream.part'}
 )
 foreach ($s in $sources) {
   $part=Join-Path $cache $s.File
   Log "Downloading: $($s.Name)"
   try {
     # Previously completed downloads are checked locally before requesting the network.
     # This is important for users whose first attempt finished downloading but
     # extraction failed because the ZipFile assembly was not loaded.
     $useCache=$false
     if (Test-Path -LiteralPath $part -PathType Leaf) {
       try {
         Add-Type -AssemblyName System.IO.Compression.FileSystem
         $check=[System.IO.Compression.ZipFile]::OpenRead($part)
         try {
           $entry=$check.Entries | Where-Object {$_.FullName -match '(^|/)bin/ffmpeg\.exe$'} | Select-Object -First 1
           if ($entry -and $entry.Length -gt 1MB) {$useCache=$true}
         } finally {$check.Dispose()}
       } catch {Log 'Existing cached file incomplete; resuming its download.'}
     }
     if ($useCache) {Log 'Valid completed archive found in cache; skipping download.'}
     else { FetchResume $s.Url $part }
     $candidate=Join-Path $cache 'ffmpeg_candidate.exe'
     ExtractBinary $part $candidate
     if (!(ValidExe $candidate)) {throw 'Downloaded FFmpeg failed execution validation'}
     Move-Item -LiteralPath $candidate -Destination $target -Force
     Log 'FFmpeg successfully downloaded, validated and installed.'
     exit 0
   } catch {Log "Source failed: $($_.Exception.Message). Partial file retained for next run."}
 }
 throw 'All sources failed. Place a valid Windows x64 ffmpeg.exe in this directory to skip downloading.'
} catch {Log "ERROR: $($_.Exception.Message)";exit 1}
