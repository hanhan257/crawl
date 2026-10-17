# Crawl toan bo galorezcollection.com (WooCommerce Store API) -> CSV.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$pageDir = Join-Path $root "output\_galorez_pages"
$csvPath = Join-Path $root "output\galorezcollection_products.csv"
$logPath = Join-Path $root "output\_galorez_crawl.log"
New-Item -ItemType Directory -Force -Path $pageDir | Out-Null

$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
$base = "https://galorezcollection.com/wp-json/wc/store/v1/products?per_page=100&page="

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host $line
}

Write-Log "probe total"
$probe = curl.exe -sI -A $ua "${base}1"
$total = 0
$pages = 0
foreach ($line in $probe) {
    if ($line -match "^[Xx]-[Ww][Pp]-[Tt]otal:\s*(\d+)") { $total = [int]$Matches[1] }
    if ($line -match "^[Xx]-[Ww][Pp]-[Tt]otal[Pp]ages:\s*(\d+)") { $pages = [int]$Matches[1] }
}
if ($pages -lt 1) { throw "Khong doc duoc X-WP-TotalPages" }
Write-Log "products=$total pages=$pages"

$download = {
    param($page, $dir, $ua, $base)
    $dest = Join-Path $dir ("{0:D4}.json" -f $page)
    if (Test-Path $dest) {
        $existing = [System.IO.File]::ReadAllText($dest).Trim()
        if ($existing.StartsWith("[") -and $existing.EndsWith("]") -and $existing.Length -gt 2) {
            return "skip $page"
        }
    }
    $url = "$base$page"
    $tmp = "$dest.part"
    for ($i = 1; $i -le 6; $i++) {
        & curl.exe -fsS --compressed --max-time 90 -A $ua -o $tmp $url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $tmp)) {
            $text = [System.IO.File]::ReadAllText($tmp).Trim()
            if ($text.StartsWith("[") -and $text.EndsWith("]")) {
                Move-Item -Force $tmp $dest
                return "ok $page"
            }
        }
        Start-Sleep -Seconds ([Math]::Min(15, $i * 2))
    }
    if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    return "fail $page"
}

$pool = [runspacefactory]::CreateRunspacePool(1, 10)
$pool.Open()

function Invoke-Pages([int[]]$pageList) {
    $batchSize = 40
    $failed = New-Object System.Collections.Generic.List[int]
    for ($offset = 0; $offset -lt $pageList.Count; $offset += $batchSize) {
        $end = [Math]::Min($offset + $batchSize, $pageList.Count) - 1
        $slice = $pageList[$offset..$end]
        $handles = @()
        foreach ($page in $slice) {
            $ps = [powershell]::Create()
            $ps.RunspacePool = $pool
            [void]$ps.AddScript($download).AddArgument($page).AddArgument($pageDir).AddArgument($ua).AddArgument($base)
            $handles += [pscustomobject]@{ Pipe = $ps; Async = $ps.BeginInvoke(); Page = $page }
        }
        foreach ($h in $handles) {
            $result = $h.Pipe.EndInvoke($h.Async)
            $h.Pipe.Dispose()
            $text = [string]$result
            if ($text -match "fail") { $failed.Add($h.Page) }
        }
        Write-Log ("fetched through page {0}/{1} failed_batch={2}" -f $slice[-1], $pages, $failed.Count)
    }
    return @($failed)
}

$missing = @()
for ($n = 1; $n -le $pages; $n++) {
    $dest = Join-Path $pageDir ("{0:D4}.json" -f $n)
    if (-not (Test-Path $dest)) { $missing += $n }
}
Write-Log "missing=$($missing.Count)"
if ($missing.Count -gt 0) {
    $failed = Invoke-Pages $missing
    if ($failed.Count -gt 0) {
        Write-Log "retry failed=$($failed.Count)"
        $failed = Invoke-Pages $failed
    }
    if ($failed.Count -gt 0) {
        Write-Log "still_failed=$($failed -join ',')"
    }
}
$pool.Close()
$pool.Dispose()

$utf8 = New-Object System.Text.UTF8Encoding $true
$writer = New-Object System.IO.StreamWriter($csvPath, $false, $utf8)
function Escape-Csv([string]$value) {
    if ($null -eq $value) { return '""' }
    return '"' + ($value.Replace('"', '""')) + '"'
}
$writer.WriteLine("title,price,link,images,cate")
$seen = @{}
$rows = 0
$shortPages = @()
try {
    for ($n = 1; $n -le $pages; $n++) {
        $dest = Join-Path $pageDir ("{0:D4}.json" -f $n)
        if (-not (Test-Path $dest)) {
            $shortPages += $n
            continue
        }
        $items = [System.IO.File]::ReadAllText($dest) | ConvertFrom-Json
        $count = @($items).Count
        if ($n -lt $pages -and $count -ne 100) { $shortPages += $n }
        foreach ($item in @($items)) {
            $id = [string]$item.id
            if (-not $id -or $seen.ContainsKey($id)) { continue }
            $seen[$id] = $true
            $minor = 2
            if ($item.prices -and $null -ne $item.prices.currency_minor_unit) {
                $minor = [int]$item.prices.currency_minor_unit
            }
            $price = ""
            $rawPrice = $item.prices.price
            if ($null -ne $rawPrice -and "$rawPrice" -ne "") {
                $div = [math]::Pow(10, $minor)
                $price = ([decimal]$rawPrice / $div).ToString("0.00", [Globalization.CultureInfo]::InvariantCulture)
            }
            $title = [System.Net.WebUtility]::HtmlDecode([string]$item.name)
            $link = [string]$item.permalink
            $images = (@($item.images) | ForEach-Object { [string]$_.src } | Where-Object { $_ }) -join " | "
            $cate = (@($item.categories) | ForEach-Object { [System.Net.WebUtility]::HtmlDecode([string]$_.name) } | Where-Object { $_ }) -join ", "
            $writer.WriteLine(("{0},{1},{2},{3},{4}" -f (Escape-Csv $title), (Escape-Csv $price), (Escape-Csv $link), (Escape-Csv $images), (Escape-Csv $cate)))
            $rows++
        }
        if ($n % 50 -eq 0 -or $n -eq $pages) {
            Write-Log "csv page $n/$pages rows=$rows"
        }
    }
}
finally {
    $writer.Flush()
    $writer.Dispose()
}

Write-Log "done rows=$rows expected=$total short=$($shortPages -join ',')"
if ($rows -ne $total) { exit 2 }
