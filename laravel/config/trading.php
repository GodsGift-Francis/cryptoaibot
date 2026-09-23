<?php

return [
    'internal_token' => env('TRADING_BOT_INTERNAL_TOKEN', ''),
    'instance_id' => env('BOT_INSTANCE_ID', 'default'),
    'max_request_skew_seconds' => (int) env('INTERNAL_API_MAX_SKEW_SECONDS', 300),
    'heartbeat_stale_seconds' => (int) env('BOT_HEARTBEAT_STALE_SECONDS', 180),
    'internal_request_retention_days' => (int) env('INTERNAL_REQUEST_RETENTION_DAYS', 14),
    'telegram_token' => env('TELEGRAM_BOT_TOKEN', ''),
    'telegram_allowed_chat_ids' => array_values(array_filter(array_map('trim', explode(',', (string) env('TELEGRAM_ALLOWED_CHAT_IDS', ''))))),
    'telegram_webhook_secret' => env('TELEGRAM_WEBHOOK_SECRET', ''),
];
