<?php
/**
 * Dan vao functions.php cua theme child (veexovar.com)
 * Fix import anh tu Redbubble / CDN + URL bi double domain
 */

// WooCommerce dung | thay vi , de tach nhieu anh (URL Redbubble co dau phay)
add_filter('woocommerce_product_import_image_separator', function () {
    return '|';
});

/**
 * Fix URL dang: https://site.com/http://site.com/wp-content/...
 */
function veexovar_fix_doubled_image_url($url) {
    if (!is_string($url) || $url === '') {
        return $url;
    }
    if (preg_match('#^https?://[^/]+/(https?://.+)$#i', $url, $matches)) {
        return $matches[1];
    }
    return $url;
}

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

// WordPress tai anh tu Redbubble CDN (them Referer + User-Agent)
add_filter('http_request_args', function ($args, $url) {
    if (is_string($url) && str_contains($url, 'redbubble.net')) {
        $args['headers']['Referer'] = 'https://www.redbubble.com/';
        $args['headers']['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
        $args['timeout'] = 30;
    }
    return $args;
}, 10, 2);
