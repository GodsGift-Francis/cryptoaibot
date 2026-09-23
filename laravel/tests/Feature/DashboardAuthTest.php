<?php

namespace Tests\Feature;

use App\Models\AuditLog;
use App\Models\BotInstance;
use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

class DashboardAuthTest extends TestCase
{
    use RefreshDatabase;

    private function bot(): BotInstance
    {
        return BotInstance::forName('default');
    }

    public function test_dashboard_and_controls_require_authentication(): void
    {
        $this->get('/dashboard')->assertRedirect('/login');
        $this->post('/bot/pause')->assertRedirect('/login');
        $this->post('/bot/emergency-stop')->assertRedirect('/login');
    }

    public function test_viewer_can_see_but_not_control(): void
    {
        $viewer = User::create(['name' => 'v', 'email' => 'v@x.test', 'password' => 'correct-horse-battery', 'role' => 'viewer']);
        $this->actingAs($viewer)->get('/dashboard')->assertOk()->assertDontSee('Emergency stop');
        $this->actingAs($viewer)->post('/bot/emergency-stop')->assertForbidden();
    }

    public function test_operator_actions_are_audited_and_resume_refused_during_emergency(): void
    {
        $op = User::create(['name' => 'o', 'email' => 'o@x.test', 'password' => 'correct-horse-battery', 'role' => 'operator']);
        $bot = $this->bot();
        $this->actingAs($op)->post('/bot/emergency-stop')->assertRedirect();
        $this->assertTrue($bot->fresh()->emergency_stop);
        $this->actingAs($op)->post('/bot/resume');
        $this->assertFalse($bot->fresh()->enabled, 'resume must not bypass an emergency stop');
        $this->actingAs($op)->post('/bot/clear-emergency-stop')->assertForbidden();   // admin only
        $this->assertSame(1, AuditLog::where('action', 'emergency_stop')->where('actor', 'o@x.test')->count());
    }

    public function test_admin_can_clear_emergency_and_acknowledge_halt(): void
    {
        $admin = User::create(['name' => 'a', 'email' => 'a@x.test', 'password' => 'correct-horse-battery', 'role' => 'admin']);
        $bot = $this->bot();
        $bot->update(['emergency_stop' => true, 'reconciliation_state' => 'HALTED']);
        $this->actingAs($admin)->post('/bot/clear-emergency-stop')->assertRedirect();
        $this->assertFalse($bot->fresh()->emergency_stop);
        $this->assertFalse($bot->fresh()->enabled, 'clearing must leave the bot paused');
        $this->actingAs($admin)->post('/bot/acknowledge-reconciliation');
        $this->assertNotNull($bot->fresh()->reconciliation_ack_token);
    }

    public function test_login_logout_and_bad_password(): void
    {
        User::create(['name' => 'o', 'email' => 'o@x.test', 'password' => 'correct-horse-battery', 'role' => 'operator']);
        $this->post('/login', ['email' => 'o@x.test', 'password' => 'wrong'])->assertSessionHasErrors('email');
        $this->assertGuest();
        $this->post('/login', ['email' => 'o@x.test', 'password' => 'correct-horse-battery'])->assertRedirect('/dashboard');
        $this->assertAuthenticated();
        $this->post('/logout')->assertRedirect('/login');
        $this->assertGuest();
    }
}
