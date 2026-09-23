@extends('layout', ['title' => 'Sign in'])
@section('content')
<div style="max-width:380px;margin:10vh auto">
  <h1>Control plane</h1>
  <p class="muted">Sign in to operate the trading bot.</p>
  @if ($errors->any())<div class="error">{{ $errors->first() }}</div>@endif
  <form method="post" action="/login" class="card">
    @csrf
    <label class="label" for="email">Email</label>
    <input id="email" name="email" type="email" value="{{ old('email') }}" required autofocus autocomplete="username">
    <label class="label" for="password">Password</label>
    <input id="password" name="password" type="password" required autocomplete="current-password">
    <button class="normal" type="submit">Sign in</button>
  </form>
</div>
@endsection
