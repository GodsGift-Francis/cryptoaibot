<?php

namespace App\Services;

use App\Models\User;
use Illuminate\Http\Request;

/** Who performed a control action, for the audit trail. */
final class ControlActor
{
    public function __construct(
        public readonly string $actor,
        public readonly string $channel,
        public readonly ?User $user = null,
        public readonly ?string $ip = null,
        public readonly ?string $userAgent = null,
    ) {}

    public static function web(Request $request): self
    {
        $user = $request->user();
        return new self($user->email, 'web', $user, $request->ip(), substr((string) $request->userAgent(), 0, 500));
    }

    public static function telegram(string $chatId): self
    {
        return new self("telegram:{$chatId}", 'telegram');
    }

    public static function cli(string $who = 'artisan'): self
    {
        return new self($who, 'cli');
    }
}
