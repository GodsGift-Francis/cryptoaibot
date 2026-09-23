<?php

namespace App\Http\Middleware;

use App\Models\InternalApiRequest;
use Closure;
use Illuminate\Database\QueryException;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Replays the stored response for a repeated X-Request-Id instead of re-executing
 * the write. The engine's outbox reuses one request id per logical message, so an
 * at-least-once redelivery never double-applies.
 */
class EnsureIdempotentRequest
{
    public function handle(Request $request, Closure $next): Response
    {
        // Heartbeats are last-write-wins status snapshots: naturally idempotent, not ledgered.
        if (! $request->isMethod('POST') || str_ends_with($request->path(), '/heartbeat')) {
            return $next($request);
        }
        $bot = (string) $request->route('instance');
        $rid = (string) $request->header('X-Request-Id');
        $endpoint = $request->route()?->uri() ?? $request->path();

        $prior = InternalApiRequest::where('bot_instance', $bot)->where('request_id', $rid)->first();
        if ($prior) {
            if ($prior->endpoint !== $endpoint) {
                return response()->json(['message' => 'X-Request-Id reused for a different endpoint'], 409);
            }
            return response()->json($prior->response_body, $prior->response_status)->header('X-Idempotent-Replay', 'true');
        }

        $response = $next($request);

        if ($response->getStatusCode() < 300) {
            try {
                InternalApiRequest::create([
                    'bot_instance' => $bot, 'request_id' => $rid, 'endpoint' => $endpoint,
                    'response_status' => $response->getStatusCode(),
                    'response_body' => json_decode((string) $response->getContent(), true),
                ]);
            } catch (QueryException) {
                // Concurrent duplicate: the unique index already holds the first result.
            }
        }

        return $response;
    }
}
