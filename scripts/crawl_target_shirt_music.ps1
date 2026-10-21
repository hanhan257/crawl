# Crawl public Target search pages for shirt categories and music genres.
# The search API returns at most 50 pages x 24 products per category.
$ErrorActionPreference = "Stop"
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
$key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = "d:\VideCodeHan\Craw\crawl" }
$outDir = Join-Path $root "output"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$shirtCats = @(
    @{ id = "9qjry"; name = "Women's T-shirts" },
    @{ id = "m7sh2"; name = "Women's Shirts & Blouses" },
    @{ id = "3r5ob"; name = "Women's Polos" },
    @{ id = "4kt5y"; name = "Men's T-shirts" },
    @{ id = "4vr7u"; name = "Men's Undershirts" },
    @{ id = "262yt"; name = "Men's Graphic Tees" }
)
$musicCats = @(
    @{ id = "yz7nt"; name = "Vinyl Records" },
    @{ id = "jss3a"; name = "CDs" },
    @{ id = "ihxvx"; name = "Cassettes" },
    @{ id = "7f0kq"; name = "Tiny Vinyl" },
    @{ id = "y6trb"; name = "Kpop Music" },
    @{ id = "5xswl"; name = "Pop Music" },
    @{ id = "5xswi"; name = "Rock Music" },
    @{ id = "5xswk"; name = "R&B Music" },
    @{ id = "5xswj"; name = "Rap Music" },
    @{ id = "5xswo"; name = "Country Music" },
    @{ id = "5xswm"; name = "Latin Music" },
    @{ id = "55u9t"; name = "Jazz Music" },
    @{ id = "55u9p"; name = "Blues Music" },
    @{ id = "5xswp"; name = "Classical Music" },
    @{ id = "55u9s"; name = "Gospel & Christian Music" },
    @{ id = "5xswh"; name = "Soundtrack & Score Music" },
    @{ id = "5xswn"; name = "Kids' Music" },
    @{ id = "u5qbs"; name = "Christmas Music" }
)

function Quote-Csv([string]$value) {
    if ($null -eq $value) { $value = "" }
    $value = $value -replace "[\r\n]+", " "
    $value = $value.Trim()
    return '"' + ($value -replace '"', '""') + '"'
}

function Get-TargetPage([string]$categoryId, [int]$offset) {
    $page = [uri]::EscapeDataString("/c/-/N-$categoryId")
    $url = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2?key=$key&channel=WEB&count=24&offset=$offset&category=$categoryId&page=$page&platform=desktop&pricing_store_id=3991&store_ids=3991&visitor_id=018C0000000000000000000000000000&zip=55403&default_purchasability_filter=true&include_sponsored=false"
    $tmp = Join-Path $env:TEMP ("target-{0}-{1}.json" -f $categoryId, $offset)
    $code = "0"
    for ($try = 0; $try -lt 3; $try++) {
        $code = & curl.exe -s -A $ua -H "Accept: application/json" --max-time 30 -o $tmp -w "%{http_code}" $url
        if ($code -eq "200" -and (Test-Path $tmp) -and (Get-Item $tmp).Length -gt 100) { break }
        Start-Sleep -Seconds (2 + $try)
    }
    if ($code -ne "200") { return $null }
    return (Get-Content $tmp -Raw | ConvertFrom-Json)
}

function Row-FromProduct($product, [string]$cate) {
    $item = $product.item
    if (-not $item) { return $null }
    $title = [System.Net.WebUtility]::HtmlDecode([string]$item.product_description.title)
    $link = [string]$item.enrichment.buy_url
    if (-not $link) { return $null }
    $price = $product.price.current_retail
    $priceText = ""
    if ($null -ne $price -and "$price" -ne "") {
        $priceText = ("{0:0.00}" -f [double]$price)
    }
    $images = New-Object System.Collections.Generic.List[string]
    $info = $item.enrichment.image_info
    if ($info.primary_image.url) { $images.Add([string]$info.primary_image.url) }
    foreach ($img in @($info.alternate_images)) {
        if ($img.url) { $images.Add([string]$img.url) }
    }
    $clean = New-Object System.Collections.Generic.List[string]
    $seenImg = @{}
    foreach ($img in $images) {
        $u = $img.Trim()
        if ($u.StartsWith("//")) { $u = "https:$u" }
        if (-not $u -or $seenImg.ContainsKey($u)) { continue }
        $seenImg[$u] = $true
        $clean.Add($u)
    }
    $tcin = [string]$product.tcin
    return [pscustomobject]@{
        tcin   = $tcin
        title  = $title
        price  = $priceText
        cate   = $cate
        link   = $link
        images = ($clean -join ",")
    }
}

function Crawl-Group($categories) {
    $rows = New-Object System.Collections.Generic.List[object]
    $seen = @{}
    foreach ($cat in $categories) {
        $first = Get-TargetPage $cat.id 0
        if (-not $first) {
            Write-Output "FAIL $($cat.id) $($cat.name)"
            continue
        }
        $meta = $first.data.search.search_response.metadata
        $pages = 1
        if ($meta.total_pages) { $pages = [Math]::Min(50, [int]$meta.total_pages) }
        $payloads = @{ 0 = $first }
        $pending = @()
        for ($page = 1; $page -lt $pages; $page++) { $pending += ($page * 24) }
        for ($i = 0; $i -lt $pending.Count; $i += 6) {
            $wave = @($pending[$i..([Math]::Min($i + 5, $pending.Count - 1))])
            $procs = @()
            foreach ($offset in $wave) {
                $pagePath = [uri]::EscapeDataString("/c/-/N-$($cat.id)")
                $url = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2?key=$key&channel=WEB&count=24&offset=$offset&category=$($cat.id)&page=$pagePath&platform=desktop&pricing_store_id=3991&store_ids=3991&visitor_id=018C0000000000000000000000000000&zip=55403&default_purchasability_filter=true&include_sponsored=false"
                $tmp = Join-Path $env:TEMP ("target-{0}-{1}.json" -f $cat.id, $offset)
                $arg = "-s -A `"$ua`" -H `"Accept: application/json`" --max-time 30 -o `"$tmp`" `"$url`""
                $psi = New-Object System.Diagnostics.ProcessStartInfo
                $psi.FileName = "curl.exe"
                $psi.Arguments = $arg
                $psi.UseShellExecute = $false
                $psi.CreateNoWindow = $true
                $proc = [System.Diagnostics.Process]::Start($psi)
                $procs += [pscustomobject]@{ proc = $proc; offset = $offset; tmp = $tmp }
            }
            foreach ($item in $procs) {
                $item.proc.WaitForExit()
                if ((Test-Path $item.tmp) -and (Get-Item $item.tmp).Length -gt 100) {
                    try { $payloads[$item.offset] = (Get-Content $item.tmp -Raw | ConvertFrom-Json) } catch {}
                }
            }
        }
        $added = 0
        foreach ($offset in (@(0) + $pending)) {
            $payload = $payloads[$offset]
            if (-not $payload) { continue }
            foreach ($product in @($payload.data.search.products)) {
                $row = Row-FromProduct $product $cat.name
                if (-not $row -or -not $row.tcin) { continue }
                if ($seen.ContainsKey($row.tcin)) { continue }
                $seen[$row.tcin] = $true
                $rows.Add($row)
                $added++
            }
        }
        Write-Output ("{0} {1} reported={2} kept={3} total_rows={4}" -f $cat.id, $cat.name, $meta.total_results, $added, $rows.Count)
    }
    return $rows
}

function Write-Products($rows, [string]$path) {
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("title,price,cate,link,images")
    foreach ($row in $rows) {
        $lines.Add((@(
            (Quote-Csv $row.title),
            (Quote-Csv $row.price),
            (Quote-Csv $row.cate),
            (Quote-Csv $row.link),
            (Quote-Csv $row.images)
        ) -join ","))
    }
    $utf8 = New-Object System.Text.UTF8Encoding $true
    [System.IO.File]::WriteAllLines($path, $lines, $utf8)
}

Write-Output "SHIRTS"
$shirts = Crawl-Group $shirtCats
$shirtPath = Join-Path $outDir "target_shirt.csv"
Write-Products $shirts $shirtPath
Write-Output "shirt_file=$shirtPath rows=$($shirts.Count)"

Write-Output "MUSIC"
$music = Crawl-Group $musicCats
$musicPath = Join-Path $outDir "target_music.csv"
Write-Products $music $musicPath
Write-Output "music_file=$musicPath rows=$($music.Count)"
