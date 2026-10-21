# Fill every product image from the product-page gallery (wsp-gallery-data).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$csvPath = Join-Path $root "output\lignevo_products.csv"
$work = Join-Path $env:TEMP "lignevo-images"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work | Out-Null

$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
$rows = @(Import-Csv -LiteralPath $csvPath)
Write-Host ("[rows] {0}" -f $rows.Count)

function Save-CurlConfig([array]$jobs, [string]$cfgPath) {
    $sb = New-Object System.Text.StringBuilder
    foreach ($job in $jobs) {
        $out = ($job.file -replace "\\", "/")
        [void]$sb.AppendLine("url = `"$($job.url)`"")
        [void]$sb.AppendLine("output = `"$out`"")
        [void]$sb.AppendLine("max-time = 45")
        [void]$sb.AppendLine("retry = 3")
        [void]$sb.AppendLine("retry-delay = 1")
    }
    [System.IO.File]::WriteAllText($cfgPath, $sb.ToString())
}

function Invoke-Downloads([array]$jobs) {
    $pending = @($jobs | Where-Object {
        -not (Test-Path $_.file) -or (Get-Item $_.file).Length -lt 800
    })
    if ($pending.Count -eq 0) { return }
    $wave = 20
    $cfg = Join-Path $work "batch.cfg"
    for ($i = 0; $i -lt $pending.Count; $i += $wave) {
        $slice = @($pending[$i..([Math]::Min($i + $wave - 1, $pending.Count - 1))])
        Save-CurlConfig $slice $cfg
        & curl.exe --silent --show-error --parallel --parallel-max 12 --config $cfg -A $ua | Out-Null
        $done = [Math]::Min($i + $wave, $pending.Count)
        if ($done % 100 -eq 0 -or $done -eq $pending.Count) {
            Write-Host ("  downloaded {0}/{1}" -f $done, $pending.Count)
        }
    }
}

$jobs = @()
for ($i = 0; $i -lt $rows.Count; $i++) {
    $jobs += @{
        url  = [string]$rows[$i].link
        file = Join-Path $work ("p{0}.html" -f $i)
        idx  = $i
    }
}
Write-Host ("[download] {0}" -f $jobs.Count)
Invoke-Downloads $jobs

$need = @($jobs | Where-Object {
    $ok = $false
    if (Test-Path $_.file) {
        $head = [System.IO.File]::ReadAllText($_.file)
        $ok = $head.Contains("wsp-gallery-data")
    }
    -not $ok
})
if ($need.Count -gt 0) {
    Write-Host ("[retry] {0}" -f $need.Count)
    foreach ($job in $need) { if (Test-Path $job.file) { Remove-Item $job.file -Force } }
    Start-Sleep -Seconds 2
    Invoke-Downloads $need
}

$srcRx = [regex]'"src"\s*:\s*"([^"]+)"'
$blockRx = [regex]'(?s)<script[^>]*id="wsp-gallery-data"[^>]*>(.*?)</script>'
$multi = 0
$missing = 0
$totalImgs = 0
for ($i = 0; $i -lt $rows.Count; $i++) {
    $file = Join-Path $work ("p{0}.html" -f $i)
    $imgs = New-Object System.Collections.Generic.List[string]
    if (Test-Path $file) {
        $html = [System.IO.File]::ReadAllText($file)
        $block = $blockRx.Match($html)
        if ($block.Success) {
            foreach ($m in $srcRx.Matches($block.Groups[1].Value)) {
                $src = ($m.Groups[1].Value -replace '\\/', '/').Trim()
                if ($src -and -not $imgs.Contains($src)) { $imgs.Add($src) }
            }
        }
    }
    if ($imgs.Count -eq 0) {
        $missing++
        if ($rows[$i].images) { $imgs.Add([string]$rows[$i].images) }
    }
    if ($imgs.Count -gt 1) { $multi++ }
    $totalImgs += $imgs.Count
    $rows[$i].images = ($imgs -join ",")
    if (($i + 1) % 2000 -eq 0) {
        Write-Host ("  parsed {0}/{1}" -f ($i + 1), $rows.Count)
    }
}
Write-Host ("[images] products={0} with_many={1} fallback_one={2} avg={3:n2}" -f $rows.Count, $multi, $missing, ($totalImgs / [Math]::Max(1, $rows.Count)))

function Quote-Csv([string]$text) {
    if ($null -eq $text) { $text = "" }
    return '"' + ($text.Replace('"', '""') -replace "[\r\n]+", " ") + '"'
}
$utf8 = New-Object System.Text.UTF8Encoding $true
$sw = New-Object System.IO.StreamWriter($csvPath, $false, $utf8)
$sw.WriteLine("title,cate,price,images,link")
foreach ($row in $rows) {
    $sw.WriteLine((@(
        (Quote-Csv $row.title),
        (Quote-Csv $row.cate),
        (Quote-Csv $row.price),
        (Quote-Csv $row.images),
        (Quote-Csv $row.link)
    ) -join ","))
}
$sw.Flush()
$sw.Close()
Write-Host ("[done] {0}" -f $csvPath)
