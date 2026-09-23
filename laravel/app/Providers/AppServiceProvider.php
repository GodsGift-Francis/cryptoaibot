<?php

namespace App\Providers;

use App\Models\User;
use Illuminate\Cache\RateLimiting\Limit;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Gate;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    public function register(): void {}

    public function boot(): void
    {
        // Authorization for trading controls.
        Gate::define('operate-bot', fn (User $u) => $u->canOperateBot());     // pause / resume / emergency stop
        Gate::define('administer-bot', fn (User $u) => $u->isAdmin());       // clear emergency, acknowledge halt

        RateLimiter::for('login', fn (Request $r) => [
            Limit::perMinute(5)->by(strtolower((string) $r->input('email')).'|'.$r->ip()),
            Limit::perMinute(20)->by($r->ip()),
        ]);
        RateLimiter::for('controls', fn (Request $r) => Limit::perMinute(20)->by((string) $r->user()?->id));
        RateLimiter::for('telegram', fn (Request $r) => Limit::perMinute(60)->by($r->ip()));
        // Engine traffic: heartbeats + outbox bursts after an outage; per bot instance.
        RateLimiter::for('bot-internal', fn (Request $r) => Limit::perMinute(600)->by((string) $r->route('instance')));
    }
}
