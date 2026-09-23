<?php

namespace Tests\Feature;

use App\Models\BotInstance;
use App\Models\Fill;
use App\Models\Order;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

class InternalApiTest extends TestCase
{
    use RefreshDatabase;

    private const TOKEN = 'test-token-0123456789-0123456789-abcdef';

    protected function setUp(): void
    {
        parent::setUp();
        BotInstance::forName('default')->update(['enabled' => true]);
    }

    private function h(?string $rid = null, array $over = []): array
    {
        return array_merge([
            'Authorization' => 'Bearer '.self::TOKEN, 'X-Bot-Instance' => 'default',
            'X-Request-Id' => $rid ?? 'req-'.uniqid(), 'X-Request-Timestamp' => (string) time(), 'Accept' => 'application/json',
        ], $over);
    }

    private function order(int $version, string $status, string $executed): array
    {
        return ['bot_instance' => 'default', 'client_order_id' => 'cb1-abc', 'client_intent_id' => 'intent-1', 'signal_id' => null,
            'exchange_order_id' => '42', 'exchange' => 'binance', 'symbol' => 'BTC/USDT', 'side' => 'BUY', 'type' => 'MARKET',
            'purpose' => 'ENTRY', 'status' => $status, 'requested_quantity' => '1', 'executed_quantity' => $executed,
            'cumulative_quote_quantity' => '100', 'average_fill_price' => null, 'limit_price' => null, 'stop_price' => null,
            'submission_ambiguous' => false, 'reject_reason' => null, 'submitted_at' => now()->toIso8601String(),
            'acknowledged_at' => null, 'last_exchange_update_at' => null, 'terminal_at' => null, 'version' => $version];
    }

    public function test_rejects_missing_or_wrong_token(): void
    {
        $this->getJson('/api/internal/v1/bots/default/control')->assertStatus(401);
        $this->getJson('/api/internal/v1/bots/default/control', $this->h(null, ['Authorization' => 'Bearer nope']))->assertStatus(401);
    }

    public function test_rejects_stale_timestamp_and_instance_mismatch(): void
    {
        $this->getJson('/api/internal/v1/bots/default/control', $this->h(null, ['X-Request-Timestamp' => (string) (time() - 3600)]))->assertStatus(401);
        $this->getJson('/api/internal/v1/bots/default/control', $this->h(null, ['X-Bot-Instance' => 'other']))->assertStatus(403);
    }

    public function test_unknown_instance_is_not_auto_created(): void
    {
        $this->getJson('/api/internal/v1/bots/ghost/control', $this->h(null, ['X-Bot-Instance' => 'ghost']))->assertStatus(404);
        $this->assertNull(BotInstance::where('name', 'ghost')->first());
    }

    public function test_control_returns_booleans(): void
    {
        $this->getJson('/api/internal/v1/bots/default/control', $this->h())
            ->assertOk()->assertJson(['enabled' => true, 'emergency_stop' => false]);
    }

    public function test_payload_validation(): void
    {
        $this->postJson('/api/internal/v1/bots/default/orders', ['bot_instance' => 'default'], $this->h())->assertStatus(422);
        $this->postJson('/api/internal/v1/bots/default/orders', array_merge($this->order(1, 'NEW', '0'), ['bot_instance' => 'other']), $this->h())->assertStatus(422);
    }

    public function test_order_mirror_is_monotonic_by_engine_version(): void
    {
        $this->postJson('/api/internal/v1/bots/default/orders', $this->order(3, 'FILLED', '1'), $this->h())->assertOk();
        $this->postJson('/api/internal/v1/bots/default/orders', $this->order(2, 'PARTIALLY_FILLED', '0.4'), $this->h())->assertOk();
        $o = Order::first();
        $this->assertSame('FILLED', $o->status);
        $this->assertEquals(1, (float) $o->executed_quantity);
    }

    public function test_request_id_replay_and_fill_idempotency(): void
    {
        $fill = ['bot_instance' => 'default', 'event_key' => 'binance:main:BTCUSDT:42:77:TRADE', 'client_order_id' => 'cb1-abc',
            'exchange_trade_id' => '77', 'symbol' => 'BTC/USDT', 'side' => 'BUY', 'quantity' => '0.4', 'price' => '100',
            'quote_quantity' => '40', 'commission' => '0.0004', 'commission_asset' => 'BTC', 'execution_time' => now()->toIso8601String()];
        $this->postJson('/api/internal/v1/bots/default/fills', $fill, $this->h('fill:k1'))->assertOk();
        $this->postJson('/api/internal/v1/bots/default/fills', $fill, $this->h('fill:k1'))->assertOk()->assertHeader('X-Idempotent-Replay', 'true');
        $this->postJson('/api/internal/v1/bots/default/fills', $fill, $this->h('fill:k2'))->assertOk()->assertJson(['inserted' => false]);
        $this->assertSame(1, Fill::count());
    }

    public function test_internal_api_is_not_available_via_browser_session(): void
    {
        $user = \App\Models\User::create(['name' => 'a', 'email' => 'a@x.test', 'password' => 'correct-horse-battery', 'role' => 'admin']);
        $this->actingAs($user)->getJson('/api/internal/v1/bots/default/control')->assertStatus(401);
    }
}
