<?php
/**
 * Dan vao functions.php cua theme child (veexovar.com)
 * Fix import anh tu Redbubble / CDN co dau phay trong URL
 */

// WooCommerce dung | thay vi , de tach nhieu anh (URL Redbubble co dau phay)
add_filter('woocommerce_product_import_image_separator', function () {
    return '|';
});

// WordPress tai anh tu Redbubble CDN (them Referer + User-Agent)
add_filter('http_request_args', function ($args, $url) {
    if (is_string($url) && str_contains($url, 'redbubble.net')) {
        $args['headers']['Referer'] = 'https://www.redbubble.com/';
        $args['headers']['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
        $args['timeout'] = 30;
    }
    return $args;
}, 10, 2);
