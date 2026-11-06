# 重新渲染 design/assets/arch/src/*.mmd → design/assets/arch/*.png
#
# 依赖：Microsoft Edge（或 Chrome）、Python + Pillow、mermaid.min.js
# 若 %TEMP%\mermaid.min.js 不存在，脚本会从 jsDelivr 下载一次（仅此一处联网）。
#
# 用法：powershell -ExecutionPolicy Bypass -File design\assets\arch\render.ps1

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8

$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcDir = Join-Path $here 'src'
$mermaid = Join-Path $env:TEMP 'mermaid.min.js'

if (-not (Test-Path $mermaid)) {
    Write-Host 'downloading mermaid.min.js ...'
    Invoke-WebRequest -Uri 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js' -OutFile $mermaid -UseBasicParsing
}

$edge = @(
    'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    'C:\Program Files\Google\Chrome\Application\chrome.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw 'no Edge/Chrome found' }

$mermaidUrl = 'file:///' + ($mermaid -replace '\\', '/')
$work = Join-Path $env:TEMP 'arch-render'
New-Item -ItemType Directory -Force -Path $work | Out-Null
$profileDir = Join-Path $work 'profile'

$html = @'
<!doctype html><html><head><meta charset="utf-8">
<style>
  body{margin:0;padding:20px;background:#fff;font-family:"Microsoft YaHei","Segoe UI",sans-serif}
  #out svg{max-width:none !important}
</style>
<script src="__MERMAID__"></script>
</head><body><div id="out"></div>
<script>
  const src = __SRC__;
  mermaid.initialize({startOnLoad:false, theme:'default', securityLevel:'loose',
    fontFamily:'"Microsoft YaHei","Segoe UI",sans-serif',
    flowchart:{htmlLabels:true, curve:'basis', nodeSpacing:46, rankSpacing:62, padding:12},
    sequence:{mirrorActors:false, actorMargin:64, boxMargin:8}});
  mermaid.render('arch', src).then(function(r){
    document.getElementById('out').innerHTML = r.svg;
    const s = document.querySelector('#out svg');
    s.removeAttribute('height');
    s.style.maxWidth = 'none';
  }).catch(function(e){ document.body.innerHTML = '<pre>ERR ' + e + '</pre>'; });
</script></body></html>
'@

$crop = @'
import sys
from PIL import Image, ImageChops
p, out = sys.argv[1], sys.argv[2]
im = Image.open(p).convert("RGB")
bg = Image.new("RGB", im.size, (255, 255, 255))
box = ImageChops.difference(im, bg).getbbox()
if box:
    pad = 24
    box = (max(0, box[0]-pad), max(0, box[1]-pad),
           min(im.width, box[2]+pad), min(im.height, box[3]+pad))
    im = im.crop(box)
im.save(out)
print(out, im.size)
'@
$cropPy = Join-Path $work 'crop.py'
Set-Content -Path $cropPy -Value $crop -Encoding UTF8

Get-ChildItem $srcDir -Filter *.mmd | Sort-Object Name | ForEach-Object {
    $name = $_.BaseName
    $src  = Get-Content $_.FullName -Raw -Encoding UTF8
    # 手工转义成 JSON 字符串字面量：Windows PowerShell 5.1 的 ConvertTo-Json 会把标量包成 {"value":...}
    $json = '"' + (($src -replace '\\', '\\\\') -replace '"', '\"' -replace "`r", '' -replace "`n", '\n' -replace "`t", '\t') + '"'

    $file = Join-Path $work "$name.html"
    $body = $html.Replace('__MERMAID__', $mermaidUrl).Replace('__SRC__', $json)
    Set-Content -Path $file -Value $body -Encoding UTF8

    $shot = Join-Path $work "$name.raw.png"
    $png  = Join-Path $here "$name.png"
    if (Test-Path $shot) { Remove-Item $shot -Force }

    $uri = 'file:///' + ($file -replace '\\', '/')
    # Edge 会把进度写 stderr，PowerShell 5.1 会当成终止错误，这里临时降级
    $ErrorActionPreference = 'Continue'
    & $edge --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-sandbox `
        --user-data-dir="$profileDir" --virtual-time-budget=20000 `
        --window-size=2000,3400 --screenshot="$shot" $uri 2>&1 | Out-Null
    $ErrorActionPreference = 'Stop'

    if (-not (Test-Path $shot)) { Write-Host "FAILED  $name"; return }
    python $cropPy $shot $png
}