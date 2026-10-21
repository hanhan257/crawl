# Crawl wearandjoy.com (ShopBase catalog API) -> CSV
# title, cate, price, images, link
# The list API caps each filter at 9950 rows, so products are sharded by tag and collection.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$outCsv = Join-Path $root "output\wearandjoy_products.csv"
$work = Join-Path $env:TEMP "wearandjoy-crawl"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work, (Join-Path $work "counts"), (Join-Path $work "pages"), (Split-Path $outCsv) | Out-Null

$base = "https://wearandjoy.com/api/catalog/next/products.json"
$colApi = "https://wearandjoy.com/api/catalog/next/collections.json"
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
$shop = "https://wearandjoy.com"
$maxPage = 199
$pageSize = 50

function Enc([string]$text) {
    return [uri]::EscapeDataString($text)
}

function KeyOf([string]$text) {
    $md5 = [System.Security.Cryptography.MD5]::Create()
    $bytes = $md5.ComputeHash([Text.Encoding]::UTF8.GetBytes($text))
    return ([BitConverter]::ToString($bytes) -replace "-", "").ToLower()
}

function Read-JsonFile([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $raw = [System.IO.File]::ReadAllText($path)
        if ([string]::IsNullOrWhiteSpace($raw) -or -not $raw.TrimStart().StartsWith("{")) { return $null }
        if (-not $raw.TrimEnd().EndsWith("}")) { return $null }
        return $raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Build-Url([hashtable]$q) {
    $parts = @(
        "limit=$pageSize",
        "page=$($q.page)",
        "sort_field=created_at",
        "sort_direction=$($q.sort)"
    )
    if ($q.tag) { $parts += "tag=$(Enc $q.tag)" }
    if ($q.q) { $parts += "q=$(Enc $q.q)" }
    if ($q.handle) { $parts += "collection_handle=$(Enc $q.handle)" }
    # "?" ends the variable name in PowerShell, so do not write "$base?".
    return $base + "?" + ($parts -join "&")
}

function Save-CurlConfig([array]$jobs, [string]$cfgPath) {
    $sb = New-Object System.Text.StringBuilder
    foreach ($job in $jobs) {
        $out = ($job.file -replace "\\", "/")
        [void]$sb.AppendLine("url = `"$($job.url)`"")
        [void]$sb.AppendLine("output = `"$out`"")
        [void]$sb.AppendLine("max-time = 45")
        [void]$sb.AppendLine("retry = 2")
        [void]$sb.AppendLine("retry-delay = 1")
    }
    [System.IO.File]::WriteAllText($cfgPath, $sb.ToString())
}

function Invoke-Downloads([array]$jobs) {
    $pending = @($jobs | Where-Object {
        -not (Test-Path $_.file) -or (Get-Item $_.file).Length -lt 40
    })
    if ($pending.Count -eq 0) { return }
    $wave = 20
    $cfg = Join-Path $work "batch.cfg"
    for ($i = 0; $i -lt $pending.Count; $i += $wave) {
        $slice = @($pending[$i..([Math]::Min($i + $wave - 1, $pending.Count - 1))])
        Save-CurlConfig $slice $cfg
        & curl.exe --silent --show-error --parallel --parallel-max 16 --config $cfg -A $ua | Out-Null
        $done = [Math]::Min($i + $wave, $pending.Count)
        if ($done % 100 -eq 0 -or $done -eq $pending.Count) {
            Write-Host ("  downloaded {0}/{1}" -f $done, $pending.Count)
        }
    }
}

function Get-ReportedCount($payload) {
    if ($null -eq $payload -or $null -eq $payload.result) { return 0 }
    $n = 0
    try { $n = [int]$payload.result.count } catch { $n = 0 }
    if ($n -lt 0) { return 0 }
    return $n
}

Write-Host "[collections]"
$colJobs = @()
foreach ($page in 1, 2) {
    $colJobs += @{
        url  = "$colApi`?limit=50&page=$page"
        file = Join-Path $work "collections-$page.json"
    }
}
Invoke-Downloads $colJobs
$collections = @()
foreach ($page in 1, 2) {
    $payload = Read-JsonFile (Join-Path $work "collections-$page.json")
    if ($payload.result.items) { $collections += @($payload.result.items) }
}
Write-Host ("  collections={0}" -f $collections.Count)

$seenTags = @{}
$tagList = New-Object System.Collections.Generic.List[string]
function Add-Tag([string]$tag) {
    $tag = ($tag | Out-String).Trim()
    if (-not $tag) { return }
    $tag = $tag.Trim()
    if ($tag.Length -lt 2 -or $tag.Length -gt 80) { return }
    $k = $tag.ToLower()
    if ($seenTags.ContainsKey($k)) { return }
    $seenTags[$k] = $true
    $tagList.Add($tag)
}

foreach ($c in $collections) {
    if ($c.title) { Add-Tag ([string]$c.title) }
    if ($c.handle) { Add-Tag (([string]$c.handle) -replace "-", " ") }
}

$slashPrefixes = @(
    "son import", "duc tm", "cam tien up", "yen nhu up", "duc gameday",
    "duc beige", "cam tien", "yen nhu", "auto-duc bs", "duc bs",
    "bestsellkowc", "nflmovewjoy", "wadmovewjoy", "wahmove mjoy"
)
$start = Get-Date "2026-08-01"
$end = Get-Date "2026-10-20"
for ($d = $start; $d -le $end; $d = $d.AddDays(1)) {
    $day = $d.Day
    $dd = "{0:d2}" -f $d.Day
    $mm = "{0:d2}" -f $d.Month
    $yy = "{0:d2}" -f ($d.Year % 100)
    Add-Tag ("tvsmovewad {0}{1}{2}" -f $day, $mm, $yy)
    Add-Tag ("tvsmovewad {0}{1}{2}" -f $dd, $mm, $yy)
    Add-Tag ("tvsmovewad {0}{1}{2}" -f $day, $d.Month, $yy)
    foreach ($prefix in $slashPrefixes) {
        Add-Tag ("{0} {1}/{2}/{3}" -f $prefix, $day, $mm, $yy)
        Add-Tag ("{0} {1}/{2}/{3}" -f $prefix, $dd, $mm, $yy)
    }
}
foreach ($extra in @(
    "hoodie", "t-shirt", "hawaiian shirt", "sweatpants", "v-neck shirt",
    "hat", "mug", "tumbler", "jersey", "flag", "quilt table runner",
    "sneaker", "sneakers", "poster", "blanket", "ornament", "phone case",
    "sweatshirt", "crewneck", "tank top", "polo", "zip hoodie"
)) { Add-Tag $extra }

Write-Host ("[probe tags] candidates={0}" -f $tagList.Count)
$probeJobs = @()
foreach ($tag in $tagList) {
    $key = KeyOf "tag|$tag"
    $probeJobs += @{
        url  = (Build-Url @{ page = 1; sort = "desc"; tag = $tag })
        file = Join-Path $work "counts\$key.json"
        tag  = $tag
        key  = $key
    }
}
Invoke-Downloads $probeJobs

$goodTags = @()
$bigTags = @()
foreach ($job in $probeJobs) {
    $payload = Read-JsonFile $job.file
    $count = Get-ReportedCount $payload
    if ($count -le 0) { continue }
    if ($count -le ($maxPage * $pageSize)) {
        $goodTags += @{ tag = $job.tag; key = $job.key; count = $count }
    } else {
        $bigTags += @{ tag = $job.tag; key = $job.key; count = $count }
    }
}
Write-Host ("  tags<=9950={0} tags>9950={1}" -f $goodTags.Count, $bigTags.Count)

$colShards = @()
foreach ($c in $collections) {
    $handle = [string]$c.handle
    if (-not $handle) { continue }
    $key = KeyOf "col|$handle"
    $file = Join-Path $work "counts\$key.json"
    # count via a dedicated probe
    $colShards += @{
        url    = (Build-Url @{ page = 1; sort = "desc"; handle = $handle })
        file   = $file
        handle = $handle
        key    = $key
        title  = [string]$c.title
    }
}
Write-Host ("[probe collections] {0}" -f $colShards.Count)
Invoke-Downloads $colShards
$goodCols = @()
foreach ($job in $colShards) {
    $payload = Read-JsonFile $job.file
    $count = Get-ReportedCount $payload
    if ($count -gt 0 -and $count -le ($maxPage * $pageSize)) {
        $goodCols += @{ handle = $job.handle; key = $job.key; count = $count; title = $job.title }
    } elseif ($count -gt ($maxPage * $pageSize)) {
        Write-Host ("  collection capped {0} count={1}" -f $job.handle, $count)
    }
}
Write-Host ("  collections to crawl={0}" -f $goodCols.Count)

$pageJobs = @()
foreach ($shard in $goodTags) {
    $pages = [Math]::Ceiling($shard.count / $pageSize)
    if ($pages -gt $maxPage) { $pages = $maxPage }
    for ($p = 1; $p -le $pages; $p++) {
        $pageJobs += @{
            url  = (Build-Url @{ page = $p; sort = "desc"; tag = $shard.tag })
            file = Join-Path $work ("pages\{0}-p{1}.json" -f $shard.key, $p)
        }
    }
}
foreach ($shard in $goodCols) {
    $pages = [Math]::Ceiling($shard.count / $pageSize)
    if ($pages -gt $maxPage) { $pages = $maxPage }
    for ($p = 1; $p -le $pages; $p++) {
        $pageJobs += @{
            url  = (Build-Url @{ page = $p; sort = "desc"; handle = $shard.handle })
            file = Join-Path $work ("pages\{0}-p{1}.json" -f $shard.key, $p)
        }
    }
}
# Both ends of oversized tags, then title-word splits if the ends do not meet.
foreach ($shard in $bigTags) {
    foreach ($sort in @("desc", "asc")) {
        for ($p = 1; $p -le $maxPage; $p++) {
            $pageJobs += @{
                url  = (Build-Url @{ page = $p; sort = $sort; tag = $shard.tag })
                file = Join-Path $work ("pages\{0}-{1}-p{2}.json" -f $shard.key, $sort, $p)
            }
        }
    }
}
Write-Host ("[pages] requests={0}" -f $pageJobs.Count)
Invoke-Downloads $pageJobs

function Add-Product($map, $item) {
    if ($null -eq $item -or $null -eq $item.id) { return }
    $id = [string]$item.id
    if ($map.ContainsKey($id)) { return }
    $handle = [string]$item.handle
    $title = [System.Net.WebUtility]::HtmlDecode([string]$item.title).Trim()
    if (-not $handle -or -not $title) { return }
    $cate = [System.Net.WebUtility]::HtmlDecode([string]$item.product_type).Trim()
    $priceVal = $item.price
    if (($null -eq $priceVal -or [double]$priceVal -le 0) -and $null -ne $item.min_price) {
        $priceVal = $item.min_price
    }
    $price = ""
    if ($null -ne $priceVal -and "$priceVal" -ne "") {
        $price = ("{0:0.00}" -f [double]$priceVal)
    }
    $imgs = New-Object System.Collections.Generic.List[string]
    foreach ($im in @($item.images)) {
        $src = ""
        if ($im -is [string]) { $src = $im }
        elseif ($null -ne $im.src) { $src = [string]$im.src }
        if ($src -and -not $imgs.Contains($src)) { $imgs.Add($src) }
    }
    if ($imgs.Count -eq 0) {
        foreach ($im in @($item.thumbnails)) {
            $src = ""
            if ($im -is [string]) { $src = $im }
            elseif ($null -ne $im.src) { $src = [string]$im.src }
            if ($src -and -not $imgs.Contains($src)) { $imgs.Add($src) }
        }
    }
    $map[$id] = @{
        title  = $title
        cate   = $cate
        price  = $price
        images = ($imgs -join " | ")
        link   = "$shop/products/$handle"
        tags   = @($item.tags | ForEach-Object { [string]$_ })
        created = $item.created_at
    }
}

function Import-Pages($map) {
    $files = Get-ChildItem (Join-Path $work "pages") -Filter *.json -ErrorAction SilentlyContinue
    $n = 0
    foreach ($f in $files) {
        $n++
        $payload = Read-JsonFile $f.FullName
        if ($null -eq $payload -or $null -eq $payload.result) { continue }
        foreach ($item in @($payload.result.items)) { Add-Product $map $item }
        if ($n % 400 -eq 0) { Write-Host ("  parsed {0}/{1} unique={2}" -f $n, $files.Count, $map.Count) }
    }
    Write-Host ("  parsed {0} files unique={1}" -f $files.Count, $map.Count)
}

$products = @{}
Write-Host "[parse]"
Import-Pages $products

# Second pass: tags seen on products but not probed yet, plus word splits for big tags.
$extraTags = New-Object System.Collections.Generic.List[string]
foreach ($p in $products.Values) {
    foreach ($t in @($p.tags)) { if ($t -and -not $seenTags.ContainsKey($t.ToLower())) { $extraTags.Add($t) } }
}
$extraTags = @($extraTags | Select-Object -Unique)
Write-Host ("[extra tags] {0}" -f $extraTags.Count)
if ($extraTags.Count -gt 0) {
    $extraJobs = @()
    foreach ($tag in $extraTags) {
        Add-Tag $tag
        $key = KeyOf "tag|$tag"
        $extraJobs += @{
            url  = (Build-Url @{ page = 1; sort = "desc"; tag = $tag })
            file = Join-Path $work "counts\$key.json"
            tag  = $tag
            key  = $key
        }
    }
    Invoke-Downloads $extraJobs
    $morePages = @()
    foreach ($job in $extraJobs) {
        $payload = Read-JsonFile $job.file
        $count = Get-ReportedCount $payload
        if ($count -le 0) { continue }
        $pages = [Math]::Min($maxPage, [Math]::Ceiling($count / $pageSize))
        $sorts = @("desc")
        if ($count -gt ($maxPage * $pageSize)) { $sorts = @("desc", "asc") }
        foreach ($sort in $sorts) {
            $limitPages = $pages
            if ($sort -eq "asc") { $limitPages = $maxPage }
            for ($p = 1; $p -le $limitPages; $p++) {
                $morePages += @{
                    url  = (Build-Url @{ page = $p; sort = $sort; tag = $job.tag })
                    file = Join-Path $work ("pages\{0}-{1}-p{2}.json" -f $job.key, $sort, $p)
                }
            }
        }
    }
    Write-Host ("  extra pages={0}" -f $morePages.Count)
    Invoke-Downloads $morePages
    Import-Pages $products
}

$splitWords = @(
    "black", "white", "red", "blue", "green", "navy", "pink", "gold", "purple",
    "orange", "yellow", "brown", "grey", "gray", "vintage", "custom", "floral",
    "halloween", "christmas", "hoodie", "shirt", "jersey", "tumbler", "sneaker",
    "eagles", "cowboys", "chiefs", "packers", "ravens", "steelers", "giants",
    "lions", "bears", "dolphins", "broncos", "raiders", "seahawks", "rams",
    "bengals", "vikings", "saints", "falcons", "panthers", "texans", "colts",
    "chargers", "patriots", "bills", "jets", "commanders", "cardinals", "titans",
    "jaguars", "browns", "49ers", "yankees", "dodgers", "lakers", "warriors",
    "celtics", "bulls", "knicks", "nfl", "nba", "mlb", "nhl", "ncaa"
)
if ($bigTags.Count -gt 0) {
    Write-Host ("[split big tags] {0}" -f $bigTags.Count)
    $splitProbes = @()
    foreach ($shard in $bigTags) {
        foreach ($word in $splitWords) {
            $key = KeyOf ("split|{0}|{1}" -f $shard.tag, $word)
            $splitProbes += @{
                url  = (Build-Url @{ page = 1; sort = "desc"; tag = $shard.tag; q = $word })
                file = Join-Path $work "counts\$key.json"
                tag  = $shard.tag
                q    = $word
                key  = $key
            }
        }
    }
    Invoke-Downloads $splitProbes
    $splitPages = @()
    foreach ($job in $splitProbes) {
        $payload = Read-JsonFile $job.file
        $count = Get-ReportedCount $payload
        if ($count -le 0 -or $count -gt ($maxPage * $pageSize)) { continue }
        $pages = [Math]::Ceiling($count / $pageSize)
        for ($p = 1; $p -le $pages; $p++) {
            $splitPages += @{
                url  = (Build-Url @{ page = $p; sort = "desc"; tag = $job.tag; q = $job.q })
                file = Join-Path $work ("pages\{0}-p{1}.json" -f $job.key, $p)
            }
        }
    }
    Write-Host ("  split pages={0}" -f $splitPages.Count)
    Invoke-Downloads $splitPages
    Import-Pages $products
}

Write-Host "[write csv]"
$utf8 = New-Object System.Text.UTF8Encoding $true
$sw = New-Object System.IO.StreamWriter($outCsv, $false, $utf8)
function Quote-Csv([string]$text) {
    if ($null -eq $text) { $text = "" }
    return '"' + ($text.Replace('"', '""') -replace "[\r\n]+", " ") + '"'
}
$sw.WriteLine("title,cate,price,images,link")
foreach ($row in $products.Values) {
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
Write-Host ("[done] rows={0} file={1}" -f $products.Count, $outCsv)
