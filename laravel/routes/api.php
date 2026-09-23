<?php

use App\Http\Controllers\Internal\V1\BotApiController;
use Illuminate\Support\Facades\Route;

// Internal engine API. Separate from browser routes; token + instance + timestamp + request-id checked,
// POSTs replay-protected by X-Request-Id. Also restrict to localhost at the web server (deploy/nginx).
Route::middleware(['bot.internal', 'throttle:bot-internal', 'bot.idempotent'])
    ->prefix('internal/v1/bots/{instance}')
    ->where(['instance' => '[A-Za-z0-9_\-]{1,64}'])
    ->group(function () {
        Route::get('/control', [BotApiController::class, 'control']);
        Route::post('/heartbeat', [BotApiController::class, 'heartbeat']);
        Route::post('/signals', [BotApiController::class, 'signals']);
        Route::post('/orders', [BotApiController::class, 'orders']);
        Route::post('/fills', [BotApiController::class, 'fills']);
        Route::post('/positions', [BotApiController::class, 'positions']);
        Route::post('/reconciliation', [BotApiController::class, 'reconciliation']);
        Route::post('/events', [BotApiController::class, 'events']);
    });
