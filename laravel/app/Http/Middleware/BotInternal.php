<?php

namespace App\Http\Middleware;

use App\Models\BotInstance;
use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Authenticates the Python engine on /api/internal/v1.
 *
 *  - Bearer token compared in constant time (hash_equals)
 *  - X-Bot-Instance must equal the {instance} route parameter and an existing bot
 *  - X-Request-Timestamp must be within the allowed clock skew (stale requests rejected)
 *  - X-Request-Id must be present and well formed (used for idempotency)
 */
class BotInternal
{
    public function handle(Request $request, Closure $next): Response
    {
        $expected = (string) config('trading.internal_token');
        if (strlen($expected) < 32 || ! hash_equals($expected, (string) $request->bearerToken())) {
            return response()->json(['message' => 'Unauthorized'], 401);
        }

        $instance = (string) $request->route('instance');
        if (! hash_equals($instance, (string) $request->header('X-Bot-Instance'))) {
            return response()->json(['message' => 'Bot instance header mismatch'], 403);
        }

        $ts = $request->header('X-Request-Timestamp');
        $skew = (int) config('trading.max_request_skew_seconds', 300);
        if (! ctype_digit((string) $ts) || abs(time() - (int) $ts) > $skew) {
            return response()->json(['message' => 'Stale or missing X-Request-Timestamp'], 401);
        }

        $rid = (string) $request->header('X-Request-Id');
        if (! preg_match('/^[A-Za-z0-9:\/._#+\-]{8,191}$/', $rid)) {
            return response()->json(['message' => 'Missing or invalid X-Request-Id'], 400);
        }

        $bot = BotInstance::where('name', $instance)->first();
        if (! $bot) {
            // Unknown instances are never auto-created: the engine must fail closed.
            return response()->json(['message' => 'Unknown bot instance'], 404);
        }
        $request->attributes->set('bot', $bot);

        return $next($request);
    }
}
