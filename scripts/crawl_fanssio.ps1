# Crawl fanssio.com (WooCommerce Store API) — title, price, cate, link, images (no description).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$outCsv = Join-Path $root "output\fanssio_all.csv"
$work = Join-Path $env:TEMP "fanssio-crawl"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $outCsv) | Out-Null

$base = "https://fanssio.com/wp-json/wc/store/v1/products"
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# Discover total pages
$headerFile = Join-Path $work "headers.txt"
curl.exe -sS -m 45 -A $ua -D $headerFile -o (Join-Path $work "page-1.json") -H "Accept: application/json" "$base`?per_page=100&page=1" | Out-Null
$totalPages = 1
$total = 0
Get-Content $headerFile | ForEach-Object {
    if ($_ -match "(?i)^x-wp-total:\s*(\d+)") { $total = [int]$Matches[1] }
    if ($_ -match "(?i)^x-wp-totalpages:\s*(\d+)") { $totalPages = [int]$Matches[1] }
}
if ($totalPages -lt 1) { $totalPages = 1 }
Write-Host "Store API reports $total products / $totalPages pages"

$requests = @()
for ($page = 1; $page -le $totalPages; $page++) {
    $requests += [pscustomobject]@{
        page = $page
        file = Join-Path $work ("page-{0}.json" -f $page)
        url  = "{0}?per_page=100&page={1}" -f $base, $page
    }
}

$wave = 4
Write-Host "Downloading $($requests.Count) pages..."
for ($i = 0; $i -lt $requests.Count; $i += $wave) {
    $batch = $requests[$i..([Math]::Min($i + $wave - 1, $requests.Count - 1))]
    $procs = @()
    foreach ($req in $batch) {
        if ($req.page -eq 1 -and (Test-Path $req.file) -and (Get-Item $req.file).Length -gt 200) {
            continue
        }
        $arg = "-sS -m 45 -o `"$($req.file)`" -A `"$ua`" -H `"Accept: application/json`" `"$($req.url)`""
        $procs += Start-Process -FilePath curl.exe -ArgumentList $arg -WindowStyle Hidden -PassThru
    }
    if ($procs.Count -gt 0) { $procs | Wait-Process }
    $done = [Math]::Min($i + $wave, $requests.Count)
    Write-Host "  $done / $($requests.Count)"
}

$failed = @($requests | Where-Object {
    -not (Test-Path $_.file) -or (Get-Item $_.file).Length -lt 50
})
if ($failed.Count -gt 0) {
    Write-Host "Retry $($failed.Count) short files..."
    foreach ($req in $failed) {
        curl.exe -sS -m 60 -A $ua -o $req.file -H "Accept: application/json" $req.url | Out-Null
        Start-Sleep -Milliseconds 250
    }
}

function Flat([object]$value) {
    return (("" + $value) -replace "[\r\n]+", " ").Trim()
}

function Decode-Html([string]$text) {
    if (-not $text) { return "" }
    return [System.Net.WebUtility]::HtmlDecode($text)
}

function Quote-Csv([object]$value) {
    $text = Flat $value
    return '"' + ($text -replace '"', '""') + '"'
}

function Format-Price($prices) {
    if (-not $prices) { return "" }
    $raw = $prices.price
    if ($null -eq $raw -or $raw -eq "") { $raw = $prices.sale_price }
    if ($null -eq $raw -or $raw -eq "") { $raw = $prices.regular_price }
    if ($null -eq $raw -or $raw -eq "") { return "" }
    $minor = 2
    try { if ($null -ne $prices.currency_minor_unit) { $minor = [int]$prices.currency_minor_unit } } catch {}
    try {
        $n = [decimal]$raw
        if ($minor -gt 0) { $n = $n / [math]::Pow(10, $minor) }
        return ("{0:0.##}" -f $n)
    } catch {
        return "$raw"
    }
}

$rows = New-Object System.Collections.Generic.List[string]
$rows.Add('"title","price","cate","link","images"') | Out-Null
$seen = @{}
$parsed = 0

foreach ($req in $requests) {
    if (-not (Test-Path $req.file) -or (Get-Item $req.file).Length -lt 50) {
        Write-Host "SKIP page $($req.page)"
        continue
    }
    $items = Get-Content $req.file -Raw | ConvertFrom-Json
    if (-not $items) { continue }
    $parsed++
    foreach ($item in @($items)) {
        $id = "" + $item.id
        if (-not $id -or $seen.ContainsKey($id)) { continue }
        $seen[$id] = $true
        $title = Decode-Html (Flat $item.name)
        $link = Flat $item.permalink
        $price = Format-Price $item.prices
        $cates = @()
        foreach ($cat in @($item.categories)) {
            $name = Decode-Html (Flat $cat.name)
            if ($name) { $cates += $name }
        }
        $cate = ($cates | Select-Object -Unique) -join ", "
        $imgs = @()
        foreach ($img in @($item.images)) {
            $src = Flat $img.src
            if ($src -like "http*") { $imgs += $src }
        }
        $images = ($imgs | Select-Object -Unique) -join ","
        if (-not $title -or -not $link) { continue }
        $rows.Add(("{0},{1},{2},{3},{4}" -f `
            (Quote-Csv $title),
            (Quote-Csv $price),
            (Quote-Csv $cate),
            (Quote-Csv $link),
            (Quote-Csv $images)
        )) | Out-Null
    }
}

$utf8Bom = New-Object System.Text.UTF8Encoding $true
[System.IO.File]::WriteAllLines($outCsv, $rows, $utf8Bom)
Write-Host "Parsed $parsed pages, unique $($seen.Count)"
Write-Host "WROTE $($seen.Count) -> $outCsv"
