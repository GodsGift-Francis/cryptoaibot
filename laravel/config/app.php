<?php

// NOTE (V1.1 fix): V1 set 'providers' => [] here. Laravel only merges top-level keys for
// config/app.php, so that REPLACED the framework's default providers (database, session,
// view, cache...) and the app could not boot. App providers live in bootstrap/providers.php.
return [
 'name'=>env('APP_NAME','Crypto Trading Control Plane'),
 'env'=>env('APP_ENV','production'),
 'debug'=>(bool)env('APP_DEBUG',false),
 'url'=>env('APP_URL','http://localhost'),
 'timezone'=>'UTC',
 'locale'=>'en', 'fallback_locale'=>'en', 'faker_locale'=>'en_US',
 'key'=>env('APP_KEY'), 'cipher'=>'AES-256-CBC',
 'maintenance'=>['driver'=>'file'],
];
