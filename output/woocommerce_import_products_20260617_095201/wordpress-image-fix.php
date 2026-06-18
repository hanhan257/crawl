<?php
/**
 * Dan vao functions.php cua theme child (veexovar.com)
 * Fix import + hien thi anh Redbubble / URL bi double domain
 */

// WooCommerce dung | thay vi , de tach nhieu anh (URL Redbubble co dau phay)
add_filter('woocommerce_product_import_image_separator', function () {
    return '|';
});

function veexovar_fix_scheme_slash($url) {
    if (!is_string($url) || $url === '') {
        return $url;
    }
    // http:/site.com -> http://site.com
    return preg_replace('#^(https?):/([^/])#i', '$1://$2', $url);
}

function veexovar_is_placeholder_url($url) {
    if (!is_string($url) || $url === '') {
        return true;
    }
    $lower = strtolower($url);
    return str_contains($lower, 'woocommerce-placeholder')
        || str_contains($lower, 'placeholder');
}

function veexovar_fix_doubled_image_url($url) {
    if (!is_string($url) || $url === '') {
        return $url;
    }

    $url = veexovar_fix_scheme_slash($url);

    // https://site.com/http://site.com/path
    if (preg_match('#^https?://[^/]+/(https?://.+)$#i', $url, $matches)) {
        return veexovar_fix_scheme_slash($matches[1]);
    }

    // https://site.com/http:/site.com/path
    if (preg_match('#^https?://[^/]+/(https?:/.+)$#i', $url, $matches)) {
        return veexovar_fix_scheme_slash($matches[1]);
    }

    return $url;
}

function veexovar_normalize_import_image_url($url) {
    $url = veexovar_fix_doubled_image_url(trim((string) $url));
    if ($url === '' || veexovar_is_placeholder_url($url)) {
        return '';
    }

    // Chi cho phep URL ngoai (Redbubble, CDN...) — khong import lai URL site minh
    $host = wp_parse_url($url, PHP_URL_HOST);
    $site_host = wp_parse_url(home_url(), PHP_URL_HOST);
    if ($host && $site_host && strcasecmp($host, $site_host) === 0) {
        // URL noi bo site — bo qua de tranh double domain / placeholder loi
        if (str_contains(strtolower($url), 'wp-content/uploads')) {
            return '';
        }
    }

    if (!preg_match('#^https?://#i', $url)) {
        return '';
    }

    return $url;
}

// Sua URL truoc khi WooCommerce tai anh ve server
add_filter('woocommerce_product_importer_parsed_data', function ($data) {
    if (empty($data['images']) || !is_array($data['images'])) {
        return $data;
    }

    $fixed = [];
    foreach ($data['images'] as $image) {
        $src = is_array($image) ? ($image['src'] ?? '') : (string) $image;
        $src = veexovar_normalize_import_image_url($src);
        if ($src !== '') {
            $fixed[] = ['src' => $src];
        }
    }
    $data['images'] = $fixed;
    return $data;
}, 20);

add_filter('wp_get_attachment_url', 'veexovar_fix_doubled_image_url', 99);
add_filter('wp_get_attachment_image_src', function ($image) {
    if (is_array($image) && !empty($image[0])) {
        $image[0] = veexovar_fix_doubled_image_url($image[0]);
    }
    return $image;
}, 99);

add_filter('woocommerce_product_get_image', function ($html) {
    return preg_replace_callback(
        '#\ssrc=(["\'])(https?://[^"\']+)\1#i',
        function ($m) {
            return ' src=' . $m[1] . esc_url(veexovar_fix_doubled_image_url($m[2])) . $m[1];
        },
        $html
    );
}, 99);

// WordPress tai anh tu Redbubble CDN
add_filter('http_request_args', function ($args, $url) {
    if (is_string($url) && str_contains($url, 'redbubble.net')) {
        $args['headers']['Referer'] = 'https://www.redbubble.com/';
        $args['headers']['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
        $args['timeout'] = 45;
        $args['reject_unsafe_urls'] = false;
    }
    return $args;
}, 10, 2);

add_filter('http_request_host_is_external', function ($is_external, $host) {
    if (str_contains($host, 'redbubble.net')) {
        return true;
    }
    return $is_external;
}, 10, 2);
