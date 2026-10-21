# Parse already-downloaded wearandjoy catalog pages into CSV.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$outCsv = Join-Path $root "output\wearandjoy_products.csv"
$work = Join-Path $env:TEMP "wearandjoy-crawl\pages"
$shop = "https://wearandjoy.com"
New-Item -ItemType Directory -Force -Path (Split-Path $outCsv) | Out-Null

function Read-JsonFile([string]$path) {
    try {
        $raw = [System.IO.File]::ReadAllText($path)
        if ([string]::IsNullOrWhiteSpace($raw) -or -not $raw.TrimStart().StartsWith("{")) { return $null }
        if (-not $raw.TrimEnd().EndsWith("}")) { return $null }
        return $raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

$products = @{}
$files = @(Get-ChildItem $work -Filter *.json)
Write-Host ("files={0}" -f $files.Count)
$bad = 0
$n = 0
foreach ($f in $files) {
    $n++
    $payload = Read-JsonFile $f.FullName
    if ($null -eq $payload -or $null -eq $payload.result) { $bad++; continue }
    foreach ($item in @($payload.result.items)) {
        if ($null -eq $item -or $null -eq $item.id) { continue }
        $id = [string]$item.id
        if ($products.ContainsKey($id)) { continue }
        $handle = [string]$item.handle
        $title = [System.Net.WebUtility]::HtmlDecode([string]$item.title).Trim()
        if (-not $handle -or -not $title) { continue }
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
        $products[$id] = @{
            title  = $title
            cate   = $cate
            price  = $price
            images = ($imgs -join " | ")
            link   = "$shop/products/$handle"
        }
    }
    if ($n % 500 -eq 0) {
        Write-Host ("parsed {0}/{1} unique={2} bad={3}" -f $n, $files.Count, $products.Count, $bad)
    }
}
Write-Host ("parsed {0} unique={1} bad={2}" -f $files.Count, $products.Count, $bad)

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
Write-Host ("done rows={0} file={1}" -f $products.Count, $outCsv)
