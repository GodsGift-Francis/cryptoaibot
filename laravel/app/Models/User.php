<?php

namespace App\Models;

use Illuminate\Foundation\Auth\User as Authenticatable;
use Illuminate\Notifications\Notifiable;

class User extends Authenticatable
{
    use Notifiable;

    public const ROLES = ['admin', 'operator', 'viewer'];

    protected $fillable = ['name', 'email', 'password', 'role'];

    protected $hidden = ['password', 'remember_token'];

    protected function casts(): array
    {
        return ['email_verified_at' => 'datetime', 'last_login_at' => 'datetime', 'password' => 'hashed'];
    }

    public function canOperateBot(): bool
    {
        return in_array($this->role, ['admin', 'operator'], true);
    }

    public function isAdmin(): bool
    {
        return $this->role === 'admin';
    }
}
