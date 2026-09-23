<?php

use App\Http\Controllers\Auth\LoginController;
use App\Http\Controllers\DashboardController;
use App\Http\Controllers\TelegramController;
use Illuminate\Support\Facades\Route;

// Public: login only. Everything else requires an authenticated session.
Route::middleware('guest')->group(function () {
    Route::get('/login', [LoginController::class, 'show'])->name('login');
    Route::post('/login', [LoginController::class, 'login'])->middleware('throttle:login');
});

Route::middleware('auth')->group(function () {
    Route::redirect('/', '/dashboard');
    Route::get('/dashboard', [DashboardController::class, 'index'])->name('dashboard');
    Route::post('/logout', [LoginController::class, 'logout'])->name('logout');

    // Trading controls: POST + CSRF (web group) + role gate + audit (BotControlService).
    Route::middleware(['can:operate-bot', 'throttle:controls'])->prefix('bot')->group(function () {
        Route::post('/pause', [DashboardController::class, 'pause'])->name('bot.pause');
        Route::post('/resume', [DashboardController::class, 'resume'])->name('bot.resume');
        Route::post('/emergency-stop', [DashboardController::class, 'emergencyStop'])->name('bot.emergency');
    });
    Route::middleware(['can:administer-bot', 'throttle:controls'])->prefix('bot')->group(function () {
        Route::post('/clear-emergency-stop', [DashboardController::class, 'clearEmergencyStop'])->name('bot.clear-emergency');
        Route::post('/acknowledge-reconciliation', [DashboardController::class, 'acknowledgeReconciliation'])->name('bot.ack-reconciliation');
    });
});

// Telegram webhook: secret path + allow-listed chat ids + rate limit (CSRF-exempt in bootstrap/app.php).
Route::post('/telegram/webhook/{secret}', [TelegramController::class, 'webhook'])->middleware('throttle:telegram');
