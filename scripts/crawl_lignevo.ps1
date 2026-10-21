# Crawl us1.lignevo.com product archive -> CSV
# title, cate, price, images, link
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$outCsv = Join-Path $root "output\lignevo_products.csv"
$work = Join-Path $env:TEMP "lignevo-crawl"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work, (Split-Path $outCsv) | Out-Null

$origin = "https://us1.lignevo.com"
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
$pages = 922

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
        -not (Test-Path $_.file) -or (Get-Item $_.file).Length -lt 500
    })
    if ($pending.Count -eq 0) { return }
    $wave = 24
    $cfg = Join-Path $work "batch.cfg"
    for ($i = 0; $i -lt $pending.Count; $i += $wave) {
        $slice = @($pending[$i..([Math]::Min($i + $wave - 1, $pending.Count - 1))])
        Save-CurlConfig $slice $cfg
        & curl.exe --silent --show-error --parallel --parallel-max 12 --config $cfg -A $ua | Out-Null
        $done = [Math]::Min($i + $wave, $pending.Count)
        Write-Host ("  downloaded {0}/{1}" -f $done, $pending.Count)
    }
}

$jobs = @()
for ($page = 1; $page -le $pages; $page++) {
    $url = if ($page -eq 1) { "$origin/products/" } else { "$origin/products/page/$page/" }
    $jobs += @{ url = $url; file = Join-Path $work ("page-{0}.html" -f $page); page = $page }
}
Write-Host ("[download] pages={0}" -f $jobs.Count)
Invoke-Downloads $jobs

$bad = @($jobs | Where-Object {
    $text = ""
    if (Test-Path $_.file) { $text = [System.IO.File]::ReadAllText($_.file) }
    $text -notmatch 'class="wsp-card"'
})
if ($bad.Count -gt 0) {
    Write-Host ("[retry] {0}" -f $bad.Count)
    foreach ($job in $bad) { if (Test-Path $job.file) { Remove-Item $job.file -Force } }
    Invoke-Downloads $bad
}

$cardRx = [regex]'<a class="wsp-card" href="([^"]+)"><span class="wsp-card-media"><img src="([^"]+)"[^>]*></span><span class="wsp-card-body"><span class="wsp-card-name">([^<]*)</span><span class="wsp-card-price">([^<]*)</span>'
$seen = @{}
$rows = New-Object System.Collections.Generic.List[object]
$emptyPages = 0
foreach ($job in $jobs) {
    if (-not (Test-Path $job.file)) { $emptyPages++; continue }
    $html = [System.IO.File]::ReadAllText($job.file)
    $matches = $cardRx.Matches($html)
    if ($matches.Count -eq 0) { $emptyPages++; continue }
    foreach ($m in $matches) {
        $link = [System.Net.WebUtility]::HtmlDecode($m.Groups[1].Value).Trim()
        if (-not $link -or $seen.ContainsKey($link)) { continue }
        $seen[$link] = $true
        $title = [System.Net.WebUtility]::HtmlDecode($m.Groups[3].Value).Trim()
        $image = [System.Net.WebUtility]::HtmlDecode($m.Groups[2].Value).Trim()
        $price = [System.Net.WebUtility]::HtmlDecode($m.Groups[4].Value)
        $price = ($price -replace '\$', '' -replace '\s*[–—-]\s*', ' - ').Trim()
        $rows.Add([pscustomobject]@{
            title  = $title
            cate   = "Rug"
            price  = $price
            images = $image
            link   = $link
        })
    }
}
Write-Host ("[parse] rows={0} empty_pages={1}" -f $rows.Count, $emptyPages)

function Quote-Csv([string]$text) {
    if ($null -eq $text) { $text = "" }
    return '"' + ($text.Replace('"', '""') -replace "[\r\n]+", " ") + '"'
}
$utf8 = New-Object System.Text.UTF8Encoding $true
$sw = New-Object System.IO.StreamWriter($outCsv, $false, $utf8)
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
Write-Host ("[done] rows={0} file={1}" -f $rows.Count, $outCsv)
