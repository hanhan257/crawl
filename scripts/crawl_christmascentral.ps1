# Crawl christmascentral.com (BigCommerce + SearchSpring) -> CSV
# title, cate, price, images (comma-separated), link
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$outCsv = Join-Path $root "output\christmascentral_products.csv"
$work = Join-Path $env:TEMP "christmascentral-crawl"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work, (Join-Path $work "pages"), (Split-Path $outCsv) | Out-Null

$api = "https://api.searchspring.net/api/search/search.json"
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
$pageSize = 100

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
        -not (Test-Path $_.file) -or (Get-Item $_.file).Length -lt 200
    })
    if ($pending.Count -eq 0) { return }
    $wave = 16
    $cfg = Join-Path $work "batch.cfg"
    for ($i = 0; $i -lt $pending.Count; $i += $wave) {
        $slice = @($pending[$i..([Math]::Min($i + $wave - 1, $pending.Count - 1))])
        Save-CurlConfig $slice $cfg
        & curl.exe --silent --show-error --parallel --parallel-max 10 --config $cfg -A $ua | Out-Null
        $done = [Math]::Min($i + $wave, $pending.Count)
        if ($done % 80 -eq 0 -or $done -eq $pending.Count) {
            Write-Host ("  downloaded {0}/{1}" -f $done, $pending.Count)
        }
    }
}

function Build-Url([string]$category, [int]$page) {
    $enc = [uri]::EscapeDataString($category)
    return $api + "?siteId=n1rgvp&resultsFormat=native&resultsPerPage=$pageSize&page=$page&filter.ss_categories_hierarchy=$enc"
}

function Read-Json([string]$path) {
    try {
        $raw = [System.IO.File]::ReadAllText($path)
        if (-not $raw.TrimStart().StartsWith("{")) { return $null }
        return $raw | ConvertFrom-Json
    } catch { return $null }
}

Write-Host "[facets]"
$facetFile = Join-Path $work "facets.json"
Invoke-Downloads @(@{ url = ($api + "?siteId=n1rgvp&resultsFormat=native&resultsPerPage=10&page=1"); file = $facetFile })
$rootJson = Read-Json $facetFile
$catFacet = @($rootJson.facets | Where-Object { $_.field -eq "ss_categories_hierarchy" })[0]
$shards = New-Object System.Collections.Generic.List[object]
$big = @()
foreach ($v in @($catFacet.values)) {
    $name = [string]$v.value
    $count = [int]$v.count
    if ($count -le 0) { continue }
    if ($count -le 10000) {
        $shards.Add([pscustomobject]@{ name = $name; count = $count })
    } else {
        $big += [pscustomobject]@{ name = $name; count = $count }
    }
}
foreach ($parent in $big) {
    Write-Host ("  split {0} count={1}" -f $parent.name, $parent.count)
    $file = Join-Path $work "split-facet.json"
    if (Test-Path $file) { Remove-Item $file -Force }
    Invoke-Downloads @(@{ url = (Build-Url $parent.name 1); file = $file })
    $split = Read-Json $file
    $facet = @($split.facets | Where-Object { $_.field -eq "ss_categories_hierarchy" })[0]
    foreach ($v in @($facet.values)) {
        $name = [string]$v.value
        if ($name -eq $parent.name) { continue }
        if (-not $name.StartsWith($parent.name + ">")) { continue }
        $depth = ($name.ToCharArray() | Where-Object { $_ -eq ">" }).Count
        if ($depth -ne 1) { continue }
        $count = [int]$v.count
        if ($count -le 0) { continue }
        if ($count -le 10000) {
            $shards.Add([pscustomobject]@{ name = $name; count = $count })
        } else {
            Write-Host ("  STILL BIG {0} {1}" -f $count, $name)
        }
    }
}
Write-Host ("[shards] {0}" -f $shards.Count)
$shards | ForEach-Object { Write-Host ("  {0}`t{1}" -f $_.count, $_.name) }

$jobs = @()
foreach ($shard in $shards) {
    $pages = [Math]::Ceiling($shard.count / $pageSize)
    if ($pages -gt 100) { $pages = 100 }
    if ($pages -lt 1) { $pages = 1 }
    $safe = ($shard.name -replace '[^\w]+', '_').Trim('_')
    if ($safe.Length -gt 60) { $safe = $safe.Substring(0, 60) }
    for ($p = 1; $p -le $pages; $p++) {
        $jobs += @{
            url  = (Build-Url $shard.name $p)
            file = Join-Path $work ("pages\{0}-p{1}.json" -f $safe, $p)
        }
    }
}
Write-Host ("[pages] {0}" -f $jobs.Count)
Invoke-Downloads $jobs

function Cate-Of($item) {
    $paths = @($item.categories_hierarchy)
    if ($paths.Count -eq 0) { $paths = @($item.ss_categories_hierarchy) }
    $clean = @()
    foreach ($path in $paths) {
        $text = [System.Net.WebUtility]::HtmlDecode([string]$path)
        $text = $text -replace '&gt;', '>'
        if (-not $text) { continue }
        $top = ($text -split '>')[0].Trim()
        if ($top -in @("Clearance", "Sale", "Shop by Collection", "Shop by Species")) { continue }
        $clean += $text
    }
    if ($clean.Count -eq 0) {
        foreach ($path in $paths) {
            $text = [System.Net.WebUtility]::HtmlDecode([string]$path) -replace '&gt;', '>'
            if ($text) { $clean += $text }
        }
    }
    if ($clean.Count -eq 0) { return "" }
    $best = $clean | Sort-Object { ($_ -split '>').Count } -Descending | Select-Object -First 1
    return (($best -split '>') | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join " > "
}

function Images-Of($item) {
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($u in @($item.imageUrl, $item.ss_image_hover, $item.custom_webimage2, $item.thumbnailImageUrl)) {
        $src = [string]$u
        if ($src -and -not $list.Contains($src)) { $list.Add($src) }
    }
    return ($list -join ",")
}

$products = @{}
$files = @(Get-ChildItem (Join-Path $work "pages") -Filter *.json)
Write-Host ("[parse] files={0}" -f $files.Count)
foreach ($f in $files) {
    $payload = Read-Json $f.FullName
    if ($null -eq $payload) { continue }
    foreach ($item in @($payload.results)) {
        $id = [string]$item.uid
        if (-not $id) { $id = [string]$item.id }
        if (-not $id -or $products.ContainsKey($id)) { continue }
        $title = [System.Net.WebUtility]::HtmlDecode([string]$item.name).Trim()
        $link = [string]$item.url
        if (-not $title -or -not $link) { continue }
        $price = ""
        if ($null -ne $item.price -and "$($item.price)" -ne "") {
            $price = ("{0:0.00}" -f [double]$item.price)
        }
        $products[$id] = @{
            title  = $title
            cate   = (Cate-Of $item)
            price  = $price
            images = (Images-Of $item)
            link   = $link
        }
    }
}
Write-Host ("[unique] {0}" -f $products.Count)

function Quote-Csv([string]$text) {
    if ($null -eq $text) { $text = "" }
    return '"' + ($text.Replace('"', '""') -replace "[\r\n]+", " ") + '"'
}
$utf8 = New-Object System.Text.UTF8Encoding $true
$sw = New-Object System.IO.StreamWriter($outCsv, $false, $utf8)
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
