# Crawl gridstyles.com (ShopBase catalog API) to output/gridstyles_products.csv
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$outCsv = Join-Path $root "output\gridstyles_products.csv"
$work = Join-Path $env:TEMP "gridstyles-crawl"
$pagesDir = Join-Path $work "pages"
$shop = "https://gridstyles.com"
$api = "$shop/api/catalog/next/products.json"
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
New-Item -ItemType Directory -Force -Path $pagesDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $outCsv) | Out-Null

function Read-JsonFile([string]$path) {
    try {
        if (-not (Test-Path $path)) { return $null }
        $raw = [System.IO.File]::ReadAllText($path)
        if ([string]::IsNullOrWhiteSpace($raw) -or -not $raw.TrimStart().StartsWith("{")) { return $null }
        if (-not $raw.TrimEnd().EndsWith("}")) { return $null }
        return $raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

$colResp = curl.exe -sS -L --max-time 30 -A $ua "$shop/api/catalog/next/collections.json?limit=50&page=1"
$colJson = $colResp | ConvertFrom-Json
$cateById = @{}
foreach ($col in @($colJson.result.items)) {
    if ($null -eq $col.id) { continue }
    $name = [System.Net.WebUtility]::HtmlDecode([string]$col.title).Trim()
    if ($name) { $cateById[[string]$col.id] = $name }
}
Write-Host ("collections={0}" -f $cateById.Count)

$firstRaw = curl.exe -sS -L --max-time 30 -A $ua ($api + "?limit=50&page=1")
[System.IO.File]::WriteAllText((Join-Path $pagesDir "p1.json"), $firstRaw)
$first = $firstRaw | ConvertFrom-Json
$total = [int]$first.result.count
$pageCount = [Math]::Ceiling($total / 50.0)
if ($pageCount -gt 199) { $pageCount = 199 }
Write-Host ("count={0} pages={1}" -f $total, $pageCount)

function Save-Pages([int[]]$pageNums) {
    if ($pageNums.Count -eq 0) { return }
    $cfg = Join-Path $work "curl-pages.txt"
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($page in $pageNums) {
        $out = (Join-Path $pagesDir ("p{0}.json" -f $page)) -replace "\\", "/"
        $url = $api + "?limit=50&page=" + $page
        $lines.Add("url = `"$url`"")
        $lines.Add("output = `"$out`"")
    }
    [System.IO.File]::WriteAllLines($cfg, $lines)
    & curl.exe -sS -L --max-time 40 --parallel --parallel-max 8 --config $cfg
}

$rest = 2..$pageCount
Save-Pages $rest

$missing = @()
foreach ($page in 1..$pageCount) {
    $path = Join-Path $pagesDir ("p{0}.json" -f $page)
    if ($null -eq (Read-JsonFile $path)) { $missing += $page }
}
if ($missing.Count -gt 0) {
    Write-Host ("retry={0}" -f $missing.Count)
    Save-Pages $missing
}

$products = @{}
$bad = 0
foreach ($page in 1..$pageCount) {
    $payload = Read-JsonFile (Join-Path $pagesDir ("p{0}.json" -f $page))
    if ($null -eq $payload -or $null -eq $payload.result) { $bad++; continue }
    foreach ($item in @($payload.result.items)) {
        if ($null -eq $item -or $null -eq $item.id) { continue }
        $id = [string]$item.id
        if ($products.ContainsKey($id)) { continue }
        $handle = [string]$item.handle
        $title = [System.Net.WebUtility]::HtmlDecode([string]$item.title).Trim()
        if (-not $handle -or -not $title) { continue }
        $names = New-Object System.Collections.Generic.List[string]
        foreach ($cid in @($item.collection_ids)) {
            $key = [string]$cid
            if ($cateById.ContainsKey($key) -and -not $names.Contains($cateById[$key])) {
                $names.Add($cateById[$key])
            }
        }
        $cate = if ($names.Count -gt 0) { $names -join " | " } else { [System.Net.WebUtility]::HtmlDecode([string]$item.product_type).Trim() }
        $priceVal = $item.price
        if (($null -eq $priceVal -or "$priceVal" -eq "") -and $null -ne $item.min_price) {
            $priceVal = $item.min_price
        }
        $price = ""
        if ($null -ne $priceVal -and "$priceVal" -ne "") {
            $price = ("{0:0.00}" -f [double]$priceVal)
        }
        $imgs = New-Object System.Collections.Generic.List[string]
        foreach ($im in @($item.images)) {
            $src = ""
            if ($im -is [string]) { $src = $im.Trim() }
            elseif ($null -ne $im.src) { $src = [string]$im.src }
            elseif ($null -ne $im.url) { $src = [string]$im.url }
            if ($src -and -not $imgs.Contains($src)) { $imgs.Add($src) }
        }
        $products[$id] = @{
            title  = $title
            cate   = $cate
            price  = $price
            images = ($imgs -join ",")
            link   = "$shop/products/$handle"
        }
    }
}
Write-Host ("unique={0} badPages={1}" -f $products.Count, $bad)

$utf8 = New-Object System.Text.UTF8Encoding $true
$sw = New-Object System.IO.StreamWriter($outCsv, $false, $utf8)
function Quote-Csv([string]$text) {
    if ($null -eq $text) { $text = "" }
    return '"' + ($text.Replace('"', '""') -replace "[\r\n]+", " ") + '"'
}
$sw.WriteLine("title,cate,price,images,link")
foreach ($row in $products.Values) {
    $sw.WriteLine((
        (Quote-Csv $row.title),
        (Quote-Csv $row.cate),
        (Quote-Csv $row.price),
        (Quote-Csv $row.images),
        (Quote-Csv $row.link)
    ) -join ",")
}
$sw.Close()
Write-Host ("csv={0}" -f $outCsv)
