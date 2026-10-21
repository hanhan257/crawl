# Crawl featherslife.com catalog (Laravel campaign API) to CSV.
# One row per campaign. Parent menus are skipped because they only repeat child categories.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$outCsv = Join-Path $root "output\featherslife_all.csv"
$work = Join-Path $env:TEMP "fl-crawl"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $outCsv) | Out-Null

$jobs = @(
    @{ slug = "2d-t-shirt"; pages = 8; title = "2D Classic T-shirt" }
    @{ slug = "3d-cap"; pages = 2; title = "3D Cap" }
    @{ slug = "3d-hoodie"; pages = 2; title = "3D Hoodie" }
    @{ slug = "3d-rug"; pages = 2; title = "3D Rug" }
    @{ slug = "3d-t-shirt"; pages = 2; title = "3D T-shirt" }
    @{ slug = "3d-wallet"; pages = 2; title = "3D Wallet" }
    @{ slug = "40oz-tumbler"; pages = 1; title = "40oz Tumbler (Opaque Lid)" }
    @{ slug = "acrylic-desk-decor"; pages = 1; title = "Acrylic Desk Decor" }
    @{ slug = "apron"; pages = 1; title = "Apron" }
    @{ slug = "aylen-liora"; pages = 1; title = "Aylen Liora" }
    @{ slug = "bedding-set"; pages = 2; title = "Bedding set" }
    @{ slug = "blanket"; pages = 3; title = "Blanket" }
    @{ slug = "canvas"; pages = 53; title = "Canvas" }
    @{ slug = "car-sunshape"; pages = 2; title = "Car Sunshade" }
    @{ slug = "clog-style-shoe"; pages = 4; title = "Clog-Style Shoe" }
    @{ slug = "every-child-matters"; pages = 4; title = "Every Child Matters" }
    @{ slug = "flag"; pages = 1; title = "Flag" }
    @{ slug = "funny-mug"; pages = 1; title = "Funny Mug" }
    @{ slug = "hawaiian-shirt"; pages = 2; title = "Hawaiian Shirt" }
    @{ slug = "hooded-blanket"; pages = 2; title = "Hooded Blanket" }
    @{ slug = "leather-bag"; pages = 1; title = "Leather Bag" }
    @{ slug = "mmiw"; pages = 2; title = "MMIW" }
    @{ slug = "ojibwe-floral"; pages = 1; title = "Ojibwe Floral" }
    @{ slug = "ornament"; pages = 1; title = "Ornament" }
    @{ slug = "phone-case"; pages = 2; title = "Phone case" }
    @{ slug = "pillow"; pages = 4; title = "Pillow" }
    @{ slug = "poster"; pages = 44; title = "Poster" }
    @{ slug = "stainless-steel-keychain"; pages = 2; title = "Stainless Steel Keychain" }
    @{ slug = "symbol"; pages = 11; title = "Animals Symbol" }
    @{ slug = "tumbler"; pages = 2; title = "Tumbler" }
    @{ slug = "wood-candle-holder"; pages = 1; title = "Wood Candle Holder" }
)

$requests = @()
foreach ($job in $jobs) {
    for ($page = 1; $page -le $job.pages; $page++) {
        $requests += [pscustomobject]@{
            slug  = $job.slug
            title = $job.title
            page  = $page
            file  = Join-Path $work ("{0}-{1}.json" -f $job.slug, $page)
            url   = "https://featherslife.com/api/shop-category/$($job.slug)?page=$page"
        }
    }
}

function Save-Url([string]$url, [string]$dest) {
    $arg = "-sS -m 50 -o `"$dest`" -w `"%{http_code}`" -A `"Mozilla/5.0`" -H `"Accept: application/json`" -H `"X-Requested-With: XMLHttpRequest`" `"$url`""
    $code = & curl.exe -sS -m 50 -o $dest -w "%{http_code}" -A "Mozilla/5.0" -H "Accept: application/json" -H "X-Requested-With: XMLHttpRequest" $url
    return "$code"
}

$wave = 6
Write-Host "Downloading $($requests.Count) pages..."
for ($i = 0; $i -lt $requests.Count; $i += $wave) {
    $batch = $requests[$i..([Math]::Min($i + $wave - 1, $requests.Count - 1))]
    $procs = @()
    foreach ($req in $batch) {
        $arg = "-sS -m 50 -o `"$($req.file)`" -A `"Mozilla/5.0`" -H `"Accept: application/json`" -H `"X-Requested-With: XMLHttpRequest`" `"$($req.url)`""
        $procs += Start-Process -FilePath curl.exe -ArgumentList $arg -WindowStyle Hidden -PassThru
    }
    $procs | Wait-Process
    $done = [Math]::Min($i + $wave, $requests.Count)
    if ($done % 30 -eq 0 -or $done -eq $requests.Count) {
        Write-Host "  $done / $($requests.Count)"
    }
}

$failed = @($requests | Where-Object {
    -not (Test-Path $_.file) -or (Get-Item $_.file).Length -lt 200
})
if ($failed.Count -gt 0) {
    Write-Host "Retry $($failed.Count) short files..."
    foreach ($req in $failed) {
        Save-Url $req.url $req.file | Out-Null
        Start-Sleep -Milliseconds 200
    }
}

function Flat([object]$value) {
    return (("" + $value) -replace "[\r\n]+", " ").Trim()
}

function Quote-Csv([object]$value) {
    $text = Flat $value
    return '"' + ($text -replace '"', '""') + '"'
}

function Product-Score([string]$categoryTitle, [string]$productTitle) {
    $cat = ($categoryTitle.ToLower() -replace "[^a-z0-9]+", " ").Trim()
    $prod = ($productTitle.ToLower() -replace "[^a-z0-9]+", " ").Trim()
    if (-not $prod) { return 0 }
    $score = 0
    foreach ($word in ($cat -split "\s+" | Where-Object { $_.Length -ge 3 })) {
        if ($prod.Contains($word)) { $score += 10 + $word.Length }
    }
    if ($prod -eq $cat) { $score += 40 }
    return $score
}

function Pick-Price($campaign, [string]$categoryTitle) {
    $items = @($campaign.campaign_products)
    if (-not $items -or $items.Count -eq 0) { return "" }
    $enabled = @($items | Where-Object { "$($_.enable)" -eq "1" -or $_.enable -eq $true })
    if ($enabled.Count -eq 0) { $enabled = $items }
    $best = $null
    $bestScore = -1
    foreach ($item in $enabled) {
        $title = ""
        if ($item.product) { $title = [string]$item.product.title }
        $score = Product-Score $categoryTitle $title
        if ($score -gt $bestScore) {
            $bestScore = $score
            $best = $item
        }
    }
    if ($null -eq $best -or $bestScore -le 0) {
        $best = $enabled | Sort-Object { [double]$_.price } | Select-Object -First 1
    }
    if ($null -eq $best -or $null -eq $best.price) { return "" }
    return ("{0:0.00}" -f [double]$best.price)
}

function Collect-Images($campaign) {
    $urls = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    foreach ($item in @($campaign.campaign_products)) {
        $raw = $item.mockups
        if (-not $raw) { continue }
        try {
            $mockups = $raw | ConvertFrom-Json
        } catch {
            continue
        }
        foreach ($mock in @($mockups)) {
            foreach ($key in @("front", "back")) {
                $url = [string]$mock.$key
                if ($url -and $url.StartsWith("http") -and -not $seen.ContainsKey($url)) {
                    $seen[$url] = $true
                    $urls.Add($url)
                    if ($urls.Count -ge 8) { return ($urls -join ",") }
                }
            }
        }
    }
    return ($urls -join ",")
}

$rows = @{}
$parsed = 0
foreach ($req in $requests) {
    if (-not (Test-Path $req.file) -or (Get-Item $req.file).Length -lt 200) {
        Write-Host "SKIP $($req.slug) p$($req.page)"
        continue
    }
    try {
        $json = Get-Content -Raw $req.file | ConvertFrom-Json
    } catch {
        Write-Host "BAD JSON $($req.slug) p$($req.page)"
        continue
    }
    $parsed++
    $catTitle = $req.title
    if ($json.category -and $json.category.title) { $catTitle = [string]$json.category.title }
    foreach ($entry in @($json.campaigns.data)) {
        $camp = $entry.campaign
        if (-not $camp -or -not $camp.id) { continue }
        if (-not $camp.campaign_products -or @($camp.campaign_products).Count -eq 0) { continue }
        $id = [string]$camp.id
        $price = Pick-Price $camp $catTitle
        $link = "https://featherslife.com/campaign/$($camp.slug)"
        if ($rows.ContainsKey($id)) {
            $existing = $rows[$id]
            $names = @($existing.cate -split ", " | Where-Object { $_ })
            if ($names -notcontains $catTitle) {
                $existing.cate = (@($names + $catTitle) -join ", ")
            }
            if (-not $existing.price -and $price) { $existing.price = $price }
            if (-not $existing.images) { $existing.images = Collect-Images $camp }
            continue
        }
        $rows[$id] = [pscustomobject]@{
            title  = Flat $camp.title
            price  = $price
            cate   = $catTitle
            link   = $link
            images = Collect-Images $camp
        }
    }
}

Write-Host "Parsed $parsed pages, unique $($rows.Count)"
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('"title","price","cate","link","images"')
foreach ($row in ($rows.Values | Sort-Object title, link)) {
    $lines.Add((@(
        (Quote-Csv $row.title),
        (Quote-Csv $row.price),
        (Quote-Csv $row.cate),
        (Quote-Csv $row.link),
        (Quote-Csv $row.images)
    ) -join ","))
}
$utf8 = New-Object System.Text.UTF8Encoding $true
[System.IO.File]::WriteAllLines($outCsv, $lines, $utf8)
Write-Host "WROTE $($lines.Count - 1) -> $outCsv"
